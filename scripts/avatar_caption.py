"""Stage D 字幕（Hypit 风格富样式）：ASS 生成 + ffmpeg 烧录。

能力对照（迁移自 Hypit caption-fine，用 ASS/libass 实现）：
  - Cue 语义分组：script.md 里用 || 手动切分（对应 Hypit 的 Cue handoff）
  - 关键词高亮：spec.keywords 指定词与颜色
  - 卡拉OK 逐字推进高亮：karaoke.enabled（按字数比例估算时序，非 ASR 对齐）
  - Dual Text：说「十块」显示「10块」，spec.dual_text
  - 时间窗换样式：spec.emphasis 指定某段用更大字号/颜色（对应 Hypit 的 Use during）
  - 中英双轨：中文主轨 + 英文要点轨（spec.points，带弹出动效）
  - 排版动效：\fad 淡入淡出 + \t 缩放弹出

用法（在项目根目录执行）：
    python avatar_caption.py build          # 生成 output/captions.ass
    python avatar_caption.py burn           # 烧录到 output/digital-human-captioned.mp4
    python avatar_caption.py all           # build + burn
    python avatar_caption.py spec          # 写出一份 caption-spec.json 模板

字幕文案来源优先级：--srt 指定 > output/digital-human.srt（whisperx）>
voice/qc.json 的时长 + script.md 原文（有台词原文时更准，且不需要 whisperx）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_common import OUT, ROOT, ffmpeg, save, state, stop

SPEC_FILE = OUT / 'caption-spec.json'
ASS_FILE = OUT / 'captions.ass'
PLAY_X, PLAY_Y = 1080, 1920

DEFAULT_SPEC = {
    'font': 'Microsoft YaHei',
    'main_size': 62,
    'main_color': '#FFFFFF',
    'outline': 3,
    'shadow': 1,
    'box': False,
    'margin_v': 300,
    'keywords': [{'text': 'AI', 'color': '#00E5A0'}],
    'karaoke': {'enabled': False, 'mode': 'trail'},
    'dual_text': [],
    'points': [],
    'point_style': {'size': 46, 'color': '#00E5A0', 'margin_v': 430},
    'emphasis': []
}


def ass_color(hex_color, alpha=0):
    """#RRGGBB -> &HAABBGGRR&（ASS 用 BGR 顺序，前两位是透明度）。"""
    value = hex_color.lstrip('#')
    r, g, b = value[0:2], value[2:4], value[4:6]
    return '&H%02X%s%s%s&' % (alpha, b, g, r)


def ass_time(seconds):
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds * 100), 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return '%d:%02d:%02d.%02d' % (hours, minutes, secs, centis)


def load_spec():
    if not SPEC_FILE.exists():
        SPEC_FILE.parent.mkdir(exist_ok=True)
        SPEC_FILE.write_text(json.dumps(DEFAULT_SPEC, ensure_ascii=False, indent=1),
                             encoding='utf-8')
        print('已写出字幕规格模板：output/caption-spec.json，按需修改后重跑 build。')
    return json.loads(SPEC_FILE.read_text(encoding='utf-8'))


def build_cues(args):
    """返回 [(start, end, text)]"""
    if args.srt:
        return parse_srt(Path(args.srt))
    srt = OUT / 'digital-human.srt'
    if srt.exists() and not args.prefer_script:
        return parse_srt(srt)
    qc_file = OUT / 'voice' / 'qc.json'
    script = ROOT / 'script.md'
    if qc_file.exists() and script.exists():
        rows = json.loads(qc_file.read_text(encoding='utf-8'))
        sentences = re.findall(r'^\s*\d+[.、]\s*(.+)$', script.read_text(encoding='utf-8'), re.M)
        cues, cursor = [], 0.0
        for row, text in zip(rows, sentences):
            pieces = [p.strip() for p in text.split('||') if p.strip()]
            weights = [len(p) for p in pieces]
            total_weight = sum(weights) or 1
            for piece, weight in zip(pieces, weights):
                share = row['duration'] * weight / total_weight
                cues.append((cursor, cursor + share, piece))
                cursor += share
        return cues
    stop('没有字幕来源：给 --srt，或先跑 whisperx 生成 srt，或确保有 voice/qc.json + script.md。')


def parse_srt(path):
    text = path.read_text(encoding='utf-8')
    cues = []
    for block in re.split(r'\n\s*\n', text.strip()):
        match = re.search(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})', block)
        if not match:
            continue
        h1, m1, s1, ms1 = (int(match.group(i)) for i in range(1, 5))
        h2, m2, s2, ms2 = (int(match.group(i)) for i in range(5, 9))
        body = block.split('\n')[-1].strip()
        cues.append((h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0,
                     h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0, body))
    if not cues:
        stop('srt 解析失败：' + str(path))
    return cues


def apply_dual_text(text, dual_text):
    for pair in dual_text:
        text = text.replace(pair['spoken'], pair.get('display', pair['spoken']))
    return text


def split_units(text):
    """中文按 1-2 字成组，拉丁词整体成组。"""
    units, buffer = [], ''
    for char in text:
        if re.match(r'[A-Za-z0-9]', char):
            buffer += char
            continue
        if buffer:
            units.append(buffer)
            buffer = ''
        units.append(char)
    if buffer:
        units.append(buffer)
    return units


def keyword_segments(text, keywords):
    """按关键词把整句切成 (片段, 颜色或 None) 段，保证跨中西文的关键词能整体命中。"""
    if not keywords:
        return [(text, None)]
    pattern = re.compile('|'.join(re.escape(k['text']) for k in keywords if k['text']))
    if not pattern.pattern:
        return [(text, None)]
    color_of = {k['text']: k['color'] for k in keywords}
    segments, cursor = [], 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            segments.append((text[cursor:match.start()], None))
        segments.append((match.group(0), color_of[match.group(0)]))
        cursor = match.end()
    if cursor < len(text):
        segments.append((text[cursor:], None))
    return segments or [(text, None)]


def render_cue_text(text, spec, emphasis, duration):
    r"""生成带样式标签的一行 ASS 文本。karaoke 开启时先按关键词切段、段内再按字数比例分 \k
    （\k 总和必须等于 Cue 时长，否则卡拉OK 会在 1 秒内跑完）。"""
    karaoke = spec.get('karaoke', {})
    text = text.replace('{', '(').replace('}', ')')
    if not karaoke.get('enabled'):
        result = text
        for keyword in spec.get('keywords', []):
            word = keyword['text'].replace('{', '(').replace('}', ')')
            if word in result:
                result = result.replace(word, '{\\c' + ass_color(keyword['color']) + '}' + word + '{\\c}')
        return result
    segments = keyword_segments(text, spec.get('keywords', []))
    weighted = []
    for segment, color in segments:
        for unit in split_units(segment):
            weighted.append((unit, color))
    total_weight = sum(len(u) for u, _ in weighted) or 1
    tags = []
    for unit, color in weighted:
        centis = max(1, int(round(100 * duration * len(unit) / total_weight)))
        override = '\\c' + ass_color(color) if color else ''
        tags.append('{\\k%d%s}%s' % (centis, override, unit))
    return ''.join(tags)


def build(args):
    spec = load_spec()
    cues = build_cues(args)
    lines = []
    lines.append('[Script Info]')
    lines.append('ScriptType: v4.00+')
    lines.append('PlayResX: %d' % PLAY_X)
    lines.append('PlayResY: %d' % PLAY_Y)
    lines.append('WrapStyle: 2')
    lines.append('ScaledBorderAndShadow: yes')
    lines.append('')
    lines.append('[V4+ Styles]')
    lines.append('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
                 'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
                 'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding')
    highlight = spec.get('karaoke', {}).get('highlight') or (
        spec['keywords'][0]['color'] if spec.get('keywords') else '#00E5A0')
    if spec.get('karaoke', {}).get('enabled'):
        # 实测确认的 libass \k 约定：未读=SecondaryColour，读过切 PrimaryColour 并保持（trail）。
        # 所以 Primary 放高亮色、Secondary 放底色，读到哪里亮到哪里。
        primary, secondary = ass_color(highlight), ass_color(spec['main_color'])
    else:
        primary = secondary = ass_color(spec['main_color'])
    main = ('Style: Main,%s,%d,%s,%s,%s,%s,0,0,0,0,100,100,0,0,%d,%d,%d,2,60,60,%d,1'
            % (spec['font'], spec['main_size'], primary, secondary,
               '&H00000000&', '&H80000000&' if spec.get('box') else '&H00000000&',
               3 if spec.get('box') else 1, spec['outline'], spec['shadow'], spec['margin_v']))
    lines.append(main)
    point_style = spec.get('point_style', {})
    point = ('Style: Point,%s,%d,%s,%s,&H00000000&,&H80000000,1,0,0,0,100,100,0,0,1,%d,1,2,60,60,%d,1'
             % (spec['font'], point_style.get('size', 46), ass_color(point_style.get('color', '#00E5A0')),
                ass_color(point_style.get('color', '#00E5A0')), spec['outline'],
                point_style.get('margin_v', 430)))
    lines.append(point)
    for index, item in enumerate(spec.get('emphasis', [])):
        base = ass_color(item.get('color', '#FFFFFF'))
        emph_primary = ass_color(highlight) if spec.get('karaoke', {}).get('enabled') else base
        lines.append('Style: Emph%d,%s,%d,%s,%s,&H00000000&,&H00000000,1,0,0,0,100,100,0,0,1,%d,%d,2,60,60,%d,1'
                     % (index, spec['font'], item.get('size', spec['main_size'] + 10),
                        emph_primary, base, spec['outline'], spec['shadow'], spec['margin_v']))
    lines.append('')
    lines.append('[Events]')
    lines.append('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text')
    for start, end, text in cues:
        text = apply_dual_text(text, spec.get('dual_text', []))
        style = 'Main'
        for index, item in enumerate(spec.get('emphasis', [])):
            if start >= item['start'] and start < item['end']:
                style = 'Emph%d' % index
        body = '{\\fad(120,120)}' + render_cue_text(text, spec, style, end - start)
        lines.append('Dialogue: 0,%s,%s,%s,,0,0,0,,%s' % (ass_time(start), ass_time(end), style, body))
    for item in spec.get('points', []):
        start = item['at']
        end = start + item.get('duration', 2.0)
        anim = '{\\fad(120,120)\\t(0,180,\\fscx108\\fscy108)\\t(180,360,\\fscx100\\fscy100)}'
        lines.append('Dialogue: 1,%s,%s,Point,,0,0,0,,%s%s' % (ass_time(start), ass_time(end), anim,
                                                               item['text'].replace('{', '(').replace('}', ')')))
    ASS_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    state['captions'] = {'source': args.srt or ('voice/qc.json+script.md' if not (OUT / 'digital-human.srt').exists() else 'srt'),
                         'cues': len(cues), 'ass': str(ASS_FILE.relative_to(ROOT))}
    state['stages']['D'] = 'ass_built'
    save()
    print('字幕 ASS 已生成：output/captions.ass（%d 条主字幕，%d 条要点）'
          % (len(cues), len(spec.get('points', []))))


def burn():
    if not ASS_FILE.exists():
        stop('缺少 output/captions.ass，请先执行 build。')
    video = OUT / 'digital-human.mp4'
    if not video.exists():
        stop('缺少 output/digital-human.mp4，请先完成合成。')
    dest = OUT / 'digital-human-captioned.mp4'
    relative = ASS_FILE.relative_to(ROOT).as_posix()
    ffmpeg(['-i', str(video), '-vf', 'subtitles=%s' % relative, '-c:v', 'libx264',
            '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy', str(dest)],
           label='burn-captions', cwd=str(ROOT))
    state['stages']['D'] = 'burned'
    save()
    print('字幕已烧录：%s' % dest.name)


def main():
    parser = argparse.ArgumentParser(description='Stage D 字幕：ASS 富样式生成与烧录')
    parser.add_argument('command', choices=['build', 'burn', 'all', 'spec'])
    parser.add_argument('--srt', help='外部字幕文件（不给则用 whisperx 结果或 script.md 推算）')
    parser.add_argument('--prefer-script', action='store_true',
                        help='即使有 whisperx 的 srt，也优先用 script.md 台词原文')
    args = parser.parse_args()
    if args.command == 'spec':
        load_spec()
    elif args.command == 'build':
        build(args)
    elif args.command == 'burn':
        burn()
    else:
        build(args)
        burn()


if __name__ == '__main__':
    main()

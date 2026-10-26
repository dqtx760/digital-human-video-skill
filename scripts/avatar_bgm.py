"""Stage C BGM：Suno 纯音乐生成 + 侧链闪避混音。

用法（在项目根目录执行）：
    python avatar_bgm.py generate            # 按 output/bgm/brief.md 生成两首候选
    python avatar_bgm.py mix --candidate 1   # 把选定候选侧链混进成片

brief.md 由执行者读文案后撰写，格式（五个维度，缺了 generate 会拒绝执行）：
    风格流派：cinematic ambient
    情绪：克制的疑虑逐渐转向坚定
    乐器：极简钢琴、微弱电子脉冲
    BPM：80
    能量曲线：开头稀疏，中段积蓄，结尾冷静收束
    避免项：人声、鼓点过重

注意：Suno 的 duration_s 参数实测被忽略（生成 70 秒以上），时长靠混音时本地裁剪；
variety 必须是字符串，传数字会 400。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_common import OUT, ROOT, api, clean, ffmpeg, ffprobe_duration, require_keys, save, state, stop

BGM_DIR = OUT / 'bgm'
BRIEF_FILE = BGM_DIR / 'brief.md'


def read_brief():
    if not BRIEF_FILE.exists():
        stop('缺少 output/bgm/brief.md。先读文案写音乐 brief（格式见本脚本 docstring），'
             '再执行 generate。音乐审美留人：生成只是出候选。')
    text = BRIEF_FILE.read_text(encoding='utf-8')
    fields = {}
    for key in ('风格流派', '情绪', '乐器', 'BPM', '能量曲线', '避免项'):
        match = re.search(key + r'[：:]\s*(.+)', text)
        if not match:
            stop('brief.md 缺少维度「' + key + '」。')
        fields[key] = match.group(1).strip()
    return fields


def build_payload(brief):
    style = '%s, %s, %s BPM' % (brief['风格流派'], brief['乐器'], brief['BPM'])
    prompt = ('%s 的纯音乐底乐，服务于口播而非盖过口播。能量曲线：%s。'
              'No vocals, no heavy drums.' % (brief['情绪'], brief['能量曲线']))
    return {'model': 'suno', 'version': 'v6', 'custom': True, 'instrumental': True,
            'title': 'Avatar BGM', 'style': style, 'prompt': prompt,
            'negative_tags': 'vocals, singing, lyrics, ' + brief['避免项'],
            'audio_format': 'mp3'}


def generate():
    brief = read_brief()
    payload = build_payload(brief)
    BGM_DIR.mkdir(exist_ok=True)
    r = api('C-bgm-submit', 'POST', 'https://api.apimart.ai/v1/music/generations',
            'apimart', json=payload)
    if r is None:
        return
    data = r.json()
    items = data.get('data', [])
    task = items[0] if isinstance(items, list) and items else items
    task_id = task.get('task_id')
    if not task_id:
        stop('Suno 提交响应缺少 task_id: ' + clean(r.text[:300]))
    state.setdefault('bgm', {})['task_id'] = task_id
    save()
    print('Suno 任务已提交：' + task_id)
    result = None
    import time
    deadline = time.time() + 330
    while time.time() < deadline:
        time.sleep(5)
        r = api('C-bgm-poll', 'GET', 'https://api.apimart.ai/v1/music/tasks/' + task_id, 'apimart')
        if r is None:
            continue
        j = r.json()
        status = j.get('data', j).get('status')
        if status in ('succeeded', 'success', 'completed'):
            result = j
            break
        if status in ('failed', 'error', 'cancelled'):
            stop('Suno 任务失败: ' + clean(json.dumps(j, ensure_ascii=False)[:500]))
    if result is None:
        stop('Suno 轮询超时。')
    (BGM_DIR / 'task_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                              encoding='utf-8')
    music = result.get('data', {}).get('result', {}).get('music', [])
    if not music:
        stop('Suno 结果里没有 music 数组: ' + clean(json.dumps(result, ensure_ascii=False)[:500]))
    for i, track in enumerate(music[:2], 1):
        url = track.get('audio_url')
        if not url:
            continue
        r = api('C-bgm-download-%d' % i, 'GET', url, None)
        if r is not None:
            path = BGM_DIR / ('bgm-c%d.mp3' % i)
            path.write_bytes(r.content)
            state['bgm']['c%d' % i] = {'duration': track.get('duration'),
                                       'tags': track.get('tags'), 'path': str(path.relative_to(ROOT))}
            save()
            print('候选 %d 已保存：%s（%.1f 秒）' % (i, path.name, track.get('duration') or 0))
    print('两首候选就绪，请用户试听选一首，然后执行 mix --candidate <1或2>。')


def mix(candidate):
    source = BGM_DIR / ('bgm-c%d.mp3' % candidate)
    if not source.exists():
        stop('找不到 %s，先执行 generate。' % source.name)
    videos = [p for p in OUT.glob('*.mp4') if 'bgm' not in p.name and 'working' not in p.name]
    if not videos:
        stop('output/ 下找不到合成成片，请先完成 Stage E 合成。')
    video = sorted(videos, key=lambda p: p.stat().st_mtime)[-1]
    total = ffprobe_duration(video)
    trim_end = round(total + 1, 3)
    fade_out_start = round(total - 1.5, 3)
    dest = video.with_name(video.stem + '-bgm-c%d.mp4' % candidate)
    filter_complex = (
        '[0:a]asplit=2[voice][sc];'
        '[1:a]atrim=0:%f,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=1.5,'
        'afade=t=out:st=%f:d=1.5,volume=0.16[bgm];'
        '[bgm][sc]sidechaincompress=threshold=0.03:ratio=6:attack=150:release=600[duck];'
        '[voice][duck]amix=inputs=2:duration=first:normalize=0[out]'
        % (trim_end, fade_out_start))
    ffmpeg(['-i', str(video), '-i', str(source), '-filter_complex', filter_complex,
            '-map', '0:v', '-map', '[out]', '-c:v', 'copy', '-c:a', 'aac',
            '-b:a', '192k', '-movflags', '+faststart', str(dest)], label='bgm-mix')
    state.setdefault('bgm', {})['mixed'] = {'candidate': candidate, 'output': str(dest.relative_to(ROOT)),
                                            'source_video': video.name, 'duration': total}
    save()
    print('已混音：%s（时长 %.3f 秒，BGM 候选 %d）' % (dest.name, total, candidate))


def main():
    parser = argparse.ArgumentParser(description='Stage C BGM：Suno 生成 + 侧链混音')
    parser.add_argument('command', choices=['generate', 'mix'])
    parser.add_argument('--candidate', type=int, default=1, choices=[1, 2])
    args = parser.parse_args()
    if args.command == 'generate':
        require_keys()
        generate()
    else:
        mix(args.candidate)


if __name__ == '__main__':
    main()

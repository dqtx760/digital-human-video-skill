"""Stage B 视频：形象图上传 + 音频公网托管 + Seedance 口型驱动生成。

用法（在项目根目录执行）：
    python avatar_video.py upload-image   # assets/ 形象图传到 APIMART 拿公网 URL
    python avatar_video.py host-audio     # 每句 WAV 经 tmpfiles 托管并解析 /dl/ 直链
    python avatar_video.py submit         # 逐句提交视频任务（generate_audio=False）
    python avatar_video.py poll           # 轮询全部任务
    python avatar_video.py download       # 下载已完成片段
    python avatar_video.py all            # 依次执行以上五步

铁律：duration 按音频时长向上取整；单段估算费用超 1 美元熔断；
换了音频必须重新 submit——旧片段的口型是对着旧音频驱动的。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_common import (OUT, ROOT, api, clean, ffprobe_duration, read_sentences,
                           require_keys, save, state, stop)

VIDEO_MODEL = 'seedance-2.0-mini'
PER_SECOND_USD = 0.03903
COST_CAP_USD = 1.0
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp')

APPEARANCE_FILE = 'assets/appearance.txt'   # 可选：补充形象描述（服装/场景）
LIP_PROMPT_TEMPLATE = (
    '9:16竖屏单人口播。参考图片中的人是唯一出镜人物，严格保持他的脸、发型、年龄、肤色、'
    '{appearance}和原图构图，全程不换脸不换人。固定镜头中近景，一镜到底，不切镜、不推拉。'
    '仅有自然眨眼和轻微表情，保持稳定姿态，不转头，不做大幅动作。'
    '参考音频是这段视频的完整实际对白，不是仅供模仿的音色样本。严格按音频原始起止时间、发音、'
    '每个字和停顿驱动嘴唇、下颌，不得增删或改写台词，不得重排节奏。音频中的前置停顿保持安静，'
    '音频结束立即闭嘴，余下时间保持稳定。画面不要字幕、文字、水印或额外人物。'
    '视频可以保持静音，但人物必须完整地做出与参考音频同步的真实中文说话口型。台词：{sentence}')


def find_image():
    assets = ROOT / 'assets'
    for candidate in sorted(assets.glob('*')):
        if candidate.suffix.lower() in IMAGE_EXTS and 'frame' not in candidate.stem:
            return candidate
    stop('assets/ 下找不到形象图（png/jpg/jpeg/webp）。没有合适照片时，'
         '先用 APIMART gpt-image-2 生成并让用户确认形象。')


def upload_image():
    if state.get('image_url'):
        print('复用已上传形象图：' + state['image_url'])
        return
    image = find_image()
    with image.open('rb') as source:
        r = api('B-image-upload', 'POST', 'https://api.apimart.ai/v1/uploads/images', 'apimart',
                files={'file': (image.name, source, 'image/png')})
    if r is not None:
        data = r.json()
        url = data.get('url') or data.get('data', {}).get('url')
        if not url:
            stop('上传响应缺少图片 URL: ' + clean(r.text[:300]))
        state['image_url'] = url
        save()
        print('形象图上传成功。')


def appearance_text():
    path = ROOT / APPEARANCE_FILE
    if path.exists():
        return path.read_text(encoding='utf-8').strip() or '服装、场景'
    return '服装、场景'


def host_audio():
    voice_dir = OUT / 'voice'
    sentences = read_sentences()
    for index in range(1, len(sentences) + 1):
        entry = state.setdefault('video', {}).setdefault(str(index), {})
        if entry.get('audio_url'):
            print('第 %d 句音频已托管，跳过。' % index)
            continue
        wav = voice_dir / ('s%d.wav' % index)
        if not wav.exists():
            stop('缺少 %s，请先执行 Stage A。' % wav.name)
        if not entry.get('audio_viewer'):
            with wav.open('rb') as source:
                r = api('B-audio-upload-%d' % index, 'POST',
                        'https://tmpfiles.org/api/v1/upload', None,
                        files={'file': (wav.name, source, 'audio/wav')})
            if r is None:
                continue
            viewer = r.json().get('data', {}).get('url')
            if not viewer:
                stop('托管响应缺少查看页 URL: ' + clean(r.text[:300]))
            entry['audio_viewer'] = viewer.replace('http://', 'https://', 1)
            save()
        r = api('B-audio-viewer-%d' % index, 'GET', entry['audio_viewer'], None)
        if r is not None:
            match = re.search(r'href="(https://tmpfiles\.org/dl/[^"]+)"', r.text)
            if not match:
                stop('查看页未找到 /dl/ 真实直链。直链 60 分钟过期，托管后请立即提交。')
            entry['audio_url'] = match.group(1)
            save()
            print('第 %d 句音频直链就绪。' % index)


def submit():
    if state.get('stages', {}).get('A') not in ('qc_passed', 'human_confirmed'):
        print('提醒：Stage A 尚未标记通过，请确认已完成声音质检与用户试听。')
    if not state.get('image_url'):
        stop('形象图未上传，请先执行 upload-image。')
    sentences = read_sentences()
    for index in range(1, len(sentences) + 1):
        entry = state.setdefault('video', {}).setdefault(str(index), {})
        if entry.get('task_id'):
            print('第 %d 句已提交过（%s），不重复提交。' % (index, entry['task_id']))
            continue
        if not entry.get('audio_url'):
            stop('第 %d 句音频未托管，请先执行 host-audio。' % index)
        wav = OUT / 'voice' / ('s%d.wav' % index)
        duration = max(4, math.ceil(ffprobe_duration(wav)))
        estimate = duration * PER_SECOND_USD
        if estimate > COST_CAP_USD:
            stop('第 %d 句估算费用 %.4f 美元超过单段 %.2f 美元上限。' % (index, estimate, COST_CAP_USD))
        prompt = LIP_PROMPT_TEMPLATE.format(appearance=appearance_text(), sentence=sentences[index - 1])
        payload = {'model': VIDEO_MODEL, 'prompt': prompt, 'image_urls': [state['image_url']],
                   'audio_urls': [entry['audio_url']], 'size': '9:16', 'resolution': '720p',
                   'duration': duration, 'generate_audio': False}
        entry.update(parameters=payload, estimated_cost_usd=round(estimate, 5))
        save()
        r = api('B-submit-%d' % index, 'POST',
                'https://api.apimart.ai/v1/videos/generations', 'apimart', json=payload)
        if r is not None:
            data = r.json()
            items = data.get('data', [])
            task = items[0] if isinstance(items, list) and items else items
            task_id = task.get('task_id') if isinstance(task, dict) else None
            if not task_id:
                stop('提交响应缺少 task_id: ' + clean(r.text[:300]))
            entry.update(task_id=task_id, status=task.get('status'))
            save()
            print(json.dumps({'sentence': index, 'task_id': task_id, 'duration': duration,
                              'estimated_cost_usd': entry['estimated_cost_usd']}, ensure_ascii=False))


def poll():
    for index, entry in sorted(state.get('video', {}).items(), key=lambda kv: int(kv[0])):
        if not entry.get('task_id') or entry.get('status') == 'completed':
            continue
        r = api('B-poll-%s' % index, 'GET',
                'https://api.apimart.ai/v1/tasks/' + entry['task_id'], 'apimart')
        if r is None:
            continue
        data = r.json().get('data', {})
        for field in ('status', 'progress', 'cost', 'credits_cost', 'error'):
            if field in data:
                entry[field] = data[field]
        videos = data.get('result', {}).get('videos', [])
        if videos:
            value = videos[0] if isinstance(videos, list) else videos
            url = value.get('url') if isinstance(value, dict) else value
            entry['result_url'] = url[0] if isinstance(url, list) else url
        save()
        print(json.dumps({'sentence': index, 'task_id': entry['task_id'],
                          'status': entry.get('status'), 'cost': entry.get('cost')},
                         ensure_ascii=False))
        if entry.get('cost') is not None and float(entry['cost']) > COST_CAP_USD:
            stop('第 %s 句实际费用 %.4f 美元超过熔断值。' % (index, float(entry['cost'])))
        if data.get('status') == 'failed':
            stop('第 %s 句任务失败: %s' % (index, clean(data.get('error', '未知错误'))))


def download():
    clips = OUT / 'clips'
    clips.mkdir(exist_ok=True)
    for index, entry in sorted(state.get('video', {}).items(), key=lambda kv: int(kv[0])):
        path = clips / ('clip%s.mp4' % index)
        if path.exists():
            print('clip%s.mp4 已存在，跳过。' % index)
            continue
        if entry.get('status') != 'completed' or not entry.get('result_url'):
            stop('第 %s 句尚未完成或缺少下载地址，禁止下载。' % index)
        r = api('B-download-%s' % index, 'GET', entry['result_url'], None)
        if r is not None:
            path.write_bytes(r.content)
            entry['local_path'] = str(path.relative_to(ROOT))
            save()
            print('已保存 clip%s.mp4，%d 字节。' % (index, len(r.content)))


def main():
    commands = {'upload-image': upload_image, 'host-audio': host_audio, 'submit': submit,
                'poll': poll, 'download': download,
                'all': lambda: (upload_image(), host_audio(), submit(), poll(), download())}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__)
        return
    require_keys()
    commands[sys.argv[1]]()


if __name__ == '__main__':
    main()

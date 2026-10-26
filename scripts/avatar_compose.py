"""Stage E 合成与质检：四段拼接 + 可选 whisperx 字幕 + 成片 QC。

用法（在项目根目录执行）：
    python avatar_compose.py compose     # 按各句音频时长拼接成 1080x1920 成片
    python avatar_compose.py subtitle    # whisperx 转写并烧录字幕（未装则提示跳过）
    python avatar_compose.py qc          # 时长一致性 + 完整解码 + 音量质检
    python avatar_compose.py all         # compose + qc（字幕按需单独跑）

铁律：音轨用 Stage A 的 TTS 人声，视频自带音轨一律丢弃；
各段时长以 voice/qc.json 的实际秒数为准（取整到 30fps 帧边界）。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_common import OUT, ROOT, ffmpeg, ffprobe_duration, save, state, stop

WIDTH, HEIGHT, FPS = 1080, 1920, 30


def load_rows():
    qc_file = OUT / 'voice' / 'qc.json'
    if not qc_file.exists():
        stop('缺少 output/voice/qc.json，请先执行 Stage A 的 qc。')
    rows = json.loads(qc_file.read_text(encoding='utf-8'))
    clips = []
    for row in rows:
        clip = OUT / 'clips' / ('clip%d.mp4' % row['sentence'])
        wav = OUT / 'voice' / ('s%d.wav' % row['sentence'])
        if not clip.exists() or not wav.exists():
            stop('缺少 clip%d.mp4 或 s%d.wav。' % (row['sentence'], row['sentence']))
        clips.append((row['sentence'], clip, wav, row['duration']))
    return clips


def compose():
    clips = load_rows()
    args = []
    filters = []
    durations = []
    for i, (index, clip, wav, seconds) in enumerate(clips):
        args += ['-i', str(clip), '-i', str(wav)]
        duration = int(seconds * FPS) / FPS
        durations.append(duration)
        filters.append(
            '[%d:v]trim=duration=%.9f,setpts=PTS-STARTPTS,'
            'scale=%d:%d:flags=lanczos,setsar=1,fps=%d,format=yuv420p[v%d]'
            % (i * 2, duration, WIDTH, HEIGHT, FPS, i))
        filters.append(
            '[%d:a]atrim=duration=%.9f,asetpts=PTS-STARTPTS,aresample=48000[a%d]'
            % (i * 2 + 1, duration, i))
    n = len(clips)
    filters.append(''.join('[v%d][a%d]' % (i, i) for i in range(n))
                   + 'concat=n=%d:v=1:a=1[v][a]' % n)
    total = sum(durations)
    dest = OUT / 'digital-human.mp4'
    temp = OUT / 'digital-human.working.mp4'
    ffmpeg(args + ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
                   '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '1',
                   '-t', '%.9f' % total, '-movflags', '+faststart', str(temp)],
          label='compose')
    temp.replace(dest)
    state['composition'] = {'width': WIDTH, 'height': HEIGHT, 'fps': FPS,
                            'segment_durations': durations, 'total_duration': total,
                            'audio_source': 'Stage A TTS；生成视频自带音轨已丢弃',
                            'note': '720p 源经 Lanczos 放大至 1080x1920，不等于原生 1080p'}
    state['stages']['E'] = 'composed'
    save()
    print(json.dumps(state['composition'], ensure_ascii=False))


def subtitle():
    video = OUT / 'digital-human.mp4'
    if not video.exists():
        stop('缺少 output/digital-human.mp4，请先 compose。')
    try:
        import whisperx  # noqa: F401
    except ImportError:
        print('未安装 whisperx，字幕阶段跳过。用户用剪映配字幕时，在交付说明里讲清楚。')
        state['stages']['D'] = 'skipped_no_whisperx'
        save()
        return
    import os
    os.environ['HF_HUB_OFFLINE'] = '1'
    device = 'cuda' if _cuda_ok() else 'cpu'
    import whisperx
    model = whisperx.load_model('small', device, compute_type='float16' if device == 'cuda' else 'int8',
                                language='zh', local_files_only=True)
    audio = whisperx.load_audio(str(video))
    result = model.transcribe(audio, batch_size=16)
    srt = OUT / 'digital-human.srt'
    _write_srt(result.get('segments', []), srt)
    burned = OUT / 'digital-human-subtitled.mp4'
    ffmpeg(['-i', str(video), '-vf', 'subtitles=%s' % str(srt).replace('\\', '/').replace(':', '\\:'),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-c:a', 'copy', str(burned)], label='burn-subtitle')
    state['stages']['D'] = 'burned'
    save()
    print('字幕已烧录：%s' % burned.name)


def _cuda_ok():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _write_srt(segments, path):
    def stamp(seconds):
        milliseconds = int(round(seconds * 1000))
        hours, milliseconds = divmod(milliseconds, 3600000)
        minutes, milliseconds = divmod(milliseconds, 60000)
        seconds, milliseconds = divmod(milliseconds, 1000)
        return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds, milliseconds)

    lines = []
    for i, segment in enumerate(segments, 1):
        lines.append(str(i))
        lines.append('%s --> %s' % (stamp(segment['start']), stamp(segment['end'])))
        lines.append(segment.get('text', '').strip())
        lines.append('')
    path.write_text('\n'.join(lines), encoding='utf-8')


def qc():
    video = OUT / 'digital-human.mp4'
    if not video.exists():
        stop('缺少 output/digital-human.mp4，请先 compose。')
    total = ffprobe_duration(video)
    expected = sum(state.get('composition', {}).get('segment_durations', []))
    decode = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video), '-f', 'null', '-'],
                            capture_output=True, text=True)
    loudness = subprocess.run(['ffmpeg', '-i', str(video), '-af', 'volumedetect',
                               '-f', 'null', '-'], capture_output=True, text=True)
    mean = max_volume = None
    for line in loudness.stderr.splitlines():
        if 'mean_volume' in line:
            mean = float(line.split(':')[1].strip().split()[0])
        if 'max_volume' in line:
            max_volume = float(line.split(':')[1].strip().split()[0])
    report = {'duration': round(total, 3), 'expected_duration': round(expected, 3),
              'duration_delta': round(abs(total - expected), 3),
              'decode_clean': decode.returncode == 0 and not decode.stderr.strip(),
              'mean_db': mean, 'max_db': max_volume,
              'clipping_free': max_volume is not None and max_volume < 0}
    state['final_qc'] = report
    ok = report['duration_delta'] < 0.2 and report['decode_clean'] and report['clipping_free']
    state['stages']['E'] = 'qc_passed' if ok else 'qc_failed'
    save()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not ok:
        stop('成片质检未过，见上表。')
    print('成片就绪：output/digital-human.mp4。接下来：BGM（avatar_bgm.py）→ 用户试听验收。')


def main():
    commands = {'compose': compose, 'subtitle': subtitle, 'qc': qc,
                'all': lambda: (compose(), qc())}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__)
        return
    commands[sys.argv[1]]()


if __name__ == '__main__':
    main()

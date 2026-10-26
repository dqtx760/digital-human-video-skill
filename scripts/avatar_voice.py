"""Stage A 声音：Fish Audio 克隆 + TTS + WAV 头修复 + 质检。

用法（在项目根目录执行）：
    python avatar_voice.py clone      # 创建克隆声音模型，拿 voice_id
    python avatar_voice.py tts        # 按 script.md 逐句合成 WAV
    python avatar_voice.py fixwav     # 修复流式 WAV 文件头（原始响应已留存）
    python avatar_voice.py qc         # 时长/音量/削顶质检
    python avatar_voice.py all        # 依次执行以上四步

铁律：不用 atempo 等后处理拉伸音频凑时长；语速不理想用 TTS 原生 prosody.speed 重录。
"""
import io
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_common import (OUT, ROOT, api, clean, ffmpeg, ffprobe_duration, forget,
                           read_sentences, require_keys, save, state, stop)

FISH_TTS_MODEL = 's2.1-pro-free'
SAMPLE_EXTS = ('.m4a', '.wav', '.mp3', '.aac')


def find_sample():
    assets = ROOT / 'assets'
    for ext in SAMPLE_EXTS:
        for name in ('voice-sample', 'voice', 'sample'):
            candidate = assets / (name + ext)
            if candidate.exists():
                return candidate
    for candidate in sorted(assets.glob('*')):
        if candidate.suffix.lower() in SAMPLE_EXTS:
            return candidate
    stop('assets/ 下找不到声音样本（支持 m4a/wav/mp3/aac）。')


def clone():
    if state.get('voice_id'):
        print('已存在 voice_id：' + state['voice_id'])
        return
    sample = find_sample()
    mime = 'audio/mp4' if sample.suffix.lower() == '.m4a' else 'audio/wav'
    with sample.open('rb') as source:
        r = api('A-clone', 'POST', 'https://api.fish.audio/model', 'fish',
                data={'type': 'tts', 'title': 'avatar-voice', 'train_mode': 'fast',
                      'visibility': 'private'},
                files={'voices': (sample.name, source, mime)})
    if r is not None:
        data = r.json()
        if not data.get('_id'):
            stop('克隆响应未包含 _id: ' + clean(r.text[:300]))
        state.update(voice_id=data['_id'], voice_state=data.get('state'))
        save()
        print(json.dumps({'voice_id': data['_id'], 'state': data.get('state')}, ensure_ascii=False))


def tts(speed=None):
    if not state.get('voice_id'):
        stop('缺少 voice_id，请先执行 clone。')
    sentences = read_sentences()
    voice_dir = OUT / 'voice'
    voice_dir.mkdir(exist_ok=True)
    for index, text in enumerate(sentences, 1):
        dest = voice_dir / ('s%d.wav' % index)
        if dest.exists():
            print('已存在 s%d.wav，跳过。' % index)
            continue
        body = {'text': text, 'reference_id': state['voice_id'], 'format': 'wav', 'normalize': True}
        if speed:
            body['prosody'] = {'speed': speed}
        r = api('A-tts-%d' % index, 'POST', 'https://api.fish.audio/v1/tts', 'fish',
                headers={'model': FISH_TTS_MODEL, 'Content-Type': 'application/json'},
                json=body)
        if r is not None:
            if r.content[:4] != b'RIFF':
                stop('第 %d 句返回的不是 WAV 音频: %s' % (index, clean(r.text[:300])))
            dest.write_bytes(r.content)
            state.setdefault('tts', {})[str(index)] = {
                'model': FISH_TTS_MODEL, 'text': text, 'bytes': len(r.content),
                'prosody_speed': speed or 1.0}
            save()
            print('已保存 s%d.wav，%d 字节' % (index, len(r.content)))


def fixwav():
    voice_dir = OUT / 'voice'
    raw_dir = voice_dir / 'raw'
    raw_dir.mkdir(exist_ok=True)
    fixed_any = False
    for src in sorted(voice_dir.glob('s*.wav')):
        if src.parent != voice_dir:
            continue
        original = raw_dir / src.name
        if original.exists():
            print('%s 已修复过，跳过。' % src.name)
            continue
        data = src.read_bytes()
        try:
            with wave.open(io.BytesIO(data), 'rb') as reader:
                params = reader.getparams()
                pcm = reader.readframes(reader.getnframes())
        except Exception as error:
            stop('%s 不是可解析的 WAV: %s' % (src.name, error))
        original.write_bytes(data)
        with wave.open(str(src), 'wb') as writer:
            writer.setnchannels(params.nchannels)
            writer.setsampwidth(params.sampwidth)
            writer.setframerate(params.framerate)
            writer.writeframes(pcm)
        declared = struct.unpack_from('<I', data, 4)[0]
        state.setdefault('wav_fix', {})[src.stem] = {
            'declared_size': declared, 'actual_size': len(data) - 8}
        fixed_any = True
        print('%s 文件头已修复（声明 %d，实际 %d）。' % (src.name, declared, len(data) - 8))
    if fixed_any:
        save()


def qc():
    voice_dir = OUT / 'voice'
    rows = []
    for src in sorted(voice_dir.glob('s*.wav')):
        duration = ffprobe_duration(src)
        result = subprocess.run(['ffmpeg', '-i', str(src), '-af', 'volumedetect',
                                 '-f', 'null', '-'], capture_output=True, text=True)
        mean = max_volume = None
        for line in result.stderr.splitlines():
            if 'mean_volume' in line:
                mean = float(line.split(':')[1].strip().split()[0])
            if 'max_volume' in line:
                max_volume = float(line.split(':')[1].strip().split()[0])
        rows.append({'sentence': int(src.stem[1:]), 'duration': round(duration, 3),
                     'mean_db': mean, 'max_db': max_volume,
                     'volume_pass': mean is not None and mean >= -30,
                     'clipping_pass': max_volume is not None and max_volume < 0})
    (voice_dir / 'qc.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
    state['audio_qc'] = rows
    state['stages']['A'] = 'qc_passed' if all(r['volume_pass'] and r['clipping_pass'] for r in rows) else 'qc_failed'
    save()
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    if state['stages']['A'] == 'qc_failed':
        stop('有句子音量/削顶不达标，见上表。修复后重跑 qc。')
    print('Stage A 质检通过。接下来请用户试听音色，确认像本人后再进 Stage B。')


def main():
    commands = {'clone': clone, 'tts': tts, 'fixwav': fixwav, 'qc': qc,
                'all': lambda: (clone(), tts(), fixwav(), qc())}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__)
        return
    if sys.argv[1] in ('clone', 'tts', 'all'):
        require_keys()
    commands[sys.argv[1]]()


if __name__ == '__main__':
    main()

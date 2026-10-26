# 提示词与命令模板

## 1. 视频口型提示词（Seedance 2.0-mini）

模板（方括号按项目替换，其余不要动，每句都在防一种崩坏）：

```
9:16竖屏单人[播客口播/访谈]。参考图片中的人是唯一出镜人物，严格保持他的脸、发型、
年龄、肤色、[服装描述]、座位、背景灯光和原图构图，全程不换脸不换人。固定镜头中近景，
一镜到底，不切镜、不推拉。仅有自然眨眼和轻微表情，保持稳定姿态，不转头，不做大幅动作。
参考音频是这段视频的完整实际对白，不是仅供模仿的音色样本。严格按音频原始起止时间、
发音、每个字和停顿驱动嘴唇、下颌，不得增删或改写台词，不得重排节奏。音频中的前置停顿
保持安静，音频结束立即闭嘴，余下时间保持稳定。画面不要字幕、文字、水印或额外人物。
视频可以保持静音，但人物必须完整地做出与参考音频同步的真实中文说话口型。台词：[本句原文]
```

为什么这样写：形象保持句防换脸崩坏；「一镜到底不推拉」防模型自作主张加运镜；
「参考音频是完整对白不是音色样本」是关键——不说这句，模型可能只模仿音色随口编词；
「结束即闭嘴」防静默尾巴期间嘴还在动的畸变（这是复刻项目里真实发生过的）。

## 2. BGM：从文案到音乐 prompt

### 2.1 先写音乐 brief（执行者读文案后输出）

| 维度 | 示例（AI 观点口播） |
|---|---|
| 风格流派 | cinematic ambient 电影感氛围 |
| 情绪 | 克制的疑虑逐渐转向坚定 |
| 乐器 | 极简钢琴、微弱电子脉冲 |
| BPM | 80 |
| 能量曲线 | 开头稀疏，中段积蓄，结尾冷静收束 |
| 避免项 | 人声、鼓点过重、宏大交响 |

### 2.2 再翻成 Suno prompt

范式：[genre] [mood] [instruments] [tempo] [energy arc]，控制在 8–12 个关键词，
模型才不跑偏。

```
style: cinematic ambient, minimal piano, subtle electronic pulse, restrained, resolute, 80 BPM
prompt: A restrained cinematic underscore for a thought-provoking monologue about
[主题]. Starts sparse and questioning, gradually builds quiet determination,
resolves calm and confident. No vocals, no heavy drums.
negative_tags: vocals, singing, lyrics, heavy drums, aggressive
```

BGM 的氛围要服务于台词，不是盖过台词——口播视频的音乐是底噪级的配角。

## 3. ffmpeg：BGM 侧链闪避混音

```bash
ffmpeg -y -i 成片.mp4 -i bgm.mp3 -filter_complex \
"[0:a]asplit=2[voice][sc];\
[1:a]atrim=0:27,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=1.5,afade=t=out:st=25.5:d=1.5,volume=0.16[bgm];\
[bgm][sc]sidechaincompress=threshold=0.03:ratio=6:attack=150:release=600[duck];\
[voice][duck]amix=inputs=2:duration=first:normalize=0[out]" \
-map 0:v -map "[out]" -c:v copy -c:a aac -b:a 192k -movflags +faststart 输出.mp4
```

四件事：人声分两路（一路直通混音、一路送侧链）；BGM 裁到比成片多 1 秒、头尾淡入
淡出 1.5 秒、音量压到约 -16dB（0.16）；sidechaincompress 让人声一响音乐自动让位、
停顿处回来（threshold 0.03 约 -30dB 起跳，ratio 6，attack 150ms，release 600ms）；
amix normalize=0 防止自动再降压。27 这个数字按实际成片时长改。

## 4. ffmpeg：四段合成

按各句音频时长（帧边界取整到 30fps）trim、缩放 1080×1920、拼接，音轨用 TTS 人声：

```bash
ffmpeg -y \
 -i clip1.mp4 -i s1.wav -i clip2.mp4 -i s2.wav -i clip3.mp4 -i s3.wav -i clip4.mp4 -i s4.wav \
 -filter_complex "\
[0:v]trim=duration=4.4,setpts=PTS-STARTPTS,scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p[v0];\
[1:a]atrim=duration=4.4,asetpts=PTS-STARTPTS,aresample=48000[a0];\
[2:v]trim=duration=5.7,setpts=PTS-STARTPTS,scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p[v1];\
[3:a]atrim=duration=5.7,asetpts=PTS-STARTPTS,aresample=48000[a1];\
[4:v]trim=duration=9.1,setpts=PTS-STARTPTS,scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p[v2];\
[5:a]atrim=duration=9.1,asetpts=PTS-STARTPTS,aresample=48000[a2];\
[6:v]trim=duration=6.0,setpts=PTS-STARTPTS,scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p[v3];\
[7:a]atrim=duration=6.0,asetpts=PTS-STARTPTS,aresample=48000[a3];\
[v0][a0][v1][a1][v2][a2][v3][a3]concat=n=4:v=1:a=1[v][a]" \
 -map "[v]" -map "[a]" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
 -c:a aac -b:a 192k -ar 48000 -ac 1 -movflags +faststart 成片.mp4
```

duration 按各句实际秒数改。视频是 720p 生成的，这里 Lanczos 放大到 1080×1920——
放大不等于原生 1080p 细节，交付时说清楚。

## 5. whisperx 字幕（本地）

```bash
# 环境：独立 venv 装 whisperx；模型已缓存时离线跑
HF_HUB_OFFLINE=1 python -c "
import whisperx
m = whisperx.load_model('small', 'cuda', compute_type='float16', language='zh',
                        local_files_only=True)
a = whisperx.load_audio('成片.mp4')
r = m.transcribe(a, batch_size=16)
"
```

CPU 就把 'cuda' 改 'cpu'、compute_type 改 'int8'。模型缓存不在时首次需联网下载。
拿到带时间戳的文本后，用 ffmpeg subtitles 过滤器烧录，或交剪映。

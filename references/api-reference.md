# API 参考（全部端点均经实测验证）

所有金额为美元。APIMART 一个 Key 通吃图片/视频/音乐；Fish Audio 独立 Key。
两个服务在国内网络都需要代理，代理配置见 pitfalls.md 第 1 条。

## 1. Fish Audio（声音克隆 + TTS）

Base: https://api.fish.audio ，认证: Authorization: Bearer <FISH_API_KEY>

### 1.1 创建克隆声音模型

POST /model ，multipart/form-data:

| 字段 | 值 | 说明 |
|---|---|---|
| type | tts | 固定 |
| title | 自取，如 dqtx-avatar | 模型名 |
| train_mode | fast | 快速模式，分钟级出模型 |
| visibility | private | 必须私有 |
| voices | 声音样本文件 | 15–30 秒干净人声，m4a/wav/mp3 |
| texts | 留空 | 留空时平台自动 ASR 转写样本文本，不用手抄 |

成功返回 201，响应体 _id 即 voice_id，永久复用，后续 TTS 都引用它。
注意：克隆配额与账号余额挂钩，0/0 额度返回 403（model creation limit reached），
需要充值后重试。

### 1.2 语音合成

POST /v1/tts ，请求头加 model: s2.1-pro-free（免费层型号），body 为 JSON:

```json
{"text": "要合成的句子", "reference_id": "<voice_id>", "format": "wav", "normalize": true}
```

返回音频字节流。两个要点：

- 响应是流式 WAV，文件头声明的长度可能大于实际数据，保存后必须修复文件头，
  否则部分解码器按声明长度截断。修复方法见 scripts/avatar_voice.py fixwav。
- 调整语速用原生参数 prosody.speed（如 {"speed": 0.85}），**不要用 ffmpeg atempo
  后处理拉伸**，原因见 SKILL.md 铁律第 1 条。

## 2. APIMART（图片 + 视频 + 音乐）

Base: https://api.apimart.ai ，认证: Authorization: Bearer <APIMART_API_KEY>

### 2.1 图片上传（形象图 / 参考图）

POST /v1/uploads/images ，multipart，字段 file。返回公网 URL（getapib.org 域名），
视频和音乐任务都引用这个 URL。一次上传全程复用，不要重复传。

### 2.2 文本生成图片（无照片时）

模型 gpt-image-2，9:16 2K 约 0.015 美元/张。生成后必须让用户确认形象
（脸像不像、构图对不对），确认后才进视频阶段——形象错了，视频全废。

### 2.3 视频生成（口型驱动，核心）

POST /v1/videos/generations ，JSON body:

```json
{
  "model": "seedance-2.0-mini",
  "prompt": "<口型提示词，见 prompts.md 模板>",
  "image_urls": ["<形象图URL>"],
  "audio_urls": ["<该句音频的公网直链>"],
  "size": "9:16",
  "resolution": "720p",
  "duration": 10,
  "generate_audio": false
}
```

参数要点：

- generate_audio=false 是数字人路线的开关：视频静音，口型完全由 audio_urls 的外挂
  音频驱动，后期贴 Stage A 的人声。设成 true 模型会自己生成人声，口型对但声音不是
  本人的克隆音。
- duration 是该句音频时长向上取整（秒），最小 4。
- 单价约 0.03903 美元/秒（720p Mini 档），提交前按 duration 估算，超 1 美元熔断。
- prompt 必须包含：形象保持描述（脸/发型/服装/场景不换）、固定机位一镜到底、
  按音频起止驱动嘴唇、结束即闭嘴、画面无字幕水纸。模板见 prompts.md。
- 返回 data[0].task_id；轮询 GET /v1/tasks/{task_id}，status 为 completed 后从
  data.result.videos[0].url 下载。单段 720p 约 2 分钟出片。

### 2.4 音乐生成（Suno）

POST /v1/music/generations ，JSON body（APIMART 封装格式，snake_case）:

```json
{
  "model": "suno",
  "version": "v6",
  "custom": true,
  "instrumental": true,
  "title": "曲目标题",
  "style": "cinematic ambient, minimal piano, 80 BPM",
  "prompt": "情绪与能量曲线描述，见 prompts.md 范式",
  "negative_tags": "vocals, singing, lyrics, heavy drums",
  "audio_format": "mp3"
}
```

要点：

- instrumental=true 加 negative_tags 挡人声，是 BGM 的必需组合。
- variety 字段类型是字符串，传数字会报 400。
- duration_s 参数实测被忽略，实际生成 70 秒以上，时长靠本地 ffmpeg atrim 裁。
- 每笔 0.05 美元，一次返回两首，在 data.result.music[] 数组里，各带 audio_url。
- instrumental 模式下响应体 lyrics 字段回显的是 prompt 描述文本，不是真歌词。
- 轮询 GET /v1/music/tasks/{task_id}，30–90 秒出结果，结果音频 URL 及时下载。

## 3. tmpfiles.org（音频公网托管）

APIMART 视频任务要求音频有公网 URL，但它没有音频上传端点，用 tmpfiles 中转：

1. POST https://tmpfiles.org/api/v1/upload （multipart，字段 file），返回查看页 URL。
2. GET 查看页，用正则从 HTML 里解析真实下载直链：href="https://tmpfiles.org/dl/..."
   （查看页链接是给浏览器看的，/dl/ 才是直链，直接用查看页 URL 会拿到 HTML）。
3. 直链 60 分钟过期。托管后立即提交视频任务，不要囤。

## 4. 本地工具

| 工具 | 用途 | 要点 |
|---|---|---|
| ffmpeg | 裁切、混音、侧链闪避、合成、质检 | 版本无所谓，命令模板见 prompts.md |
| whisperx small | 字幕转写 | 离线跑法见 pitfalls.md 第 5 条 |
| Python requests | 全部 API 调用 | 必须 trust_env=False，见 pitfalls.md 第 1 条 |

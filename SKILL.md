---
name: digital-human-video
description: 数字人视频生成——用一张照片、一段声音样本和一份文案，产出本人出镜的口播短视频。流水线为 Fish Audio 声音克隆加 TTS、Seedance 2.0-mini 音频驱动口型、Suno 生成 BGM、whisperx 字幕、ffmpeg 合成，全部走 APIMART 与 Fish Audio 公开 API。当用户提到数字人、口播视频、克隆自己的声音出镜、让照片说话、avatar video、digital human，或希望把一份文案变成自己出镜的短视频时使用本技能，即使用户没有明说「数字人」三个字；用 WorkBuddy、豆包、GPT、Codex、Claude Code 等任意智能体执行均可。
---

# 数字人视频生成

## 这个技能做什么

输入三样东西——一份文案、一张形象照、一段声音样本——产出一条本人出镜的竖屏口播视频：
克隆本人的声音念文案，用 Seedance 2.0-mini 让照片中的人物按音频对口型，配 Suno 生成的 BGM，
烧录带关键词高亮与卡拉OK 推进的富样式字幕，最后 ffmpeg 合成。全程只调用文档化的公开 API，
任何能跑 Python 和 ffmpeg 的智能体都能按本技能执行，不依赖特定客户端。

整条流水线的每个参数、每个坑都在真实项目中验证过（见 references/），不是纸面方案。

## 开始前必须向用户要齐的输入

1. **文案**：存为项目根目录 script.md，每句一行，格式为「1. 台词」「2. 台词」。句子是分段
   生成的单位，一句太长（超过 15 秒）就拆成两句。
2. **形象照**：assets/ 下的照片，竖版 9:16 最佳（例如 1152×2048）。照片质量直接决定
   成片质量：正脸、光线匀、不戴墨镜口罩。用户没有合适照片时，用 GPT Image 2 生成
   （见 references/api-reference.md），并要求用户确认形象后再进入视频阶段。
3. **声音样本**：assets/ 下 15–30 秒干净人声（无背景音乐、无混响），用于 Fish Audio 克隆。
4. **两个 API Key**：Fish Audio 和 APIMART 各一个。用环境变量传入，**禁止写入任何文件、
   禁止出现在命令回显里**。脚本从环境变量读取，缺 key 时给出提示并退出，不会落盘。

## 成本与账号

| 组成部分 | 模式 | 成本（元） | 模型来源 | 备注 | 邀请链接 |
|---|---|---|---|---|---|
| 人物 | GPT image 2 | 0.1 | APIMART API | 仅无照片时生成 | https://apimart.ai/register?aff=QVui |
| 视频 | Seedance 2.0-mini | 10 | APIMART API | 720x1280，按秒计费 | https://apimart.ai/register?aff=QVui |
| BGM | Suno | 0.35 | APIMART API | 纯音乐模式，一次两首 | |
| 声音克隆 | s2.1-pro-free | 0 | Fish Audio API | 免费层，voice_id 永久复用 | https://fish.audio/?aff=X7QT3YAX3N4Q4 |
| 字幕 | whisperx small | 0 | 本地运行 | 一次性装好环境 | |

实测口径：Seedance 2.0-mini 720p 约 0.039 美元/秒，一条 30 秒视频约 12 元；Fish Audio
克隆需账号有余额（0/0 额度会返回 403），TTS 走免费层不收费；Suno 每首 0.05 美元。
一条完整视频的现实成本约 10–13 元。

## 五阶段流程

每个阶段有**门禁**：不满足就不进入下一阶段。状态存 output/state.json，脚本可断点续跑。

### Stage A 声音（Fish Audio）

1. `python scripts/avatar_voice.py clone`：上传声音样本建私有克隆模型，拿回 voice_id。
2. `python scripts/avatar_voice.py tts`：按 script.md 逐句合成 WAV。
3. `python scripts/avatar_voice.py fixwav`：修复流式 WAV 文件头（Fish 返回的头声明长度
   大于实际，不修复部分解码器会截断音频）。
4. `python scripts/avatar_voice.py qc`：逐句检查时长、平均音量、削顶。

**门禁**：全部句子音量不低于 -30dBFS、无削顶。**预估时长只是参考，不拿它当硬目标**——
为凑时长做变速是明确禁止的（见铁律）。用户试听确认音色像不像本人，才算过。

### Stage B 视频（Seedance 2.0-mini）

1. `python scripts/avatar_video.py upload-image`：形象图传到 APIMART 拿公网 URL。
2. `python scripts/avatar_video.py host-audio`：每句 WAV 经 tmpfiles.org 托管，解析出
   /dl/ 真实直链（APIMART 没有音频上传端点；直链 60 分钟过期，传完立即提交）。
3. `python scripts/avatar_video.py submit`：逐句提交，核心参数 generate_audio=False
   （视频静音，口型由外挂音频驱动——这是数字人路线的开关）。duration 按音频时长向上
   取整，单段费用估算超过 1 美元自动熔断。
4. `python scripts/avatar_video.py poll` + `download`：轮询下载。

**门禁**：抽帧检查人物不崩、不换脸；嘴部有张合。**换了音频必须重跑本阶段**——旧视频的
口型是对着旧音频驱动的，直接换音轨不等于同步。

### Stage C BGM（Suno）

1. 读文案写音乐 brief（风格/情绪/BPM/能量曲线/避免项），范式见 references/prompts.md。
2. `python scripts/avatar_bgm.py generate`：instrumental 纯音乐模式出两首候选。
3. `python scripts/avatar_bgm.py mix`：裁剪、淡入淡出、压到人声下约 -16dB、
   sidechaincompress 侧链闪避，混进成片。
4. 用户试听选一首。音乐审美是主观的，自动化到「出合格候选」为止，选曲留给人。

### Stage D 字幕（ASS 富样式，本地零成本）

不依赖任何在线服务，用 ASS/libass 实现一套接近专业剪辑软件的字幕效果。`avatar_caption.py`
的六种能力（每一项都逐帧抽像素验证过，不是纸面说明）：

| 能力 | 怎么配 | 说明 |
|---|---|---|
| Cue 分组 | script.md 台词里写 `||` | 一句长台词切成两条字幕，各自独立计时 |
| 关键词高亮 | `spec.keywords` | 例如 `AI`→青色、`5000人`→金色，跨中西文的词整体命中 |
| 卡拉OK 逐字推进 | `spec.karaoke.enabled` | 读到哪个字亮到哪个字，读过的不回退 |
| Dual Text | `spec.dual_text` | 说「十块」画面显示「10块」，口语与书面分离 |
| 时间窗换样式 | `spec.emphasis` | 指定 3–6 秒用更大字号/别的颜色，做重点句 |
| 中英双轨 + 排版动效 | `spec.points` | 中文主轨 + 英文要点轨，带 `\fad` 淡入、`\t` 缩放弹出 |

```bash
python scripts/avatar_caption.py spec    # 写出 output/caption-spec.json 模板
python scripts/avatar_caption.py all     # 生成 ASS + 烧录成 digital-human-captioned.mp4
```

字幕文案来源优先级：`--srt` 指定 > whisperx 的 `output/digital-human.srt` >
`voice/qc.json` 时长 + script.md 原文。**有台词原文时优先用原文**（加 `--prefer-script`），
它比 ASR 转写准，且不需要装 whisperx。

两个必须知道的 libass 约定（都踩过坑，见 references/pitfalls.md）：卡拉OK 的未读色是
SecondaryColour、读过切 PrimaryColour 并保持；`\k` 的单位是厘秒且**总和必须等于该条
Cue 的时长**，写成百分比会让卡拉OK 在一秒内跑完。

用户用剪映等工具配字幕时，本阶段可跳过，但要在交付说明里讲清楚。

### Stage E 合成与质检

四段视频按音频时长 trim 对齐，缩放 1080×1920，30fps，音轨用 Stage A 的 TTS
（视频自带音轨丢弃），ffmpeg 合成。合成命令模板在 references/prompts.md。
质检：总时长与各段之和一致、波形与人声零偏移、无削顶、完整解码。

## 铁律（每条都用真实教训换来的）

1. **禁止用 atempo 等后处理拉伸音频凑时长**。拉伸会让语速骤降、听感拖沓。要调整语速，
   用 Fish TTS 原生的 prosody.speed 参数重录（例如 s3=0.85、s4=0.9），或接受自然时长。
2. **API Key 只进进程内存**。环境变量传入，脚本 atexit 清除，输出里出现 key 形态的字符串
   一律替换为占位符。项目文件、报告、日志里不得出现 key。
3. **同一操作标签连续失败两次即停止**，写报告说明原因，不无限重试。HTTP 401 立即停止
   （key 问题）。单段视频费用估算超过 1 美元熔断。
4. **预估时长是参考不是指标**。给执行者的 QC 标准不能是「凑数型」的。
5. **用户听感优先于自动统计**。时长、音量、削顶全过，不代表听感没问题；用户说第 10 秒
   起不对劲，那就是有问题，从那个时间点倒查。
6. **未通过人工验收的不能叫成品**。交付时明确列出哪些项已验、哪些没验。

## 环境陷阱速查

Windows 上跑本流水线有四个已证实的坑，动手前读 references/pitfalls.md：
Python 请求必须 trust_env=False 否则连错代理、沙箱与真实文件系统是两套视图、tmpfiles
直链要解析查看页、Fish 流式 WAV 头要修。每个坑里有根因和修法。

## 脚本使用

```bash
export APIMART_API_KEY=... FISH_API_KEY=...   # 或让脚本提示时输入
python scripts/avatar_voice.py all     # Stage A 全流程
python scripts/avatar_video.py all     # Stage B 全流程
python scripts/avatar_bgm.py all       # Stage C
python scripts/avatar_caption.py all   # Stage D（不需要 key）
```

| 脚本 | 阶段 | 是否需要 key | 作用 |
|---|---|---|---|
| avatar_voice.py | A | Fish Audio | 声音克隆、逐句 TTS、修 WAV 头、质检 |
| avatar_video.py | B | APIMART | 传图、托管音频、提交口型视频、轮询下载 |
| avatar_bgm.py | C | APIMART（generate 要，mix 不要） | Suno 出 BGM 候选、侧链闪避混音 |
| avatar_caption.py | D | 否 | 富样式字幕生成与烧录 |
| avatar_compose.py | E | 否 | 拼接、缩放、转写、终检 |

脚本从当前目录读 script.md 和 assets/，产出到 output/，状态存 output/state.json，
中断后重跑同一命令即续跑。每个脚本支持 --help 查看子命令。

## 安全边界（分发给他人的承诺）

- 脚本只连接四个文档化端点：api.fish.audio、api.apimart.ai、tmpfiles.org，以及本地
  ffmpeg/whisperx。没有任何遥测、自动更新、匿名统计。
- 不读写项目目录之外的任何路径。
- API Key 只在进程内存，退出即清除；所有落盘文本经过 key 形态过滤。
- 整包可全文审计：SKILL.md 加 references/ 加 scripts/，无二进制、无可执行安装步骤。

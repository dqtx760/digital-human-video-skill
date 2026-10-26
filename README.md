# Hypipit 数字人视频生成 Skill

给 AI 助手一句话，用**你自己的形象**和**你自己的声音**，把一份文案变成一条竖屏口播视频。

你提供三样东西：一份文案、一张照片、一段 15–30 秒的声音样本。技能负责剩下的：克隆你的声音、
让照片里的人按你的声音对口型、配上贴合文案情绪的 BGM、烧录字幕、合成 1080×1920 成片。

- 声音克隆：Fish Audio（s2.1-pro-free，免费层）
- 口型驱动：Seedance 2.0-mini（APIMART）
- BGM：Suno（APIMART，纯音乐模式）
- 字幕：whisperx small（本地跑，免费）
- 合成：ffmpeg

一条 30 秒视频的成本约 10–13 元人民币。技能不绑定任何客户端，WorkBuddy、Claude Code、Codex、
豆包、ChatGPT 等任何能加载 Skill 并执行脚本的 AI 助手都能跑。

---

## 前置条件

### 1. 本地环境

| 依赖 | 是否必须 | 安装方式 |
|---|---|---|
| Python 3.10+ | 必须 | python.org 或系统包管理器 |
| requests | 必须 | `pip install requests` |
| ffmpeg | 必须 | Windows `winget install ffmpeg`；macOS `brew install ffmpeg`；Linux `apt install ffmpeg` |
| whisperx | 可选（字幕） | `pip install whisperx`，装了才能跑字幕，不装不影响出片 |
| 代理（国内网络） | 必须 | 两个 API 在国内需走代理，默认 `http://127.0.0.1:7897`，用 `AVATAR_PROXY` 环境变量改 |

验证环境：

```bash
ffmpeg -version
python -c "import requests; print('requests ok')"
```

### 2. 两个 API Key

| 服务 | 用途 | 费用 | 获取 |
|---|---|---|---|
| Fish Audio | 声音克隆与合成 | 免费层即可 | https://fish.audio/?aff=X7QT3YAX3N4Q4 |
| APIMART | 形象图、视频、BGM | 按量付费，视频约 10 元/条 | https://apimart.ai/register?aff=QVui |

Key 通过环境变量传给脚本，**不会写入任何文件**：

```bash
export FISH_API_KEY=你的Fish密钥
export APIMART_API_KEY=你的APIMART密钥
```

### 3. 安装技能

方式一：把本文件夹整体复制到你的客户端 skills 目录（例如 `~/.workbuddy/skills/`、
`~/.claude/skills/`），重启动客户端即可识别。

方式二：如果客户端支持，直接安装仓库里的 `Hypipit 数字人视频生成.skill` 包。

---

## 使用示例

### 示例一：从文案到成片（完整流程）

**第一步：准备项目文件夹**

```
my-video/
├── script.md              # 文案，每句一行
├── assets/
│   ├── photo.png          # 形象照，竖版 9:16
│   └── voice-sample.m4a   # 声音样本，15–30 秒干净人声
```

`script.md` 写法：

```
1. 所有人都低估了AI，物理世界也将被AI击穿。
2. 所有人对AI的重视程度或者说信仰其实都不是特别够，包括我自己。
3. 目前全世界范围内对尖端的AI模型真正的了解或者在开发的，全世界范围内最多大概只有5000人。
4. 事实上目前真正看到了最顶尖AI能力的效果的人真的是非常非常少。
```

**第二步：对 AI 助手说一句话**

```
帮我用我的照片和声音，把 my-video 里的文案做成一条数字人视频。
```

助手会按技能里的五阶段执行：克隆声音 → 逐句合成语音 → 生成口型视频 → 配 BGM → 合成成片，
每个阶段有质量门禁，会在需要你确认的地方停下来（音色像不像你、BGM 选哪首）。

**第三步：也可以自己跑命令**

```bash
cd my-video
export FISH_API_KEY=... APIMART_API_KEY=... AVATAR_ROOT=$(pwd)

# Stage A 声音：克隆 + 逐句合成 + 修 WAV 头 + 质检
python <技能目录>/scripts/avatar_voice.py all      # 听一下 output/voice/ 确认音色

# Stage B 视频：上传形象图 + 托管音频 + 生成口型视频
python <技能目录>/scripts/avatar_video.py all

# Stage E 合成
python <技能目录>/scripts/avatar_compose.py all    # 产出 output/digital-human.mp4

# Stage C BGM（需要先写 output/bgm/brief.md，见下）
python <技能目录>/scripts/avatar_bgm.py generate   # 出两首候选
python <技能目录>/scripts/avatar_bgm.py mix --candidate 1
```

任何一步中断，重跑同一条命令即可续跑（状态存在 `output/state.json`）。

### 示例二：给已有视频配 BGM

先写音乐简报 `output/bgm/brief.md`：

```
风格流派：cinematic ambient
情绪：克制的疑虑逐渐转向坚定
乐器：极简钢琴、微弱电子脉冲
BPM：80
能量曲线：开头稀疏，中段积蓄，结尾冷静收束
避免项：人声、鼓点过重
```

然后：

```bash
python <技能目录>/scripts/avatar_bgm.py generate    # Suno 出两首纯音乐候选
python <技能目录>/scripts/avatar_bgm.py mix --candidate 1
```

BGM 会裁到成片长度、头尾淡入淡出、压到人声下约 -16dB，并用侧链闪避让人说话时音乐自动让位。
两首候选选哪首留给你试听决定——技能只负责出合格候选。

### 示例三：让 AI 助手排查问题

```
视频生成完了，但我听着人声从第 10 秒开始不对劲，拖拖沓沓的像变速了，你排查一下。
```

助手会按 `references/pitfalls.md` 定位：用户说的时间点对齐某句的音频起点，确认内容完整后，
判定为后处理拉伸所致，改用 TTS 原生语速参数重录并重生成对应片段——不会用再次拉伸掩盖问题。

---

## 目录结构

```
├── SKILL.md                    技能主入口（流程、铁律、成本）
├── references/
│   ├── api-reference.md        四个端点的实测参数与返回结构
│   ├── pitfalls.md             10 个环境坑的根因与修法
│   └── prompts.md              口型提示词、BGM 范式、ffmpeg 命令模板
├── scripts/                    五个可执行脚本（见上）
└── evals/evals.json            测试用例
```

---

## 常见问题

**提示缺少环境变量**：Key 没 export。脚本不会把 Key 写进文件，只能从环境变量读。

**请求 502 / 连接失败**：国内网络需要代理。确认代理在跑，端口不对就改 `AVATAR_PROXY`。
如果 curl 通但脚本不通，是 Python 合并了环境里的代理变量，脚本已内置 `trust_env=False` 处理。

**Fish 克隆返回 403**：账号额度为 0，充值后重试。

**声音听起来拖沓**：绝不要为了凑时长拉伸音频。用 TTS 原生语速参数重录，或接受自然时长。

**视频嘴型和声音不同步**：换了音频必须重新生成视频，直接换音轨不等于同步。

---

## 安全与隐私

- 脚本只连接四个文档化端点：`api.fish.audio`、`api.apimart.ai`、`tmpfiles.org`，以及本地 ffmpeg / whisperx。
- 没有任何遥测、自动更新、统计上报，整包可全文审计。
- API Key 只在进程内存，退出即清除；所有落盘文本会过滤 Key 形态的字符串。
- 不读写项目目录之外的路径。

## 许可

MIT。分发时请保留成本表里的平台注册链接。

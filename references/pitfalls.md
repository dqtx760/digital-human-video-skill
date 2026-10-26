# 环境陷阱（全部实测踩过，含根因与修法）

## 1. Python 请求连错代理：trust_env=False

**现象**：curl 带 -x http://127.0.0.1:7897 访问 API 正常返回 200，同样的 URL 用
Python requests 却报 Tunnel connection failed: 502 Bad Gateway，且每次必现。

**根因**：宿主环境（如 WorkBuddy 沙箱）会给 Python 子进程注入
HTTP_PROXY / HTTPS_PROXY 指向自己的服务代理（如 127.0.0.1:61676），requests 默认
trust_env=True 会把环境变量里的代理和显式 proxies 合并，连到了错误的代理上。curl 用
命令行 -x 指定，不受环境影响，所以只有 Python 挂。

**修法**：requests.Session() 之后立刻 session.trust_env = False，再设显式 proxies。
scripts/avatar_common.py 已内置。代理端口用环境变量 AVATAR_PROXY 覆盖，默认 7897。

## 2. 沙箱与真实文件系统是两套视图

**现象**：脚本在沙箱里跑，写文件成功，但用户在资源管理器里看不到；或反过来，工具写的
.py 文件沙箱里找不到，报 FileNotFoundError。

**根因**：Agent 的沙箱 bash 有独立文件系统，和用户可见的真实盘不共享；/tmp 跨进程
也不共享。

**修法**：产出必须落到用户能看到的路径时（本项目就是），用沙箱绕过模式执行
（Bash 工具 dangerouslyDisableSandbox=true），脚本里用 Windows 绝对路径或
os.path.expanduser。验证产物也要用同样模式。另外注意：Git Bash 的 /c/... 路径
不能直接喂给 Windows 版 python，要用 C:\... 原生路径。

## 3. bash heredoc 内联 Python 必坏

**现象**：把多行 Python 塞进 bash -c 的 heredoc，报 unexpected EOF while looking for
matching quote。

**根因**：bash 按 shell 引号规则解析 heredoc 内容，Python 的三引号字符串会破坏配对。

**修法**：永远先用文件写入工具落成 .py，再执行。不要图省事内联。

## 4. Fish TTS 流式 WAV 头声明长度大于实际

**现象**：下载的 s1.wav 用 ffmpeg 能解，但播放器放到一半就静音；或读取报错。

**根因**：Fish 流式返回的 WAV，RIFF 头里声明的文件长度大于实际字节数（流式写头时
还不知道总长）。

**修法**：用 Python wave 模块读 PCM 数据后重写文件头（scripts/avatar_voice.py
fixwav 子命令）。原始响应先留存到 output/voice/raw/ 再修，修复动作只能做一次。

## 5. tmpfiles 直链必须解析查看页

**现象**：上传后拿到查看页 URL，直接当音频 URL 提交给视频任务，任务失败或下载到 HTML。

**根因**：查看页是给人看的，真实下载地址是页面里 href="/dl/<ts>.<hash>/<id>/<file>"
这个形式，且查看页会把 http 跳 https。

**修法**：GET 查看页，正则提取 /dl/ 直链再提交。直链 60 分钟过期，托管后立即提交，
不要囤。scripts/avatar_video.py host-audio 已封装。

## 6. 给音频做 QC 时 silencedetect 没输出

**现象**：ffmpeg -v error 加 silencedetect 过滤器，什么都解析不到。

**根因**：silencedetect 的结果走 info 级日志，-v error 把它抑制了。

**修法**：解析 silencedetect 时不要加 -v error，或显式 -v info 再抓 stderr。

## 7. atempo 拉伸凑时长（最贵的事故）

**现象**：自动 QC 时长偏差全过，用户试听说「从第 10 秒起不对劲，拖音、像电音」。

**根因**：QC 规则把「预估时长偏差小于 20%」当硬目标，执行者用 ffmpeg atempo 0.75
把音频拉长凑数，语速掉四分之一，听感拖沓。定位方法：用户说的时间点正好是某句起点，
faster-whisper 转写确认内容没错、频谱没伪影——内容是好的，是节奏被毁了。

**修法**：预防胜于治疗。QC 标准里预估时长只作参考；要慢就用 TTS 原生 prosody.speed
重录；接受自然时长。给执行 Agent 的指标不能是「凑数型」的。

## 8. Suno 两个参数怪癖

- variety 传数字报 400（Go 后端按 string 解析），要传 "2" 这样的字符串。
- duration_s 被忽略，生成出来 70 秒以上，时长必须本地 ffmpeg atrim 裁，别指望接口。

## 9. 计费日志的时间列是完成时间

对账时按「提交时间 = 显示时间 − 用时列」还原，能和本地文件修改时间互相验证。
另外：失败/被放弃的提交可能不计费（不出现在消费记录里），项目脚本记录的 task_id
可能与账单对不上——**以平台消费记录为准，以项目记录为线索**。

## 10. 沙箱代理让 curl 写文件偶发失败

curl: (23) client returned ERROR on write of N bytes。改用 Python urllib.request
下载落盘更稳。scripts 里的下载都用 requests 流式写文件，已验证可靠。

## 11. ASS 卡拉OK 的颜色语义是反直觉的

**现象**：按「Primary=正文色、Secondary=高亮色」写样式，卡拉OK 全是白的，或者整行
一开始就是高亮色、看不出推进。

**根因**：libass 的 `\k` 约定是——**未读部分用 SecondaryColour，读到的部分切到
PrimaryColour 并保持**（trail 模式）。直觉上「Primary 是主色」刚好写反了。

**修法**：卡拉OK 开启时 Primary 放高亮色、Secondary 放正文底色；不开卡拉OK 时两者
都放正文色。scripts/avatar_caption.py 已按此实现。

## 12. `\k` 总和必须等于 Cue 时长

**现象**：卡拉OK 一秒内就跑完，或者整行提前全部点亮。

**根因**：`\k` 的单位是**厘秒**（1/100 秒），不是百分比。如果按「占比」写成了
加起来等于 100 的数，那条 Cue 的总时长就被当成 1 秒。

**修法**：按字数比例把 Cue 时长分配成厘秒：
`centis = round(100 * cue_duration * len(unit) / total_chars)`。

## 13. 样式继承会覆盖关键词高亮

**现象**：给某段加了 emphasis（换大字号/换色）后，关键词高亮失效，整行变成一个颜色。

**根因**：emphasis 生成的是独立 Style，它的 PrimaryColour 直接覆盖行内 `{\c...}` 的
效果。

**修法**：卡拉OK 开启时，Emph 样式的 PrimaryColour 也要设成高亮色，SecondaryColour
才放 emphasis 自己的颜色。

## 14. Windows 上 ffmpeg subtitles 过滤器不认盘符路径

**现象**：`Unable to parse option value "C\:/path/captions.ass" as image size`。

**根因**：subtitles 过滤器把路径里的冒号当成了参数分隔符。

**修法**：传**相对路径**并给 ffmpeg 指定 cwd，例如 `subtitles=output/captions.ass` +
`cwd=项目根目录`。avatar_common.ffmpeg() 已支持 cwd 参数。

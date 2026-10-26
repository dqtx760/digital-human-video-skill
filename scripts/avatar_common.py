"""数字人视频流水线 - 共享基础设施。

密钥只从环境变量读取（APIMART_API_KEY / FISH_API_KEY），绝不落盘；
请求强制 trust_env=False 并走显式代理（见 references/pitfalls.md 第 1 条）；
状态存 <root>/output/state.json，支持断点续跑；同一标签连续失败两次即停止。
"""
import argparse
import atexit
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8')

# 项目根目录：环境变量 AVATAR_ROOT 优先，否则当前工作目录。
# 根目录下应有 script.md 和 assets/，产出写到 output/。
ROOT = Path(os.environ.get('AVATAR_ROOT') or Path.cwd())
OUT = ROOT / 'output'
STATE = OUT / 'state.json'
PROXY = os.environ.get('AVATAR_PROXY', 'http://127.0.0.1:7897')
KEYS = {}

state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {
    'stages': {}, 'failures': {}, 'events': [], 'video': {}, 'tts': {}}
OUT.mkdir(exist_ok=True)


def save():
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def clean(value):
    text = str(value)
    for key in KEYS.values():
        if key:
            text = text.replace(key, '[已隐藏密钥]')
    return re.sub(r'sk-[A-Za-z0-9_-]{12,}', '[已隐藏密钥]', text)


def stop(reason):
    save()
    (OUT / 'report.md').write_text(
        '# 数字人视频执行报告\n\n状态：已停止。\n\n- 停止原因：' + clean(reason) + '\n'
        '- 非敏感参数与事件流水：state.json。\n'
        '- 未通过人工验收的产出不能认定为成品。\n', encoding='utf-8')
    print('STOP: ' + clean(reason), flush=True)
    raise SystemExit(2)


def failed(label, reason):
    state['failures'][label] = state['failures'].get(label, 0) + 1
    state['events'].append({'step': label, 'result': 'failed', 'reason': clean(reason)[:500]})
    save()
    print('FAILED %s %d/2: %s' % (label, state['failures'][label], clean(reason)[:300]), flush=True)
    if 'HTTP 401' in str(reason):
        stop(label + ' 返回 401，密钥无效，立即停止。')
    if state['failures'][label] >= 2:
        stop(label + ' 连续失败两次；最后错误：' + str(reason)[:300])
    return None


def api(label, method, url, service=None, proxy=True, **kwargs):
    if state['failures'].get(label, 0) >= 2:
        stop(label + ' 已连续失败两次，禁止再次调用。')
    headers = dict(kwargs.pop('headers', {}))
    if service:
        if service not in KEYS:
            stop('缺少 ' + service.upper() + '_API_KEY 环境变量。密钥请用环境变量传入，不要写进文件。')
        headers['Authorization'] = 'Bearer ' + KEYS[service]
    proxies = {'http': PROXY, 'https': PROXY} if proxy else {}
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.request(method, url, headers=headers, proxies=proxies,
                                   timeout=(20, 180), **kwargs)
    except requests.RequestException as error:
        session.close()
        return failed(label, type(error).__name__ + ': ' + str(error)[:200])
    if not 200 <= response.status_code < 300:
        body = clean(response.text[:1000])
        session.close()
        return failed(label, 'HTTP %d: %s' % (response.status_code, body))
    state['failures'][label] = 0
    state['events'].append({'step': label, 'result': 'ok', 'http': response.status_code})
    save()
    print('OK %s: HTTP %d' % (label, response.status_code), flush=True)
    session.close()
    return response


def require_keys():
    for name in ('APIMART_API_KEY', 'FISH_API_KEY'):
        value = os.environ.get(name, '').strip()
        if not value:
            stop('缺少环境变量 ' + name + '。请 export 后重跑；密钥禁止写入任何文件。')
        KEYS['apimart' if name.startswith('APIMART') else 'fish'] = value


def forget():
    KEYS.clear()


atexit.register(forget)


def read_sentences():
    path = ROOT / 'script.md'
    if not path.exists():
        stop('项目根目录缺少 script.md（每句一行，格式「1. 台词」）。')
    found = re.findall(r'^\s*\d+[.、]\s*(.+)$', path.read_text(encoding='utf-8'), re.M)
    if not found:
        stop('script.md 里没解析到句子，格式应为「1. 台词」。')
    return [s.strip() for s in found]


def ffmpeg(args, label='ffmpeg', cwd=None):
    """cwd 用来配合过滤器里的相对路径：Windows 下 subtitles= 遇到盘符冒号容易解析失败，
    切到项目根目录后只传相对路径最稳。"""
    result = subprocess.run(['ffmpeg', '-hide_banner', '-y'] + args,
                            capture_output=True, text=True, cwd=cwd)
    if result.returncode:
        stop(label + ' 失败: ' + clean(result.stderr[-800:]))
    return result


def ffprobe_duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        stop('ffprobe 失败: ' + result.stderr[-300:])
    return float(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description='数字人视频流水线共享层（被其他脚本 import）')
    parser.parse_args()


if __name__ == '__main__':
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神剧情助手 - 桌面启动器

双击运行即可打开聊天客户端。自动启动 Flask 后端服务。
"""

import os
import sys
import time
import threading
import socket

# 确定项目根目录（开发模式用脚本所在目录，打包模式用 exe 所在目录）
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

# 抑制 huggingface 离线警告
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

if sys.platform == "win32" and sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def find_free_port(start=5000):
    """找一个空闲端口"""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _shutdown_old_server(port: int):
    """尝试关闭端口上的旧 Flask 实例"""
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/shutdown", timeout=2)
    except Exception:
        # /api/shutdown 没有响应体是正常的（服务器在响应前就退出了）
        pass


def main():
    # 确保 WebView2 以中文模式启动（IME 必须）
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--lang=zh-CN"

    PORT = find_free_port(5000)
    BASE_URL = f"http://127.0.0.1:{PORT}"

    # ---- 清理旧进程残留 ----
    if PORT == 5000:
        # 5000 端口空闲，说明没有旧实例，正常启动
        pass
    else:
        # 5000 端口被占用，尝试杀掉旧进程
        print(f"[启动器] 检测到旧实例占用端口 5000，尝试清理...")
        try:
            _shutdown_old_server(5000)
            time.sleep(1)
            # 重新检查 5000 是否可用
            PORT = find_free_port(5000)
            BASE_URL = f"http://127.0.0.1:{PORT}"
            if PORT == 5000:
                print("[启动器] 旧实例已清理，使用端口 5000")
            else:
                print(f"[启动器] 清理后端口 5000 仍被占用，使用端口 {PORT}")
        except Exception:
            print(f"[启动器] 无法清理旧实例，使用端口 {PORT}")

    # ---- 后台启动 Flask ----
    from genshin_story_web_api import app

    def run_flask():
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # ---- 等待 Flask 就绪 ----
    print(f"[启动器] 等待 Flask 服务启动 (端口 {PORT})...")
    import urllib.request
    max_wait = 60
    for _ in range(max_wait):
        try:
            resp = urllib.request.urlopen(f"{BASE_URL}/api/status", timeout=1)
            if resp.status == 200:
                print("[启动器] Flask 服务已就绪")
                break
        except Exception:
            time.sleep(1)
    else:
        print("[启动器] 警告：Flask 服务启动超时，尝试继续...")

    # 额外等待确保所有路由（/chat等）完全初始化
    time.sleep(2)
    print("[启动器] 等待路由初始化完成...")

    # ---- 打开 pywebview 窗口 ----
    import webview

    chat_url = f"{BASE_URL}/chat"

    window = webview.create_window(
        title="原神剧情助手 - 提瓦特档案馆",
        url=chat_url,
        width=1100,
        height=750,
        min_size=(900, 600),
    )

    print(f"[启动器] 窗口已打开: {chat_url}")
    webview.start(
        private_mode=False,
        storage_path=os.path.join(os.environ['LOCALAPPDATA'], 'GenshinStoryAssistant', 'WebView2')
    )
    print("[启动器] 窗口已关闭，退出。")
    os._exit(0)


if __name__ == "__main__":
    main()

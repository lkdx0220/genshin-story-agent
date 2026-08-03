#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键打包脚本 —— 将 launcher.py 打包为单 exe 文件

运行后将生成 CASE-原神剧情助手/原神剧情助手.exe
压缩整个 CASE-原神剧情助手 文件夹发给朋友即可使用。
"""

import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "原神剧情助手"

# 路径修正
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check_prerequisites():
    """检查必要工具"""
    try:
        subprocess.run(["pyinstaller", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[错误] 未安装 pyinstaller，请先运行: pip install pyinstaller")
        return False
    return True


def clean_dist():
    """清理旧的构建产物"""
    for d in ["dist", "build"]:
        path = os.path.join(BASE_DIR, d)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"[清理] 已删除 {d}/")
    spec = os.path.join(BASE_DIR, f"{EXE_NAME}.spec")
    if os.path.exists(spec):
        os.remove(spec)
        print(f"[清理] 已删除 {EXE_NAME}.spec")


def build():
    """执行 pyinstaller 打包"""
    print(f"[打包] 开始构建 {EXE_NAME}.exe ...")

    args = [
        sys.executable, "-m", "PyInstaller",
        "launcher.py",
        "--onefile",
        "--windowed",
        f"--name={EXE_NAME}",
        "--icon=ui/app_icon.ico",
        "--clean",
        "--noconfirm",
        # 收集关键包的完整子模块
        "--collect-all=langchain",
        "--collect-all=langchain_core",
        "--collect-all=langchain_community",
        "--collect-all=langgraph",
        "--collect-all=langsmith",
        "--collect-all=dashscope",
        # 显式导入项目自有模块
        "--hidden-import=genshin_story_agent",
        "--hidden-import=genshin_story_web_api",
        "--hidden-import=character_aliases",
        "--hidden-import=genshin_knowledge_base",
        "--hidden-import=genshin_knowledge_base.roles",
        "--hidden-import=genshin_knowledge_base.regions",
        "--hidden-import=genshin_knowledge_base.quests",
        "--hidden-import=genshin_knowledge_base.books",
        "--hidden-import=genshin_knowledge_base.weapons",
        "--hidden-import=genshin_knowledge_base.main_story",
        "--hidden-import=kb_vector_store",
        "--hidden-import=memory_manager",
        # 内嵌 .env 文件（API key 编译进 exe，分发时可删除目录中的 .env）
        "--add-data=.env;.",
        # 排除不需要的大型包以减小体积
        "--exclude-module=torch",
        "--exclude-module=torchvision",
        "--exclude-module=numpy",
        "--exclude-module=scipy",
        "--exclude-module=pandas",
        "--exclude-module=matplotlib",
        "--exclude-module=PIL",
        "--exclude-module=cv2",
        "--exclude-module=sklearn",
        "--exclude-module=jupyter",
        "--exclude-module=notebook",
        "--exclude-module=ipython",
    ]

    result = subprocess.run(args, cwd=BASE_DIR)
    if result.returncode != 0:
        print("[错误] PyInstaller 打包失败，请检查上方输出")
        sys.exit(1)


def post_build():
    """构建后处理：复制 exe 到项目根目录"""
    exe_src = os.path.join(BASE_DIR, "dist", f"{EXE_NAME}.exe")
    exe_dst = os.path.join(BASE_DIR, f"{EXE_NAME}.exe")

    if os.path.exists(exe_src):
        shutil.copy2(exe_src, exe_dst)
        size_mb = os.path.getsize(exe_dst) / (1024 * 1024)
        print(f"[完成] {EXE_NAME}.exe 已生成 ({size_mb:.1f} MB)")
        print(f"[完成] 双击 {EXE_NAME}.exe 即可启动")
        print(f"[提示] 压缩整个 CASE-原神剧情助手 文件夹发给朋友即可使用")
    else:
        print("[错误] 打包产物未找到，请检查终端输出")


def main():
    os.chdir(BASE_DIR)

    if not check_prerequisites():
        sys.exit(1)

    clean_dist()
    build()
    post_build()


if __name__ == "__main__":
    main()

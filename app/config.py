# -*- coding: utf-8 -*-
"""全局配置：环境变量、API key、路径常量、记忆配置。

所有模块的配置中心，不依赖其他 app/ 模块。
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")

from dotenv import load_dotenv

# ====== .env 加载（兼容 PyInstaller 打包模式）======
if getattr(sys, 'frozen', False):
    # exe 模式：从 exe 同目录读取外部 .env，避免把密钥打进成品。
    _ENV_PATH = os.path.join(os.path.dirname(sys.executable), '.env')
    if os.path.exists(_ENV_PATH):
        load_dotenv(_ENV_PATH)
else:
    load_dotenv()

# ====== HuggingFace 镜像（避免 sentence-transformers 联网）======
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# ====== 禁用 LangSmith tracing（避免浪费 API 限额）======
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_ENDPOINT"] = ""
os.environ["LANGCHAIN_API_KEY"] = ""
os.environ["LANGCHAIN_PROJECT"] = ""

# ====== Windows 控制台 UTF-8 ======
if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ====== 项目根目录与资源路径 ======
# 本文件位于 app/config.py，项目根 = app/ 的父目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(_PROJECT_ROOT, "content_data")
PROMPTS_DIR = os.path.join(_PROJECT_ROOT, "prompts")

# ====== API 配置 ======
# 主：token-plan 专用接口（新 Key 对应的 OpenAI 兼容端点）
QWEN_API_KEY = os.getenv("DASHSCOPE_API_KEY")
QWEN_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
# 备用：原 DashScope 接口（新 Key 配额用尽/联不通时自动切回）
QWEN_FALLBACK_API_KEY = os.getenv("DASHSCOPE_FALLBACK_API_KEY", "") or None
QWEN_FALLBACK_BASE_URL = os.getenv("DASHSCOPE_FALLBACK_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# ====== 多轮记忆配置 ======
RECENT_TURNS = 3       # 最近 N 轮完整保留
SUMMARY_TRIGGER = 5    # 总轮数超过此值时触发摘要

# ====== Agent 迭代上限 ======
MAX_AGENT_ITERATIONS = 10
MAX_PLAN_RETRIES = 2       # Plan Agent 无工具调用时的最大强制重试次数
MAX_FAST_ITERATIONS = 2    # 快速路径的最大工具调用轮次

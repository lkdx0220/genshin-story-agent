# -*- coding: utf-8 -*-
"""RAG 对话记忆：跨会话持久化与初始化。

封装 GenshinRAGMemory 的可用性检测与实例化，
对 agent 层屏蔽 RAG_AVAILABLE=False 的情况。
"""
try:
    from memory_manager import GenshinRAGMemory
    RAG_AVAILABLE = True
    print("[初始化] RAG记忆系统已加载")
except ImportError:
    RAG_AVAILABLE = False
    print("[警告] RAG记忆系统不可用")

# 模块级单例：不可用时保持 None
rag_memory = None
if RAG_AVAILABLE:
    try:
        rag_memory = GenshinRAGMemory(persist_dir="./conversation_memory")
        print("[初始化] RAG记忆管理器已就绪")
    except Exception as e:
        print(f"[错误] RAG记忆初始化失败: {e}")

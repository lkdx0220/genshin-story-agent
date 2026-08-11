# -*- coding: utf-8 -*-
"""原神剧情助手 Agent - 模块化包

参照 OpenManus 的分层模式拆分：
- app/config.py: 环境变量、API key、路径常量
- app/llm.py: LLM 实例与重试封装
- app/schema.py: Agent 状态、迭代常量、系统 prompt
- app/data.py: 知识库加载与数据辅助函数
- app/retrieval.py: 别名处理、BM25、混合检索、RRF 融合
- app/formatters.py: 角色/地区/剧情等格式化函数
- app/rag_memory.py: RAG 对话记忆
- app/tools/: 27 个 @tool 工具，按类别分文件
- app/agent/: LangGraph 节点与工具执行器
- app/workflow.py: StateGraph 拼装
"""

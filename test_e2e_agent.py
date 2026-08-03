#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
端到端测试 -- 跑完整 Agent 流程（路由器 → Plan Agent → 工具调用 → Answer Agent）
选取 6 个代表性案例，展示路由结果和最终回答。
"""

import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import intent_router
from intent_router import route_intent, get_tools_for_intent, TOOL_GROUPS, get_last_raw_response

# 清空路由缓存
intent_router._route_cache.clear()

# ====== 标签中文映射 ======
LABEL_CN = {
    "A": "搜索检索",
    "B": "角色查询",
    "C1": "装备养成",
    "C2": "世界生态",
    "D": "剧情任务",
    "E": "书籍文献",
    "F": "溯源追踪",
    "ALL": "全量工具",
}

TOOL_COUNT_CN = {
    "A": 2, "B": 6, "C1": 5, "C2": 5, "D": 3, "E": 2, "F": 1, "ALL": 24,
}


def fmt_labels(labels):
    """把 ['B', 'D'] 翻译成 ['B(角色查询)', 'D(剧情任务)']"""
    return ", ".join(f"{l}({LABEL_CN.get(l, l)})" for l in labels)


def fmt_tool_count(labels):
    if "ALL" in labels:
        return 24
    names = set()
    for l in labels:
        if l in TOOL_GROUPS:
            names.update(TOOL_GROUPS[l])
    names.add("hybrid_search")
    return len(names)


# ====== 6 个代表性测试案例 ======
E2E_CASES = [
    {
        "id": "E1",
        "desc": "正常-任务梗概（期望D）",
        "question": "捕风讲了什么",
        "conv_summary": "",
        "turn": 0,
    },
    {
        "id": "E2",
        "desc": "正常-角色查询（期望B）",
        "question": "胡桃是谁？",
        "conv_summary": "",
        "turn": 0,
    },
    {
        "id": "E3",
        "desc": "攻击4-多轮指代失败案例",
        "question": "她唱的那首歌原文是什么？",
        "conv_summary": "用户问胡桃传说任务中的幽灵小九九，接着问了这个小女孩的故事",
        "turn": 3,
    },
    {
        "id": "E4",
        "desc": "攻击5-动机询问（路由到D而非C2）",
        "question": "深渊教团为什么要毁灭七国？",
        "conv_summary": "",
        "turn": 0,
    },
    {
        "id": "E5",
        "desc": "攻击6-跨类型对比（千夜浮梦被误判为书）",
        "question": "千夜浮梦的故事和纳西妲的传说任务都提到了轮回，它们对轮回的理解有什么异同？",
        "conv_summary": "",
        "turn": 0,
    },
    {
        "id": "E6",
        "desc": "攻击7-全量工具触发",
        "question": "帮我汇总一下'命运的织机'的所有一手资料，包括任务原文、书籍段落和角色语音",
        "conv_summary": "",
        "turn": 0,
    },
]


def run_single_e2e(case):
    """跑单个案例的完整 Agent 流程"""
    import genshin_story_agent as agent_mod

    tid = case["id"]
    desc = case["desc"]
    question = case["question"]
    conv_summary = case["conv_summary"]
    turn = case["turn"]

    print(f"\n{'=' * 70}")
    print(f"[{tid}] {desc}")
    print(f"{'=' * 70}")
    print(f"  用户问题: {question}")
    if conv_summary:
        print(f"  对话摘要: {conv_summary[:80]}...")

    # Step 1: 路由
    labels = route_intent(question, conversation_summary=conv_summary, turn_number=turn)
    raw = get_last_raw_response()
    tool_count = fmt_tool_count(labels)
    print(f"\n  >> 路由结果: {fmt_labels(labels)}")
    print(f"  >> LLM原始回复: {raw}")
    print(f"  >> 暴露工具数: {tool_count} 个")

    # Step 2: 调用完整 Agent
    print(f"\n  >> 正在调用 Plan Agent + Answer Agent...")
    agent = agent_mod.create_agent_workflow()

    start = datetime.now()
    try:
        result = agent.invoke({
            "user_query": question,
            "rewritten_query": None,
            "alias_notes": None,
            "conversation_history": [],
            "conversation_summary": conv_summary,
            "messages": [],
            "final_response": None,
            "iteration": 0,
            "intent_labels": None,
        })
        elapsed = (datetime.now() - start).total_seconds()
        response = result.get("final_response", "(未生成回答)")
        actual_labels = result.get("intent_labels") or labels
        exec_mode = result.get("execution_mode", "?")

        print(f"\n  >> 执行模式: {exec_mode}  |  意图标签: {fmt_labels(actual_labels)}")
        print(f"  >> 耗时: {elapsed:.1f}s")
        print(f"\n  {'─' * 60}")
        print(f"  >> 最终回答:")
        print(f"  {'─' * 60}")
        print(response[:2000])
        if len(response) > 2000:
            print(f"  ... (截断，共 {len(response)} 字)")

    except Exception as e:
        print(f"\n  !! Agent 调用失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("=" * 70)
    print("端到端测试 -- 完整 Agent 流程")
    print("测试用例: 6 个代表性案例")
    print("=" * 70)

    for case in E2E_CASES:
        run_single_e2e(case)
        # 每个案例之间等一下，避免 API 限流
        time.sleep(1)

    print(f"\n{'=' * 70}")
    print("端到端测试完成")
    print("=" * 70)


if __name__ == "__main__":
    main()

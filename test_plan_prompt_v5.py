#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Plan Prompt V5 瘦身版 -- 20 题行为验证测试

测试范围：
  - 搜索豁免边界          (题 1-2)
  - NPC 成长协议          (题 3-4，成对测试)
  - P2 降级深度判断        (题 5-6，成对测试)
  - P3 对比分析强制全文    (题 7-8，含武器对比冲突验证)
  - 前提待验证+信号传递    (题 9-11，含 research_assist)
  - 失败熔断链式验证       (题 12→13→14)
  - 上下文指代消解        (题 15-16)
  - 工具选择+引述验证     (题 17-18)
  - 模糊地带+格式合规     (题 19-20)

执行顺序：
  1. 先跑 #8（武器对比冲突）和 #11（信号传递）-- 验证硬性缺陷修复
  2. #3+#4 成对测试（NPC 协议宽泛/漏触发）
  3. #12→#13→#14 链式测试（熔断状态保持）
  4. 其余题目

用法: python test_plan_prompt_v5.py [--baseline]
      加 --baseline 会使用稳定版旧 Prompt 跑对照测试
"""

import sys
import os
import re
import json
import time
import copy
import argparse
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ====== 常量 ======
RESULT_FILE = os.path.join(os.path.dirname(__file__), "plan_test_results_v5.json")
BASELINE_FILE = os.path.join(os.path.dirname(__file__), "plan_test_results_baseline.json")
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts", "system")

# ====== 测试题定义 ======
# 优先级: "critical"=先跑, "paired"=成对, "chain"=链式, "normal"=普通
TEST_CASES = [
    # ─── Critical: 验证硬性缺陷修复 ───
    {
        "id": 8, "priority": "critical", "title": "P3对比-武器冲突",
        "question": "千夜浮梦和裁叶萃光的背景故事有什么异同？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["对比分析"],
            "must_tool": ["query_weapon"],
            "forbid_tool": ["load_quest_content", "load_book_content"],
            "must_signal": [],
            "max_tool_count": 4,
        },
        "note": "P3规则+示例验证：武器对比应用query_weapon，不应尝试load_quest_content",
    },
    {
        "id": 11, "priority": "critical", "title": "降级研究-信号传递",
        "question": "编剧当初设计纳西妲时，为什么把她设定为500岁而不是更年长？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["降级研究"],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": ["response_mode=research_assist"],
            "max_tool_count": 4,
        },
        "note": "Plan-Answer接口完整性：工具决策末尾必须包含 response_mode=research_assist",
    },
    # ─── Paired: NPC 协议（#3+#4 成对测试）───
    {
        "id": 3, "priority": "paired", "title": "NPC成长-语义触发",
        "question": "请梳理流浪者从散兵到现在的完整心路历程和人物弧光。",
        "conversation_summary": "", "turn_number": 0, "alias_notes": "[别名标注] 散兵 -> 流浪者（已确认）",
        "checks": {
            "intent": ["NPC成长"],
            "must_tool": ["query_character", "hybrid_search", "load_quest_content"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 10,
        },
        "note": "抽象词（心路历程/人物弧光）触发NPC协议3轮",
    },
    {
        "id": 4, "priority": "paired", "title": "NPC成长-负向约束",
        "question": "散兵为什么这么讨厌雷电将军？",
        "conversation_summary": "", "turn_number": 0, "alias_notes": "[别名标注] 散兵 -> 流浪者（已确认）",
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
            "hard_check": lambda r: not r.get("triggered_npc_protocol", False),
            "hard_check_desc": "不应触发NPC协议（非成长类问题）",
        },
        "note": "防止NPC协议泛化滥用：非成长类问题不触发3轮协议",
    },
    # ─── Chain: 熔断链式验证 ───
    {
        "id": 12, "priority": "chain", "chain_seq": 1, "chain_group": "circuit_breaker",
        "title": "失败熔断-P1降级优先",
        "question": '请加载任务\u201c影蝶之章\u201d的完整内容',
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["load_quest_content", "hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 6,
        },
        "note": "P1降级不计入熔断：第1次load失败→P1触发hybrid_search重试→不触发熔断",
    },
    {
        "id": 13, "priority": "chain", "chain_seq": 2, "chain_group": "circuit_breaker",
        "title": "失败熔断-真正终止",
        "question": "请查询'提瓦特量子力学'和'璃月核电站'这两个概念",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": [],  # 第二次失败后应停止
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 2,
            "hard_check": lambda r: r.get("stopped_after_failures", 0) <= 2,
            "hard_check_desc": "连续2次未找到后应停止",
        },
        "note": "失败熔断生效：2次未找到后立即停止",
    },
    {
        "id": 14, "priority": "chain", "chain_seq": 3, "chain_group": "circuit_breaker",
        "title": "熔断优先级-并行vs单文本",
        "question": "对比《拾枝者·戴因斯雷布》和《卡利贝尔》的叙事结构",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["对比分析"],
            "must_tool": ["load_quest_content"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
            "min_parallel_loads": 2,
        },
        "note": "并行加载 > 单文本熔断：同轮并行发出2个load_*",
    },
    # ─── Normal: 其余测试 ───
    {
        "id": 1, "priority": "normal", "title": "搜索豁免-正例",
        "question": "提瓦特大陆的'岩王帝君'是指谁？",
        "conversation_summary": "", "turn_number": 0,
        "alias_notes": "[别名标注] 岩王帝君 -> 钟离（已确认）",
        "checks": {
            "intent": ["身份查询"],
            "tool_count": 0,
            "forbid_tool": [],
            "must_signal": ["别名标注已给出答案"],
            "max_tool_count": 0,
        },
        "note": "别名标注+纯身份查询→豁免搜索",
    },
    {
        "id": 2, "priority": "normal", "title": "搜索豁免-反例",
        "question": "钟离的性格怎么样？他平时喜欢做什么？",
        "conversation_summary": "", "turn_number": 0,
        "alias_notes": "[别名标注] 岩王帝君 -> 钟离（已确认）",
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 5,
        },
        "note": "身份确认≠内容豁免：必须调用hybrid_search",
    },
    {
        "id": 5, "priority": "normal", "title": "P2降级-深度不足",
        "question": "为什么须弥的雨林会枯萎？请说明根本原因和背后的历史事件",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
        },
        "note": "P2语义判断：追问原因/历史时需hybrid_search深层搜索",
    },
    {
        "id": 6, "priority": "normal", "title": "P2降级-深度充足",
        "question": "蒙德城的风神是谁？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["身份查询"],
            "must_tool": [],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 2,
        },
        "note": "简单身份查询，不追加无意义二次搜索",
    },
    {
        "id": 7, "priority": "normal", "title": "P3对比-任务全文",
        "question": "《我们终将重逢》和《回响》中，戴因斯雷布对深渊的态度有何转变？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["对比分析"],
            "must_tool": ["load_quest_content"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
            "min_parallel_loads": 2,
        },
        "note": "隐含对比+强制全文：并行调用2个load_quest_content",
    },
    {
        "id": 9, "priority": "normal", "title": "前提待验证-真矛盾",
        "question": "为什么芙宁娜在枫丹审判中没有被处死？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": ["前提待验证"],
            "max_tool_count": 4,
        },
        "note": "前提错误+信号传递：标注'前提待验证'",
    },
    {
        "id": 10, "priority": "normal", "title": "前提待验证-伪矛盾",
        "question": "为什么钟离在魔神战争中没有死？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": ["前提待验证"],
            "max_tool_count": 4,
        },
        "note": "前提成立但表述易误导：标注'前提待验证'，搜索后确认",
    },
    {
        "id": 15, "priority": "normal", "title": "上下文指代消解",
        "question": "她小时候的经历是怎样的？",
        "conversation_summary": "用户之前询问了'仆人'阿蕾奇诺的背景信息。阿蕾奇诺是愚人众第四席执行官。",
        "turn_number": 1,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 5,
        },
        "note": "跨轮指代：将'她'解析为'阿蕾奇诺'",
    },
    {
        "id": 16, "priority": "normal", "title": "上下文无关切换",
        "question": "万国诸卷拾遗里关于稻妻的内容有哪些？",
        "conversation_summary": "用户之前询问了钟离的传说任务剧情。",
        "turn_number": 1,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": ["load_book_content"],
            "must_signal": [],
            "max_tool_count": 3,
        },
        "note": "话题切换：按新问题处理；万国诸卷拾遗非书籍，用hybrid_search",
    },
    {
        "id": 17, "priority": "normal", "title": "元数据查询",
        "question": "《竹林月夜》是哪一版更新的？作者是谁？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["元数据查询"],
            "must_tool": ["get_book_metadata"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 2,
        },
        "note": "新增工具选择：书籍元数据→get_book_metadata",
    },
    {
        "id": 18, "priority": "normal", "title": "引述验证-精确匹配",
        "question": "原文检索'欲买桂花同载酒，终不似，少年游'在游戏中的出处",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
        },
        "note": "引述验证：hybrid_search→命中后load_*确认",
    },
    {
        "id": 19, "priority": "normal", "title": "模糊地带-倾向搜索",
        "question": "甘雨的工作日常是什么样的？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": [],
            "must_tool": ["hybrid_search"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 4,
        },
        "note": "'以搜索为默认'：不确定时必须调用工具",
    },
    {
        "id": 20, "priority": "normal", "title": "格式合规性",
        "question": "胡桃传说任务是哪个？",
        "conversation_summary": "", "turn_number": 0,
        "checks": {
            "intent": ["任务名查询"],
            "must_tool": ["query_quest"],
            "forbid_tool": [],
            "must_signal": [],
            "max_tool_count": 2,
            "format_checks": [
                ("用户问题回显", "必须逐字复制用户原始提问"),
                ("用户意图", "必须有意图描述"),
                ("工具决策", "必须有工具、参数、原因"),
            ],
        },
        "note": "强制输出格式：【执行报告】三字段完整",
    },
]


# ====== 分析函数 ======
def analyze_plan_output(execution_plan: str, tool_calls: list, test: dict) -> dict:
    """分析 Plan Agent 输出，与预期行为对比，返回结果字典。"""

    result = {
        "id": test["id"],
        "title": test["title"],
        "passed": True,
        "failures": [],
        "failure_types": [],
        "warnings": [],
        "execution_plan": execution_plan,
        "tool_names": [tc.get("name", "?") for tc in tool_calls],
        "tool_count": len(tool_calls),
        "triggered_npc_protocol": False,
        "stopped_after_failures": 0,
    }

    checks = test.get("checks", {})

    # ---- 格式检查 ----
    if "format_checks" in checks:
        for field_name, desc in checks["format_checks"]:
            if field_name not in execution_plan:
                result["passed"] = False
                result["failures"].append(f"缺少字段: {field_name} ({desc})")
                result["failure_types"].append("A")

    # ---- 工具数量检查 ----
    max_tc = checks.get("max_tool_count")
    if max_tc is not None and len(tool_calls) > max_tc:
        result["passed"] = False
        result["failures"].append(
            f"工具调用过多: {len(tool_calls)} > {max_tc} (调用了: {result['tool_names']})"
        )
        result["failure_types"].append("B")

    # 零工具豁免
    if checks.get("tool_count") == 0 and len(tool_calls) > 0:
        result["passed"] = False
        result["failures"].append(
            f"应豁免搜索(0工具)，但调用了: {result['tool_names']}"
        )
        result["failure_types"].append("A")

    # ---- 必须调用的工具 ----
    must_tools = checks.get("must_tool", [])
    for mt in must_tools:
        if mt not in result["tool_names"]:
            result["passed"] = False
            result["failures"].append(
                f"缺少必须工具: {mt} (实际调用了: {result['tool_names']})"
            )
            result["failure_types"].append("A")

    # ---- 禁止调用的工具 ----
    forbid_tools = checks.get("forbid_tool", [])
    for ft in forbid_tools:
        if ft in result["tool_names"]:
            result["passed"] = False
            result["failures"].append(
                f"不应调用工具: {ft} (实际调用了: {result['tool_names']})"
            )
            result["failure_types"].append("A")

    # ---- 信号检查 ----
    must_signals = checks.get("must_signal", [])
    for ms in must_signals:
        if ms not in execution_plan:
            result["passed"] = False
            result["failures"].append(
                f"缺少信号标记: {ms}"
            )
            result["failure_types"].append("A")

    # ---- 意图检查 ----
    expected_intents = checks.get("intent", [])
    if expected_intents:
        intent_match = any(ei in execution_plan for ei in expected_intents)
        if not intent_match:
            result["warnings"].append(
                f"意图未匹配预期: 期望含{expected_intents}之一"
            )

    # ---- 并行加载检查 ----
    min_parallel = checks.get("min_parallel_loads", 0)
    if min_parallel > 1:
        load_tool_count = sum(1 for t in result["tool_names"] if t.startswith("load_"))
        if load_tool_count < min_parallel:
            result["passed"] = False
            result["failures"].append(
                f"并行加载不足: load_*工具{load_tool_count}个 < {min_parallel}个"
            )
            result["failure_types"].append("A")

    # ---- NPC 协议触发检测 ----
    npc_keywords = ["query_character", "NPC成长", "第1轮", "第2轮", "第3轮"]
    result["triggered_npc_protocol"] = any(
        kw in execution_plan for kw in npc_keywords
    )

    # ---- 硬性检查（自定义 lambda）----
    hard_check = checks.get("hard_check")
    if hard_check and not hard_check(result):
        result["passed"] = False
        result["failures"].append(checks.get("hard_check_desc", "硬性检查失败"))
        result["failure_types"].append("A")

    return result


def run_single_test(tc: dict, plan_agent_fn) -> dict:
    """执行单个测试用例，调用 plan_agent 并分析输出。"""

    # 构造最小 state
    state = {
        "user_query": tc["question"],
        "messages": [],
        "intent_labels": [],
        "intent_expanded": False,
        "iteration": 0,
        "alias_notes": tc.get("alias_notes", ""),
        "conversation_summary": tc.get("conversation_summary", ""),
        "conversation_history": [],
        "rewritten_query": None,
        "final_response": None,
        "consecutive_not_found": 0,
        "format_retry": 0,
        "plan_retry": 0,
        "execution_plan": None,
        "run_id": None,
    }

    # 模拟多轮对话
    turn_num = tc.get("turn_number", 0)
    if turn_num > 0 and tc.get("conversation_summary"):
        state["conversation_history"] = [{}] * turn_num
        # 添加摘要消息到 messages
        from langchain_core.messages import SystemMessage
        state["messages"] = [SystemMessage(
            content=f"[对话摘要] {tc['conversation_summary']}"
        )]

    try:
        result_dict = plan_agent_fn(state)
        messages = result_dict.get("messages", [])
        execution_plan = result_dict.get("execution_plan", "")

        if not messages:
            # 无工具调用的场景（如豁免搜索）
            return analyze_plan_output(execution_plan, [], tc)

        last_msg = messages[-1]
        tool_calls = getattr(last_msg, "tool_calls", []) if hasattr(last_msg, "tool_calls") else []
        return analyze_plan_output(execution_plan, tool_calls, tc)

    except Exception as e:
        return {
            "id": tc["id"], "title": tc["title"],
            "passed": False,
            "failures": [f"执行异常: {str(e)}"],
            "failure_types": ["C"],
            "warnings": [],
            "execution_plan": "",
            "tool_names": [],
            "tool_count": 0,
        }


def print_result(r: dict, idx: int, total: int):
    """格式化打印单个测试结果。"""
    status = "PASS" if r["passed"] else "FAIL"
    icon = "OK" if r["passed"] else "FAIL"
    print(f"[{icon}] [#{idx}/{total}] {r['title']}")
    print(f"  Q: {r.get('_question', '')}")
    if r.get("tool_names"):
        print(f"  工具: {r['tool_names']} ({r.get('tool_count', 0)}个)")
    elif r.get("tool_count", 0) == 0:
        print(f"  工具: (无 -- 豁免搜索)")
    if r["execution_plan"]:
        # 只打印前 3 行
        lines = r["execution_plan"].strip().split("\n")[:4]
        for line in lines:
            if line.strip():
                print(f"  |> {line.strip()[:100]}")
    if r["failures"]:
        for f in r["failures"]:
            print(f"  ! 失败: {f}")
    if r["warnings"]:
        for w in r["warnings"]:
            print(f"  ~ 警告: {w}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="跑稳定版旧 Prompt 对照测试")
    args = parser.parse_args()

    print("=" * 70)
    if args.baseline:
        print("Plan Prompt V5 瘦身版 -- 对照测试 (旧版 V4 Prompt)")
    else:
        print("Plan Prompt V5 瘦身版 -- 20 题行为验证测试")
    print("=" * 70)

    # ---- 准备环境 ----
    print("[准备] 加载 Agent 模块 ...")

    # 对于 baseline 模式，交换 prompt 文件
    if args.baseline:
        # 备份新 prompt → 只用旧 prompt 跑一次
        print("[准备] 切换到旧版 V4 Prompt ...")
        # 先读取稳定版的 prompt
        stable_prompt_path = os.path.join(
            os.path.dirname(__file__), "..",
            "CASE-原神剧情助手-稳定版", "prompts", "system", "agent_system_v4_plan.txt"
        )
        if os.path.exists(stable_prompt_path):
            with open(stable_prompt_path, "r", encoding="utf-8") as f:
                old_prompt = f.read()
            # 保存当前 prompt
            current_prompt_path = os.path.join(PROMPTS_DIR, "agent_system_v4_plan.txt")
            with open(current_prompt_path, "r", encoding="utf-8") as f:
                new_prompt = f.read()
            # 写入旧 prompt
            with open(current_prompt_path, "w", encoding="utf-8") as f:
                f.write(old_prompt)
            print(f"  -> 已加载稳定版 Prompt ({len(old_prompt)} 字符)")
            restore_prompt = new_prompt
        else:
            print("  ! 稳定版 Prompt 文件不存在，取消 baseline 测试")
            return
    else:
        restore_prompt = None

    # 导入 Agent 模块（需要在 prompt 切换之后）
    import genshin_story_agent as agent
    plan_fn = agent.plan_agent

    # 清除路由缓存
    try:
        import intent_router
        intent_router._route_cache.clear()
    except Exception:
        pass

    # ---- 排序题目 ----
    # critical > paired > chain > normal
    priority_order = {"critical": 0, "paired": 1, "chain": 2, "normal": 3}
    sorted_cases = sorted(TEST_CASES, key=lambda t: (priority_order.get(t["priority"], 9), t["id"]))

    # ---- 逐题执行 ----
    all_results = []
    passed_count = 0
    failed_count = 0
    failure_by_type = {"A": 0, "B": 0, "C": 0}

    total = len(sorted_cases)
    for idx, tc in enumerate(sorted_cases):
        print(f"\n--- [#{idx+1}/{total}] ID={tc['id']} [{tc['priority']}] {tc['title']} ---")
        print(f"  Q: {tc['question']}")
        if tc.get("note"):
            print(f"  NOTE: {tc['note']}")

        result = run_single_test(tc, plan_fn)
        result["_question"] = tc["question"]
        result["_note"] = tc.get("note", "")
        result["_priority"] = tc["priority"]

        all_results.append(result)

        if result["passed"]:
            passed_count += 1
        else:
            failed_count += 1
            for ft in result["failure_types"]:
                failure_by_type[ft] = failure_by_type.get(ft, 0) + 1

        print_result(result, idx + 1, total)

        # 小停顿避免 API 限流
        time.sleep(1)

    # ---- 汇总 ----
    print("\n" + "=" * 70)
    print("测试汇总")
    print("=" * 70)
    print(f"通过: {passed_count}/{total}  |  失败: {failed_count}/{total}")
    print(f"通过率: {passed_count / total * 100:.0f}%")
    print()

    # 分组统计
    for prio in ["critical", "paired", "chain", "normal"]:
        prio_results = [r for r in all_results if r["_priority"] == prio]
        prio_passed = sum(1 for r in prio_results if r["passed"])
        print(f"  {prio:10s}: {prio_passed}/{len(prio_results)}")

    print()

    # 失败类型统计
    if failure_by_type:
        print("失败类型分布:")
        for ft in ["A", "B", "C"]:
            count = failure_by_type.get(ft, 0)
            bar = "#" * count if count > 0 else ""
            desc = {"A": "规则理解错误(需改Prompt)", "B": "执行不稳定(需加示例)", "C": "工具异常(非Prompt问题)"}
            print(f"  {ft}类 ({desc[ft]}): {count} {bar}")

    # 失败详单
    failed_results = [r for r in all_results if not r["passed"]]
    if failed_results:
        print(f"\n失败详单 ({len(failed_results)} 题):")
        for r in failed_results:
            print(f"  [#{r['id']}] {r['title']}")
            for f in r["failures"]:
                print(f"      - {f}")

    # ---- 保存结果 ----
    save_path = BASELINE_FILE if args.baseline else RESULT_FILE
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {save_path}")

    # ---- 恢复 Prompt ----
    if restore_prompt:
        print("[清理] 恢复新版 Prompt ...")
        with open(current_prompt_path, "w", encoding="utf-8") as f:
            f.write(restore_prompt)
        print("  -> Prompt 已恢复")

    # ---- A/B 对比提示 ----
    if not args.baseline:
        baseline_exists = os.path.exists(BASELINE_FILE)
        if baseline_exists:
            print("\n[A/B对比] baseline 结果已存在，运行 diff 分析:")
            with open(RESULT_FILE, "r", encoding="utf-8") as f:
                v5 = json.load(f)
            with open(BASELINE_FILE, "r", encoding="utf-8") as f:
                baseline = json.load(f)
            v5_map = {r["id"]: r for r in v5}
            bl_map = {r["id"]: r for r in baseline}

            print(f"\n{'ID':<5} {'题目':<25} {'旧版':<6} {'新版':<6} {'变化':>8}")
            print("-" * 55)
            for tc in sorted_cases:
                tid = tc["id"]
                v5r = v5_map.get(tid, {})
                blr = bl_map.get(tid, {})
                v5_passed = "PASS" if v5r.get("passed") else "FAIL"
                bl_passed = "PASS" if blr.get("passed") else "FAIL"
                v5_tc = v5r.get("tool_count", "?")
                bl_tc = blr.get("tool_count", "?")
                if bl_passed != v5_passed:
                    change = f"! {bl_passed}->{v5_passed}"
                elif v5_tc != bl_tc and isinstance(v5_tc, int) and isinstance(bl_tc, int):
                    change = f"工具 {bl_tc}->{v5_tc}"
                    if v5_tc < bl_tc:
                        change += " (减少)"
                    elif v5_tc > bl_tc:
                        change += " (增加)"
                else:
                    change = "-"
                print(f"{tid:<5} {tc['title']:<25} {bl_passed:<6} {v5_passed:<6} {change:>8}")
        else:
            print("\n[A/B对比] 尚未生成 baseline。运行以下命令生成:")
            print("  python test_plan_prompt_v5.py --baseline")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工具路由重构 - 20 题高压测试脚本

测试范围：
  攻击1: 书名/任务名混淆    (题 1-3)
  攻击2: 隐性行为词          (题 4-6)
  攻击3: 跨层追问            (题 7-8)
  攻击4: 多轮指代漂移        (题 9-10, 模拟历史)
  攻击5: 伪百科              (题 11-12)
  攻击6: 组合拳              (题 13-14)
  攻击7: 兜底压力            (题 15-16)
  回归测试 (正常场景)        (题 17-20)

用法: python test_router_20.py
"""

import sys
import os

# 确保从本目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from intent_router import route_intent, TOOL_GROUPS, get_tools_for_intent, get_last_raw_response
from intent_router import _anchor_entities, _format_grounding
import importlib
import intent_router

# 清空路由缓存（避免上次运行结果干扰）
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


def fmt_labels(labels):
    """把 ['B', 'D'] 翻译成中文"""
    return ", ".join(f"{l}({LABEL_CN.get(l, l)})" for l in labels)

# 尝试加载工具映射
try:
    import genshin_story_agent
    _actual_tools = getattr(genshin_story_agent, '_tools_by_name', {})
except Exception:
    _actual_tools = {}

# ====== 20 道测试题 ======
TEST_CASES = [
    # ─── 攻击1: 书名/任务名混淆 ───
    {
        "id": 1,
        "attack": "攻击1-书名伪装",
        "question": "《白之公主与六侏儒》讲了什么？",
        "expected_labels": ["E"],  # Stage 0 锚定为书籍，E 足够
        "must_have": "E",
        "must_not_have": None,
        "note": "Stage 0 锚定→E。如需任务内容，Plan Agent 有 P1 降级",
    },
    {
        # Stage 0 锚定为书籍，E 足够加载全文
        "id": 2,
        "attack": "攻击1-中文书名",
        "question": "浮槃歌卷讲了什么",
        "expected_labels": ["E"],
        "must_have": "E",
        "must_not_have": None,
        "note": "Stage 0 锚定→E(书籍)，无需 D",
    },
    {
        "id": 3,
        "attack": "攻击1-真任务名",
        "question": "捕风讲了什么",
        "expected_labels": ["D"],
        "must_have": "D",
        "must_not_have": "E",  # 明确是任务名，不应归为 E
    },
    # ─── 攻击2: 隐性行为词 ───
    {
        "id": 4,
        "attack": "攻击2-隐性行为词",
        "question": "阿佩普的绿洲守望者是怎么来的？",
        "expected_labels": ["C2", "D"],  # 怪物/NPC + "怎么来的"
        "must_have": "D",
        "must_not_have": None,
    },
    {
        "id": 5,
        "attack": "攻击2-身世询问",
        "question": "蒂蕾娜的身世是什么？",
        "expected_labels": ["B", "D"],
        "must_have": "D",
        "must_not_have": None,
    },
    {
        "id": 6,
        "attack": "攻击2-关系询问",
        "question": "钟离和若陀龙王是什么关系？",
        "expected_labels": ["B", "D"],  # 问角色+关系=需要剧情
        "must_have": "D",
        "must_not_have": None,
    },
    # ─── 攻击3: 跨层追问 ───
    {
        "id": 7,
        "attack": "攻击3-武器背景人物",
        "question": "裁叶萃光的背景故事里提到的那个学者是谁？",
        "expected_labels": ["C1"],  # 主要入口是武器 → C1
        "must_have": "C1",
        "must_not_have": None,
        "note": "C1 够用（hybrid_search 常驻可补位），后续需 Plan Agent 用 hybrid_search 追查",
    },
    {
        "id": 8,
        "attack": "攻击3-圣遗物故事角色",
        "question": "绝缘之旗印故事里提到的那个将军是谁？",
        "expected_labels": ["C1"],
        "must_have": "C1",
        "must_not_have": None,
        "note": "同上，C1 返回后有 hybrid_search 补位",
    },
    # ─── 攻击4: 多轮指代漂移 ───
    {
        "id": 9,
        "attack": "攻击4-多轮指代1",
        "question": "她是怎么死的？",
        "conversation_summary": "用户询问钟离的传说任务《盐花》讲了什么，助手介绍该任务讲述了盐之魔神赫乌莉亚的故事，她是一位仁慈的魔神，最终被自己的子民用剑刺杀，化为盐晶。",
        "turn_number": 2,
        "expected_labels": ["D"],
        "must_have": "D",
        "must_not_have": None,
        "note": "她→赫乌莉亚。纯代词无法被 Stage 0 锚定，但摘要已指明盐花任务。D 加载全文即够用",
    },
    {
        "id": 10,
        "attack": "攻击4-多轮指代2",
        "question": "钟离在任务中怎么评价这件事？",
        "conversation_summary": "用户追问赫乌莉亚的死因，助手解释她是被自己的子民背叛刺杀的，因为子民认为她太过仁慈软弱，无法在魔神战争中保护他们。",
        "turn_number": 3,
        "expected_labels": ["B", "D"],
        "must_have": "D",
        "must_not_have": None,
        "note": "多轮后追问同任务中钟离的态度。摘要指代链：赫乌莉亚→盐花任务",
    },
    # ─── 攻击5: 伪百科 ───
    {
        "id": 11,
        "attack": "攻击5-动机询问",
        "question": "深渊教团为什么要毁灭七国？",
        "expected_labels": ["C2"],  # query_concept 为主
        "must_have": "C2",
        "must_not_have": None,
        "note": "C2 够用，但 Plan Agent 需用 P2 规则补 hybrid_search（动机在任务/编年史中）",
    },
    {
        "id": 12,
        "attack": "攻击5-深层原因",
        "question": "魔神战争的根本原因是什么？",
        "expected_labels": ["C2"],
        "must_have": "C2",
        "must_not_have": None,
        "note": "概念类问题，C2(query_concept)直接返回后，Plan Agent 的 P2 规则自动补 hybrid_search",
    },
    # ─── 攻击6: 组合拳 ───
    {
        "id": 13,
        "attack": "攻击6-角色多任务对比",
        "question": "对比一下《我们终将重逢》和《卡利贝尔》里戴因斯雷布对深渊的态度变化",
        "expected_labels": ["B", "D"],
        "must_have": "D",
        "must_not_have": None,
        "note": "规则7: 角色+多任务对比 → B,D。P3 强制加载两个任务全文",
    },
    {
        "id": 14,
        "attack": "攻击6-跨类型对比",
        "question": "千夜浮梦的故事和纳西妲的传说任务都提到了轮回，它们对轮回的理解有什么异同？",
        "expected_labels": ["C1", "D"],  # Stage 0 锚定: 千夜浮梦→C1, 纳西妲→B
        "must_have": "C1",               # C1 是本次修复的关键——之前被误判为 E(书籍)
        "must_not_have": "E",            # 绝不能出现 E
        "note": "Stage 0 锚定千夜浮梦→C1, 纳西妲→B。P3 要求加载全文",
    },
    # ─── 攻击7: 兜底压力 ───
    {
        "id": 15,
        "attack": "攻击7-全局词汇",
        "question": "帮我汇总一下'命运的织机'的所有一手资料，包括任务原文、书籍段落和角色语音",
        "expected_labels": ["ALL"],
        "must_have": "ALL",
        "must_not_have": None,
        "note": "规则9: 全局词汇 → ALL，直接全量工具",
    },
    {
        "id": 16,
        "attack": "攻击7-全貌请求",
        "question": "关于坎瑞亚的全部信息",
        "expected_labels": ["ALL"],
        "must_have": "ALL",
        "must_not_have": None,
        "note": "\"全部信息\" → ALL",
    },
    # ─── 回归测试 (正常场景) ───
    {
        "id": 17,
        "attack": "回归-角色查询",
        "question": "胡桃是谁？",
        "expected_labels": ["B"],
        "must_have": "B",
        "must_not_have": None,
    },
    {
        "id": 18,
        "attack": "回归-任务梗概",
        "question": "《巨龙与自由的变奏》讲了什么？",
        "expected_labels": ["D"],
        "must_have": "D",
        "must_not_have": None,
    },
    {
        "id": 19,
        "attack": "回归-列表查询",
        "question": "有哪些火元素角色？",
        "expected_labels": ["B"],
        "must_have": "B",
        "must_not_have": None,
    },
    {
        "id": 20,
        "attack": "回归-溯源查询",
        "question": "天理的维系者第一次出场是在哪？",
        "expected_labels": ["F"],
        "must_have": "F",
        "must_not_have": None,
    },
]


def validate_expected(labels, expected):
    """宽松校验：只要 expected 中的每个标签都在 labels 中即可（允许多标签）"""
    return all(l in labels for l in expected)


def run_all_tests():
    """执行全部 20 题测试"""
    passed = 0
    failed = 0
    details = []

    print("=" * 70)
    print("工具路由重构 - 20 题高压测试")
    print("=" * 70)

    for tc in TEST_CASES:
        tid = tc["id"]
        question = tc["question"]
        attack = tc["attack"]
        conv_summary = tc.get("conversation_summary", "")
        turn_number = tc.get("turn_number", 0)
        expected = tc["expected_labels"]
        must_have = tc.get("must_have")
        must_not_have = tc.get("must_not_have")
        note = tc.get("note", "")

        # 调用路由器
        try:
            labels = route_intent(
                user_query=question,
                conversation_summary=conv_summary,
                turn_number=turn_number,
            )
            # 获取 Stage 0 锚定结果
            candidate_labels, anchored, _pnote = _anchor_entities(question)
        except Exception as e:
            labels = ["ERROR"]
            print(f"\n[#{tid:02d}] {attack}")
            print(f"  Q: {question}")
            print(f"  *** 路由器调用失败: {e}")
            failed += 1
            details.append({
                "id": tid, "attack": attack, "question": question,
                "expected": expected, "actual": ["ERROR"],
                "result": "FAIL (调用失败)"
            })
            continue

        # 判断结果
        passes = validate_expected(labels, expected)
        if must_have and must_have not in labels:
            passes = False
        if must_not_have and must_not_have in labels:
            passes = False

        status = "PASS" if passes else "FAIL"
        if passes:
            passed += 1
        else:
            failed += 1

        # 工具数量
        tool_count = 0
        if "ALL" in labels:
            tool_count = 24
        else:
            grp_tools = set()
            for lbl in labels:
                if lbl in TOOL_GROUPS:
                    grp_tools.update(TOOL_GROUPS[lbl])
            grp_tools.add("hybrid_search")
            tool_count = len(grp_tools)

        # 打印结果
        flag = "[OK]" if passes else "[XX]"
        print(f"\n{flag} [#{tid:02d}] {attack} | 工具数: {tool_count}")
        print(f"  Q: {question}")
        if conv_summary:
            print(f"  摘要: {conv_summary[:80]}...")
        if anchored:
            parts = []
            for n, labels in anchored:
                label_str = "/".join(LABEL_CN.get(l, l) for l in labels)
                parts.append(f"{n}({label_str})")
            print(f"  锚定: {', '.join(parts)}")
        print(f"  期望: {fmt_labels(expected)}  →  实际: {fmt_labels(labels)}")
        if not passes:
            raw = get_last_raw_response()
            print(f"  LLM原始回复: {raw}")
        if note:
            print(f"  说明: {note}")

        details.append({
            "id": tid, "attack": attack, "question": question,
            "expected": expected, "actual": labels,
            "result": status,
        })

    # ====== 汇总 ======
    print("\n" + "=" * 70)
    print(f"测试完成: 通过 {passed}/{len(TEST_CASES)}, 失败 {failed}/{len(TEST_CASES)}")
    print("=" * 70)

    if failed > 0:
        print("\n--- 失败明细 ---")
        for d in details:
            if d["result"] != "PASS":
                print(f"  [#{d['id']:02d}] {d['attack']}")
                print(f"    Q: {d['question']}")
                print(f"    期望: {fmt_labels(d['expected'])}  实际: {fmt_labels(d['actual'])}")

    # ====== 分组统计 ======
    print("\n--- 分组准确率 ---")
    groups = {}
    for tc in TEST_CASES:
        attack = tc["attack"].split("-")[0] if "-" in tc["attack"] else tc["attack"]
        groups.setdefault(attack, {"pass": 0, "total": 0})
    for d in details:
        attack = d["attack"].split("-")[0] if "-" in d["attack"] else d["attack"]
        groups[attack]["total"] += 1
        if d["result"] == "PASS":
            groups[attack]["pass"] += 1

    for grp_name, stats in groups.items():
        rate = stats["pass"] / stats["total"] * 100 if stats["total"] > 0 else 0
        bar = "#" * int(rate / 5) + "-" * (20 - int(rate / 5))
        print(f"  {grp_name:6s} [{bar}] {stats['pass']}/{stats['total']} ({rate:.0f}%)")

    # ====== 工具收敛度 ======
    print("\n--- 工具收敛度 ---")
    total_tools = 0
    min_tools = 999
    max_tools = 0
    all_label_counts = {}
    for d in details:
        actual = d["actual"]
        if "ALL" in actual:
            count = 24
        else:
            grp_tools = set()
            for lbl in actual:
                if lbl in TOOL_GROUPS:
                    grp_tools.update(TOOL_GROUPS[lbl])
            grp_tools.add("hybrid_search")
            count = len(grp_tools)
        total_tools += count
        min_tools = min(min_tools, count)
        max_tools = max(max_tools, count)
        for lbl in actual:
            all_label_counts[lbl] = all_label_counts.get(lbl, 0) + 1

    avg_tools = total_tools / len(TEST_CASES)
    print(f"  平均每次暴露: {avg_tools:.1f} 个工具")
    print(f"  最少: {min_tools} | 最多: {max_tools}")
    cn_dist = {f"{k}({LABEL_CN.get(k, k)})": v for k, v in sorted(all_label_counts.items(), key=lambda x: -x[1])}
    print(f"  标签分布: {cn_dist}")

    # 对比原方案（固定 24 个）
    reduction = (1 - avg_tools / 24) * 100
    print(f"  工具减少: 24 → {avg_tools:.1f} (减少 {reduction:.0f}%)")

    return passed, failed


def test_entity_chain():
    """验证多轮指代链的摘要质量（基于真实数据：钟离传说任务《盐花》）"""
    print("\n" + "=" * 70)
    print("多轮指代链 - 摘要质量测试")
    print("=" * 70)

    # 模拟三轮对话（真实数据链）
    question_1 = "钟离的传说任务盐花讲了什么？"
    question_2 = "她是怎么死的？"
    question_3 = "钟离怎么评价这件事？"

    # 模拟自动生成的摘要
    import genshin_story_agent
    summary_func = genshin_story_agent._summarize_conversation

    turns = [
        {"user": question_1,
         "assistant": "钟离传说任务【古闻之章·盐花】讲述了盐之魔神赫乌莉亚的故事，她是一位仁慈的魔神，最终被自己的子民用剑刺杀，化为盐晶。"},
        {"user": question_2,
         "assistant": "赫乌莉亚是被自己的子民背叛刺杀的，因为子民认为她太过仁慈软弱，无法在魔神战争中保护他们。"},
    ]

    summary = summary_func("", turns)
    print(f"  第1-2轮摘要: {summary}")

    # 检查摘要是否保留了关键实体链
    checks = [
        ("赫乌莉亚", "是否保留了\"赫乌莉亚\""),
        ("钟离", "是否保留了\"钟离\""),
        ("盐花", "是否保留了\"盐花\""),
        ("魔神", "是否保留了\"魔神\""),
    ]
    for keyword, desc in checks:
        found = keyword in summary
        flag = "[OK]" if found else "[!!]"
        print(f"  {flag} {desc}: {'是' if found else '否（可能丢失指代链）'}")

    print(f"\n  第3轮路由: {question_3}")
    labels = route_intent(question_3, conversation_summary=summary, turn_number=3)
    print(f"  路由结果: {fmt_labels(labels)}")
    raw = get_last_raw_response()
    print(f"  LLM原始回复: {raw}")
    has_d = "D" in labels
    flag = "[OK]" if has_d else "[!!]"
    print(f"  {flag} D 组工具: {'已注入' if has_d else '缺失（无法加载盐花任务全文）'}")


if __name__ == "__main__":
    passed, failed = run_all_tests()

    # 仅展示状态，不做 exit code 判断（路由器的准确率允许非 100%）
    print(f"\n最终结果: {passed} 通过, {failed} 失败")

    # 额外：多轮指代链测试
    test_entity_chain()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最终双版本对比测试 - 8道综合题"""
import sys, os, io, warnings, json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
os.environ["LANGCHAIN_TRACING_V2"] = "false"
warnings.filterwarnings("ignore")

QUESTIONS = [
    ("Q1-多源冲突消歧", (
        '关于"水仙十字结社"的创立时间，Wiki的"组织历史"条目、'
        '"任务·未完成的喜剧"中的对话文本、以及书籍《雷穆利亚衰亡史》卷三的记载是否存在差异？'
        '若有，请列出各来源的具体说法，并依据任务文本中的直接陈述判断哪个版本为当前游戏内采用的正史。'
    )),
    ("Q2-非实体概念具象化", (
        '在枫丹地区的书籍与任务文本中，"原始胎海之水"被赋予了哪些不同的隐喻或象征意义'
        '（如"溶解""回归""审判"等）？请按隐喻类型分类，列出每种隐喻出现的具体文本名称、'
        '上下文段落、以及该隐喻所服务的叙事目的。'
    )),
    ("Q3-反事实推理验证", (
        '如果玩家在完成世界任务「大梦的醒转」前，提前击败了Boss「吞星之鲸」，'
        '任务流程中哪些原本会触发的NPC对话或过场动画将永久缺失？'
        '请基于任务配置文本与分支逻辑描述，列出这些缺失内容及其对后续剧情理解的影响。'
    )),
    ("Q4-长尾知识精准召回", (
        '在枫丹地区所有可收集的"石板铭文"中，有哪些铭文的内容提及了"雷穆利亚"'
        '但并未出现在任何主线或世界任务的直接对话中？'
        '请列出这些铭文的地理位置、完整原文、以及它们为雷穆利亚世界观补充了哪些未被任务覆盖的细节。'
    )),
    ("Q5-模糊查询意图澄清", (
        '当用户提问"枫丹那个关于画家的任务怎么做"时，Wiki中存在多个与"画家"相关的任务'
        '（如「绘染丹青」「未完成的喜剧」中的画家支线等）。'
        '请说明Agent应如何通过检索返回的候选结果特征'
        '（如任务标题关键词、触发地点、关联NPC）来区分用户真实意图，并给出正确的任务引导。'
    )),
    ("Q6-多跳因果链构建", (
        '从世界任务「古老的颜色」中获得的关键道具「奇特的零件」，'
        '其最终用途是如何通过至少三个中间环节（如NPC对话、书籍线索、环境解谜）逐步揭示的？'
        '请按顺序列出每个环节的触发条件、提供的信息增量、以及该信息如何导向下一环节。'
    )),
    ("Q7-简单清单", (
        '世界任务「未完成的喜剧」中，玩家需要与哪些NPC进行对话才能推进流程？'
        '请按对话顺序列出NPC名称。'
    )),
    ("Q8-简单统计", (
        '调查点文本"古老的铭文"在枫丹地区共出现几次？'
        '每次出现的地理位置及铭文内容摘要是什么？'
    )),
]

RESULTS = []

for qid, question in QUESTIONS:
    print(f"\n{'='*60}")
    print(f"  {qid}")
    print(f"{'='*60}")

    for version_label, agent_dir in [
        ("修改用", BASE),
        ("稳定版", os.path.join(BASE, "..", "CASE-原神剧情助手-稳定版")),
    ]:
        agent_dir = os.path.abspath(agent_dir)
        print(f"\n--- {version_label} ---")

        # 隔离每轮环境
        old_path = sys.path.copy()
        old_cwd = os.getcwd()
        sys.path.insert(0, agent_dir)
        os.chdir(agent_dir)

        # 重新加载模块（不同目录的相同模块名需要强制重新加载）
        for mod in list(sys.modules.keys()):
            if mod.startswith(("genshin_story_agent", "genshin_knowledge_base",
                               "intent_router", "character_aliases", "memory_manager",
                               "kb_vector_store", "wiki_data_tools")):
                sys.modules.pop(mod, None)

        _old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            from genshin_story_agent import create_agent_workflow
        finally:
            init_log = sys.stdout.getvalue()
            sys.stdout = _old

        print(f"  [init] {agent_dir}")
        agent = create_agent_workflow()
        start = datetime.now()

        try:
            result = agent.invoke({
                "user_query": question,
                "rewritten_query": None,
                "alias_notes": None,
                "conversation_history": [],
                "conversation_summary": "",
                "messages": [],
                "final_response": None,
                "iteration": 0,
            })
            elapsed = (datetime.now() - start).total_seconds()
            response = result.get("final_response", "NO RESPONSE")
            print(f"  ELAPSED: {elapsed:.1f}s | RESPONSE: {len(response)} chars")
            # 简要预览
            preview = response[:200].replace('\n', ' ')
            print(f"  PREVIEW: {preview}...")
        except Exception as e:
            elapsed = (datetime.now() - start).total_seconds()
            response = f"[ERROR] {e}"
            print(f"  ERROR after {elapsed:.1f}s: {e}")

        RESULTS.append({
            "qid": qid,
            "version": version_label,
            "question": question,
            "elapsed": round(elapsed, 1),
            "response_len": len(response),
            "response": response,
        })

        # 恢复环境
        sys.path = old_path
        os.chdir(old_cwd)

# ====== 输出汇总 ======
print(f"\n\n{'#'*60}")
print(f"#  最终对比测试汇总")
print(f"{'#'*60}\n")

print(f"{'题目':<20} {'修改用耗时':>10} {'修改用字数':>10} {'稳定版耗时':>10} {'稳定版字数':>10}")
print("-" * 65)
for i in range(0, len(RESULTS), 2):
    r_mod = RESULTS[i]
    r_sta = RESULTS[i+1]
    print(f"{r_mod['qid']:<20} {r_mod['elapsed']:>7.1f}s  {r_mod['response_len']:>7}字  "
          f"{r_sta['elapsed']:>7.1f}s  {r_sta['response_len']:>7}字")

# 保存详细结果
report_path = os.path.join(BASE, "_final_test_report.json")
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
print(f"\n详细结果已保存至: {report_path}")

print("\nDone.")

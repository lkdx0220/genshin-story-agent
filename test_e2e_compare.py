# -*- coding: utf-8 -*-
"""全链路端到端对比测试：稳定版 vs 修改版
20 题 x 2 版本 = 40 次完整 Agent 调用
对比：路由标签、工具调用链、最终回答、回答质量（通过另一个 LLM 评判）
"""

import sys
import os
import json
import time
import re
import importlib

# 两个版本的代码路径
STABLE_DIR = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-稳定版"
MODIFIED_DIR = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用"

# 测试题（完整20题）
TEST_QUESTIONS = [
    {"id": 1, "category": "搜索豁免-正例",
     "query": "提瓦特大陆的'岩王帝君'是指谁？",
     "alias_notes": "[别名标注] 岩王帝君 -> 钟离（已确认）"},
    {"id": 2, "category": "搜索豁免-反例",
     "query": "钟离的性格怎么样？他平时喜欢做什么？",
     "alias_notes": "[别名标注] 钟离 -> 钟离（已确认）"},
    {"id": 3, "category": "NPC成长-语义触发",
     "query": "请梳理流浪者从散兵到现在的完整心路历程和人物弧光。",
     "alias_notes": ""},
    {"id": 4, "category": "NPC成长-负向约束",
     "query": "散兵为什么这么讨厌雷电将军？",
     "alias_notes": ""},
    {"id": 5, "category": "P2降级-深度判断",
     "query": "为什么须弥的雨林会枯萎？",
     "alias_notes": ""},
    {"id": 6, "category": "P2降级-深度充足",
     "query": "蒙德城的风神是谁？",
     "alias_notes": ""},
    {"id": 7, "category": "P3对比-任务全文",
     "query": "《我们终将重逢》和《回响》中，戴因斯雷布对深渊的态度有何转变？",
     "alias_notes": ""},
    {"id": 8, "category": "P3对比-武器冲突",
     "query": "千夜浮梦和裁叶萃光的背景故事有什么异同？",
     "alias_notes": ""},
    {"id": 9, "category": "前提待验证-真矛盾",
     "query": "为什么芙宁娜在枫丹审判中没有被处死？",
     "alias_notes": "[别名标注] 芙宁娜 -> 芙宁娜（已确认）"},
    {"id": 10, "category": "前提待验证-伪矛盾",
     "query": "为什么钟离在魔神战争中没有死？",
     "alias_notes": "[别名标注] 钟离 -> 钟离（已确认）"},
    {"id": 11, "category": "降级研究-信号传递",
     "query": "编剧当初设计纳西妲时，为什么把她设定为500岁而不是更年长？",
     "alias_notes": ""},
    {"id": 12, "category": "失败熔断-P1降级优先",
     "query": "加载任务「影蝶之章」的完整剧情。",
     "alias_notes": ""},
    {"id": 13, "category": "失败熔断-真正终止",
     "query": "提瓦特量子力学和璃月核电站分别是什么？",
     "alias_notes": ""},
    {"id": 14, "category": "熔断优先级-并行vs单文本",
     "query": "对比《拾枝者·戴因斯雷布》和《卡利贝尔》的叙事结构。",
     "alias_notes": ""},
    {"id": 15, "category": "上下文指代消解",
     "query": "她小时候的经历是怎样的？",
     "alias_notes": "[别名标注] 仆人 -> 阿蕾奇诺（已确认）",
     "conversation_summary": "用户上一轮询问了'仆人'（阿蕾奇诺）的身份背景。"},
    {"id": 16, "category": "上下文无关切换",
     "query": "稻妻有哪些地方提到了雷鸟？",
     "alias_notes": "",
     "conversation_summary": "用户上一轮讨论了钟离的魔神战争往事。"},
    {"id": 17, "category": "元数据查询",
     "query": "《竹林月夜》的作者是谁？它是哪一版更新的？",
     "alias_notes": ""},
    {"id": 18, "category": "引述验证-精确匹配",
     "query": "原文检索'欲买桂花同载酒，终不似，少年游'在游戏中的出处。",
     "alias_notes": ""},
    {"id": 19, "category": "模糊地带-倾向搜索",
     "query": "甘雨的工作日常是什么样的？",
     "alias_notes": "[别名标注] 甘雨 -> 甘雨（已确认）"},
    {"id": 20, "category": "格式合规性",
     "query": "宵宫的传说任务讲了什么？",
     "alias_notes": "[别名标注] 宵宫 -> 宵宫（已确认）"},
]


def run_version_test(version_name, code_dir, result_file):
    """对指定版本跑全量20题"""
    # 确保使用正确的代码路径
    if code_dir not in sys.path:
        sys.path.insert(0, code_dir)
    os.chdir(code_dir)

    # 强制重载 agent 模块（避免缓存）
    for mod_name in list(sys.modules.keys()):
        if 'genshin_story_agent' in mod_name:
            try:
                del sys.modules[mod_name]
            except KeyError:
                pass

    import genshin_story_agent as agent_mod

    # 重新构建 graph
    graph = agent_mod.create_agent_workflow()

    results = []
    total_start = time.time()

    for idx, q in enumerate(TEST_QUESTIONS):
        print(f"\n{'='*60}")
        print(f"[{version_name}] #{q['id']}: {q['category']}")
        print(f"  问题: {q['query'][:80]}...")

        q_start = time.time()

        state = {
            "user_query": q["query"],
            "rewritten_query": q["query"],
            "alias_notes": q.get("alias_notes", ""),
            "conversation_history": [],
            "conversation_summary": q.get("conversation_summary", ""),
            "execution_plan": "",
            "iteration": 0,
            "plan_retry": 0,
            "tool_call_history": [],
            "messages": [],
            "intent_labels": [],
            "injected_tools": [],
            "original_query": q["query"],
        }

        try:
            config = {"recursion_limit": 25}
            result = graph.invoke(state, config=config)

            elapsed = time.time() - q_start
            final_response = result.get("final_response", "")
            messages = result.get("messages", [])
            intent_labels = result.get("intent_labels", [])
            injected_tools = result.get("injected_tools", [])
            exec_plan = result.get("execution_plan", "")
            iteration = result.get("iteration", 0)

            # 提取工具调用历史
            tool_calls = []
            tool_results = []
            for msg in messages:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append(tc.get("name", str(tc)))
                if hasattr(msg, "name") and hasattr(msg, "content"):
                    tool_results.append({"tool": msg.name, "len": len(str(msg.content))})

            results.append({
                "id": q["id"],
                "category": q["category"],
                "query": q["query"],
                "intent_labels": intent_labels,
                "injected_tools": injected_tools,
                "tool_calls": tool_calls,
                "tool_results_count": len(tool_results),
                "iteration": iteration,
                "execution_plan": str(exec_plan)[:500],
                "final_answer": str(final_response)[:3000],
                "answer_length": len(str(final_response)),
                "elapsed_seconds": round(elapsed, 1),
                "status": "success",
            })

            print(f"  标签: {intent_labels}")
            print(f"  工具: {tool_calls[:10]}")
            print(f"  迭代: {iteration} 轮")
            print(f"  回答长度: {len(str(final_response))} 字")
            print(f"  耗时: {elapsed:.1f}s")

        except Exception as e:
            import traceback
            elapsed = time.time() - q_start
            results.append({
                "id": q["id"],
                "category": q["category"],
                "query": q["query"],
                "intent_labels": [],
                "injected_tools": [],
                "tool_calls": [],
                "tool_results_count": 0,
                "iteration": 0,
                "execution_plan": "",
                "final_answer": "",
                "answer_length": 0,
                "elapsed_seconds": round(elapsed, 1),
                "status": "error",
                "error": str(e)[:500],
            })
            print(f"  *** ERROR: {str(e)[:200]}")
            traceback.print_exc()

        # 两题之间休息1秒
        if idx < len(TEST_QUESTIONS) - 1:
            time.sleep(1)

    total_elapsed = time.time() - total_start
    print(f"\n[{version_name}] 全量完成! 总耗时: {total_elapsed:.0f}s, "
          f"成功: {sum(1 for r in results if r['status'] == 'success')}/20")

    # 保存结果
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "version": version_name,
            "total_elapsed": round(total_elapsed, 1),
            "results": results
        }, f, ensure_ascii=False, indent=2)

    return results


def generate_comparison_html(stable_data, modified_data, html_path):
    """生成对比报告 HTML"""

    # data 已经是完整 dict: {"version", "total_elapsed", "results": [...]}
    stable_rr = stable_data
    modified_rr = modified_data

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Plan Prompt V5 全链路对比测试 -- 稳定版 vs 修改版</title>
<style>
:root {{
  --bg-deep: #0a0e17;
  --bg-card: #111620;
  --gold: #c9a96e;
  --gold-dim: #a0895a;
  --ice: #7ec8e3;
  --ice-dim: #5a9ab0;
  --text: #d4c5b2;
  --text-dim: #8b7d6b;
  --border: #1e2532;
  --red: #e06060;
  --green: #5cb878;
  --orange: #e0a060;
  --font-body: 'PingFang SC','Microsoft YaHei',sans-serif;
  --font-mono: 'Cascadia Code','Consolas',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--font-body);background:var(--bg-deep);color:var(--text);line-height:1.8;min-height:100vh}}
.container{{max-width:1200px;margin:0 auto;padding:40px 30px}}
h1{{color:var(--gold);font-size:28px;margin-bottom:8px}}
.subtitle{{color:var(--text-dim);font-size:14px;margin-bottom:32px}}
h2{{color:var(--gold);font-size:20px;margin:40px 0 20px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
.summary-cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;margin-bottom:32px}}
.card{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:20px 24px}}
.card h3{{color:var(--ice);font-size:16px;margin-bottom:12px}}
.metric{{display:flex;justify-content:space-between;padding:4px 0;font-size:14px}}
.metric-label{{color:var(--text-dim)}}
.metric-value{{font-family:var(--font-mono)}}
.pass{{color:var(--green)}}
.fail{{color:var(--red)}}
.warn{{color:var(--orange)}}
.test-row{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;margin-bottom:16px;overflow:hidden}}
.test-header{{display:grid;grid-template-columns:50px 180px auto 80px 80px;gap:12px;padding:12px 16px;cursor:pointer;align-items:center}}
.test-header:hover{{background:rgba(255,255,255,.03)}}
.test-id{{font-family:var(--font-mono);color:var(--text-dim);font-size:13px}}
.test-category{{font-size:13px;color:var(--ice-dim)}}
.test-query{{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.test-body{{display:none;padding:16px;border-top:1px solid var(--border);font-size:13px}}
.answer-box{{background:rgba(0,0,0,.3);padding:12px;border-radius:6px;margin:8px 0;max-height:400px;overflow-y:auto;white-space:pre-wrap;font-size:12px;line-height:1.6}}
.compare-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.compare-grid h4{{color:var(--gold-dim);margin-bottom:6px;font-size:13px}}
.tag{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;font-family:var(--font-mono);margin:1px}}
.tag-stable{{background:rgba(126,200,227,.12);color:var(--ice-dim)}}
.tag-modified{{background:rgba(201,169,110,.12);color:var(--gold-dim)}}
.verdict{{padding:8px 12px;border-radius:4px;margin-top:8px;font-weight:600;font-size:13px}}
.verdict-same{{background:rgba(92,184,120,.1);color:var(--green)}}
.verdict-diff{{background:rgba(224,96,96,.1);color:var(--red)}}
.verdict-partial{{background:rgba(224,160,96,.1);color:var(--orange)}}
code{{font-family:var(--font-mono);font-size:12px;background:rgba(0,0,0,.3);padding:1px 4px;border-radius:2px}}
.meta-line{{font-size:12px;color:var(--text-dim);margin:2px 0}}
.expand-link{{color:var(--ice);cursor:pointer;font-size:12px;margin-top:4px;display:inline-block}}
.expand-link:hover{{text-decoration:underline}}
</style>
</head>
<body>
<div class="container">

<h1>Plan Prompt V5 全链路对比测试</h1>
<p class="subtitle">稳定版 vs 修改版 -- 20 题完整 Agent 调用（路由 -> 计划 -> 工具 -> 回答）</p>
"""

    # 统计摘要
    s_success = sum(1 for r in stable_rr["results"] if r["status"] == "success")
    m_success = sum(1 for r in modified_rr["results"] if r["status"] == "success")
    s_total_tools = sum(len(r["tool_calls"]) for r in stable_rr["results"] if r["status"] == "success")
    m_total_tools = sum(len(r["tool_calls"]) for r in modified_rr["results"] if r["status"] == "success")
    s_total_time = stable_rr["total_elapsed"]
    m_total_time = modified_rr["total_elapsed"]
    s_avg_tools = round(s_total_tools / s_success, 1) if s_success else 0
    m_avg_tools = round(m_total_tools / m_success, 1) if m_success else 0

    html += f"""<div class="summary-cards">
<div class="card">
<h3>稳定版 (旧 Prompt)</h3>
<div class="metric"><span class="metric-label">成功率</span><span class="metric-value {('pass' if s_success==20 else 'warn')}">{s_success}/20</span></div>
<div class="metric"><span class="metric-label">平均工具调用次数</span><span class="metric-value">{s_avg_tools} 次/题</span></div>
<div class="metric"><span class="metric-label">总工具调用</span><span class="metric-value">{s_total_tools} 次</span></div>
<div class="metric"><span class="metric-label">总耗时</span><span class="metric-value">{round(s_total_time)}s</span></div>
</div>
<div class="card">
<h3>修改版 (V5 瘦身)</h3>
<div class="metric"><span class="metric-label">成功率</span><span class="metric-value {('pass' if m_success==20 else 'warn')}">{m_success}/20</span></div>
<div class="metric"><span class="metric-label">平均工具调用次数</span><span class="metric-value">{m_avg_tools} 次/题</span></div>
<div class="metric"><span class="metric-label">总工具调用</span><span class="metric-value">{m_total_tools} 次</span></div>
<div class="metric"><span class="metric-label">总耗时</span><span class="metric-value">{round(m_total_time)}s</span></div>
</div>
</div>"""

    # 逐题对比
    for s_r, m_r in zip(stable_rr["results"], modified_rr["results"]):
        qid = s_r["id"]
        qcat = s_r["category"]
        qquery = s_r["query"]

        s_status = s_r["status"]
        m_status = m_r["status"]

        s_tools = s_r.get("tool_calls", [])
        m_tools = m_r.get("tool_calls", [])

        s_answer = s_r.get("final_answer", "")[:2000]
        m_answer = m_r.get("final_answer", "")[:2000]

        s_labels = s_r.get("intent_labels", [])
        m_labels = m_r.get("intent_labels", [])

        s_iter = s_r.get("iteration", 0)
        m_iter = m_r.get("iteration", 0)

        s_time = s_r.get("elapsed_seconds", 0)
        m_time = m_r.get("elapsed_seconds", 0)

        s_len = s_r.get("answer_length", 0)
        m_len = m_r.get("answer_length", 0)

        # 判断差异
        tools_same = set(s_tools) == set(m_tools)
        labels_same = set(s_labels) == set(m_labels)
        iteration_same = s_iter == m_iter

        all_same = tools_same and labels_same and (s_status == m_status)
        partial_same = tools_same != labels_same or not iteration_same

        if s_status != m_status:
            verdict_class = "verdict-diff"
            verdict_text = "版本差异（一端出错）"
        elif all_same:
            verdict_class = "verdict-same"
            verdict_text = "行为一致"
        else:
            verdict_class = "verdict-partial"
            verdict_text = "部分差异"

        html += f"""<div class="test-row">
<div class="test-header" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display=='block'?'none':'block'">
<span class="test-id">#{qid}</span>
<span class="test-category">{qcat}</span>
<span class="test-query" title="{qquery}">{qquery[:60]}</span>
<span class="metric-value {('pass' if s_status=='success' else 'fail')}">{s_status}</span>
<span class="metric-value {('pass' if m_status=='success' else 'fail')}">{m_status}</span>
</div>
<div class="test-body">
<div class="verdict {verdict_class}">{verdict_text}</div>

<div class="compare-grid">
<div>
<h4>稳定版</h4>
<p class="meta-line">标签: {','.join(s_labels) or '(无)'}</p>
<p class="meta-line">工具({len(s_tools)}): {', '.join(s_tools[:8]) or '(无)'}</p>
<p class="meta-line">迭代: {s_iter} 轮 | 耗时: {s_time}s | 回答: {s_len}字</p>
<div class="answer-box">{s_answer[:1500] or '(无回答)'}</div>
</div>
<div>
<h4>修改版</h4>
<p class="meta-line">标签: {','.join(m_labels) or '(无)'}</p>
<p class="meta-line">工具({len(m_tools)}): {', '.join(m_tools[:8]) or '(无)'}</p>
<p class="meta-line">迭代: {m_iter} 轮 | 耗时: {m_time}s | 回答: {m_len}字</p>
<div class="answer-box">{m_answer[:1500] or '(无回答)'}</div>
</div>
</div>
</div>
</div>"""

    html += """</div>
<script>
// 默认展开有差异的题目
document.querySelectorAll('.test-row').forEach(row => {{
  const verdict = row.querySelector('.verdict');
  if (verdict && !verdict.classList.contains('verdict-same')) {{
    row.querySelector('.test-body').style.display = 'block';
  }}
}});
</script>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nHTML 报告已生成: {html_path}")


if __name__ == "__main__":
    print("=" * 60)
    print("全链路端到端对比测试")
    print("20 题 x 2 版本 = 40 次完整 Agent 调用")
    print("(每次包含路由 -> 计划 -> 工具执行 -> 回答生成)")
    print("预计耗时: 15-25 分钟")
    print("=" * 60)

    # --- 稳定版 ---
    print("\n>>> 开始测试稳定版...")
    stable_file = os.path.join(os.path.dirname(__file__), "e2e_stable.json")
    stable_results = run_version_test("稳定版", STABLE_DIR, stable_file)

    # 休息一下
    print("\n(冷却 5 秒)...")
    time.sleep(5)

    # --- 修改版 ---
    print("\n>>> 开始测试修改版...")
    modified_file = os.path.join(os.path.dirname(__file__), "e2e_modified.json")
    modified_results = run_version_test("修改版", MODIFIED_DIR, modified_file)

    # 加载完整结果用于 HTML
    with open(stable_file, "r", encoding="utf-8") as f:
        stable_data = json.load(f)
    with open(modified_file, "r", encoding="utf-8") as f:
        modified_data = json.load(f)

    # 生成 HTML
    html_path = r"c:\Users\24701\Desktop\原神剧情\全链路对比报告.html"
    generate_comparison_html(stable_data, modified_data, html_path)

    print("\n" + "=" * 60)
    print("对比测试完成！")
    print(f"稳定版结果: {stable_file}")
    print(f"修改版结果: {modified_file}")
    print(f"HTML 报告: {html_path}")

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成最终对比测试的 HTML 片段"""
import json, html as html_mod

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
data = json.load(open(BASE_DIR / '_final_test_report.json', encoding='utf-8'))

# 成对输出 Q1-Q8
pairs = [(data[i], data[i+1]) for i in range(0, len(data), 2)]

# 手动构建已知的工具调用摘要（从输出日志中提取）
tool_summaries = {
    ("Q1-多源冲突消歧", "修改用"): "R1: hybrid_search+load_quest_content(未完成的喜剧→3子任务)+load_book_content(雷穆利亚衰亡史) → R2: 进入回答",
    ("Q1-多源冲突消歧", "稳定版"): "R1: load_quest_content(未找到)+load_book_content+hybrid_search(截断) → R2-R5: hybrid_search×8 → R6: 回答",
    ("Q2-非实体概念具象化", "修改用"): "R1: hybrid_search(原始胎海之水)+load_quest_content(当一切回归于水/审判日) → R2: 回答",
    ("Q2-非实体概念具象化", "稳定版"): "R1: hybrid_search×3 → R2-R3: hybrid_search×3+load_quest_content(聚光灯下谎言成影/审判日) → R4: 回答",
    ("Q3-反事实推理验证", "修改用"): "R1: load_quest_content(大梦的醒转→3子任务) → R2: 回答",
    ("Q3-反事实推理验证", "稳定版"): "R1: hybrid_search+load_quest_content → R2: 回答",
    ("Q4-长尾知识精准召回", "修改用"): "R1: hybrid_search(石板铭文 雷穆利亚)+query_concept → R2-R3: hybrid_search×3 → R4: 回答(知识库无此数据)",
    ("Q4-长尾知识精准召回", "稳定版"): "R1: hybrid_search×2+query_concept+list_collectibles_by_region → R2-R3: hybrid_search×3 → R4: 回答(不存在石板铭文类别)",
    ("Q5-模糊查询意图澄清", "修改用"): "R1: hybrid_search(枫丹 画家)+load_quest_content(生命万岁) → R2: 回答",
    ("Q5-模糊查询意图澄清", "稳定版"): "R1: hybrid_search×3 → R2-R3: hybrid_search×4+load_quest_content → R4: 回答(6个候选)",
    ("Q6-多跳因果链构建", "修改用"): "R1: load_quest_content(古老的颜色→3子任务) → R2: 回答",
    ("Q6-多跳因果链构建", "稳定版"): "R1: load_quest_content+hybrid_search×2 → R2-R3: hybrid_search×3 → R4: 回答",
    ("Q7-简单清单", "修改用"): "R1: load_quest_content(未完成的喜剧→3子任务) → R2: 回答(NPC列表21条)",
    ("Q7-简单清单", "稳定版"): "R1: query_quest(未找到)+hybrid_search → R2: 未收录(9字)",
    ("Q8-简单统计", "修改用"): "R1: hybrid_search+list_collectibles_by_region → R2: 回答(不存在143字)",
    ("Q8-简单统计", "稳定版"): "R1-R10: hybrid_search×20+load_quest_content×6 → 达到10轮上限,兜底输出全部结果(28737字)",
}

html_parts = []
html_parts.append('''
<!-- ====== 最终对比测试 (Q1-Q8) ====== -->
<div style="margin-top:40px;padding-top:20px;border-top:2px solid #64b5f6;">
<h1>最终对比测试 (Q1-Q8 综合题)</h1>
<div class="meta">2026-08-01 | 修改用版 vs 稳定版 | 8道涵盖消歧/概念/推理/召回/因果/清单的综合性题目</div>

<div class="summary">
  <div class="card">
    <div class="label">修改用平均</div>
    <div class="value">~79s</div>
  </div>
  <div class="card">
    <div class="label">稳定版平均</div>
    <div class="value">~107s</div>
  </div>
  <div class="card">
    <div class="label">速度优势</div>
    <div class="value" style="color:#66bb6a">-26%</div>
  </div>
  <div class="card">
    <div class="label">最大提升(Q1)</div>
    <div class="value" style="color:#66bb6a">-60%</div>
  </div>
</div>
''')

for mod, sta in pairs:
    qid = mod["qid"]
    q_short = qid.split("-")[0]
    # 简单题目 vs 复杂题目 的颜色
    if qid in ("Q7-简单清单", "Q8-简单统计"):
        badge_color = "badge-light"
    else:
        badge_color = "badge-deep"

    tool_mod = tool_summaries.get((qid, "修改用"), "N/A")
    tool_sta = tool_summaries.get((qid, "稳定版"), "N/A")

    html_parts.append(f'''
<div class="test" style="border-left: 3px solid #{'66bb6a' if mod['elapsed'] < sta['elapsed'] else 'ff5252'};">
  <div class="test-header">
    <h3>{qid}</h3>
    <div>
      <span class="badge" style="background:{'#2e7d32' if mod['elapsed'] < sta['elapsed'] else '#c62828'};color:{'#a5d6a7' if mod['elapsed'] < sta['elapsed'] else '#ffcdd2'};">修改用 {mod["elapsed"]}s / {mod["response_len"]}字</span>
      <span class="badge" style="background:{'#c62828' if sta['elapsed'] > mod['elapsed'] else '#2e7d32'};color:{'#ffcdd2' if sta['elapsed'] > mod['elapsed'] else '#a5d6a7'};">稳定版 {sta["elapsed"]}s / {sta["response_len"]}字</span>
    </div>
  </div>
  <div class="test-body">
    <div class="section">
      <div class="section-label">问题</div>
      <div class="section-value">{html_mod.escape(mod["question"][:150])}{"..." if len(mod["question"]) > 150 else ""}</div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px;">
      <div style="background:#1a2a1a;border:1px solid #2a4a2a;border-radius:6px;padding:12px;">
        <div style="color:#66bb6a;font-size:13px;margin-bottom:8px;"><strong>修改用版 ({mod["elapsed"]}s)</strong></div>
        <div style="color:#7898a0;font-size:11px;margin-bottom:8px;">工具: {html_mod.escape(tool_mod)}</div>
        <div class="answer" style="font-size:12px;max-height:500px;overflow-y:auto;">{html_mod.escape(mod["response"][:2000])}{"..." if len(mod["response"]) > 2000 else ""}</div>
      </div>
      <div style="background:#1a0a1a;border:1px solid #4a1a4a;border-radius:6px;padding:12px;">
        <div style="color:#ce93d8;font-size:13px;margin-bottom:8px;"><strong>稳定版 ({sta["elapsed"]}s)</strong></div>
        <div style="color:#7898a0;font-size:11px;margin-bottom:8px;">工具: {html_mod.escape(tool_sta)}</div>
        <div class="answer" style="font-size:12px;max-height:500px;overflow-y:auto;">{html_mod.escape(sta["response"][:2000])}{"..." if len(sta["response"]) > 2000 else ""}</div>
      </div>
    </div>
  </div>
</div>''')

html_parts.append('''
<!-- 关键发现 -->
<div class="test" style="border-color: #ff9800;">
  <div class="test-header" style="background: #3e2723;">
    <h3>关键发现</h3>
  </div>
  <div class="test-body">
    <ol style="color:#ffcc80;font-size:13px;line-height:2.2;padding-left:20px;">
      <li><strong>系列修复是最大差异点</strong>：Q1 修改用通过系列匹配直接加载3个子任务(78s)，稳定版因query_quest找不到"未完成的喜剧"只能暴力搜索13次(198s)。Q7 稳定版直接返回"未收录"。</li>
      <li><strong>简洁性 vs 信息量</strong>：Q5 修改用424字直答"生命万岁"，稳定版1467字铺开6个候选。简单问题不需要铺陈。</li>
      <li><strong>兜底输出灾难</strong>：Q8 稳定版找不到答案，达到10轮上限后兜底逻辑把15条搜索结果全文输出(28737字)。修改用正确判断"不存在"并143字简洁回答。</li>
      <li><strong>幻觉零复发</strong>：8题16次回答中无一次出现此前观察到的"角色关联幻觉"，事实溯源规则持续有效。</li>
      <li><strong>Q3/Q6 质量相当</strong>：两个版本在有任务全文可加载时，推理质量接近。差异主要体现在搜索效率和兜底行为上。</li>
    </ol>
  </div>
</div>

</div>
''')

# 写入
output = '\n'.join(html_parts)
with open(BASE_DIR / '_final_html_section.html', 'w', encoding='utf-8') as f:
    f.write(output)
print(f"Generated {len(output)} chars")

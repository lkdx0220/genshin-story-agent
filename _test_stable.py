#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""稳定版 vs 修改用 对比测试 - 极端复杂题"""
import sys, os, io, warnings
from datetime import datetime

STABLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CASE-原神剧情助手-稳定版")
os.chdir(STABLE_DIR)
sys.path.insert(0, STABLE_DIR)
os.environ["LANGCHAIN_TRACING_V2"] = "false"
warnings.filterwarnings("ignore")

_old_stdout = sys.stdout
sys.stdout = io.StringIO()
from genshin_story_agent import create_agent_workflow
sys.stdout = _old_stdout

COMPLEX_QUESTION = (
    '在4.0到5.0这一整年的枫丹版本周期里，有哪些原本只是活动剧情里的背景板NPC，'
    '后来不仅进了常驻玩法，还在后续的主线或间章中揭示了他们与"水仙十字结社"直接或间接关联？'
    '请列出他们的名字、身份转变的关键节点（精确到具体任务/活动名称），'
    '并说明他们在进入常驻玩法后，语音或故事文本中是否保留了早期活动里对这个势力的态度。'
)

print("=" * 60)
print("  稳定版测试: 枫丹NPC与水仙十字结社")
print("=" * 60)

agent = create_agent_workflow()
start = datetime.now()

try:
    result = agent.invoke({
        "user_query": COMPLEX_QUESTION,
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
    print(f"\n=== ELAPSED: {elapsed:.1f}s ===")
    print(f"=== RESPONSE ({len(response)} chars) ===")
    print(response)
    print(f"=== END ===")
except Exception as e:
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n[ERROR after {elapsed:.1f}s] {e}")
    import traceback
    traceback.print_exc()

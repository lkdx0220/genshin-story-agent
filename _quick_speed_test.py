#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速延迟测试"""
import sys, os, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import warnings
warnings.filterwarnings("ignore")

import io
_old_stdout = sys.stdout
sys.stdout = io.StringIO()
from genshin_story_agent import create_agent_workflow
sys.stdout = _old_stdout
print("Agent loaded, testing...\n")

COMPLEX_QUESTION = (
    '在4.0到5.0这一整年的枫丹版本周期里，有哪些原本只是活动剧情里的背景板NPC，'
    '后来不仅进了常驻玩法，还在后续的主线或间章中揭示了他们与"水仙十字结社"直接或间接关联？'
    '请列出他们的名字、身份转变的关键节点（精确到具体任务/活动名称），'
    '并说明他们在进入常驻玩法后，语音或故事文本中是否保留了早期活动里对这个势力的态度。'
)

TEST_QUESTIONS = [
    ("1", "极端复杂(枫丹NPC)", COMPLEX_QUESTION),
]

agent = create_agent_workflow()

for qid, qtype, query in TEST_QUESTIONS:
    print(f"\n{'='*60}")
    print(f"  TEST {qid}: [{qtype}]")
    print(f"{'='*60}")

    start = datetime.now()
    try:
        result = agent.invoke({
            "user_query": query,
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
        print(f"\n  [ERROR after {elapsed:.1f}s] {e}")
        import traceback
        traceback.print_exc()

print("\nDone.")

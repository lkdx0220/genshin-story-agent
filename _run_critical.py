#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时脚本：快速跑 #8 和 #11 两个 critical 题目"""
import sys
sys.path.insert(0, ".")

from test_plan_prompt_v5 import TEST_CASES, run_single_test, print_result
import genshin_story_agent as agent
import intent_router
intent_router._route_cache.clear()

plan_fn = agent.plan_agent
FILTER_IDS = {8, 11}

for tc in TEST_CASES:
    if tc["id"] not in FILTER_IDS:
        continue
    print(f"--- ID={tc['id']} {tc['title']} ---")
    print(f"  Q: {tc['question']}")
    print(f"  NOTE: {tc['note']}")
    result = run_single_test(tc, plan_fn)
    result["_question"] = tc["question"]
    status = "PASS" if result["passed"] else "FAIL"
    print(f"  Result: {status}")
    print(f"  Tools: {result.get('tool_names', [])}")
    if result["execution_plan"]:
        lines = result["execution_plan"].strip().split("\n")[:8]
        for line in lines:
            if line.strip():
                print(f"  |> {line.strip()[:130]}")
    if result["failures"]:
        for f in result["failures"]:
            print(f"  ! FAIL: {f}")
    print()

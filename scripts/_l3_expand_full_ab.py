# -*- coding: utf-8 -*-
"""L3 第五步图谱扩展 A/B 端到端实验（临时脚本，不提交）。

变体：
  on100 / on20 / off
只跑 C4（四条至冬支线综合题），记录：
- 总耗时
- L3 分段生成调用次数与 token 用量
- 证据包字符数、实体档案数
- 最终答案长度与关键实体覆盖
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(r"C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用")
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

import app.agent.nodes as nodes  # noqa: E402
import app.agent.l3_panorama as l3  # noqa: E402
from app.workflow import create_agent_workflow  # noqa: E402
from wiki_entry_graph import WikiEntryGraph  # noqa: E402

C4_QUESTION = (
    "我没看明白至冬国的“她的宫殿正坍塌向风雪、爱憎的赫斯珀利德斯、"
    "一边是宫殿一边是陵阙、在生命的寓所”这四条大型支线任务组成的庞大的支线任务，"
    "你能结合任务文本、对应地区的地图文本，帮我梳理一下剧情，并评价参与其中出现的所有角色吗？"
    "部分角色还与别的支线任务、地图文本有所关联，需要综合来看"
)

EXPECTED = [
    "伊兹梅洛", "卡谢伊", "莫罗", "阿尔维斯", "拉莉莎", "弗瑞奥萨",
    "欧维", "弗谢沃洛德", "完全树", "沙皇白桦", "扎拉",
    "炉火融炼之心", "勒庇依", "雪国的妖精", "影中沉凝的幻灭",
    "深廊终曲", "一引三收", "曾为灵魂的木舟", "纸页泛黄的文件",
]


class UsageWrapper:
    """包装 L3 section 模型，累计 invoke 的 input/output token。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

    def invoke(self, *args, **kwargs):
        response = self.inner.invoke(*args, **kwargs)
        usage = getattr(response, "usage_metadata", None) or {}
        self.calls += 1
        self.input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        self.total_tokens = self.input_tokens + self.output_tokens
        return response

    def __getattr__(self, item):
        return getattr(self.inner, item)


def apply_variant(name: str, usage: UsageWrapper) -> None:
    if name == "off":
        nodes._LORE_EXPAND_TYPES = set()
    elif name == "on20":
        original = WikiEntryGraph.expand

        def _expand_with_cap(self, entry_id, limit=20):
            return original(self, entry_id, limit=min(int(limit or 0), 20))

        WikiEntryGraph.expand = _expand_with_cap
    l3.answer_llm_l3_fast = usage


def extract_panorama_content(messages) -> str:
    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if content.strip().startswith("[全景全文读取]"):
            return content
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["on100", "on20", "off"], required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    usage = UsageWrapper(l3.answer_llm_l3_fast)
    apply_variant(args.variant, usage)

    agent = create_agent_workflow()
    state = {
        "user_query": C4_QUESTION,
        "rewritten_query": None,
        "alias_notes": None,
        "conversation_history": [],
        "conversation_summary": "",
        "messages": [],
        "final_response": None,
        "iteration": 0,
        "plan_retry": 0,
        "execution_plan": None,
        "intent_labels": None,
        "run_id": f"exp_full_{args.variant}",
        "execution_mode": None,
        "fast_iteration": 0,
    }

    t0 = time.time()
    result = agent.invoke(state)
    duration = round(time.time() - t0, 1)
    answer = result.get("final_response") or ""
    messages = result.get("messages") or []
    evidence = extract_panorama_content(messages)

    row = {
        "variant": args.variant,
        "duration_sec": duration,
        "answer_chars": len(answer),
        "evidence_chars": len(evidence),
        "l3_calls": usage.calls,
        "l3_input_tokens": usage.input_tokens,
        "l3_output_tokens": usage.output_tokens,
        "l3_total_tokens": usage.total_tokens,
        "expected_hits_in_answer": [k for k in EXPECTED if k in answer],
        "expected_hits_in_evidence": [k for k in EXPECTED if k in evidence],
        "expected_total": len(EXPECTED),
        "answer": answer,
    }
    print(json.dumps({k: v for k, v in row.items() if k != "answer"}, ensure_ascii=False), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{args.variant}] -> {args.out}", flush=True)


if __name__ == "__main__":
    main()

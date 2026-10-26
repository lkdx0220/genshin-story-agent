"""Finite Chinese grammar routed to the unchanged reviewed answer view."""
from __future__ import annotations

import re
from pathlib import Path

from .answer_view import AnswerReader

QUESTION_SCOPE = "finite_chinese_question_grammar_v1"
_NAMES = {"流浪者": "char:wanderer", "纳西妲": "char:nahida"}
_FIELDS = {"元素": "element", "武器": "weapon", "国家": "nation", "当前组织归属": "affiliation"}
_PHASES = {"前": "before_recovery", "后": "after_recovery", "前后": "both"}
_EVENTS = {
    "纳西妲关于记录创建的自述是什么": "event:nahida-record-creation-report",
    "纳西妲关于记忆保存的自述是什么": "event:nahida-memory-storage-report",
    "纳西妲的监督请求记录是什么": "event:nahida-supervision-request",
    "纳西妲相关的记忆回归叙述是什么": "event:nahida-memory-return",
}
_CLARIFICATION = "请指定恢复记忆前、恢复记忆后，或前后对照。"


def _route(status, reason_code, request=None):
    return {"status": status, "reason_code": reason_code, "request": request, "prompt": None}


def parse_question(question, entity_id):
    """Full-string syntax matching only; no resource reads or fact generation."""
    if entity_id not in _NAMES.values():
        return _route("unsupported", "entity_unsupported")
    if not isinstance(question, str) or not question or len(question) > 256:
        return _route("unsupported", "question_invalid")
    text = question.strip(" ")
    if text.endswith(("?", "？")):
        text = text[:-1]
    # Keep internal whitespace, Unicode forms and leftover punctuation intact.
    if not text:
        return _route("unsupported", "question_invalid")
    request = None
    target = None
    match = re.fullmatch(r"(流浪者|纳西妲)的(.+)是什么", text)
    if match:
        name, labels = match.groups()
        fields = labels.split("和")
        if 1 <= len(fields) <= 4 and len(set(fields)) == len(fields) and all(f in _FIELDS for f in fields):
            target = _NAMES[name]
            request = {"mode": "gaps", "fields": [_FIELDS[f] for f in fields]}
    if request is None:
        match = re.fullmatch(r"流浪者恢复记忆(前后|前|后)的记忆情况是什么", text)
        if match:
            target = "char:wanderer"
            request = {"mode": "memory", "phase": _PHASES[match[1]]}
        elif text == "流浪者的记忆情况是什么":
            target = "char:wanderer"
            request = {"mode": "memory"}
        elif text == "流浪者的记忆备份记录是什么":
            target = "char:wanderer"
            request = {"mode": "record", "record_id": "memory:nahida-backup"}
        elif text in _EVENTS:
            target = "char:nahida"
            request = {"mode": "record", "record_id": _EVENTS[text]}
    if request is None:
        return _route("unsupported", "question_unsupported")
    if target != entity_id:
        return _route("unsupported", "question_entity_mismatch")
    if request == {"mode": "memory"}:
        return _route("needs_condition", "memory_phase_required", request)
    return _route("matched", "grammar_matched", request)


class QuestionReader:
    """Preserve trusted capture and result semantics through composition."""

    def __init__(self, root: Path, *, entity_id: str, hooks=None):
        self.entity_id = entity_id
        self._answer_reader = AnswerReader(root, entity_id=entity_id, hooks=hooks)

    @property
    def read_attempts(self):
        return self._answer_reader.read_attempts

    def query(self, question, *, identity_path: Path, trusted_identity_sha256: str):
        routing = parse_question(question, self.entity_id)
        # Even syntax failures retain the established freshness-first behavior.
        # An invalid selector produces no factual projection and no fallback.
        result = self._answer_reader.query(
            routing["request"] if routing["request"] is not None else {},
            identity_path=identity_path,
            trusted_identity_sha256=trusted_identity_sha256,
        )
        if result["freshness_status"] == "fresh" and result["answer_status"] == "needs_condition":
            routing["prompt"] = _CLARIFICATION
        return {"format_version": 1, "question_scope": QUESTION_SCOPE,
                "routing": routing, "result": result}

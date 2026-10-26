"""Plain-text presentation of an internal, captured QuestionReader result.

This formatter neither establishes trust nor reads resources. The unchanged
QuestionReader establishes the selected local snapshot's capture-time status.
"""
from __future__ import annotations

from pathlib import Path
import unicodedata

from .question_entry import QuestionReader

_NAMES = {"char:wanderer": "流浪者", "char:nahida": "纳西妲"}
_PHASES = {None: "记录", "before_recovery": "恢复记忆前", "after_recovery": "恢复记忆后"}
_SCOPE = "范围：固定可信本地快照，仅表示捕获时匹配，不表示远端最新。"
_BASIS = {"speaker_lines": "角色发言", "narration": "叙述", "inner_monologue": "内心独白"}
_AUTHORITY = {"non_authoritative": "非权威", "authoritative": "权威标注"}
_EVIDENCE_KEYS = ("source_span_id", "basis", "authority")


def _require(condition, reason):
    if not condition:
        raise ValueError("readable_result_invalid:" + reason)


def _visible(text, *, multiline=False):
    """Make terminal controls visible without Unicode normalization."""
    _require(isinstance(text, str), "text")
    chars = []
    for char in text:
        point = ord(char)
        if char == "\n" and multiline:
            chars.append(char)
        elif point < 32 or 127 <= point <= 159 or unicodedata.category(char) in {"Cf", "Cs"}:
            chars.append(f"\\u{point:04x}" if point <= 0xffff else f"\\U{point:08x}")
        else:
            chars.append(char)
    return "".join(chars)


def _citation_links(claims, citations):
    """Validate record association, retaining each evidence occurrence."""
    _require(isinstance(citations, list), "citations")
    ids = []
    for claim in claims:
        _require(isinstance(claim, dict), "claim")
        rid = claim.get("record_id")
        _require(isinstance(rid, str) and bool(rid) and rid not in ids, "claim_id")
        _require(claim.get("status") == "candidate", "claim_status")
        _require(isinstance(claim.get("text"), str), "claim_text")
        phase = claim.get("phase")
        _require(phase is None or isinstance(phase, str), "phase")
        _require(phase in _PHASES, "phase")
        record = claim.get("record")
        _require(isinstance(record, dict) and isinstance(record.get("evidence"), list)
                 and bool(record["evidence"]), "record_evidence")
        ids.append(rid)
    projections = {rid: [] for rid in ids}
    links = {rid: [] for rid in ids}
    for number, citation in enumerate(citations, 1):
        _require(isinstance(citation, dict), "citation")
        rid = citation.get("record_id")
        _require(isinstance(rid, str) and rid in links, "citation_record")
        for key in (*_EVIDENCE_KEYS, "url"):
            _require(isinstance(citation.get(key), str) and bool(citation[key]), "citation_" + key)
        projections[rid].append({key: citation[key] for key in _EVIDENCE_KEYS})
        links[rid].append(number)
    for claim in claims:
        _require(projections[claim["record_id"]] == claim["record"]["evidence"], "citation_evidence")
    return links


def render_question_result(envelope) -> str:
    """Render internal S6-B output; this is not an authenticity validator.

    Nonfresh states intentionally ignore all payload fields. For fresh states,
    structural errors remain program errors rather than a forged 'unknown'.
    """
    _require(isinstance(envelope, dict) and isinstance(envelope.get("result"), dict), "envelope")
    result = envelope["result"]
    freshness = result.get("freshness_status")
    if freshness == "stale":
        return "所选本地快照已发生变化，本次不提供材料答复。"
    if freshness == "unknown":
        return "无法核验所选本地快照，本次不提供材料答复。"
    _require(freshness == "fresh", "freshness_status")
    entity = result.get("entity_id")
    _require(isinstance(entity, str) and entity in _NAMES, "entity")
    status = result.get("answer_status")
    header = f"{_NAMES[entity]}｜材料答复\n{_SCOPE}"
    if status == "unsupported":
        return header + "\n当前有限问句或材料记录不支持本次答复。"
    if status == "needs_condition":
        routing = envelope.get("routing")
        _require(isinstance(routing, dict), "routing")
        prompt = routing.get("prompt")
        _require(isinstance(prompt, str) and bool(prompt), "prompt")
        return header + "\n" + _visible(prompt, multiline=True)
    _require(status == "answered", "answer_status")
    _require(result.get("page_status") == "draft", "page_status")
    _require(isinstance(result.get("read_snapshot"), dict), "read_snapshot")
    answer = result.get("answer")
    claims, gaps, citations = result.get("claims"), result.get("gaps"), result.get("citations")
    _require(isinstance(answer, str) and bool(answer), "answer")
    _require(isinstance(claims, list) and isinstance(gaps, list), "payload_lists")
    _require(bool(claims) != bool(gaps), "projection_kind")
    links = _citation_links(claims, citations)
    if gaps:
        _require(not citations, "gap_citations")
        _require(all(isinstance(gap, dict) and gap.get("status") == "unknown"
                     and isinstance(gap.get("text"), str) for gap in gaps), "gaps")
    lines = [header, "页面为草稿（draft）；材料记录为候选（candidate）。", "", _visible(answer, multiline=True)]
    if claims:
        lines += ["", "阶段与来源定位："]
        for number, claim in enumerate(claims, 1):
            refs = "、".join(f"[{reference}]" for reference in links[claim["record_id"]])
            lines.append(f"记录{number}：{_PHASES[claim.get('phase')]}；引用{refs}")
        for number, citation in enumerate(citations, 1):
            basis, authority = citation["basis"], citation["authority"]
            lines += [f"[{number}] {_BASIS.get(basis, '证据标注')}（{_visible(basis)}）；"
                      f"{_AUTHORITY.get(authority, '权威性标注')}（{_visible(authority)}）",
                      "    片段：" + _visible(citation["source_span_id"]),
                      "    来源：" + _visible(citation["url"])]
    return "\n".join(lines)


class TextQuestionReader:
    """One captured query, unchanged envelope plus deterministic text."""

    def __init__(self, root: Path, *, entity_id: str, hooks=None):
        self._question_reader = QuestionReader(root, entity_id=entity_id, hooks=hooks)

    @property
    def read_attempts(self):
        return self._question_reader.read_attempts

    def query(self, question, *, identity_path: Path, trusted_identity_sha256: str):
        envelope = self._question_reader.query(question, identity_path=identity_path,
                                                trusted_identity_sha256=trusted_identity_sha256)
        return {"envelope": envelope, "text": render_question_result(envelope)}

"""Finite deterministic answer views over an accepted captured local snapshot.

Requests are explicit selectors, not natural-language interpretation. A catalog
digest authorizes the meaning of a reviewed record; a new identity alone does
not authorize a changed record's semantic projection.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .freshness_reader import FreshnessReader
from .support_catalog import CATALOG

_PHASE_ORDER = ("before_recovery", "after_recovery")
_FIELD_LABELS = {"element": "元素", "weapon": "武器", "nation": "国家", "affiliation": "当前组织归属"}


class _Unsupported(ValueError):
    """An explicit answer contract cannot be satisfied by this snapshot."""


def _declared_phase(spec):
    if not hasattr(spec, "phase"):
        raise _Unsupported("catalog_phase_undeclared")
    phase = spec.phase
    if phase is not None and (not isinstance(phase, str) or phase not in _PHASE_ORDER):
        raise _Unsupported("catalog_phase_invalid")
    return phase


def _record_digest(record: dict) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _select(request, entity_id):
    if not isinstance(request, dict) or not isinstance(request.get("mode"), str):
        raise _Unsupported("request_invalid")
    mode = request["mode"]
    if mode == "memory":
        if entity_id != "char:wanderer" or set(request) - {"mode", "phase"}:
            raise _Unsupported("request_invalid")
        if "phase" not in request:
            return "needs_condition", []
        phase = request["phase"]
        if not isinstance(phase, str) or phase not in (*_PHASE_ORDER, "both"):
            raise _Unsupported("request_invalid")
        entries = [(rid, _declared_phase(spec)) for rid, spec in CATALOG.items()
                   if spec.entity_id == entity_id and spec.section == "memory_records"]
        stages = _PHASE_ORDER if phase == "both" else (phase,)
        selected = [rid for stage in stages for rid, declared in entries if declared == stage]
        if not selected:
            raise _Unsupported("memory_phase_unsupported")
        return "records", selected
    if mode == "record" and set(request) == {"mode", "record_id"}:
        record_id = request["record_id"]
        if not isinstance(record_id, str) or record_id not in CATALOG:
            raise _Unsupported("record_unsupported")
        if CATALOG[record_id].entity_id != entity_id:
            raise _Unsupported("record_entity_mismatch")
        return "records", [record_id]
    if mode == "gaps" and set(request) == {"mode", "fields"}:
        fields = request["fields"]
        if (not isinstance(fields, list) or not fields
                or any(not isinstance(f, str) or f not in _FIELD_LABELS for f in fields)
                or len(set(fields)) != len(fields)):
            raise _Unsupported("request_invalid")
        return "gaps", fields
    raise _Unsupported("request_invalid")


class AnswerReader:
    def __init__(self, root: Path, *, entity_id: str, hooks=None):
        self.entity_id = entity_id
        self._captured = None
        hook_map = dict(hooks or {})
        external = hook_map.pop("after_capture", None)

        def capture(*, captured):
            self._captured = dict(captured)
            if external is not None:
                external(captured=dict(captured))

        hook_map["after_capture"] = capture
        self._reader = FreshnessReader(root, hooks=hook_map, entity_id=entity_id)

    @property
    def read_attempts(self):
        return self._reader.read_attempts

    def query(self, request, *, identity_path: Path, trusted_identity_sha256: str) -> dict:
        self._captured = None
        # Empty query exercises the unchanged capture/validation contract. Its
        # legacy ranking/answer is not used to infer field absence or stage.
        checked = self._reader.query("", identity_path=identity_path,
                                     trusted_identity_sha256=trusted_identity_sha256, snapshot={})
        result = {
            "format_version": 1, "entity_id": self.entity_id,
            "freshness_status": checked["status"], "answer_status": "unavailable",
            "reason_code": checked["reason_code"], "reason_resource": checked["reason_resource"],
            "answer": None, "claims": [], "gaps": [], "citations": [],
            "read_snapshot": checked["read_snapshot"], "cache_reused": checked["cache_reused"],
            "freshness_scope": checked["freshness_scope"], "page_status": None,
            "answer_scope": "finite_reviewed_record_and_explicit_gap_projection",
        }
        if checked["status"] != "fresh":
            return result
        if self._captured is None:
            raise RuntimeError("fresh reader omitted capture")
        # The Reader already validated these bytes; no resource is reopened.
        text = self._captured["page"].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        page = json.loads(text.split("\n---\n", 1)[0][4:])
        spans = json.loads(self._captured["span_index"].decode("utf-8"))
        result["page_status"] = page.get("page_status")
        try:
            mode, selectors = _select(request, self.entity_id)
            if mode == "needs_condition":
                result.update(answer_status="needs_condition", reason_code="memory_phase_required")
                return result
            if page.get("page_status") != "draft":
                raise _Unsupported("page_status_unsupported")
            # This finite projection has no conflict-resolution semantics.
            # A record digest alone cannot authorize new page-level disputes.
            if page.get("conflicts") != []:
                raise _Unsupported("page_conflicts_unsupported")
            if mode == "records":
                claims, citations = self._records(page, spans, selectors, checked["read_snapshot"])
                lines = [f"[{c['status']}] " + (f"({c['phase']}) " if c["phase"] else "") + c["text"] for c in claims]
                result.update(claims=claims, citations=citations,
                              answer="【材料中的候选记录；页面 draft】\n" + "\n".join(lines))
            else:
                gaps = self._gaps(page, selectors, checked["read_snapshot"])
                result.update(gaps=gaps, answer="\n".join(g["text"] for g in gaps))
            result.update(answer_status="answered", reason_code="projection_complete")
        except _Unsupported as exc:
            result.update(answer_status="unsupported", reason_code=str(exc))
        return copy.deepcopy(result)

    def _records(self, page, spans, selectors, snapshot):
        urls = {row["span_id"]: row.get("url") for row in spans}
        claims, citations = [], []
        for record_id in selectors:
            spec = CATALOG[record_id]
            phase = _declared_phase(spec)
            records = page.get(spec.section)
            if not isinstance(records, list):
                raise _Unsupported("record_missing")
            matches = [(i, r) for i, r in enumerate(records)
                       if isinstance(r, dict) and r.get(spec.id_key) == record_id]
            if len(matches) != 1:
                raise _Unsupported("record_missing_or_duplicate")
            i, record = matches[0]
            if _record_digest(record) != spec.sha256:
                raise _Unsupported("unsupported_record_revision")
            # The reviewed record includes evidence basis, authority, subject,
            # qualifications and status. No substring guessing replaces it.
            text = record["content" if spec.section == "memory_records" else "description"]
            claims.append({"record_id": record_id, "text": text, "status": record["status"],
                           "phase": phase, "record": copy.deepcopy(record),
                           "location": {"page_sha256": snapshot["page_sha256"],
                                        "pointer": f"/{spec.section}/{i}"}})
            for evidence in record["evidence"]:
                span_id = evidence["source_span_id"]
                if not urls.get(span_id):
                    raise _Unsupported("evidence_unavailable")
                citations.append(dict(copy.deepcopy(evidence), record_id=record_id, url=urls[span_id]))
        return claims, citations

    @staticmethod
    def _gaps(page, fields, snapshot):
        assertions = page.get("field_assertions")
        if not isinstance(assertions, list):
            raise _Unsupported("gap_not_explicit")
        gaps = []
        for field in fields:
            matches = [(i, a) for i, a in enumerate(assertions)
                       if isinstance(a, dict) and a.get("path") == "/" + field]
            if len(matches) != 1 or field not in page:
                raise _Unsupported("gap_not_explicit")
            i, assertion = matches[0]
            empty_value = [] if field == "affiliation" else None
            if (page[field] != empty_value or assertion.get("status") != "unknown"
                    or assertion.get("evidence") != [] or assertion.get("conflict_ids") != []):
                raise _Unsupported("gap_not_explicit")
            label = _FIELD_LABELS[field]
            text = f"本批材料未确认{label}。"
            if field == "affiliation":
                text += "空数组不表示无组织，也不证明当前归属。"
            gaps.append({"field": field, "status": "unknown", "text": text,
                         "location": {"page_sha256": snapshot["page_sha256"],
                                      "value_pointer": "/" + field,
                                      "assertion_pointer": f"/field_assertions/{i}"}})
        return gaps

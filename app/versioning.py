# -*- coding: utf-8 -*-
"""版本契约统一解析（B1）。

外部兼容字段：
- introduced_in / label
- valid_from / valid_from_label
- valid_to / valid_to_label
- verified_in / verified_in_labels
- QuerySnapshot.game_version / game_version_label

统一解析为 ResolvedVersion；ingest / lint / runtime 复用本模块，避免三套语义。

R1：双值组合继承实际匹配断言的可信度；candidate / unknown 不升级为 confirmed。
R2：未配对双值不得降级成数字比较；snapshot / observed_at / 范围端点分别执行配对门。
R3：索引侧固定注册表副本；本模块保持纯数据结构。
R4：解析结果保留实际匹配/比较依赖的 evidence。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "wiki" / "__schema__" / "version-registry.json"

VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
VERSION_ID_RE = re.compile(r"^version:[a-z0-9][a-z0-9_-]*$")
SPAN_ID_RE = re.compile(r"^span:[a-z0-9_-]+$")
ASSERTION_STATUSES = {"confirmed", "candidate", "unknown"}


class VersionRegistryError(ValueError):
    """注册表结构、证据或映射冲突。"""


@dataclass(frozen=True)
class ResolvedVersion:
    version_id: Optional[str] = None
    raw_label: Optional[str] = None
    canonical_label: Optional[str] = None
    numeric: Optional[str] = None
    order: Optional[int] = None
    label_status: str = "unknown"
    numeric_status: str = "unknown"
    order_status: str = "unknown"
    matched_by: Optional[str] = None
    matched_status: str = "unknown"
    pair_status: str = "single"
    input_kind: str = "none"
    unresolved_reason: Optional[str] = None
    registry_revision: Optional[str] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    order_evidence: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def identity_confirmed(self) -> bool:
        return bool(self.version_id) and self.matched_status == "confirmed"


def normalize_numeric(value: str) -> str:
    """统一数字版本身份：3.3 与 3.3.0 归一为 3.3；保留至少两段。"""
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise VersionRegistryError("bad numeric version: " + str(value))
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def registry_fingerprint(registry: Dict[str, Any]) -> str:
    payload = json.dumps(registry, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise VersionRegistryError(message)


def _combine_status(*statuses: str) -> str:
    if any(status == "candidate" for status in statuses):
        return "candidate"
    if any(status != "confirmed" for status in statuses):
        return "unknown"
    return "confirmed"


def _assertion(entry: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = entry.get(key)
    _assert(isinstance(value, dict), f"{entry.get('version_id')}: {key} must be an object")
    status = value.get("status")
    _assert(status in ASSERTION_STATUSES, f"{entry.get('version_id')}: {key} bad status")
    return value


def _check_evidence(
    entry: Dict[str, Any],
    key: str,
    assertion: Dict[str, Any],
    known_span_ids: Optional[Iterable[str]] = None,
) -> None:
    if assertion.get("status") != "confirmed":
        return
    evidence = assertion.get("evidence")
    _assert(isinstance(evidence, list) and evidence, f"{entry.get('version_id')}: confirmed {key} needs evidence")
    known = set(known_span_ids) if known_span_ids is not None else None
    for item in evidence:
        _assert(isinstance(item, dict), f"{entry.get('version_id')}: bad {key} evidence item")
        span_id = item.get("source_span_id")
        _assert(
            isinstance(span_id, str) and SPAN_ID_RE.fullmatch(span_id),
            f"{entry.get('version_id')}: bad {key} source_span_id",
        )
        if known is not None:
            _assert(span_id in known, f"{entry.get('version_id')}: {key} source_span_id not found: {span_id}")


def _numeric_key(value: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in normalize_numeric(value).split("."))


def validate_registry(registry: Dict[str, Any], known_span_ids: Optional[Iterable[str]] = None) -> None:
    _assert(isinstance(registry, dict), "registry must be an object")
    versions = registry.get("versions")
    _assert(isinstance(versions, list) and versions, "registry.versions required")
    seen_ids = set()
    seen_labels = set()
    seen_numeric = {}
    seen_order = {}
    for entry in versions:
        _assert(isinstance(entry, dict), "registry entry must be an object")
        version_id = entry.get("version_id")
        _assert(isinstance(version_id, str) and VERSION_ID_RE.fullmatch(version_id), "bad version_id")
        _assert(version_id not in seen_ids, "duplicate version_id")
        seen_ids.add(version_id)

        label = _assertion(entry, "label")
        _check_evidence(entry, "label", label, known_span_ids)
        label_value = label.get("value")
        _assert(isinstance(label_value, str) and label_value.strip(), f"{version_id}: label required")
        _assert(label_value not in seen_labels, f"{version_id}: duplicate label")
        seen_labels.add(label_value)

        numeric = _assertion(entry, "numeric")
        _check_evidence(entry, "numeric", numeric, known_span_ids)
        numeric_value = numeric.get("value")
        if numeric_value is not None:
            _assert(
                isinstance(numeric_value, str) and VERSION_RE.fullmatch(numeric_value), f"{version_id}: bad numeric"
            )
            normalized = normalize_numeric(numeric_value)
            _assert(normalized not in seen_numeric, f"{version_id}: duplicate normalized numeric {normalized}")
            seen_numeric[normalized] = version_id
        else:
            _assert(numeric.get("status") != "confirmed", f"{version_id}: confirmed numeric cannot be null")

        order = _assertion(entry, "order")
        _check_evidence(entry, "order", order, known_span_ids)
        order_value = order.get("value")
        if order_value is not None:
            _assert(isinstance(order_value, int) and not isinstance(order_value, bool), f"{version_id}: bad order")
            if order.get("status") == "confirmed":
                _assert(order_value not in seen_order, f"{version_id}: duplicate confirmed order")
                seen_order[order_value] = version_id
        else:
            _assert(order.get("status") != "confirmed", f"{version_id}: confirmed order cannot be null")

        aliases = entry.get("aliases") or []
        _assert(isinstance(aliases, list), f"{version_id}: aliases must be a list")
        for alias in aliases:
            _assert(isinstance(alias, dict), f"{version_id}: alias must be an object")
            _assert(alias.get("status") in ASSERTION_STATUSES, f"{version_id}: alias bad status")
            _check_evidence(entry, "alias", alias, known_span_ids)
            alias_value = alias.get("value")
            _assert(isinstance(alias_value, str) and alias_value.strip(), f"{version_id}: alias value required")
            _assert(alias_value not in seen_labels, f"{version_id}: duplicate alias/label")
            seen_labels.add(alias_value)

    _check_numeric_order_consistency(versions)


def _check_numeric_order_consistency(versions: List[Dict[str, Any]]) -> None:
    """confirmed numeric 与 confirmed order 必须方向一致，保证可比较结果的传递性。"""
    comparable = []
    for entry in versions:
        numeric = entry.get("numeric") or {}
        order = entry.get("order") or {}
        if (
            numeric.get("status") == "confirmed"
            and numeric.get("value")
            and order.get("status") == "confirmed"
            and order.get("value") is not None
        ):
            comparable.append((entry.get("version_id"), _numeric_key(numeric["value"]), order["value"]))
    for index, left in enumerate(comparable):
        for right in comparable[index + 1 :]:
            numeric_cmp = (left[1] > right[1]) - (left[1] < right[1])
            order_cmp = (left[2] > right[2]) - (left[2] < right[2])
            if numeric_cmp != order_cmp:
                raise VersionRegistryError(f"confirmed numeric/order conflict between {left[0]} and {right[0]}")


_REGISTRY_CACHE: Dict[Tuple[str, int, int, Optional[Tuple[str, ...]]], Dict[str, Any]] = {}


def load_registry(path: Optional[Path] = None, known_span_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_REGISTRY_PATH
    _assert(registry_path.exists(), "version registry missing: " + str(registry_path))
    if known_span_ids is None:
        known_span_ids = global_known_span_ids()
    stat = registry_path.stat()
    known_key = tuple(sorted(set(known_span_ids)))
    key = (str(registry_path.resolve()), stat.st_mtime_ns, stat.st_size, known_key)
    if key not in _REGISTRY_CACHE:
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise VersionRegistryError("version registry is not valid JSON") from exc
        validate_registry(data, known_span_ids=known_span_ids)
        _REGISTRY_CACHE[key] = data
    return _REGISTRY_CACHE[key]


def global_known_span_ids(root: Optional[Path] = None) -> set:
    """从仓库 raw/*/span_index.json 汇总可用于注册表证据审计的 span。"""
    base = Path(root) if root else PROJECT_ROOT
    ids = set()
    for path in sorted((base / "raw").glob("*/span_index.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("span_id"):
                ids.add(row["span_id"])
    return ids


def _find_label_match(registry: Dict[str, Any], label: str) -> Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    for entry in registry.get("versions") or []:
        assertion = entry.get("label") or {}
        if assertion.get("value") == label:
            return entry, "label", assertion
        for alias in entry.get("aliases") or []:
            if alias.get("value") == label:
                return entry, "alias", alias
    return None


def _find_numeric_match(registry: Dict[str, Any], numeric: str) -> Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    normalized = normalize_numeric(numeric)
    for entry in registry.get("versions") or []:
        assertion = entry.get("numeric") or {}
        value = assertion.get("value")
        if value and normalize_numeric(value) == normalized:
            return entry, "numeric", assertion
    return None


def _make_resolved(
    entry: Dict[str, Any],
    registry: Dict[str, Any],
    *,
    raw_label: Optional[str],
    numeric: Optional[str],
    matched_by: str,
    matched_status: str,
    pair_status: str,
    input_kind: str,
    evidence: Optional[List[Dict[str, Any]]] = None,
    unresolved_reason: Optional[str] = None,
) -> ResolvedVersion:
    label_assertion = entry.get("label") or {}
    numeric_assertion = entry.get("numeric") or {}
    order_assertion = entry.get("order") or {}
    combined_evidence = list(evidence or [])
    for item in numeric_assertion.get("evidence") or []:
        if item not in combined_evidence:
            combined_evidence.append(item)
    return ResolvedVersion(
        version_id=entry.get("version_id"),
        raw_label=raw_label,
        canonical_label=label_assertion.get("value"),
        numeric=numeric_assertion.get("value"),
        order=order_assertion.get("value"),
        label_status=label_assertion.get("status") or "unknown",
        numeric_status=numeric_assertion.get("status") or "unknown",
        order_status=order_assertion.get("status") or "unknown",
        matched_by=matched_by,
        matched_status=matched_status,
        pair_status=pair_status,
        input_kind=input_kind,
        unresolved_reason=unresolved_reason,
        registry_revision=registry.get("registry_revision"),
        evidence=combined_evidence,
        order_evidence=list(order_assertion.get("evidence") or []),
    )


def _resolve_double(
    registry: Dict[str, Any],
    label: str,
    numeric: str,
    label_match: Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]],
    numeric_match: Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]],
) -> ResolvedVersion:
    if label_match and numeric_match:
        if label_match[0] is not numeric_match[0]:
            raise VersionRegistryError("numeric/label resolve to different version ids")
        label_assertion = label_match[2]
        numeric_assertion = numeric_match[2]
        evidence = list(label_assertion.get("evidence") or []) + list(numeric_assertion.get("evidence") or [])
        return _make_resolved(
            numeric_match[0],
            registry,
            raw_label=label,
            numeric=numeric,
            matched_by=label_match[1] + "+numeric",
            matched_status=_combine_status(label_assertion.get("status"), numeric_assertion.get("status")),
            pair_status="matched",
            input_kind="double",
            evidence=evidence,
        )
    if label_match or numeric_match:
        entry, matched_by, assertion = label_match or numeric_match
        return ResolvedVersion(
            raw_label=label,
            numeric=numeric,
            canonical_label=(entry.get("label") or {}).get("value"),
            matched_by=matched_by,
            matched_status=assertion.get("status") or "unknown",
            pair_status="unresolved",
            input_kind="double",
            unresolved_reason="one side unregistered",
            registry_revision=registry.get("registry_revision"),
            evidence=list(assertion.get("evidence") or []),
        )
    return ResolvedVersion(
        raw_label=label,
        numeric=numeric,
        pair_status="unresolved",
        input_kind="double",
        unresolved_reason="both sides unregistered",
        registry_revision=registry.get("registry_revision"),
    )


def _resolve_single(
    registry: Dict[str, Any],
    label: Optional[str],
    numeric: Optional[str],
    match: Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]],
) -> ResolvedVersion:
    input_kind = "numeric" if numeric is not None else "label"
    if match is None:
        return ResolvedVersion(
            raw_label=label,
            numeric=numeric,
            pair_status="unresolved",
            input_kind=input_kind,
            unresolved_reason="unregistered",
            registry_revision=registry.get("registry_revision"),
        )
    entry, matched_by, assertion = match
    return _make_resolved(
        entry,
        registry,
        raw_label=label,
        numeric=numeric,
        matched_by=matched_by,
        matched_status=assertion.get("status") or "unknown",
        pair_status="single",
        input_kind=input_kind,
        evidence=list(assertion.get("evidence") or []),
    )


def resolve_version(
    numeric: Optional[str] = None,
    label: Optional[str] = None,
    registry: Optional[Dict[str, Any]] = None,
) -> ResolvedVersion:
    if numeric is not None:
        normalize_numeric(numeric)
    registry = registry if registry is not None else load_registry()
    label_match = _find_label_match(registry, label) if label else None
    numeric_match = _find_numeric_match(registry, numeric) if numeric else None
    if numeric is not None and label is not None:
        return _resolve_double(registry, label, numeric, label_match, numeric_match)
    return _resolve_single(registry, label, numeric, label_match or numeric_match)


def _numeric_usable(value: ResolvedVersion) -> bool:
    if value.numeric_status == "candidate":
        return False
    if value.numeric_status == "confirmed":
        return True
    # 未确认的 numeric 只有用户原文显式输入的数字才可直接比较；
    # 由 label / alias 解析带出的 unknown numeric 不控制排序。
    return value.input_kind == "numeric"


def _compare_numeric(left: ResolvedVersion, right: ResolvedVersion) -> Optional[int]:
    if not left.numeric or not right.numeric:
        return None
    if not _numeric_usable(left) or not _numeric_usable(right):
        return None
    a = _numeric_key(left.numeric)
    b = _numeric_key(right.numeric)
    size = max(len(a), len(b))
    a = a + (0,) * (size - len(a))
    b = b + (0,) * (size - len(b))
    return (a > b) - (a < b)


def _compare_order(left: ResolvedVersion, right: ResolvedVersion) -> Optional[int]:
    if left.order is None or right.order is None:
        return None
    if left.order_status != "confirmed" or right.order_status != "confirmed":
        return None
    return (left.order > right.order) - (left.order < right.order)


def _comparison_allowed(left: ResolvedVersion, right: ResolvedVersion) -> bool:
    if left.input_kind == "double" and left.pair_status != "matched":
        return False
    if right.input_kind == "double" and right.pair_status != "matched":
        return False
    if left.pair_status == "matched" and left.matched_status != "confirmed":
        return False
    if right.pair_status == "matched" and right.matched_status != "confirmed":
        return False
    if left.matched_status != "confirmed" or right.matched_status != "confirmed":
        return (
            left.input_kind == "numeric"
            and right.input_kind == "numeric"
            and left.numeric_status != "candidate"
            and right.numeric_status != "candidate"
        )
    return True


def compare_versions(left: ResolvedVersion, right: ResolvedVersion) -> Optional[int]:
    """返回 -1/0/1；无法确定性比较时返回 None。"""
    if not _comparison_allowed(left, right):
        return None
    if left.version_id and right.version_id and left.version_id == right.version_id:
        return 0
    numeric_cmp = _compare_numeric(left, right)
    if numeric_cmp is not None:
        return numeric_cmp
    return _compare_order(left, right)


def range_contains(
    value: ResolvedVersion,
    lower: Optional[ResolvedVersion] = None,
    upper: Optional[ResolvedVersion] = None,
) -> Optional[bool]:
    """资料范围：lower 含起点，upper 不含终点；无法判定返回 None。"""
    if lower is not None:
        cmp_lower = compare_versions(value, lower)
        if cmp_lower is None:
            return None
        if cmp_lower < 0:
            return False
    if upper is not None:
        cmp_upper = compare_versions(value, upper)
        if cmp_upper is None:
            return None
        if cmp_upper >= 0:
            return False
    return True

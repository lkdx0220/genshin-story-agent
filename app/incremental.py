# -*- coding: utf-8 -*-
"""B4-01 首批：只读增量计划（依赖图 + 指纹 diff + plan/explain）。

边界：
- 不执行 rebuild，不推进成功 baseline，不修改 ingest 输出。
- 只写 reports/incremental/{plan.json,graph.json}。
- state.json 只能由成功构建提交；plan 不得写 state.json。
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = REPO_ROOT / "config" / "incremental-inputs.json"
DEFAULT_STATE = REPO_ROOT / "reports" / "incremental" / "state.json"
DEFAULT_PLAN = REPO_ROOT / "reports" / "incremental" / "plan.json"
DEFAULT_GRAPH = REPO_ROOT / "reports" / "incremental" / "graph.json"
PROTOCOL_VERSION = "b4-01-plan-v1"
SKIP_DIRS = {".git", "__pycache__", "reports", "audits", ".venv", "node_modules"}
COMMON_KEYS = ("schema", "template", "schema_lock", "registry", "lint", "prompts", "inputs_registry", "build_code")


class IncrementalError(ValueError):
    """输入登记、依赖图或 state 非法。"""


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IncrementalError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise IncrementalError(f"invalid JSON: {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise IncrementalError(f"path escapes project root: {path}") from exc


def _inside_root(path: Path) -> Path:
    resolved = path.resolve()
    root = REPO_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise IncrementalError(f"path escapes project root: {path}")
    return resolved


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise IncrementalError(message)


def _hash_if_exists(path: Path) -> Optional[str]:
    try:
        return _sha_bytes(path.read_bytes())
    except FileNotFoundError:
        return None


def _hash_tree(base: Path, suffixes: Optional[Iterable[str]] = None) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not base.exists():
        return result
    allowed = set(suffixes) if suffixes is not None else None
    for path in sorted(base.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if allowed is not None and path.suffix not in allowed:
            continue
        result[_relative(path)] = _sha_bytes(path.read_bytes())
    return result


BUILD_FILES = (
    "ingest.py",
    "wiki_lint.py",
    "wiki/__schema__/source-span-format.md",
    "app/canonical.py",
    "app/applicability.py",
    "app/choice_contract.py",
    "app/choice_proofs.py",
    "app/versioning.py",
)


def _hash_build_code() -> Dict[str, Optional[str]]:
    return {rel: _hash_if_exists(REPO_ROOT / rel) for rel in BUILD_FILES}


def _common_fingerprints(inputs_path: Optional[Path] = None) -> Dict[str, Any]:
    prompts = _hash_tree(REPO_ROOT / "wiki" / "__schema__", {".md", ".json"})
    prompts = {key: value for key, value in prompts.items() if key.endswith(".prompt.md")}
    return {
        "schema": _hash_if_exists(REPO_ROOT / "wiki" / "__schema__" / "entity.schema.json"),
        "template": _hash_if_exists(REPO_ROOT / "wiki" / "__schema__" / "entity-template.md"),
        "schema_lock": _hash_if_exists(REPO_ROOT / "docs" / "schema-lock.json"),
        "registry": _hash_if_exists(REPO_ROOT / "wiki" / "__schema__" / "version-registry.json"),
        "lint": _hash_tree(REPO_ROOT / "lint"),
        "prompts": prompts,
        "inputs_registry": _hash_if_exists(inputs_path or DEFAULT_INPUTS),
        "build_code": _hash_build_code(),
        "protocol": PROTOCOL_VERSION,
    }


def _manifest_sources(manifest: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for source in manifest.get("sources") or []:
        source_id = source.get("source_id")
        file_name = source.get("file")
        if isinstance(source_id, str) and source_id and isinstance(file_name, str) and file_name:
            result[source_id] = {"file": file_name, "declared": str(source.get("sha256") or "")}
    return result


def _normalize_pages(managed: Any, index: int, seen_page_keys: set) -> List[Dict[str, Any]]:
    _need(isinstance(managed, list) and bool(managed), f"inputs[{index}] managed_pages required")
    pages = []
    for page_index, page in enumerate(managed):
        _need(isinstance(page, dict), f"inputs[{index}].managed_pages[{page_index}] must be an object")
        page_key = page.get("page_key")
        _need(
            isinstance(page_key, str) and bool(page_key) and page_key not in seen_page_keys,
            f"duplicate/invalid page_key: {page_key}",
        )
        seen_page_keys.add(page_key)
        response = page.get("response")
        _need(isinstance(response, str) and bool(response), f"managed page needs response: {page_key}")
        _inside_root(REPO_ROOT / response)
        pages.append(
            {
                "page_key": page_key,
                "entity_id": page.get("entity_id"),
                "response": response,
                "output": page.get("output"),
            }
        )
    return pages


def _normalize_entry(item: Any, index: int, seen_bundle_keys: set, seen_page_keys: set) -> Dict[str, Any]:
    _need(isinstance(item, dict), f"inputs[{index}] must be an object")
    bundle_key = item.get("bundle_key")
    root = item.get("root")
    _need(
        isinstance(bundle_key, str) and bool(bundle_key) and bundle_key not in seen_bundle_keys,
        f"inputs[{index}] bad bundle_key",
    )
    _need(isinstance(root, str) and bool(root), f"inputs[{index}] bad root")
    root_path = _inside_root(REPO_ROOT / root)
    _need(root_path.is_dir(), f"inputs[{index}] root missing: {root}")
    pages = _normalize_pages(item.get("managed_pages"), index, seen_page_keys)
    seen_bundle_keys.add(bundle_key)
    return {
        "bundle_key": bundle_key,
        "root": _relative(root_path),
        "data_mode": item.get("data_mode") or "fixture",
        "managed_pages": pages,
        "sources": list(item.get("sources") or []),
        "profile_ids": list(item.get("profile_ids") or []),
        "prompt": item.get("prompt"),
    }


def load_inputs(path: Path = DEFAULT_INPUTS) -> Dict[str, Any]:
    data = _read_json(Path(path))
    _need(isinstance(data, dict) and data.get("format_version") == 1, "incremental inputs format_version must be 1")
    entries = data.get("inputs")
    _need(isinstance(entries, list) and bool(entries), "incremental inputs must contain a non-empty inputs array")
    normalized: Dict[str, Any] = {"format_version": 1, "inputs": [], "_inputs_path": str(Path(path).resolve())}
    normalized_inputs: List[Dict[str, Any]] = normalized["inputs"]
    seen_bundle_keys: set = set()
    seen_page_keys: set = set()
    for index, item in enumerate(entries):
        normalized_inputs.append(_normalize_entry(item, index, seen_bundle_keys, seen_page_keys))
    return normalized


def _validation_errors(root: Path, manifest: Dict[str, Any], item: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    lock_path = REPO_ROOT / "docs" / "schema-lock.json"
    if not lock_path.exists():
        errors.append(f"{item['bundle_key']}:lock_missing")
    else:
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lock = None
            errors.append(f"{item['bundle_key']}:lock_invalid_json")
        if isinstance(lock, dict):
            if lock.get("schema_version") != "0.2.0-draft":
                errors.append(f"{item['bundle_key']}:lock_version_mismatch")
            hashes = lock.get("sha256")
            if not isinstance(hashes, dict) or not hashes:
                errors.append(f"{item['bundle_key']}:lock_empty")
            else:
                for rel, expected in hashes.items():
                    path = REPO_ROOT / rel
                    if not path.exists() or _hash_if_exists(path) != expected:
                        errors.append(f"{item['bundle_key']}:lock_hash_mismatch:{rel}")
            machine = REPO_ROOT / "wiki" / "__schema__" / "entity.schema.json"
            template = REPO_ROOT / "wiki" / "__schema__" / "entity-template.md"
            if machine.exists() and template.exists():
                try:
                    schema = json.loads(machine.read_text(encoding="utf-8"))
                    if schema.get("x-template-sha256") != _hash_if_exists(template):
                        errors.append(f"{item['bundle_key']}:lock_template_mismatch")
                except (OSError, json.JSONDecodeError):
                    errors.append(f"{item['bundle_key']}:lock_schema_unreadable")
    try:
        import ingest

        bundle = ingest.load_bundle(root)
    except Exception as exc:  # noqa: BLE001 - diagnostics are part of the contract
        errors.append(f"{item['bundle_key']}:load_bundle:{exc}")
        bundle = None
    if bundle is not None:
        if manifest.get("choice_proofs_file"):
            try:
                from app.choice_proofs import load_choice_proofs

                load_choice_proofs(bundle["root"], bundle, required=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{item['bundle_key']}:choice_proofs:{exc}")
    for page in item["managed_pages"]:
        if not (REPO_ROOT / page["response"]).exists():
            errors.append(f"{page['page_key']}:response_missing")
    return errors


def fingerprint_inputs(inputs: Dict[str, Any], inputs_path: Optional[Path] = None) -> Dict[str, Any]:
    selected_inputs = Path(inputs_path or inputs.get("_inputs_path") or DEFAULT_INPUTS)
    observed: Dict[str, Any] = {"common": _common_fingerprints(selected_inputs), "inputs": {}}
    observed_inputs: Dict[str, Any] = observed["inputs"]
    for item in inputs["inputs"]:
        bundle_key = item["bundle_key"]
        root = _inside_root(REPO_ROOT / item["root"])
        manifest_path = root / "manifest.json"
        manifest_error = None
        try:
            manifest = _read_json(manifest_path) if manifest_path.exists() else {}
        except IncrementalError as exc:
            manifest_error = str(exc)
            manifest = {}
        if not isinstance(manifest, dict):
            manifest_error = manifest_error or f"manifest must be object: {bundle_key}"
            manifest = {}
        actual_sources: Dict[str, Dict[str, Optional[str]]] = {}
        for source_id, source_meta in sorted(_manifest_sources(manifest).items()):
            actual_sources[source_id] = {
                "file": source_meta["file"],
                "declared": source_meta["declared"],
                "actual": _hash_if_exists(root / source_meta["file"]),
            }
        proof_file = manifest.get("choice_proofs_file")
        proof_path = root / proof_file if isinstance(proof_file, str) and proof_file else None
        pages: Dict[str, Any] = {}
        for page in item["managed_pages"]:
            pages[page["page_key"]] = {
                "response": _hash_if_exists(REPO_ROOT / page["response"]),
                "output": _hash_if_exists(REPO_ROOT / page["output"]) if page.get("output") else None,
            }
        observed_inputs[bundle_key] = {
            "root": item["root"],
            "manifest_error": manifest_error,
            "validation_errors": ([manifest_error] if manifest_error else [])
            + _validation_errors(root, manifest, item),
            "manifest": _hash_if_exists(manifest_path),
            "manifest_data": manifest,
            "sources": actual_sources,
            "proof": _hash_if_exists(proof_path) if proof_path else None,
            "proof_declared": manifest.get("choice_proofs_sha256"),
            "profiles": {
                profile_id: _hash_if_exists(
                    REPO_ROOT / "wiki" / "__schema__" / "entities" / f"{profile_id.replace(':', '_')}.json"
                )
                for profile_id in item["profile_ids"]
            },
            "prompt": _hash_if_exists(REPO_ROOT / item["prompt"]) if item.get("prompt") else None,
            "pages": pages,
        }
    return observed


def load_baseline(path: Path = DEFAULT_STATE) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    path = Path(path)
    if not path.exists():
        return None, ["state_unavailable"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["state_corrupt"]
    if not isinstance(data, dict) or data.get("format_version") != 1:
        return None, ["state_version_unsupported"]
    if data.get("validated") is not True or data.get("project") != "b4-incremental":
        return None, ["state_not_validated"]
    if not isinstance(data.get("generation"), str) or not data["generation"]:
        return None, ["state_generation_missing"]
    if data.get("protocol") != PROTOCOL_VERSION:
        return None, ["state_protocol_mismatch"]
    if not isinstance(data.get("inputs"), dict) or "common" not in data["inputs"] or "inputs" not in data["inputs"]:
        return None, ["state_inputs_incomplete"]
    if not isinstance(data.get("graph"), dict):
        return None, ["state_graph_missing"]
    return data, []


def make_baseline(
    observed: Dict[str, Any],
    generation: str = "test-generation",
    graph: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """测试/成功构建方可使用；plan 自身绝不调用。"""
    return {
        "format_version": 1,
        "generation": generation,
        "validated": True,
        "project": "b4-incremental",
        "protocol": PROTOCOL_VERSION,
        "inputs": observed,
        "graph": graph or {"format_version": 1, "protocol": PROTOCOL_VERSION, "nodes": [], "edges": []},
    }


def plan_generation(observed: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> str:
    payload = {
        "observed": observed,
        "baseline_generation": (baseline or {}).get("generation"),
        "protocol": PROTOCOL_VERSION,
    }
    return _sha_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _diff_common(baseline_common: Dict[str, Any], observed_common: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for key in COMMON_KEYS:
        if baseline_common.get(key) != observed_common.get(key):
            events.append({"kind": "common_input_changed", "key": key})
    if baseline_common.get("protocol") != observed_common.get("protocol"):
        events.append({"kind": "protocol_changed", "key": "protocol"})
    return events


def _diff_sources(
    bundle_key: str, old: Dict[str, Any], new: Dict[str, Any], page_keys: List[str]
) -> Tuple[List[Dict[str, Any]], List[str], List[str], bool]:
    events: List[Dict[str, Any]] = []
    blocked: List[str] = []
    unknown: List[str] = []
    old_sources = old.get("sources") or {}
    new_sources = new.get("sources") or {}
    full_required = set(old_sources) != set(new_sources)
    if full_required:
        unknown.append(f"{bundle_key}:input_source_added_or_removed")
    for file_name in sorted(set(old_sources) | set(new_sources)):
        old_entry = old_sources.get(file_name) or {}
        new_entry = new_sources.get(file_name) or {}
        if old_entry.get("actual") != new_entry.get("actual"):
            events.append(
                {"kind": "source_file_changed", "bundle_key": bundle_key, "file": file_name, "page_keys": page_keys}
            )
        declared = new_entry.get("declared")
        if declared and new_entry.get("actual") != declared:
            blocked.append(f"{bundle_key}:{file_name}:raw_snapshot_hash_mismatch")
    return events, blocked, unknown, full_required


def _diff_pages(bundle_key: str, old_pages: Dict[str, Any], new_pages: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for page_key in sorted(set(old_pages) | set(new_pages)):
        old_page = old_pages.get(page_key) or {}
        new_page = new_pages.get(page_key) or {}
        if old_page.get("output") != new_page.get("output"):
            events.append({"kind": "generated_output_modified", "bundle_key": bundle_key, "page_key": page_key})
        if old_page.get("response") != new_page.get("response"):
            events.append({"kind": "response_changed", "bundle_key": bundle_key, "page_key": page_key})
    return events


def _diff_input(
    bundle_key: str, old: Dict[str, Any], new: Dict[str, Any], item: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str], List[str], bool]:
    page_keys = [page["page_key"] for page in item["managed_pages"]]
    events: List[Dict[str, Any]] = []
    blocked: List[str] = []
    if old.get("manifest") != new.get("manifest"):
        events.append({"kind": "manifest_changed", "bundle_key": bundle_key, "page_keys": page_keys})
    if old.get("proof") != new.get("proof"):
        events.append({"kind": "proof_changed", "bundle_key": bundle_key, "page_keys": page_keys})
    if old.get("proof_declared") != new.get("proof_declared") or (
        new.get("proof_declared") and new.get("proof") != new.get("proof_declared")
    ):
        blocked.append(f"{bundle_key}:proof_hash_mismatch")
    source_events, source_blocked, unknown, full_required = _diff_sources(bundle_key, old, new, page_keys)
    events.extend(source_events)
    blocked.extend(source_blocked)
    if (old.get("profiles") or {}) != (new.get("profiles") or {}):
        events.append({"kind": "profile_changed", "bundle_key": bundle_key, "page_keys": page_keys})
    if old.get("prompt") != new.get("prompt"):
        events.append({"kind": "prompt_changed", "bundle_key": bundle_key, "page_keys": page_keys})
    old_errors = old.get("validation_errors") or []
    new_errors = new.get("validation_errors") or []
    if new_errors and new_errors != old_errors:
        blocked.extend(f"{bundle_key}:validation:{error}" for error in new_errors)
        events.append({"kind": "validation_blocked", "bundle_key": bundle_key, "page_keys": page_keys})
    events.extend(_diff_pages(bundle_key, old.get("pages") or {}, new.get("pages") or {}))
    return events, blocked, unknown, full_required


def diff_fingerprints(
    baseline: Optional[Dict[str, Any]],
    observed: Dict[str, Any],
    inputs: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str], List[str], bool]:
    events: List[Dict[str, Any]] = []
    blocked: List[str] = []
    unknown: List[str] = []
    if baseline is None:
        return events, blocked, unknown, True
    baseline_inputs = (baseline.get("inputs") or {}).get("inputs") or {}
    observed_inputs = observed.get("inputs") or {}
    full_required = set(baseline_inputs) != set(observed_inputs)
    if full_required:
        unknown.append("input_set_changed")
    common_events = _diff_common((baseline.get("inputs") or {}).get("common") or {}, observed.get("common") or {})
    if common_events:
        full_required = True
        events.extend(common_events)
        for event in common_events:
            blocked.append(f"common:{event.get('key')}")
    for item in inputs["inputs"]:
        bundle_key = item["bundle_key"]
        old = baseline_inputs.get(bundle_key)
        new = observed_inputs.get(bundle_key)
        if old is None or new is None:
            full_required = True
            unknown.append(f"{bundle_key}:unmapped_input")
            continue
        item_events, item_blocked, item_unknown, item_full = _diff_input(bundle_key, old, new, item)
        events.extend(item_events)
        blocked.extend(item_blocked)
        unknown.extend(item_unknown)
        full_required = full_required or item_full
    return events, blocked, unknown, full_required


def _append_ref(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], owner: Tuple, node_type: str, value: Any
) -> None:
    if not value:
        return
    key = (node_type, str(value))
    nodes.append({"key": key, "type": node_type, "business_id": str(value)})
    edges.append({"consumer": owner, "dependency": key})


def _add_evidence(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], owner: Tuple, record: Dict[str, Any]
) -> None:
    for evidence in record.get("evidence") or []:
        if isinstance(evidence, dict) and evidence.get("source_span_id"):
            span_key = ("span", str(evidence["source_span_id"]))
            nodes.append({"key": span_key, "type": "span", "span_id": str(evidence["source_span_id"])})
            edges.append({"consumer": owner, "dependency": span_key})


def _add_refs(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], owner: Tuple, record: Dict[str, Any]) -> None:
    for identity_id in record.get("identity_version_ids") or []:
        _append_ref(nodes, edges, owner, "identity_version", identity_id)
    for proof_id in record.get("player_choice_proof_refs") or []:
        _append_ref(nodes, edges, owner, "proof", proof_id)
    for conflict_id in record.get("conflict_ids") or []:
        _append_ref(nodes, edges, owner, "conflict", conflict_id)
    story_scope = record.get("story_scope") or {}
    for edge in ("from_event_id", "to_event_id"):
        _append_ref(nodes, edges, owner, "event", story_scope.get(edge))
    version = record.get("version") or {}
    for key in ("version_id", "valid_from_label", "valid_to_label"):
        _append_ref(nodes, edges, owner, "version", version.get(key))


def _add_identity_nodes(
    page_data: Dict[str, Any], page_node: Tuple, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> None:
    for identity in page_data.get("identity_versions") or []:
        if not isinstance(identity, dict):
            continue
        owner = (page_node[1], "identity_version", str(identity.get("identity_version_id") or ""))
        nodes.append({"key": owner, "type": "identity_version", "page_key": page_node[1], "business_id": owner[2]})
        edges.append({"consumer": page_node, "dependency": owner})
        for name in identity.get("names") or []:
            if isinstance(name, dict):
                name_key = (page_node[1], "name", str(name.get("name_id") or ""))
                nodes.append({"key": name_key, "type": "name", "page_key": page_node[1], "business_id": name_key[2]})
                edges.append({"consumer": owner, "dependency": name_key})
                _add_evidence(nodes, edges, name_key, name)
                _add_refs(nodes, edges, name_key, name)
        _add_evidence(nodes, edges, owner, identity)
        _add_refs(nodes, edges, owner, identity)


def _add_alternatives(
    owner: Tuple, record: Dict[str, Any], key: str, index: int, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> None:
    for alt_index, alternative in enumerate(record.get("alternatives") or []):
        if not isinstance(alternative, dict):
            continue
        alt_key = (
            owner[0],
            "conflict_alternative",
            str(alternative.get("alternative_id") or f"/{key}/{index}/alternatives/{alt_index}"),
        )
        nodes.append({"key": alt_key, "type": "conflict_alternative", "page_key": owner[0], "business_id": alt_key[2]})
        edges.append({"consumer": owner, "dependency": alt_key})
        _add_evidence(nodes, edges, alt_key, alternative)
        _add_refs(nodes, edges, alt_key, alternative)
        proposed = alternative.get("proposed_value")
        if isinstance(proposed, dict):
            _add_evidence(nodes, edges, alt_key, proposed)
            _add_refs(nodes, edges, alt_key, proposed)


def _add_flat_records(
    page_data: Dict[str, Any], page_node: Tuple, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> None:
    definitions = (
        ("events", "event", "event_id"),
        ("relations", "relation", "relation_id"),
        ("memory_records", "memory_record", "memory_record_id"),
        ("affiliation_states", "affiliation_state", "state_id"),
        ("attribute_records", "attribute", "attribute_id"),
        ("conflicts", "conflict", "conflict_id"),
    )
    for key, record_type, business_key in definitions:
        for index, record in enumerate(page_data.get(key) or []):
            if not isinstance(record, dict):
                continue
            owner = (page_node[1], record_type, str(record.get(business_key) or f"/{key}/{index}"))
            nodes.append({"key": owner, "type": record_type, "page_key": page_node[1], "business_id": owner[2]})
            edges.append({"consumer": page_node, "dependency": owner})
            _add_evidence(nodes, edges, owner, record)
            _add_refs(nodes, edges, owner, record)
            _add_alternatives(owner, record, key, index, nodes, edges)


def _field_support_keys(page_data: Dict[str, Any], page_key: str, path: str) -> List[Tuple]:
    if path in ("/canonical_name",) or path.startswith("/aliases/"):
        return [
            (page_key, "name", str(name.get("name_id")))
            for identity in page_data.get("identity_versions") or []
            for name in identity.get("names") or []
            if isinstance(name, dict)
        ]
    if path in ("/element", "/weapon", "/nation"):
        attr_key = path.lstrip("/")
        return [
            (page_key, "attribute", str(record.get("attribute_id")))
            for record in page_data.get("attribute_records") or []
            if isinstance(record, dict) and record.get("key") == attr_key
        ]
    if path == "/affiliation":
        return [
            (page_key, "affiliation_state", str(state.get("state_id")))
            for state in page_data.get("affiliation_states") or []
            if isinstance(state, dict)
        ]
    return []


def _add_fields(
    page_data: Dict[str, Any], page_node: Tuple, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> None:
    for assertion in page_data.get("field_assertions") or []:
        if isinstance(assertion, dict) and "path" in assertion:
            field_key = (page_node[1], "field", str(assertion["path"]))
            nodes.append({"key": field_key, "type": "field", "page_key": page_node[1], "business_id": field_key[2]})
            edges.append({"consumer": page_node, "dependency": field_key})
            _add_evidence(nodes, edges, field_key, assertion)
            for support in _field_support_keys(page_data, page_node[1], str(assertion["path"])):
                edges.append({"consumer": field_key, "dependency": support})


def _resolve_local_reference_edges(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    owners: Dict[Tuple[str, str], Tuple] = {}
    for node in nodes:
        key = tuple(node["key"])
        if len(key) == 3 and key[1] in _REFERENCE_TYPES:
            owners[(key[1], key[2])] = key
    resolved = []
    for edge in edges:
        dependency = tuple(edge["dependency"])
        if len(dependency) == 2 and dependency[0] in _REFERENCE_TYPES:
            match = owners.get((dependency[0], dependency[1]))
            if match is not None:
                edge = dict(edge, dependency=match)
        resolved.append(edge)
    return resolved


def _record_keys(page_data: Dict[str, Any], page_key: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    page_node = ("page", page_key)
    nodes.append({"key": page_node, "type": "page", "page_key": page_key})
    _add_identity_nodes(page_data, page_node, nodes, edges)
    _add_flat_records(page_data, page_node, nodes, edges)
    _add_fields(page_data, page_node, nodes, edges)
    return nodes, _resolve_local_reference_edges(nodes, edges)


def _collect_source_nodes(
    manifest: Dict[str, Any], bundle_key: str, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    for source in manifest.get("sources") or []:
        source_id = source.get("source_id") or source.get("file")
        source_key = ("source", str(source_id))
        nodes.append({"key": source_key, "type": "source", "source_id": str(source_id)})
        edges.append({"consumer": ("bundle", bundle_key), "dependency": source_key})
        for anchor in sorted((source.get("paragraphs") or {}).keys()):
            paragraph_key = ("paragraph", str(source_id), anchor)
            nodes.append({"key": paragraph_key, "type": "paragraph", "source_id": str(source_id), "anchor": anchor})
            edges.append({"consumer": paragraph_key, "dependency": source_key})
    return nodes, edges


def _load_page_data(path: Path, page_key: str, diagnostics: List[str]) -> Dict[str, Any]:
    try:
        import ingest

        data, _ = ingest.parse_page(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 - diagnostics are part of the contract
        diagnostics.append(f"{page_key}:response_unavailable:{exc}")
        return {}


def _dedup(items: List[Dict[str, Any]], key_field: str) -> List[Dict[str, Any]]:
    result: Dict[Any, Dict[str, Any]] = {}
    for item in items:
        result[tuple(item[key_field])] = item
    return [result[key] for key in sorted(result, key=str)]


def _dedup_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: Dict[Tuple, Dict[str, Any]] = {}
    for edge in edges:
        result[(tuple(edge["consumer"]), tuple(edge["dependency"]))] = edge
    return [result[key] for key in sorted(result, key=str)]


def _span_paragraph_edges(
    inputs: Dict[str, Any], nodes: List[Dict[str, Any]], diagnostics: List[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    edges: List[Dict[str, Any]] = []
    for item in inputs["inputs"]:
        root = _inside_root(REPO_ROOT / item["root"])
        try:
            import ingest

            bundle = ingest.load_bundle(root)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(f"{item['bundle_key']}:bundle_unavailable:{exc}")
            continue
        manifest_path = root / "manifest.json"
        try:
            manifest = _read_json(manifest_path) if manifest_path.exists() else {}
        except IncrementalError as exc:
            diagnostics.append(f"{item['bundle_key']}:manifest_unavailable:{exc}")
            manifest = {}
        anchors_by_source = {
            source.get("source_id"): sorted((source.get("paragraphs") or {}).keys())
            for source in manifest.get("sources") or []
            if isinstance(source, dict)
        }
        counters = {}
        for paragraph in bundle.get("paragraphs") or []:
            source_id = paragraph["source_id"]
            position = counters.get(source_id, 0)
            counters[source_id] = position + 1
            anchors = anchors_by_source.get(source_id) or []
            anchor = anchors[position] if position < len(anchors) else f"p{position + 1:03d}"
            paragraph_key = ("paragraph", source_id, anchor)
            span_key = ("span", paragraph["span_id"])
            nodes.append({"key": span_key, "type": "span", "span_id": paragraph["span_id"]})
            edges.append({"consumer": span_key, "dependency": paragraph_key})
    return nodes, edges


_REFERENCE_TYPES = {
    "event",
    "identity_version",
    "name",
    "attribute",
    "affiliation_state",
    "conflict",
    "memory_record",
    "relation",
}


def _resolve_reference_edges(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], diagnostics: List[str]
) -> List[Dict[str, Any]]:
    owners: Dict[Tuple[str, str], List[Tuple]] = {}
    for node in nodes:
        key = tuple(node["key"])
        if len(key) == 3 and key[1] in _REFERENCE_TYPES:
            owners.setdefault((key[1], key[2]), []).append(key)
    resolved = []
    for edge in edges:
        dependency = tuple(edge["dependency"])
        if len(dependency) == 2 and dependency[0] in _REFERENCE_TYPES:
            matches = owners.get((dependency[0], dependency[1])) or []
            if len(matches) == 1:
                edge = dict(edge, dependency=matches[0])
            elif not matches:
                diagnostics.append(f"unresolved_reference:{dependency}")
            else:
                diagnostics.append(f"ambiguous_reference:{dependency}")
        resolved.append(edge)
    return resolved


def collect_dependencies(inputs: Dict[str, Any]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = [{"key": ("index", "canonical"), "type": "index"}]
    edges: List[Dict[str, Any]] = []
    diagnostics: List[str] = []
    for item in inputs["inputs"]:
        bundle_key = item["bundle_key"]
        root = _inside_root(REPO_ROOT / item["root"])
        nodes.append({"key": ("bundle", bundle_key), "type": "bundle", "bundle_key": bundle_key})
        try:
            manifest = _read_json(root / "manifest.json")
        except IncrementalError as exc:
            diagnostics.append(f"{bundle_key}:manifest_unavailable:{exc}")
            manifest = {}
        nodes, edges = _collect_source_nodes(manifest, bundle_key, nodes, edges)
        for page in item["managed_pages"]:
            page_node = ("page", page["page_key"])
            nodes.append(
                {"key": page_node, "type": "page", "page_key": page["page_key"], "entity_id": page.get("entity_id")}
            )
            edges.append({"consumer": page_node, "dependency": ("bundle", bundle_key)})
            edges.append({"consumer": ("index", "canonical"), "dependency": page_node})
            page_data = _load_page_data(REPO_ROOT / page["response"], page["page_key"], diagnostics)
            page_nodes, page_edges = _record_keys(page_data, page["page_key"])
            nodes.extend(page_nodes)
            edges.extend(page_edges)
    nodes, span_edges = _span_paragraph_edges(inputs, nodes, diagnostics)
    edges.extend(span_edges)
    edges = _resolve_reference_edges(nodes, edges, diagnostics)
    return {
        "format_version": 1,
        "protocol": PROTOCOL_VERSION,
        "nodes": _dedup(nodes, "key"),
        "edges": _dedup_edges(edges),
        "diagnostics": diagnostics,
    }


def _reverse_adjacency(graph: Dict[str, Any]) -> Dict[Tuple, List[Tuple]]:
    reverse: Dict[Tuple, List[Tuple]] = {}
    for edge in graph.get("edges") or []:
        reverse.setdefault(tuple(edge["dependency"]), []).append(tuple(edge["consumer"]))
    return reverse


def _propagate(queue: List[Tuple[Tuple, str, int]], reverse: Dict[Tuple, List[Tuple]], add_page: Any) -> bool:
    visited: set = set()
    full_required = False
    while queue:
        node, origin, depth = queue.pop(0)
        marker = (node, origin)
        if marker in visited:
            continue
        visited.add(marker)
        if depth > 32:
            full_required = True
            break
        for consumer in reverse.get(node, []):
            if consumer[0] == "page":
                add_page(consumer[1], origin)
            queue.append((consumer, origin, depth + 1))
    return full_required


def mark_dirty(
    events: List[Dict[str, Any]],
    inputs: Dict[str, Any],
    graph: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, List[str]], List[str], bool]:
    dirty: Dict[str, List[str]] = {}
    full_required = False
    managed = {item["bundle_key"]: [page["page_key"] for page in item["managed_pages"]] for item in inputs["inputs"]}
    reverse = _reverse_adjacency(graph) if graph else {}
    queue: List[Tuple[Tuple, str, int]] = []

    def add_page(page_key: str, reason: str) -> None:
        dirty.setdefault(page_key, [])
        if reason not in dirty[page_key]:
            dirty[page_key].append(reason)

    page_kinds = {
        "source_file_changed",
        "manifest_changed",
        "proof_changed",
        "generated_output_modified",
        "response_changed",
        "validation_blocked",
    }
    for event in events:
        kind = event.get("kind")
        if kind in page_kinds:
            for page_key in event.get("page_keys") or managed.get(event.get("bundle_key"), []):
                add_page(page_key, kind)
                queue.append((("page", page_key), kind, 0))
        elif kind in ("profile_changed", "prompt_changed"):
            for page_key in event.get("page_keys") or []:
                add_page(page_key, kind)
                queue.append((("page", page_key), kind, 0))
        else:
            full_required = True
    full_required = _propagate(queue, reverse, add_page) or full_required
    return dirty, [], full_required


def compute_freshness(
    inputs: Dict[str, Any],
    observed: Dict[str, Any],
    baseline: Optional[Dict[str, Any]],
    dirty: Dict[str, List[str]],
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    baseline_inputs = ((baseline or {}).get("inputs") or {}).get("inputs") or {}
    observed_inputs = observed.get("inputs") or {}
    for item in inputs["inputs"]:
        base = baseline_inputs.get(item["bundle_key"])
        current = observed_inputs.get(item["bundle_key"]) or {}
        for page in item["managed_pages"]:
            page_key = page["page_key"]
            if base is None:
                result[page_key] = "unknown"
                continue
            old_page = (base.get("pages") or {}).get(page_key) or {}
            new_page = (current.get("pages") or {}).get(page_key) or {}
            if (
                not old_page.get("output")
                or not old_page.get("response")
                or not new_page.get("output")
                or not new_page.get("response")
            ):
                result[page_key] = "unknown"
                continue
            changed = (
                page_key in dirty
                or old_page.get("output") != new_page.get("output")
                or old_page.get("response") != new_page.get("response")
            )
            result[page_key] = "stale" if changed else "fresh"
    return result


def _field_impacts(graph: Optional[Dict[str, Any]], dirty: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    if not dirty or not graph:
        return {}
    fields_by_page: Dict[str, List[str]] = {}
    for node in graph.get("nodes") or []:
        if node.get("type") == "field" and node.get("page_key"):
            fields_by_page.setdefault(node["page_key"], []).append(str(node.get("business_id")))
    result: Dict[str, List[Dict[str, Any]]] = {}
    for page_key, reasons in dirty.items():
        result[page_key] = [
            {
                "field": field_path,
                "reasons": reasons,
                "action": "evidence_rebind" if "source_file_changed" in reasons else "validation",
            }
            for field_path in sorted(fields_by_page.get(page_key, []))
        ]
    return result


def _merge_graphs(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(old, dict) or old.get("protocol") != PROTOCOL_VERSION:
        if old:
            new = dict(new)
            new["diagnostics"] = sorted(set((new.get("diagnostics") or []) + ["old_graph_unbound"]))
        return new
    nodes = {tuple(node["key"]): node for node in old.get("nodes") or []}
    for node in new.get("nodes") or []:
        nodes[tuple(node["key"])] = node
    edges: Dict[Tuple, Dict[str, Any]] = {}
    for edge in (old.get("edges") or []) + (new.get("edges") or []):
        edges[(tuple(edge["consumer"]), tuple(edge["dependency"]))] = edge
    return {
        "format_version": 1,
        "protocol": PROTOCOL_VERSION,
        "nodes": [nodes[key] for key in sorted(nodes, key=str)],
        "edges": [edges[key] for key in sorted(edges, key=str)],
        "diagnostics": sorted(set((old.get("diagnostics") or []) + (new.get("diagnostics") or []))),
    }


def build_plan(
    inputs: Optional[Dict[str, Any]] = None,
    state_path: Path = DEFAULT_STATE,
) -> Dict[str, Any]:
    inputs = inputs or load_inputs()
    observed = fingerprint_inputs(inputs)
    baseline, baseline_errors = load_baseline(state_path)
    not_implemented = ["rebuild", "runtime_stale"]
    if baseline is None:
        current_errors = [
            f"{bundle_key}:validation:{error}"
            for bundle_key, entry in (observed.get("inputs") or {}).items()
            for error in entry.get("validation_errors") or []
        ]
        return {
            "format_version": 1,
            "generation": plan_generation(observed, None),
            "full_required": True,
            "unknown": baseline_errors or ["state_unavailable"],
            "blocked": sorted(set(current_errors)),
            "dirty": {},
            "observed": observed,
            "not_implemented": not_implemented,
            "freshness": {page["page_key"]: "unknown" for item in inputs["inputs"] for page in item["managed_pages"]},
        }
    events, blocked, unknown, full_required = diff_fingerprints(baseline, observed, inputs)
    graph = _merge_graphs((baseline or {}).get("graph"), collect_dependencies(inputs))
    dirty, _mark_blocked, mark_full = mark_dirty(events, inputs, graph)
    full_required = full_required or mark_full
    freshness = compute_freshness(inputs, observed, baseline, dirty)
    if full_required:
        freshness = {key: "stale" for key in freshness}
    graph_diagnostics = graph.get("diagnostics") or []
    unknown = sorted(set(unknown + [f"graph:{item}" for item in graph_diagnostics]))
    return {
        "format_version": 1,
        "generation": plan_generation(observed, baseline),
        "full_required": full_required,
        "unknown": unknown,
        "blocked": sorted(set(blocked)),
        "dirty": {key: sorted(set(value)) for key, value in sorted(dirty.items())},
        "observed": observed,
        "not_implemented": not_implemented,
        "freshness": freshness,
        "events": events,
        "field_impacts": _field_impacts(graph, dirty),
        "graph_diagnostics": graph_diagnostics,
    }


def explain_page(plan: Dict[str, Any], page_key: str) -> Dict[str, Any]:
    result = {
        "page_key": page_key,
        "dirty": (plan.get("dirty") or {}).get(page_key, []),
        "freshness": (plan.get("freshness") or {}).get(page_key, "unknown"),
        "full_required": bool(plan.get("full_required")),
        "blocked": list(plan.get("blocked") or []),
        "not_implemented": list(plan.get("not_implemented") or []),
    }
    impacts = (plan.get("field_impacts") or {}).get(page_key)
    if impacts:
        result["field_impacts"] = impacts
    if plan.get("snapshot_only"):
        result["snapshot_only"] = True
    if plan.get("plan_outdated"):
        result["plan_outdated"] = True
    return result


def _derived_root() -> Path:
    return (REPO_ROOT / "reports" / "incremental").resolve()


def _protected_paths(inputs: Dict[str, Any], state_path: Path, inputs_path: Path) -> set:
    protected = {Path(inputs_path).resolve(), Path(state_path).resolve()}
    protected.add((REPO_ROOT / "wiki" / "__schema__" / "entity.schema.json").resolve())
    protected.add((REPO_ROOT / "wiki" / "__schema__" / "entity-template.md").resolve())
    protected.add((REPO_ROOT / "wiki" / "__schema__" / "source-span-format.md").resolve())
    protected.add((REPO_ROOT / "wiki" / "__schema__" / "version-registry.json").resolve())
    protected.add((REPO_ROOT / "docs" / "schema-lock.json").resolve())
    for item in inputs["inputs"]:
        root = _inside_root(REPO_ROOT / item["root"])
        protected.add((root / "manifest.json").resolve())
        manifest_data: Dict[str, Any] = {}
        try:
            manifest_data = _read_json(root / "manifest.json") if (root / "manifest.json").exists() else {}
        except IncrementalError:
            manifest_data = {}
        for source_meta in _manifest_sources(manifest_data).values():
            protected.add((root / source_meta["file"]).resolve())
        proof_file = manifest_data.get("choice_proofs_file")
        if isinstance(proof_file, str) and proof_file:
            protected.add((root / proof_file).resolve())
        for page in item["managed_pages"]:
            protected.add((REPO_ROOT / page["response"]).resolve())
            if page.get("output"):
                protected.add((REPO_ROOT / page["output"]).resolve())
        if item.get("prompt"):
            protected.add((REPO_ROOT / item["prompt"]).resolve())
        for profile_id in item["profile_ids"]:
            protected.add(
                (REPO_ROOT / "wiki" / "__schema__" / "entities" / f"{profile_id.replace(':', '_')}.json").resolve()
            )
    return protected


def _ensure_derived_output(path: Path, protected: set, siblings: List[Path]) -> Path:
    resolved = Path(path).resolve()
    root = _derived_root()
    if resolved != root and root not in resolved.parents:
        raise IncrementalError(f"derived output must stay under {root}: {resolved}")
    if resolved in protected:
        raise IncrementalError(f"derived output overlaps protected input: {resolved}")
    for sibling in siblings:
        if resolved == Path(sibling).resolve():
            raise IncrementalError(f"derived outputs must not overlap: {resolved}")
    return resolved


def _ensure_read_path(path: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    root = REPO_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise IncrementalError(f"{label} must stay inside project: {resolved}")
    return resolved


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.incremental")
    parser.add_argument("command", choices=["plan", "explain", "graph"])
    parser.add_argument("--inputs", default=str(DEFAULT_INPUTS))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH))
    parser.add_argument("--page")
    parser.add_argument("--snapshot-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        inputs_path = _ensure_read_path(Path(args.inputs), "inputs")
        state_path = _ensure_read_path(Path(args.state), "state")
        inputs = load_inputs(inputs_path)
        protected = _protected_paths(inputs, state_path, inputs_path)
        plan_path = _ensure_derived_output(Path(args.plan), protected, [Path(args.graph)])
        graph_path = _ensure_derived_output(Path(args.graph), protected, [plan_path])
        if args.command == "graph":
            graph = collect_dependencies(inputs)
            _write_json(graph_path, graph)
            print(
                json.dumps(
                    {"graph": str(graph_path), "nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "plan":
            plan = build_plan(inputs, state_path)
            _write_json(plan_path, plan)
            _write_json(graph_path, collect_dependencies(inputs))
            print(
                json.dumps(
                    {"plan": str(plan_path), "full_required": plan["full_required"], "dirty": len(plan["dirty"])},
                    ensure_ascii=False,
                )
            )
            return 0
        current_plan = build_plan(inputs, state_path)
        if args.snapshot_only and plan_path.exists():
            plan = _read_json(plan_path)
            plan["snapshot_only"] = True
            if plan.get("generation") != current_plan.get("generation"):
                plan["plan_outdated"] = True
        else:
            plan = current_plan
        if not args.page:
            print(json.dumps(plan.get("freshness") or {}, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(explain_page(plan, args.page), ensure_ascii=False, indent=2))
        return 0
    except IncrementalError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

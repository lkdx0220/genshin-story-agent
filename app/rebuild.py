# -*- coding: utf-8 -*-
"""B4-02 B202：单页重建提交事务（journal / 锁 / CAS / 恢复）。"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app import incremental as inc

EXECUTION_PROTOCOL = "b4-02-commit-v1"
REQUIRED_JOURNAL_FIELDS = (
    "state",
    "output",
    "page_key",
    "plan_generation",
    "parent_generation",
    "old_state_sha256",
    "new_state_sha256",
    "old_output_sha256",
    "new_output_sha256",
    "output_old_exists",
    "backup",
    "provenance",
    "provenance_sha256",
)


class RebuildError(Exception):
    """单页提交事务失败。"""


def _root() -> Path:
    return inc.REPO_ROOT


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_json(payload: Any) -> str:
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _hash_bytes(path: Path) -> Optional[str]:
    try:
        return _sha_bytes(path.read_bytes())
    except (OSError, PermissionError):
        return None


def _state_digest(path: Path) -> Optional[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _sha_json(payload)


def _provenance_digest(directory: Path) -> Optional[str]:
    if not directory or not directory.exists():
        return None
    items = []
    try:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                items.append((str(path.relative_to(directory)), _sha_bytes(path.read_bytes())))
    except (OSError, PermissionError):
        return None
    return _sha_json(items)


def _journal_dir() -> Path:
    return _root() / "reports" / "incremental" / "journal"


def _backup_dir(txid: str) -> Path:
    return _root() / "reports" / "incremental" / "backups" / txid


def _staging_dir(txid: str) -> Path:
    return _root() / "reports" / "incremental" / "staging" / txid


def _provenance_dir(txid: str) -> Path:
    return _root() / "reports" / "incremental" / "provenance" / txid


def _lock_path() -> Path:
    return _root() / "reports" / "incremental" / "commit.lock"


def _journal_path(txid: str) -> Path:
    return _journal_dir() / f"{txid}.json"


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(_root().resolve()))


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _read_journal(txid: str) -> Optional[Dict[str, Any]]:
    path = _journal_path(txid)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "recovery_required", "corrupt": True}
    return payload if isinstance(payload, dict) else {"status": "recovery_required", "corrupt": True}


def _write_journal(txid: str, status: str, **fields: Any) -> None:
    existing = _read_journal(txid) or {}
    payload = dict(existing)
    payload.update(fields)
    payload["format_version"] = 1
    payload["transaction_id"] = txid
    payload["status"] = status
    _atomic_write_json(_journal_path(txid), payload)


def _journal_list() -> Tuple[list, Optional[str]]:
    directory = _journal_dir()
    if not directory.exists():
        return [], None
    try:
        return sorted(path.name for path in directory.iterdir() if path.is_file()), None
    except (OSError, NotADirectoryError, PermissionError) as exc:
        return [], str(exc)


def _acquire_lock(txid: str) -> Tuple[Optional[int], Optional[dict]]:
    path = _lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, {"status": "lock_error", "errors": [f"lock_parent:{exc}"]}
    handle: Optional[int] = None
    try:
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, json.dumps({"transaction_id": txid, "pid": os.getpid()}).encode("utf-8"))
        return handle, None
    except FileExistsError:
        if handle is not None:
            os.close(handle)
        return None, {"status": "lock_busy", "transaction_id": txid}
    except OSError as exc:
        if handle is not None:
            try:
                os.close(handle)
            except OSError:
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None, {"status": "lock_error", "errors": [f"lock_acquire:{exc}"]}


def _release_lock(handle: Optional[int]) -> Optional[str]:
    try:
        if handle is not None:
            os.close(handle)
    except OSError as exc:
        return f"lock_close:{exc}"
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError as exc:
        return f"lock_unlink:{exc}"
    return None


def _transaction_id(
    plan_generation: str,
    parent_generation: str,
    page_key: str,
    candidate_hash: str,
    response_path: str,
    output_path: str,
) -> str:
    return _sha_json(
        {
            "protocol": EXECUTION_PROTOCOL,
            "project_root": str(_root().resolve()),
            "plan_generation": plan_generation,
            "parent_generation": parent_generation,
            "page_key": page_key,
            "candidate_sha256": candidate_hash,
            "response": response_path,
            "output": output_path,
        }
    )


def _prepare_paths(plan_path, state_path, inputs_path, candidate_manifest_path):
    try:
        plan_path = inc._ensure_file_inside(plan_path, "plan")
        inputs_path = inc._ensure_file_inside(inputs_path, "inputs")
        state_path = inc._ensure_read_path(Path(state_path), "state")
        if candidate_manifest_path is not None:
            candidate_manifest_path = inc._ensure_file_inside(candidate_manifest_path, "candidate_manifest")
    except inc.IncrementalError as exc:
        return None, {"status": "rejected", "errors": [str(exc)]}
    return {
        "plan_path": plan_path,
        "state_path": Path(state_path),
        "inputs_path": inputs_path,
        "candidate_manifest_path": candidate_manifest_path,
    }, None


def _validate_request(paths):
    try:
        inputs = inc.load_inputs(paths["inputs_path"])
    except inc.IncrementalError as exc:
        return None, {"status": "rejected", "errors": [f"inputs_invalid:{exc}"]}
    output_errors = inc._validate_registered_outputs(
        inputs,
        paths["inputs_path"],
        paths["state_path"],
        paths["plan_path"],
        paths["candidate_manifest_path"],
    )
    if output_errors:
        return None, {"status": "rejected", "errors": output_errors}
    try:
        plan_data = json.loads(paths["plan_path"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, {"status": "rejected", "errors": [f"plan_unreadable:{exc}"]}
    if not isinstance(plan_data, dict) or plan_data.get("format_version") != 1:
        return None, {"status": "rejected", "errors": ["plan_invalid"]}
    if plan_data.get("execution_protocol") != inc.EXECUTION_PROTOCOL:
        return None, {"status": "rejected", "errors": ["plan_execution_protocol_mismatch"]}
    candidates, errors = inc._load_candidate_manifest(paths["candidate_manifest_path"])
    if errors or len(candidates) != 1:
        return None, {"status": "rejected", "errors": errors or ["candidate_manifest_invalid"]}
    candidate = candidates[0]
    try:
        candidate_path = inc._ensure_file_inside(_root() / candidate["response"], "candidate")
    except inc.IncrementalError as exc:
        return None, {"status": "rejected", "errors": [str(exc)]}
    candidate_hash = _hash_bytes(candidate_path)
    if candidate_hash is None:
        return None, {"status": "rejected", "errors": ["candidate_unreadable"]}
    registry = inc._page_registry(inputs)
    registered = registry.get(candidate["page_key"])
    if registered is None:
        return None, {"status": "rejected", "errors": ["candidate_unknown_page"]}
    output_path = inc._inside_root(_root() / registered["output"])
    return {
        "inputs": inputs,
        "plan_data": plan_data,
        "candidate": candidate,
        "candidate_path": candidate_path,
        "candidate_hash": candidate_hash,
        "registry": registry,
        "registered": registered,
        "output_path": output_path,
    }, None


def _freeze_candidate(ctx, txid):
    staging_dir = _staging_dir(txid)
    staging_dir.mkdir(parents=True, exist_ok=True)
    frozen = staging_dir / "candidate.md"
    shutil.copy2(ctx["candidate_path"], frozen)
    if _hash_bytes(frozen) != ctx["candidate_hash"]:
        return None, {"status": "rejected", "errors": ["candidate_binding_mismatch"]}
    ctx["frozen_candidate"] = frozen
    ctx["staging_dir"] = staging_dir
    return ctx, None


def _stage_candidate(page_key, registered, candidate_path, staging_dir, txid):
    import ingest

    root = _root() / registered["root"]
    staged_output = staging_dir / "page.md"
    text, bundle = ingest.ingest(
        root,
        _root() / "wiki" / "__schema__" / "entity-template.md",
        response_file=candidate_path,
        provider="response-file",
        output=staged_output,
        prompt_path=_root() / registered["prompt"] if registered.get("prompt") else None,
        expected_entity_id=registered.get("entity_id"),
    )
    data, _ = ingest.parse_page(text)
    if registered.get("entity_id") and data.get("entity_id") != registered.get("entity_id"):
        raise RebuildError("staged entity_id mismatch")
    if data.get("data_mode") and data.get("data_mode") != registered.get("data_mode"):
        raise RebuildError("staged data_mode mismatch")
    if data.get("page_status") in ("accepted", "published"):
        raise RebuildError("staged page_status is not draft")
    if ingest.lint(text, bundle) != [f"L{i}" for i in range(1, 9)]:
        raise RebuildError("staged independent lint failed")
    reports = sorted(staging_dir.glob("_provenance/*/ingest-report.json"))
    if not reports:
        raise RebuildError("staged provenance missing")
    provenance_dir = reports[0].parent
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    if report.get("publication") != "draft_only":
        raise RebuildError("staged publication is not draft_only")
    report["provenance"] = str(_provenance_dir(txid).resolve())
    reports[0].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "text": text,
        "output_sha256": _sha_bytes(text.encode("utf-8")),
        "provenance_staging": provenance_dir,
        "provenance": _provenance_dir(txid),
        "provenance_sha256": _provenance_digest(provenance_dir),
    }


def _prepare_commit(plan_path, state_path, inputs_path, candidate_manifest_path):
    paths, error = _prepare_paths(plan_path, state_path, inputs_path, candidate_manifest_path)
    if error:
        return None, error
    request, error = _validate_request(paths)
    if error:
        return None, error
    ctx = dict(paths)
    ctx.update(request)
    txid = _transaction_id(
        ctx["plan_data"].get("generation"),
        ctx["plan_data"].get("baseline_generation"),
        ctx["candidate"]["page_key"],
        ctx["candidate_hash"],
        _relative(ctx["candidate_path"]),
        _relative(ctx["output_path"]),
    )
    ctx["txid"] = txid
    ctx, error = _freeze_candidate(ctx, txid)
    if error:
        return None, error
    try:
        ctx["staged"] = _stage_candidate(
            ctx["candidate"]["page_key"],
            ctx["registered"],
            ctx["frozen_candidate"],
            ctx["staging_dir"],
            txid,
        )
    except (RebuildError, inc.IncrementalError, OSError) as exc:
        return None, {"status": "failed", "transaction_id": txid, "errors": [f"staging:{exc}"]}
    return ctx, None


def _observed_response_hash(observed, bundle_key, page_key):
    entry = (observed.get("inputs") or {}).get(bundle_key) or {}
    return ((entry.get("pages") or {}).get(page_key) or {}).get("response")


def _cas_check(ctx):
    current_observed = inc.fingerprint_inputs(ctx["inputs"])
    if current_observed != ctx["plan_data"].get("observed"):
        return None, {"status": "plan_outdated", "errors": ["observed_fingerprint_mismatch"]}
    precheck = inc.precheck_rebuild(
        ctx["plan_path"], ctx["state_path"], ctx["inputs_path"], ctx["candidate_manifest_path"]
    )
    if precheck.get("action") != "single_page" or precheck.get("build_set") != [ctx["candidate"]["page_key"]]:
        return None, {"status": "rejected", "precheck": precheck}
    baseline, errors = inc.load_baseline(ctx["state_path"])
    if baseline is None:
        return None, {"status": "rejected", "errors": errors or ["state_unavailable"]}
    if baseline.get("generation") != ctx["plan_data"].get("baseline_generation"):
        return None, {"status": "plan_outdated", "errors": ["baseline_generation_mismatch"]}
    if baseline.get("generation") != precheck.get("baseline_generation"):
        return None, {"status": "plan_outdated", "errors": ["baseline_generation_mismatch"]}
    observed_hash = _observed_response_hash(
        current_observed, ctx["registered"]["bundle_key"], ctx["candidate"]["page_key"]
    )
    if observed_hash != ctx["candidate_hash"]:
        return None, {"status": "rejected", "errors": ["candidate_binding_mismatch"]}
    if _hash_bytes(ctx["candidate_path"]) != ctx["candidate_hash"]:
        return None, {"status": "rejected", "errors": ["candidate_binding_mismatch"]}
    return {"baseline": baseline, "current_observed": current_observed}, None


def _observed_with_output(observed, page_key, output_sha256):
    payload = json.loads(json.dumps(observed, ensure_ascii=False))
    for entry in (payload.get("inputs") or {}).values():
        if page_key in (entry.get("pages") or {}):
            entry["pages"][page_key]["output"] = output_sha256
            return payload
    raise RebuildError("page not found in observed snapshot")


def _build_state(ctx, baseline, current_observed):
    new_state = inc.make_baseline(
        _observed_with_output(current_observed, ctx["candidate"]["page_key"], ctx["staged"]["output_sha256"]),
        generation=_sha_json(
            {
                "parent": baseline.get("generation"),
                "transaction_id": ctx["txid"],
                "output": ctx["staged"]["output_sha256"],
            }
        ),
        graph=inc.collect_dependencies(ctx["inputs"]),
    )
    entry = {
        "response": ctx["candidate_hash"],
        "response_path": _relative(ctx["candidate_path"]),
        "output": ctx["staged"]["output_sha256"],
        "output_path": _relative(ctx["output_path"]),
        "provenance": _relative(ctx["staged"]["provenance"]),
        "provenance_sha256": ctx["staged"]["provenance_sha256"],
    }
    new_state["build"] = {
        "parent_generation": baseline.get("generation"),
        "plan_generation": ctx["plan_data"].get("generation"),
        "transaction_id": ctx["txid"],
        "mode": "response-file",
        "status": "committed",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "pages": {ctx["candidate"]["page_key"]: entry},
    }
    entry["generation"] = new_state["generation"]
    return new_state


def _cleanup_transaction(txid, backup_path, staging_dir):
    _journal_path(txid).unlink(missing_ok=True)
    if backup_path is not None:
        backup_path.unlink(missing_ok=True)
    shutil.rmtree(staging_dir, ignore_errors=True)


def _prepared_payload(ctx, baseline, new_state, backup_path, old_exists):
    return {
        "page_key": ctx["candidate"]["page_key"],
        "state": _relative(ctx["state_path"]),
        "output": _relative(ctx["output_path"]),
        "plan_generation": ctx["plan_data"].get("generation"),
        "parent_generation": baseline.get("generation"),
        "old_state_sha256": _state_digest(ctx["state_path"]),
        "new_state_sha256": _sha_json(new_state),
        "old_output_sha256": _hash_bytes(ctx["output_path"]),
        "new_output_sha256": ctx["staged"]["output_sha256"],
        "output_old_exists": old_exists,
        "backup": _relative(backup_path) if old_exists else None,
        "provenance": _relative(ctx["staged"]["provenance"]),
        "provenance_sha256": ctx["staged"]["provenance_sha256"],
    }


def _restore_output(output_path, backup_path, old_exists):
    if old_exists and backup_path is not None and backup_path.exists():
        temporary = output_path.with_name(f".{output_path.name}.restore.tmp")
        shutil.copy2(backup_path, temporary)
        os.replace(temporary, output_path)
    else:
        output_path.unlink(missing_ok=True)


def _publish(ctx, baseline, new_state):
    output_path = ctx["output_path"]
    backup_path = _backup_dir(ctx["txid"]) / output_path.name
    old_exists = output_path.exists()
    if old_exists:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, backup_path)
    payload = _prepared_payload(ctx, baseline, new_state, backup_path, old_exists)
    state_replaced = False
    try:
        _write_journal(ctx["txid"], "prepared", **payload)
        ctx["staged"]["provenance"].parent.mkdir(parents=True, exist_ok=True)
        if ctx["staged"]["provenance"].exists():
            raise RebuildError("provenance target already exists")
        shutil.move(str(ctx["staged"]["provenance_staging"]), str(ctx["staged"]["provenance"]))
        if _provenance_digest(ctx["staged"]["provenance"]) != ctx["staged"]["provenance_sha256"]:
            raise RebuildError("provenance hash mismatch")
        target_tmp = output_path.with_name(f".{output_path.name}.{ctx['txid']}.tmp")
        target_tmp.write_bytes(ctx["staged"]["text"].encode("utf-8"))
        os.replace(target_tmp, output_path)
        _write_journal(ctx["txid"], "output_replaced")
        state_tmp = ctx["state_path"].with_name(f".{ctx['state_path'].name}.{ctx['txid']}.tmp")
        state_tmp.write_text(json.dumps(new_state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(state_tmp, ctx["state_path"])
        state_replaced = True
        _write_journal(ctx["txid"], "committed")
        try:
            _cleanup_transaction(ctx["txid"], backup_path if old_exists else None, ctx["staging_dir"])
        except OSError as exc:
            return {
                "status": "committed_cleanup_pending",
                "transaction_id": ctx["txid"],
                "warnings": [f"cleanup:{exc}"],
            }
        return {"status": "committed", "transaction_id": ctx["txid"], "generation": new_state["generation"]}
    except Exception as exc:
        return _finalize_failure(ctx, payload, backup_path, old_exists, state_replaced, exc)


def _finalize_failure(ctx, payload, backup_path, old_exists, state_replaced, exc):
    output_path = ctx["output_path"]
    state_path = ctx["state_path"]
    actual_state = _state_digest(state_path)
    actual_output = _hash_bytes(output_path)
    if state_replaced or (
        actual_state == payload["new_state_sha256"] and actual_output == payload["new_output_sha256"]
    ):
        try:
            _write_journal(ctx["txid"], "committed", error=str(exc))
        except OSError:
            pass
        return {
            "status": "committed_cleanup_pending",
            "transaction_id": ctx["txid"],
            "warnings": [f"post_commit:{exc}"],
        }
    if actual_state == payload["old_state_sha256"] and actual_output == payload["new_output_sha256"]:
        try:
            _restore_output(output_path, backup_path, old_exists)
        except OSError as rollback_error:
            try:
                _write_journal(ctx["txid"], "recovery_required", error=str(exc), rollback_error=str(rollback_error))
            except OSError:
                pass
            return {
                "status": "recovery_required",
                "transaction_id": ctx["txid"],
                "errors": [str(exc), f"rollback:{rollback_error}"],
            }
        try:
            _cleanup_transaction(ctx["txid"], backup_path if old_exists else None, ctx["staging_dir"])
        except OSError:
            pass
        return {"status": "failed", "transaction_id": ctx["txid"], "errors": [str(exc)], "recovery": "rolled_back"}
    if actual_state == payload["old_state_sha256"] and actual_output == payload["old_output_sha256"]:
        try:
            _journal_path(ctx["txid"]).unlink(missing_ok=True)
            _discard_staging(ctx)
            if backup_path is not None:
                backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"status": "failed", "transaction_id": ctx["txid"], "errors": [str(exc)]}
    try:
        _write_journal(ctx["txid"], "recovery_required", error=str(exc))
    except OSError:
        pass
    return {"status": "recovery_required", "transaction_id": ctx["txid"], "errors": [str(exc)]}


def _resolve_one_journal(name: str) -> Optional[dict]:
    path = _journal_dir() / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "recovery_required", "errors": [f"journal_corrupt:{name}"]}
    if not isinstance(payload, dict) or not set(REQUIRED_JOURNAL_FIELDS) <= set(payload):
        return {"status": "recovery_required", "errors": [f"journal_incomplete:{name}"]}
    state_path = _root() / payload["state"]
    output_path = _root() / payload["output"]
    backup = _root() / payload["backup"] if payload.get("backup") else None
    old_exists = bool(payload.get("output_old_exists"))
    actual_state = _state_digest(state_path)
    actual_output = _hash_bytes(output_path)
    actual_provenance = _provenance_digest(_root() / payload["provenance"])
    if actual_state == payload["new_state_sha256"] and actual_output == payload["new_output_sha256"]:
        if actual_provenance != payload["provenance_sha256"]:
            return {"status": "recovery_required", "errors": [f"journal_provenance_mismatch:{name}"]}
        _cleanup_transaction(payload["transaction_id"], None, _staging_dir(payload["transaction_id"]))
        return None
    if actual_state == payload["old_state_sha256"] and actual_output == payload["new_output_sha256"]:
        try:
            _restore_output(output_path, backup, old_exists)
        except OSError as exc:
            return {"status": "recovery_required", "errors": [f"journal_rollback_failed:{name}:{exc}"]}
        if actual_provenance == payload["provenance_sha256"]:
            shutil.rmtree(_root() / payload["provenance"], ignore_errors=True)
        _cleanup_transaction(payload["transaction_id"], None, _staging_dir(payload["transaction_id"]))
        return None
    if actual_state == payload["old_state_sha256"] and actual_output == payload["old_output_sha256"]:
        _cleanup_transaction(payload["transaction_id"], None, _staging_dir(payload["transaction_id"]))
        return None
    return {"status": "recovery_required", "errors": [f"journal_ambiguous:{name}"]}


def _resolve_pending_journals(txid: str):
    names, error = _journal_list()
    if error:
        return {"status": "recovery_required", "errors": [f"journal_unreadable:{error}"]}
    for name in names:
        result = _resolve_one_journal(name)
        if result is not None:
            return result
    return None


def _replay_record(ctx):
    baseline, _errors = inc.load_baseline(ctx["state_path"])
    if baseline is None:
        return None
    build = baseline.get("build")
    if not isinstance(build, dict) or build.get("transaction_id") != ctx["txid"]:
        return None
    page_key = ctx["candidate"]["page_key"]
    pages = build.get("pages") or {}
    if set(pages) != {page_key}:
        return None
    entry = pages[page_key]
    if baseline.get("generation") != entry.get("generation"):
        return None
    if entry.get("response") != ctx["candidate_hash"]:
        return None
    if entry.get("response_path") != _relative(ctx["candidate_path"]):
        return None
    if entry.get("output_path") != _relative(ctx["output_path"]):
        return None
    if _hash_bytes(ctx["output_path"]) != entry.get("output"):
        return None
    if _provenance_digest(_root() / entry.get("provenance", "")) != entry.get("provenance_sha256"):
        return None
    return build


def _discard_staging(ctx):
    shutil.rmtree(ctx.get("staging_dir"), ignore_errors=True)


def _run_locked(ctx):
    pending = _resolve_pending_journals(ctx["txid"])
    if pending is not None:
        _discard_staging(ctx)
        return pending
    current = _read_journal(ctx["txid"])
    if current and current.get("status") == "committed":
        try:
            _cleanup_transaction(ctx["txid"], None, ctx["staging_dir"])
        except OSError as exc:
            return {"status": "recovery_required", "errors": [f"journal_cleanup:{exc}"]}
    replay = _replay_record(ctx)
    if replay is not None:
        _discard_staging(ctx)
        return {"status": "already_committed", "transaction_id": ctx["txid"], "build": replay}
    cas, error = _cas_check(ctx)
    if error:
        _discard_staging(ctx)
        return error
    new_state = _build_state(ctx, cas["baseline"], cas["current_observed"])
    return _publish(ctx, cas["baseline"], new_state)


def commit_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None):
    ctx, error = _prepare_commit(plan_path, state_path, inputs_path, candidate_manifest_path)
    if error:
        return error
    handle, lock_error = _acquire_lock(ctx["txid"])
    if lock_error:
        _discard_staging(ctx)
        return lock_error
    try:
        result = _run_locked(ctx)
    finally:
        release_error = _release_lock(handle)
    if release_error:
        result = dict(result)
        result.setdefault("warnings", [])
        result["warnings"] = list(result["warnings"]) + [release_error]
    return result


def recover_transaction(txid: Optional[str] = None):
    if txid:
        return _resolve_one_journal(f"{txid}.json")
    names, error = _journal_list()
    if error:
        return {"status": "recovery_required", "errors": [f"journal_unreadable:{error}"]}
    for name in names:
        result = _resolve_one_journal(name)
        if result is not None:
            return result
    return {"status": "ok", "resolved": len(names)}

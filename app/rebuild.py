# -*- coding: utf-8 -*-
"""B4-02 B202：单页重建提交事务（journal / 锁 / CAS / 恢复）。"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from app import incremental as inc

EXECUTION_PROTOCOL = "b4-02-commit-v1"
JOURNAL_STATUSES = ("prepared", "output_replaced", "committed", "recovery_required")


class RebuildError(Exception):
    """单页提交事务失败。"""


def _root() -> Path:
    return inc.REPO_ROOT


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_json(payload: Any) -> str:
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


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


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _write_journal(txid: str, status: str, **fields: Any) -> None:
    if status not in JOURNAL_STATUSES:
        raise RebuildError(f"bad journal status: {status}")
    payload = {"format_version": 1, "transaction_id": txid, "status": status}
    payload.update(fields)
    _atomic_write_json(_journal_path(txid), payload)


def _read_journal(txid: str) -> Optional[Dict[str, Any]]:
    path = _journal_path(txid)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "recovery_required", "corrupt": True}
    return payload if isinstance(payload, dict) else {"status": "recovery_required"}


def _unresolved_other_journals(txid: str) -> list:
    directory = _journal_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(path.name for path in directory.iterdir() if path.is_file())
    except (OSError, NotADirectoryError, PermissionError):
        return ["__journal_path_invalid__"]
    return [name for name in entries if name != f"{txid}.json"]


def _acquire_lock(txid: str):
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(handle, json.dumps({"transaction_id": txid, "pid": os.getpid()}).encode("utf-8"))
    return handle


def _release_lock(handle) -> None:
    try:
        os.close(handle)
    finally:
        path = _lock_path()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _transaction_id(plan_generation: str, parent_generation: str, page_key: str, candidate_hash: str) -> str:
    payload = {
        "protocol": EXECUTION_PROTOCOL,
        "project_root": str(_root().resolve()),
        "plan_generation": plan_generation,
        "parent_generation": parent_generation,
        "page_key": page_key,
        "candidate_sha256": candidate_hash,
    }
    return _sha_json(payload)


def _replay_record(state_path: Path, txid: str, candidate_hash: str, output_path: Path) -> Optional[Dict[str, Any]]:
    baseline, _errors = inc.load_baseline(state_path)
    if baseline is None:
        return None
    build = baseline.get("build")
    if not isinstance(build, dict) or build.get("transaction_id") != txid:
        return None
    pages = build.get("pages") or {}
    page_keys = list(pages)
    if len(page_keys) != 1:
        return None
    entry = pages[page_keys[0]]
    if entry.get("response") != candidate_hash:
        return None
    if entry.get("output") != inc._hash_if_exists(output_path):
        return None
    return build


def _restore_output(output_path: Path, backup_path: Optional[Path], old_exists: bool) -> None:
    if old_exists and backup_path is not None and backup_path.exists():
        temporary = output_path.with_name(f".{output_path.name}.restore.tmp")
        shutil.copy2(backup_path, temporary)
        os.replace(temporary, output_path)
    else:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass


def _recover(
    txid: str, output_path: Path, backup_path: Optional[Path], old_exists: bool, journal: Optional[Dict[str, Any]]
) -> str:
    status = (journal or {}).get("status")
    if status in ("prepared", "output_replaced", "recovery_required"):
        _restore_output(output_path, backup_path, old_exists)
        _write_journal(txid, "recovery_required", stage="rollback_attempted", previous_status=status)
        return "rolled_back"
    return "noop"


def _stage_candidate(page_key: str, registered: Dict[str, Any], candidate, staging_dir: Path) -> Dict[str, Any]:
    import ingest

    root = _root() / registered["root"]
    staging_dir.mkdir(parents=True, exist_ok=True)
    candidate_copy = staging_dir / "candidate.md"
    shutil.copy2(_root() / candidate["response"], candidate_copy)
    staged_output = staging_dir / "page.md"
    text, bundle = ingest.ingest(
        root,
        _root() / "wiki" / "__schema__" / "entity-template.md",
        response_file=candidate_copy,
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
    passed = ingest.lint(text, bundle)
    if passed != [f"L{i}" for i in range(1, 9)]:
        raise RebuildError("staged independent lint failed")
    reports = sorted(staging_dir.glob("_provenance/*/ingest-report.json"))
    if not reports:
        raise RebuildError("staged provenance missing")
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    if report.get("publication") != "draft_only":
        raise RebuildError("staged publication is not draft_only")
    return {
        "text": text,
        "output_sha256": _sha_bytes(text.encode("utf-8")),
        "staged_output": staged_output,
        "provenance_dir": reports[0].parent,
        "report": report,
    }


def _observed_with_output(observed: Dict[str, Any], page_key: str, output_sha256: str) -> Dict[str, Any]:
    payload = json.loads(json.dumps(observed, ensure_ascii=False))
    for entry in (payload.get("inputs") or {}).values():
        if page_key in (entry.get("pages") or {}):
            entry["pages"][page_key]["output"] = output_sha256
            return payload
    raise RebuildError("page not found in observed snapshot")


def _prepare_commit(plan_path, state_path, inputs_path, candidate_manifest_path):
    try:
        plan_data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, {"status": "rejected", "errors": ["plan_unreadable"]}
    inputs = inc.load_inputs(inputs_path)
    candidates, errors = inc._load_candidate_manifest(candidate_manifest_path)
    if errors or len(candidates) != 1:
        return False, {"status": "rejected", "errors": errors or ["candidate_manifest_invalid"]}
    candidate = candidates[0]
    page_key = candidate["page_key"]
    registry = inc._page_registry(inputs)
    registered = registry.get(page_key)
    if registered is None:
        return False, {"status": "rejected", "errors": ["candidate_unknown_page"]}
    candidate_hash = inc._hash_if_exists(_root() / candidate["response"])
    if not candidate_hash:
        return False, {"status": "rejected", "errors": ["candidate_unreadable"]}
    output_path = inc._inside_root(_root() / registered["output"])
    txid = _transaction_id(
        plan_data.get("generation"),
        plan_data.get("baseline_generation"),
        page_key,
        candidate_hash,
    )
    return True, {
        "plan_path": Path(plan_path),
        "state_path": Path(state_path),
        "inputs_path": Path(inputs_path),
        "candidate_manifest_path": candidate_manifest_path,
        "plan_data": plan_data,
        "inputs": inputs,
        "candidate": candidate,
        "candidate_hash": candidate_hash,
        "page_key": page_key,
        "registry": registry,
        "registered": registered,
        "output_path": output_path,
        "txid": txid,
    }


def _cas_state(ctx):
    baseline, baseline_errors = inc.load_baseline(ctx["state_path"])
    if baseline is None:
        return {"status": "rejected", "errors": baseline_errors or ["state_unavailable"]}
    precheck = inc.precheck_rebuild(
        ctx["plan_path"], ctx["state_path"], ctx["inputs_path"], ctx["candidate_manifest_path"]
    )
    if precheck.get("action") != "single_page" or not precheck.get("executable"):
        return {"status": "rejected", "precheck": precheck}
    if baseline.get("generation") != precheck.get("baseline_generation"):
        return {"status": "plan_outdated", "errors": ["baseline_generation_mismatch"]}
    current_observed = inc.fingerprint_inputs(ctx["inputs"])
    if current_observed != ctx["plan_data"].get("observed"):
        return {"status": "plan_outdated", "errors": ["observed_fingerprint_mismatch"]}
    return {
        "baseline": baseline,
        "precheck": precheck,
        "current_observed": current_observed,
    }


def _build_new_state(ctx, baseline, precheck, current_observed):
    staging_dir = _staging_dir(ctx["txid"])
    staged = _stage_candidate(ctx["page_key"], ctx["registered"], ctx["candidate"], staging_dir)
    new_state = inc.make_baseline(
        _observed_with_output(current_observed, ctx["page_key"], staged["output_sha256"]),
        generation=_sha_json(
            {
                "parent": baseline.get("generation"),
                "transaction_id": ctx["txid"],
                "output": staged["output_sha256"],
            }
        ),
        graph=baseline.get("graph"),
    )
    final_provenance = _provenance_dir(ctx["txid"])
    if final_provenance.exists():
        shutil.rmtree(final_provenance)
    shutil.move(str(staged["provenance_dir"]), str(final_provenance))
    provenance_hash = _sha_json(
        sorted(
            (str(path.relative_to(final_provenance)), _sha_bytes(path.read_bytes()))
            for path in final_provenance.rglob("*")
            if path.is_file()
        )
    )
    new_state["build"] = {
        "parent_generation": baseline.get("generation"),
        "plan_generation": precheck.get("plan_generation"),
        "transaction_id": ctx["txid"],
        "mode": "response-file",
        "status": "committed",
        "pages": {
            ctx["page_key"]: {
                "response": ctx["candidate_hash"],
                "output": staged["output_sha256"],
                "provenance": str(final_provenance.relative_to(_root())),
                "provenance_sha256": provenance_hash,
            }
        },
    }
    return {
        "staged": staged,
        "new_state": new_state,
        "final_provenance": final_provenance,
        "staging_dir": staging_dir,
    }


def _publish(ctx, built):
    output_path = ctx["output_path"]
    staged = built["staged"]
    new_state = built["new_state"]
    backup_path = _backup_dir(ctx["txid"]) / output_path.name
    old_exists = output_path.exists()
    if old_exists:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, backup_path)
    _write_journal(
        ctx["txid"],
        "prepared",
        page_key=ctx["page_key"],
        output=str(output_path.relative_to(_root())),
        output_old_exists=old_exists,
        backup=str(backup_path.relative_to(_root())) if old_exists else None,
        old_output_sha256=inc._hash_if_exists(output_path),
        new_output_sha256=staged["output_sha256"],
        new_state_sha256=_sha_json(new_state),
        provenance=str(built["final_provenance"].relative_to(_root())),
    )
    target_tmp = output_path.with_name(f".{output_path.name}.{ctx['txid']}.tmp")
    target_tmp.write_bytes(staged["text"].encode("utf-8"))
    os.replace(target_tmp, output_path)
    _write_journal(ctx["txid"], "output_replaced")
    state_tmp = ctx["state_path"].with_name(f".{ctx['state_path'].name}.{ctx['txid']}.tmp")
    state_tmp.write_text(json.dumps(new_state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(state_tmp, ctx["state_path"])
    _write_journal(ctx["txid"], "committed")
    try:
        _journal_path(ctx["txid"]).unlink()
        if old_exists:
            backup_path.unlink()
        shutil.rmtree(built["staging_dir"], ignore_errors=True)
    except OSError:
        return {
            "status": "committed_cleanup_pending",
            "transaction_id": ctx["txid"],
            "generation": new_state["generation"],
        }
    return {"status": "committed", "transaction_id": ctx["txid"], "generation": new_state["generation"]}


def _run_locked(ctx):
    backup_path = None
    old_exists = False
    try:
        other = _unresolved_other_journals(ctx["txid"])
        if other:
            return {"status": "recovery_required", "errors": ["journal_unresolved"] + other}
        replay = _replay_record(ctx["state_path"], ctx["txid"], ctx["candidate_hash"], ctx["output_path"])
        if replay is not None:
            return {"status": "already_committed", "transaction_id": ctx["txid"], "build": replay}
        cas = _cas_state(ctx)
        if cas.get("status"):
            return cas
        built = _build_new_state(ctx, cas["baseline"], cas["precheck"], cas["current_observed"])
        backup_path = _backup_dir(ctx["txid"]) / ctx["output_path"].name
        old_exists = ctx["output_path"].exists()
        return _publish(ctx, built)
    except RebuildError as exc:
        journal = _read_journal(ctx["txid"])
        return {
            "status": "failed",
            "transaction_id": ctx["txid"],
            "errors": [str(exc)],
            "recovery": _recover(ctx["txid"], ctx["output_path"], backup_path, old_exists, journal),
        }
    except Exception as exc:  # noqa: BLE001 - transaction boundary must fail closed
        journal = _read_journal(ctx["txid"])
        return {
            "status": "failed",
            "transaction_id": ctx["txid"],
            "errors": [str(exc)],
            "recovery": _recover(ctx["txid"], ctx["output_path"], backup_path, old_exists, journal),
        }


def commit_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None):
    if _unresolved_other_journals(""):
        return {"status": "recovery_required", "errors": ["journal_unresolved"] + _unresolved_other_journals("")}
    ok, ctx = _prepare_commit(plan_path, state_path, inputs_path, candidate_manifest_path)
    if not ok:
        return ctx
    handle = _acquire_lock(ctx["txid"])
    if handle is None:
        return {"status": "lock_busy", "transaction_id": ctx["txid"]}
    try:
        return _run_locked(ctx)
    finally:
        _release_lock(handle)

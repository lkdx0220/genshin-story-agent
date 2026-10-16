# -*- coding: utf-8 -*-
"""B4-02 B203：v2 journal 恢复/cleanup 最小实现（I3 前置）。

只处理 v2 journal，按实际资源 hash 判定完整旧版/完整新版。
"""
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from app import incremental as inc

JOURNAL_PROTOCOL = "b4-02-multi-commit-v1"
STATE_REL = "reports/incremental/state.json"


def _root() -> Path:
    return inc.REPO_ROOT


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha(payload: Any) -> str:
    return _sha_bytes(_canonical_bytes(payload))


def _sha_json(payload: Any) -> str:
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _hash_bytes(path: Path) -> Optional[str]:
    try:
        return _sha_bytes(Path(path).read_bytes())
    except (OSError, PermissionError):
        return None


def _state_digest(path: Path) -> Optional[str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _sha_json(payload)


def _journal_dir() -> Path:
    return _root() / "reports" / "incremental" / "journal"


def _journal_path(name: str) -> Path:
    return _journal_dir() / name


def _relative(path: Path) -> str:
    return str(Path(path).resolve().relative_to(_root().resolve())).replace("\\", "/")


def _slug(page_key: str) -> str:
    return hashlib.sha256(page_key.encode("utf-8")).hexdigest()[:16]


def _inside(relative: str) -> Optional[Path]:
    if not isinstance(relative, str) or not relative:
        return None
    try:
        path = (_root() / relative).resolve()
    except OSError:
        return None
    root = _root().resolve()
    if path != root and root not in path.parents:
        return None
    return path


def _load_journal(name: str) -> Optional[Dict[str, Any]]:
    path = _journal_path(name)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"corrupt": True}
    return payload if isinstance(payload, dict) else {"corrupt": True}


def _default_page_paths(txid: str, page_key: str) -> Dict[str, str]:
    slug = _slug(page_key)
    return {
        "backup": f"reports/incremental/backups/{txid}/{slug}.bak",
        "provenance": f"reports/incremental/provenance/{txid}/{slug}",
    }


def _journal_identity(name: str, journal: Dict[str, Any]) -> Optional[str]:
    txid = name[:-5] if name.endswith(".json") else name
    if journal.get("format_version") != 2 or journal.get("protocol") != JOURNAL_PROTOCOL:
        return f"journal_identity_invalid:{name}"
    if journal.get("transaction_id") != txid:
        return f"journal_identity_invalid:{name}"
    payload = journal.get("request_payload")
    if not isinstance(payload, dict):
        return f"journal_identity_invalid:{name}"
    root_id = inc._project_root_id()
    if journal.get("project_root_id") != root_id or payload.get("project_root_id") != root_id:
        return f"journal_identity_invalid:{name}"
    try:
        request_id = _canonical_sha(payload)
    except (TypeError, ValueError):
        return f"journal_identity_invalid:{name}"
    if journal.get("request_id") != request_id:
        return f"journal_identity_invalid:{name}"
    token = journal.get("execution_token")
    if not isinstance(token, str) or not token:
        return f"journal_identity_invalid:{name}"
    if _canonical_sha({"request_id": request_id, "execution_token": token}) != txid:
        return f"journal_identity_invalid:{name}"
    return None


def _journal_registry(name: str, journal: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    inputs_meta = journal.get("inputs")
    if not isinstance(inputs_meta, dict) or not isinstance(inputs_meta.get("path"), str):
        return None, f"journal_identity_invalid:{name}"
    inputs_path = _inside(inputs_meta["path"])
    if inputs_path is None or not inputs_path.is_file():
        return None, f"journal_resource_unreadable:{name}"
    try:
        inputs = inc.load_inputs(inputs_path)
    except (inc.IncrementalError, OSError, UnicodeDecodeError):
        return None, f"journal_resource_unreadable:{name}"
    return inc._page_registry(inputs), None


def _journal_header_projection(name: str, journal: Dict[str, Any], payload: Dict[str, Any], token: str) -> Optional[str]:
    if journal.get("state", {}).get("path") != STATE_REL:
        return f"journal_resource_ownership:{name}"
    if payload.get("state") != STATE_REL:
        return f"journal_projection_mismatch:{name}"
    if payload.get("inputs") != journal.get("inputs"):
        return f"journal_projection_mismatch:{name}"
    if payload.get("parent_generation") != journal.get("parent_generation"):
        return f"journal_projection_mismatch:{name}"
    if payload.get("plan_generation") != journal.get("plan_generation"):
        return f"journal_projection_mismatch:{name}"
    txid = name[:-5] if name.endswith(".json") else name
    if journal.get("staging_root") != f"reports/incremental/staging/{txid}/{token}":
        return f"journal_resource_ownership:{name}"
    return None


def _journal_page_paths(name: str, page: Dict[str, Any], registered: Dict[str, Any], txid: str) -> Optional[str]:
    response: Dict[str, Any] = page.get("response") or {}
    output: Dict[str, Any] = page.get("output") or {}
    provenance: Dict[str, Any] = page.get("provenance") or {}
    backup: Dict[str, Any] = page.get("backup") or {}
    response_path = response.get("path")
    output_path = output.get("path")
    if not isinstance(response_path, str) or response_path != registered["response"]:
        return f"journal_resource_ownership:{name}"
    if not isinstance(output_path, str) or output_path != registered["output"]:
        return f"journal_resource_ownership:{name}"
    if _inside(output_path) is None:
        return f"journal_resource_ownership:{name}"
    defaults = _default_page_paths(txid, page["page_key"])
    if not isinstance(backup.get("path"), str) or backup.get("path") != defaults["backup"]:
        return f"journal_resource_ownership:{name}"
    if not isinstance(provenance.get("dir"), str) or provenance.get("dir") != defaults["provenance"]:
        return f"journal_resource_ownership:{name}"
    return None


def _journal_page_projection(name: str, page: Dict[str, Any], payload_page: Dict[str, Any], registered: Dict[str, Any]) -> Optional[str]:
    response: Dict[str, Any] = page.get("response") or {}
    if payload_page.get("response") != registered["response"] or payload_page.get("output") != registered["output"]:
        return f"journal_projection_mismatch:{name}"
    if payload_page.get("candidate_sha256") != response.get("sha256"):
        return f"journal_projection_mismatch:{name}"
    response_path_value = response.get("path")
    response_path = _inside(response_path_value) if isinstance(response_path_value, str) else None
    if response_path is None or not response_path.is_file():
        return f"journal_resource_unreadable:{name}"
    if _hash_bytes(response_path) != response.get("sha256"):
        return f"journal_projection_mismatch:{name}"
    return None


def _journal_page(
    name: str,
    page: Dict[str, Any],
    payload_page: Dict[str, Any],
    registry: Dict[str, Any],
    txid: str,
) -> Optional[str]:
    key = page.get("page_key")
    if not isinstance(key, str):
        return f"journal_projection_mismatch:{name}"
    registered = registry.get(key)
    if registered is None:
        return f"journal_projection_mismatch:{name}"
    error = _journal_page_paths(name, page, registered, txid)
    if error:
        return error
    return _journal_page_projection(name, page, payload_page, registered)


def _journal_pages(name: str, journal: Dict[str, Any], payload: Dict[str, Any], registry: Dict[str, Any], txid: str) -> Tuple[List[str], Optional[str]]:
    pages = journal.get("pages")
    if not isinstance(pages, list) or not pages:
        return [], f"journal_projection_mismatch:{name}"
    payload_pages = payload.get("pages")
    if not isinstance(payload_pages, list):
        return [], f"journal_projection_mismatch:{name}"
    payload_by_key = {
        item.get("page_key"): item for item in payload_pages if isinstance(item, dict) and isinstance(item.get("page_key"), str)
    }
    keys = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("page_key"), str):
            return [], f"journal_projection_mismatch:{name}"
        key = page["page_key"]
        if key in keys:
            return [], f"journal_projection_mismatch:{name}"
        keys.append(key)
        payload_page = payload_by_key.get(key)
        if not isinstance(payload_page, dict):
            return [], f"journal_projection_mismatch:{name}"
        error = _journal_page(name, page, payload_page, registry, txid)
        if error:
            return [], error
    if keys != sorted(keys):
        return [], f"journal_projection_mismatch:{name}"
    if payload.get("page_keys") != keys:
        return [], f"journal_projection_mismatch:{name}"
    return keys, None


def _validate_journal(name: str, journal: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    txid = name[:-5] if name.endswith(".json") else name
    error = _journal_identity(name, journal)
    if error:
        return None, [error]
    payload = journal.get("request_payload")
    assert isinstance(payload, dict)
    registry, error = _journal_registry(name, journal)
    if error:
        return None, [error]
    assert registry is not None
    token = journal.get("execution_token")
    assert isinstance(token, str)
    error = _journal_header_projection(name, journal, payload, token)
    if error:
        return None, [error]
    keys, error = _journal_pages(name, journal, payload, registry, txid)
    if error:
        return None, [error]
    return {"name": name, "txid": txid, "journal": journal, "keys": keys}, []


def _page_status(page: Dict[str, Any]) -> str:
    output: Dict[str, Any] = page.get("output") or {}
    old_exists = output.get("old_exists")
    old_sha = output.get("old_sha256")
    new_sha = output.get("new_sha256")
    relative = output.get("path")
    path = _inside(relative) if isinstance(relative, str) else None
    if path is None:
        return "conflict"
    actual = _hash_bytes(path)
    if actual == new_sha:
        return "new"
    if old_exists:
        if actual == old_sha:
            return "old"
        return "conflict"
    if actual is None:
        return "old"
    return "conflict"


def _cleanup_backups(pages: List[Dict[str, Any]], journal_name: str) -> List[str]:
    errors = []
    for page in pages:
        backup = page.get("backup") or {}
        if isinstance(backup.get("path"), str):
            backup_path = _inside(backup["path"])
            if backup_path is not None:
                try:
                    backup_path.unlink(missing_ok=True)
                except OSError:
                    errors.append(f"journal_cleanup_failed:{journal_name}")
    return errors


def _cleanup_provenance(pages: List[Dict[str, Any]], journal_name: str) -> List[str]:
    errors = []
    for page in pages:
        provenance: Dict[str, Any] = page.get("provenance") or {}
        relative = provenance.get("dir")
        directory = _inside(relative) if isinstance(relative, str) else None
        if directory is not None and directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError:
                errors.append(f"journal_cleanup_failed:{journal_name}")
    return errors


def _cleanup_staging(staging_root: Optional[str], journal_name: str) -> List[str]:
    if not isinstance(staging_root, str):
        return []
    staging = _inside(staging_root)
    if staging is None or not staging.exists():
        return []
    try:
        shutil.rmtree(staging)
    except OSError:
        return [f"journal_cleanup_failed:{journal_name}"]
    return []


def _cleanup_empty_backup_dir(txid: str) -> None:
    backup_dir = _root() / "reports/incremental/backups" / txid
    try:
        if backup_dir.is_dir() and not any(backup_dir.iterdir()):
            backup_dir.rmdir()
    except OSError:
        pass


def _cleanup_owned(txid: str, pages: List[Dict[str, Any]], journal_name: str, remove_provenance: bool, staging_root: Optional[str]) -> List[str]:
    errors = _cleanup_backups(pages, journal_name)
    if remove_provenance:
        errors += _cleanup_provenance(pages, journal_name)
    errors += _cleanup_staging(staging_root, journal_name)
    _cleanup_empty_backup_dir(txid)
    if errors:
        return errors
    try:
        _journal_path(journal_name).unlink(missing_ok=True)
    except OSError:
        errors.append(f"journal_cleanup_failed:{journal_name}")
    return errors


def _manifest_member_map(manifest: Dict[str, Any]) -> Optional[Dict[str, str]]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return None
    listed: Dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            return None
        relative = item["path"]
        if not relative or relative.startswith("/") or ".." in PurePosixPath(relative).parts or relative.endswith("/"):
            return None
        if relative in listed:
            return None
        listed[relative] = item["sha256"]
    return listed


def _actual_member_map(directory: Path) -> Optional[Dict[str, bytes]]:
    actual: Dict[str, bytes] = {}
    for path in Path(directory).rglob("*"):
        if path.is_symlink():
            return None
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative == "manifest.json":
                continue
            actual[relative] = path.read_bytes()
    return actual


def _provenance_valid(directory: Path, manifest_sha256: str) -> bool:
    manifest_path = Path(directory) / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if _canonical_sha(manifest) != manifest_sha256:
        return False
    listed = _manifest_member_map(manifest)
    actual = _actual_member_map(directory)
    if listed is None or actual is None or set(actual) != set(listed):
        return False
    return all(_sha_bytes(actual[relative]) == listed[relative] for relative in actual)


def _committed_state_binding_valid(journal: Dict[str, Any], state: Dict[str, Any]) -> bool:
    build = state.get("build")
    if not isinstance(build, dict) or build.get("version") != 2:
        return False
    if build.get("request_id") != journal.get("request_id") or build.get("transaction_id") != journal.get("transaction_id"):
        return False
    pages = build.get("pages")
    journal_pages = journal.get("pages") or []
    if not isinstance(pages, dict) or set(pages) != {page.get("page_key") for page in journal_pages}:
        return False
    for page in journal_pages:
        key = page.get("page_key")
        entry = pages[key]
        provenance = entry.get("provenance") or {}
        journal_provenance = page.get("provenance") or {}
        if provenance.get("dir") != journal_provenance.get("dir"):
            return False
        if provenance.get("manifest_sha256") != journal_provenance.get("manifest_sha256"):
            return False
        state_members = provenance.get("members")
        journal_members = journal_provenance.get("members")
        if state_members is not None and state_members != journal_members:
            return False
    return True


def _recover_branch(journal: Dict[str, Any], state_path: Path, name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    actual_state = _state_digest(state_path)
    if actual_state == (journal.get("state") or {}).get("new_sha256"):
        return "new", None
    if actual_state == (journal.get("state") or {}).get("old_sha256"):
        return "old", None
    return None, {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}


def _recover_statuses(pages: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[Dict[str, Any]]]:
    statuses = [(_page_status(page), page) for page in pages]
    if any(status == "conflict" for status, _page in statuses):
        return [], {"status": "recovery_required", "resolved": 0, "errors": []}
    return statuses, None


def _recover_new_branch(name: str, txid: str, journal: Dict[str, Any], state_path: Path, statuses: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
    if any(status != "new" for status, _page in statuses):
        return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    try:
        committed_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    if not _committed_state_binding_valid(journal, committed_state):
        return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    for _status, page in statuses:
        provenance: Dict[str, Any] = page.get("provenance") or {}
        relative = provenance.get("dir")
        manifest_sha = provenance.get("manifest_sha256")
        directory = _inside(relative) if isinstance(relative, str) else None
        if directory is None or not isinstance(manifest_sha, str) or not _provenance_valid(directory, manifest_sha):
            return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    errors = _cleanup_owned(txid, journal["pages"], name, remove_provenance=False, staging_root=journal.get("staging_root"))
    if errors:
        return {"status": "committed_cleanup_pending", "resolved": 0, "errors": errors}
    return {"status": "ok", "resolved": 1}


def _recover_old_preflight(name: str, statuses: List[Tuple[str, Dict[str, Any]]]) -> Tuple[Optional[List[Tuple[Dict[str, Any], Dict[str, Any], Path, Optional[Path]]]], Optional[Dict[str, Any]]]:
    preflight: List[Tuple[Dict[str, Any], Dict[str, Any], Path, Optional[Path]]] = []
    for status, page in statuses:
        if status != "new":
            continue
        output: Dict[str, Any] = page.get("output") or {}
        relative = output.get("path")
        output_path = _inside(relative) if isinstance(relative, str) else None
        if output_path is None:
            return None, {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_ownership:{name}"]}
        if output.get("old_exists") is False:
            preflight.append((page, output, output_path, None))
            continue
        backup: Dict[str, Any] = page.get("backup") or {}
        backup_relative = backup.get("path")
        backup_path = _inside(backup_relative) if isinstance(backup_relative, str) else None
        if backup_path is None or not backup_path.is_file() or _hash_bytes(backup_path) != output.get("old_sha256"):
            return None, {"status": "recovery_required", "resolved": 0, "errors": [f"journal_backup_invalid:{name}"]}
        preflight.append((page, output, output_path, backup_path))
    return preflight, None


def _recover_old_execute(name: str, preflight: List[Tuple[Dict[str, Any], Dict[str, Any], Path, Optional[Path]]]) -> Optional[Dict[str, Any]]:
    for _page, output, output_path, backup_path in reversed(preflight):
        try:
            if backup_path is None:
                output_path.unlink(missing_ok=True)
                continue
            temporary = output_path.with_name(f".{output_path.name}.restore.tmp")
            shutil.copy2(backup_path, temporary)
            os.replace(temporary, output_path)
        except OSError:
            return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_cleanup_failed:{name}"]}
        if _hash_bytes(output_path) != output.get("old_sha256"):
            return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    return None


def _recover_one(name: str) -> Dict[str, Any]:
    journal = _load_journal(name)
    if journal is None:
        return {"status": "ok", "resolved": 0}
    validated, errors = _validate_journal(name, journal)
    if errors:
        return {"status": "recovery_required", "resolved": 0, "errors": errors}
    assert validated is not None
    txid = validated["txid"]
    journal = validated["journal"]
    state = journal.get("state")
    state_relative = state.get("path") if isinstance(state, dict) else None
    state_path = _inside(state_relative) if isinstance(state_relative, str) else None
    if state_path is None:
        return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_unreadable:{name}"]}
    branch, branch_error = _recover_branch(journal, state_path, name)
    if branch_error:
        return branch_error
    pages = journal.get("pages")
    assert isinstance(pages, list)
    statuses, status_error = _recover_statuses(pages)
    if status_error:
        return {"status": "recovery_required", "resolved": 0, "errors": [f"journal_resource_conflict:{name}"]}
    if branch == "new":
        return _recover_new_branch(name, txid, journal, state_path, statuses)
    preflight, preflight_error = _recover_old_preflight(name, statuses)
    if preflight_error:
        return preflight_error
    assert preflight is not None
    execute_error = _recover_old_execute(name, preflight)
    if execute_error:
        return execute_error
    cleanup_errors = _cleanup_owned(txid, journal["pages"], name, remove_provenance=True, staging_root=journal.get("staging_root"))
    if cleanup_errors:
        return {"status": "recovery_required", "resolved": 0, "errors": cleanup_errors}
    return {"status": "ok", "resolved": 1}
def _recover_v2_locked(name: str):
    from app import rebuild as single

    txid = name[:-5] if name.endswith(".json") else name
    handle, lock_error = single._acquire_lock(txid)
    if lock_error:
        return lock_error
    try:
        return _recover_one(name)
    finally:
        single._release_lock(handle)


def recover_transaction(txid: Optional[str] = None):
    from app import rebuild as single

    if txid is not None:
        name = f"{txid}.json"
        if not _journal_path(name).is_file():
            return {"status": "ok", "resolved": 0}
        journal = _load_journal(name)
        if isinstance(journal, dict) and journal.get("format_version") == 1:
            return single.recover_transaction(txid)
        return _recover_v2_locked(name)

    names = sorted(path.name for path in _journal_dir().glob("*.json")) if _journal_dir().is_dir() else []
    resolved = 0
    for name in names:
        journal = _load_journal(name)
        if isinstance(journal, dict) and journal.get("format_version") == 1:
            result = single.recover_transaction(name[:-5])
        else:
            result = _recover_v2_locked(name)
        if result["status"] != "ok":
            return result
        resolved += result.get("resolved", 0)
    return {"status": "ok", "resolved": resolved}

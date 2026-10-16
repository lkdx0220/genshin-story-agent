# -*- coding: utf-8 -*-
"""B4-02 B203：多页提交事务（I1/I2 最小实现）。

实现范围：
  precheck_rebuild / commit_rebuild / recover_transaction
  身份、journal v2、候选冻结、staging、provenance、逐页 replace、state 提交点。
"""
import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import incremental as inc
from app import rebuild as single

MULTI_PROTOCOL = "b4-02-multi-commit-v1"
JOURNAL_FORMAT = 2
GREEN_LINT = [f"L{i}" for i in range(1, 9)]


class RebuildMultiError(Exception):
    """多页提交事务失败。"""


def _root() -> Path:
    return inc.REPO_ROOT


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_json(payload: Any) -> str:
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha(payload: Any) -> str:
    return _sha_bytes(_canonical_bytes(payload))


def _hash_bytes(path: Path) -> Optional[str]:
    try:
        return _sha_bytes(path.read_bytes())
    except (OSError, PermissionError):
        return None


def _state_digest(path: Path) -> Optional[str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _sha_json(payload)


def _relative(path: Path) -> str:
    return str(Path(path).resolve().relative_to(_root().resolve())).replace("\\", "/")


def _slug(page_key: str) -> str:
    return hashlib.sha256(page_key.encode("utf-8")).hexdigest()[:16]


def _journal_dir() -> Path:
    return _root() / "reports" / "incremental" / "journal"


def _journal_path(txid: str) -> Path:
    return _journal_dir() / f"{txid}.json"


def _staging_root(txid: str, token: str) -> str:
    return f"reports/incremental/staging/{txid}/{token}"


def _backup_path(txid: str, page_key: str) -> str:
    return f"reports/incremental/backups/{txid}/{_slug(page_key)}.bak"


def _provenance_path(txid: str, page_key: str) -> str:
    return f"reports/incremental/provenance/{txid}/{_slug(page_key)}"


def _lock_path() -> Path:
    return _root() / "reports" / "incremental" / "commit.lock"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _journal_list() -> List[str]:
    directory = _journal_dir()
    if not directory.exists():
        return []
    try:
        return sorted(path.name for path in directory.iterdir() if path.is_file())
    except OSError:
        return []


def _acquire_lock(txid: str) -> Tuple[Optional[int], Optional[dict]]:
    path = _lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, json.dumps({"transaction_id": txid, "pid": os.getpid()}).encode("utf-8"))
        return handle, None
    except FileExistsError:
        return None, {"status": "lock_busy", "transaction_id": txid}
    except OSError as exc:
        return None, {"status": "lock_error", "errors": [f"lock_acquire:{exc}"]}


def _release_lock(handle: Optional[int]) -> None:
    if handle is not None:
        try:
            os.close(handle)
        except OSError:
            pass
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def _candidate_hash(case_root: Path, candidate: Dict[str, Any]) -> Optional[str]:
    path = Path(case_root) / candidate["response"]
    if not path.is_file():
        return None
    return _hash_bytes(path)


def _observed_candidate_hash(observed: Dict[str, Any], bundle_key: str, page_key: str) -> Optional[str]:
    entry = (observed.get("inputs") or {}).get(bundle_key) or {}
    return ((entry.get("pages") or {}).get(page_key) or {}).get("response")


def _managed_scope(inputs: Dict[str, Any], plan: Dict[str, Any]) -> List[str]:
    if plan.get("full_required") is True:
        return sorted(page["page_key"] for item in inputs["inputs"] for page in item["managed_pages"])
    dirty = plan.get("dirty")
    if not isinstance(dirty, dict):
        return []
    return sorted(dirty)


def _load_plan(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"plan_unreadable:{exc}"]
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        return None, ["plan_invalid"]
    if payload.get("execution_protocol") != inc.EXECUTION_PROTOCOL:
        return None, ["plan_execution_protocol_mismatch"]
    return payload, []


def _load_state(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    if not Path(path).exists():
        return None, ["bootstrap_not_supported"]
    try:
        json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ["unknown:state_corrupt"]
    baseline, errors = inc.load_baseline(Path(path))
    if baseline is None:
        return None, errors or ["unknown:state_corrupt"]
    return baseline, []


def _validate_plan(inputs, state_path, plan_path, plan):
    baseline, errors = _load_state(state_path)
    if baseline is None:
        return None, errors
    authoritative = inc.build_plan(inputs, state_path)
    if plan.get("baseline_generation") != baseline.get("generation"):
        return None, ["plan_outdated"]
    if plan.get("generation") != authoritative.get("generation"):
        return None, ["plan_mismatch"]
    if inc._plan_decision_fields(plan) != inc._plan_decision_fields(authoritative):
        return None, ["plan_mismatch"]
    return baseline, []


def _validate_candidates(inputs, plan, manifest_path, baseline):
    registry = inc._page_registry(inputs)
    scope = _managed_scope(inputs, plan)
    candidates, errors = inc._load_candidate_manifest(manifest_path)
    if errors:
        return None, errors
    by_page = {item["page_key"]: item for item in candidates}
    if len(by_page) != len(candidates):
        return None, [f"candidate_manifest_invalid:{candidates[0]['page_key'] if candidates else ''}"]
    if sorted(by_page) != scope:
        return None, ["candidate_set_mismatch"]
    for page_key in scope:
        candidate = by_page[page_key]
        registered = registry.get(page_key)
        if registered is None:
            return None, ["candidate_set_mismatch"]
        hash_value = _candidate_hash(_root(), candidate)
        if hash_value is None:
            return None, ["candidate_unreadable"]
        observed_hash = _observed_candidate_hash(plan.get("observed") or {}, registered["bundle_key"], page_key)
        if observed_hash != hash_value:
            return None, ["candidate_binding_mismatch"]
    return candidates, []


def _capability() -> Dict[str, Any]:
    return dict(inc.CAPABILITY_DEFAULT)


def _reject(errors: List[str], *, status: str = "rejected", **extra: Any) -> Dict[str, Any]:
    payload = {
        "action": "reject",
        "executable": False,
        "build_set": [],
        "errors": list(errors),
        "capability": _capability(),
    }
    payload.update(extra)
    payload["commit_status"] = status
    return payload


def _precheck(inputs, state_path, plan_path, inputs_path, candidate_manifest_path):
    protection_errors = inc._validate_registered_outputs(
        inputs, inputs_path, state_path, plan_path, candidate_manifest_path
    )
    if protection_errors:
        return _reject(protection_errors)
    plan, errors = _load_plan(plan_path)
    if errors:
        return _reject(errors)
    baseline, errors = _validate_plan(inputs, state_path, plan_path, plan)
    if errors:
        return _reject(errors)

    unresolved = _journal_list()
    if unresolved:
        return _reject([f"journal_unresolved:{name}" for name in unresolved], status="recovery_required")

    scope = _managed_scope(inputs, plan)
    if inc._removed_pages(state_path, inputs):
        return _reject(["removal_not_supported"])
    if plan.get("unknown"):
        return _reject([f"unknown:{plan['unknown'][0]}"])
    if plan.get("blocked"):
        return _reject([f"blocked:{plan['blocked'][0]}"])
    if plan.get("full_required") is True and len(scope) == 1:
        return _reject(["full_required_not_supported"])
    if not scope:
        candidates, errors = inc._load_candidate_manifest(candidate_manifest_path)
        if errors:
            return _reject(["candidate_manifest_invalid"])
        if candidates:
            return _reject(["candidate_set_mismatch"])
        return {
            "action": "no_op",
            "executable": False,
            "build_set": [],
            "errors": [],
            "candidate_binding_passed": False,
            "full_validation_passed": "not_run",
            "capability": _capability(),
        }

    candidates, errors = _validate_candidates(inputs, plan, candidate_manifest_path, baseline)
    if errors:
        return _reject(errors)

    if len(scope) == 1:
        return {
            "action": "single_page",
            "executable": True,
            "build_set": scope,
            "errors": [],
            "candidate_binding_passed": True,
            "full_validation_passed": "not_run",
            "capability": _capability(),
        }
    return {
        "action": "multi_page",
        "executable": True,
        "build_set": scope,
        "errors": [],
        "candidate_binding_passed": True,
        "full_validation_passed": "not_run",
        "capability": _capability(),
    }


def precheck_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None):
    try:
        plan_path = inc._ensure_file_inside(plan_path, "plan")
        inputs_path = inc._ensure_file_inside(inputs_path, "inputs")
        state_path = Path(state_path)
        if candidate_manifest_path is not None:
            candidate_manifest_path = inc._ensure_file_inside(candidate_manifest_path, "candidate_manifest")
    except inc.IncrementalError as exc:
        return _reject([str(exc)])
    default_state = (_root() / "reports/incremental/state.json").resolve()
    if Path(state_path).resolve() != default_state:
        return _reject(["state_path_not_supported"])
    try:
        inputs = inc.load_inputs(inputs_path)
    except inc.IncrementalError as exc:
        return _reject([f"inputs_invalid:{exc}"])
    return _precheck(inputs, state_path, plan_path, inputs_path, candidate_manifest_path)


def _request_payload(inputs, state_path, inputs_path, plan, candidates):
    registry = inc._page_registry(inputs)
    scope = sorted(item["page_key"] for item in candidates)
    pages = []
    for item in sorted(candidates, key=lambda entry: entry["page_key"]):
        registered = registry[item["page_key"]]
        pages.append(
            {
                "page_key": item["page_key"],
                "response": registered["response"],
                "output": registered["output"],
                "candidate_sha256": _hash_bytes(_root() / item["response"]),
            }
        )
    dirty = {}
    for key in sorted(plan.get("dirty") or {}):
        dirty[key] = list((plan.get("dirty") or {})[key])
    return {
        "project_root_id": inc._project_root_id(),
        "protocol": MULTI_PROTOCOL,
        "inputs": {"path": _relative(inputs_path), "sha256": _hash_bytes(inputs_path)},
        "state": _relative(state_path),
        "plan_generation": plan.get("generation"),
        "parent_generation": plan.get("baseline_generation"),
        "page_keys": scope,
        "pages": pages,
        "execution": {
            "observed": plan.get("observed"),
            "dirty": dirty,
            "full_required": bool(plan.get("full_required")),
            "blocked": sorted(set(plan.get("blocked") or [])),
            "unknown": sorted(set(plan.get("unknown") or [])),
            "affected_pages": sorted(set(plan.get("affected_pages") or [])),
        },
    }


def _write_provenance_manifest(directory: Path) -> Dict[str, Any]:
    files = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file() and path.relative_to(directory).as_posix() != "manifest.json":
            files.append(
                {"path": path.relative_to(directory).as_posix(), "sha256": _sha_bytes(path.read_bytes())}
            )
    manifest = {"protocol": "b4-02-provenance-v1", "files": files}
    (Path(directory) / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _stage_all(ctx):
    import ingest

    root = _root()
    inputs = ctx["inputs"]
    candidates = ctx["candidates"]
    registry = inc._page_registry(inputs)
    staging = root / ctx["staging_root"]
    staging.mkdir(parents=True, exist_ok=True)
    staged = {}
    try:
        for candidate in sorted(candidates, key=lambda item: item["page_key"]):
            page_key = candidate["page_key"]
            registered = registry[page_key]
            ctx["fail_page"] = page_key
            frozen = staging / f"{_slug(page_key)}.md"
            shutil.copy2(root / candidate["response"], frozen)
            output = staging / _slug(page_key) / "page.md"
            text, bundle = ingest.ingest(
                root / registered["root"],
                root / "wiki/__schema__/entity-template.md",
                response_file=frozen,
                provider="response-file",
                output=output,
                prompt_path=root / registered["prompt"] if registered.get("prompt") else None,
                expected_entity_id=registered.get("entity_id"),
            )
            data, _ = ingest.parse_page(text)
            if registered.get("entity_id") and data.get("entity_id") != registered.get("entity_id"):
                raise RebuildMultiError("staged entity_id mismatch")
            if data.get("data_mode") and data.get("data_mode") != registered.get("data_mode"):
                raise RebuildMultiError("staged data_mode mismatch")
            if data.get("page_status") in ("accepted", "published"):
                raise RebuildMultiError("staged page_status is not draft")
            if ingest.lint(text, bundle) != GREEN_LINT:
                raise RebuildMultiError("staged independent lint failed")
            reports = sorted(output.parent.glob("_provenance/*/ingest-report.json"))
            if not reports:
                raise RebuildMultiError("staged provenance missing")
            provenance_staging = reports[0].parent
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            if report.get("publication") != "draft_only":
                raise RebuildMultiError("staged publication is not draft_only")
            manifest = _write_provenance_manifest(provenance_staging)
            staged[page_key] = {
                "text": text,
                "output_sha256": _sha_bytes(text.encode("utf-8")),
                "provenance_staging": provenance_staging,
                "provenance_dir": _provenance_path(ctx["txid"], page_key),
                "frozen": frozen,
                "manifest": manifest,
            }
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return None, {"status": "rejected", "errors": [f"staging_failed:{ctx.get('fail_page', '')}:{exc}"]}
    return staged, None


def _build_state(ctx, staged):
    inputs = ctx["inputs"]
    plan = ctx["plan"]
    registry = inc._page_registry(inputs)
    scope = ctx["build_set"]
    frozen_inputs = json.loads(json.dumps(inputs, ensure_ascii=False))
    for item in frozen_inputs["inputs"]:
        for page in item["managed_pages"]:
            if page["page_key"] in scope:
                page["response"] = _relative(staged[page["page_key"]]["frozen"])
    observed = inc.fingerprint_inputs(frozen_inputs)
    for page_key in scope:
        for entry in (observed.get("inputs") or {}).values():
            if page_key in (entry.get("pages") or {}):
                entry["pages"][page_key]["output"] = staged[page_key]["output_sha256"]
                break
    graph = inc.collect_dependencies(frozen_inputs)
    generation = _sha_json(
        {
            "parent": ctx["baseline"]["generation"],
            "transaction_id": ctx["txid"],
            "pages": {key: staged[key]["output_sha256"] for key in scope},
        }
    )
    new_state = inc.make_baseline(observed, generation, graph)
    pages = {}
    for page_key in scope:
        registered = registry[page_key]
        provenance_dir = staged[page_key]["provenance_dir"]
        manifest_path = staged[page_key]["provenance_staging"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pages[page_key] = {
            "response": {
                "path": registered["response"],
                "sha256": _hash_bytes(_root() / registered["response"]),
            },
            "output": {"path": registered["output"], "sha256": staged[page_key]["output_sha256"]},
            "provenance": {
                "dir": provenance_dir,
                "manifest_sha256": _canonical_sha(manifest),
                "members": manifest.get("files") or [],
            },
            "generation": generation,
        }
    new_state["build"] = {
        "version": 2,
        "request_id": ctx["request_id"],
        "transaction_id": ctx["txid"],
        "execution_token": ctx["token"],
        "parent_generation": ctx["baseline"]["generation"],
        "plan_generation": plan.get("generation"),
        "generation": generation,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
    }
    return new_state


def _journal_payload(ctx, new_state):
    pages = []
    for page_key in ctx["build_set"]:
        registered = inc._page_registry(ctx["inputs"])[page_key]
        staged = ctx["staged"][page_key]
        manifest = json.loads((staged["provenance_staging"] / "manifest.json").read_text(encoding="utf-8"))
        output_path = _root() / registered["output"]
        pages.append(
            {
                "page_key": page_key,
                "response": {
                    "path": registered["response"],
                    "sha256": _hash_bytes(_root() / registered["response"]),
                },
                "output": {
                    "path": registered["output"],
                    "old_exists": output_path.exists(),
                    "old_sha256": _hash_bytes(output_path),
                    "new_sha256": staged["output_sha256"],
                },
                "backup": {
                    "path": _backup_path(ctx["txid"], page_key),
                    "sha256": _hash_bytes(output_path),
                },
                "provenance": {
                    "dir": staged["provenance_dir"],
                    "manifest_sha256": _canonical_sha(manifest),
                    "members": manifest.get("files") or [],
                },
            }
        )
    return {
        "format_version": JOURNAL_FORMAT,
        "protocol": MULTI_PROTOCOL,
        "project_root_id": inc._project_root_id(),
        "request_payload": ctx["request_payload"],
        "request_id": ctx["request_id"],
        "execution_token": ctx["token"],
        "transaction_id": ctx["txid"],
        "plan_generation": ctx["plan"].get("generation"),
        "parent_generation": ctx["baseline"].get("generation"),
        "inputs": ctx["request_payload"]["inputs"],
        "state": {"path": _relative(ctx["state_path"]), "old_sha256": _sha_json(ctx["baseline"]), "new_sha256": _sha_json(new_state)},
        "staging_root": ctx["staging_root"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared",
        "pages": pages,
    }


def _rollback_outputs_from_disk(ctx, journal):
    root = _root()
    journal_name = f"{ctx['txid']}.json"
    pages = {page["page_key"]: page for page in journal["pages"]}
    for page_key in reversed(ctx["build_set"]):
        page = pages[page_key]
        output = page["output"]
        output_path = (root / output["path"]).resolve()
        actual = _hash_bytes(output_path)
        old_sha = output.get("old_sha256")
        new_sha = output.get("new_sha256")
        if actual == new_sha:
            if output.get("old_exists") is False:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    return f"journal_cleanup_failed:{journal_name}"
            else:
                backup = (root / page["backup"]["path"]).resolve()
                if not backup.is_file() or _hash_bytes(backup) != old_sha:
                    return f"journal_backup_invalid:{journal_name}"
                try:
                    temporary = output_path.with_name(f".{output_path.name}.restore.tmp")
                    shutil.copy2(backup, temporary)
                    os.replace(temporary, output_path)
                except OSError:
                    return f"journal_cleanup_failed:{journal_name}"
                if _hash_bytes(output_path) != old_sha:
                    return f"journal_resource_conflict:{journal_name}"
        elif actual == old_sha or (output.get("old_exists") is False and actual is None):
            continue
        else:
            return f"journal_resource_conflict:{journal_name}"
    return None


def _cleanup_failed_transaction(ctx, journal):
    root = _root()
    journal_name = f"{ctx['txid']}.json"
    errors = []
    for page in journal["pages"]:
        provenance = root / page["provenance"]["dir"]
        if provenance.exists():
            try:
                shutil.rmtree(provenance)
            except OSError:
                errors.append(f"journal_cleanup_failed:{journal_name}")
        backup = root / page["backup"]["path"]
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            errors.append(f"journal_cleanup_failed:{journal_name}")
    staging = root / journal["staging_root"]
    if staging.exists():
        try:
            shutil.rmtree(staging)
        except OSError:
            errors.append(f"journal_cleanup_failed:{journal_name}")
    for parent in (
        root / "reports/incremental/staging" / ctx["txid"],
        root / "reports/incremental/backups" / ctx["txid"],
        root / "reports/incremental/provenance" / ctx["txid"],
    ):
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
    if errors:
        return errors
    try:
        _journal_path(ctx["txid"]).unlink(missing_ok=True)
    except OSError:
        errors.append(f"journal_cleanup_failed:{journal_name}")
    return errors


def _write_journal_status(ctx, journal, status):
    journal["status"] = status
    _atomic_write_json(_journal_path(ctx["txid"]), journal)


def _publish_backups(ctx):
    root = _root()
    backups = []
    for page_key in ctx["build_set"]:
        registered = inc._page_registry(ctx["inputs"])[page_key]
        output_path = root / registered["output"]
        if output_path.exists():
            backup = root / _backup_path(ctx["txid"], page_key)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, backup)
            backups.append((page_key, backup))
    return backups


def _publish_provenance(ctx):
    root = _root()
    for page_key in ctx["build_set"]:
        target = root / ctx["staged"][page_key]["provenance_dir"]
        if target.exists():
            raise RebuildMultiError("provenance target already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ctx["staged"][page_key]["provenance_staging"]), str(target))


def _publish_outputs(ctx):
    root = _root()
    for page_key in ctx["build_set"]:
        registered = inc._page_registry(ctx["inputs"])[page_key]
        output_path = (root / registered["output"]).resolve()
        temporary = output_path.with_name(f".{output_path.name}.{ctx['txid']}.tmp")
        temporary.write_bytes(ctx["staged"][page_key]["text"].encode("utf-8"))
        os.replace(temporary, output_path)


def _publish_state(ctx, new_state):
    state_tmp = ctx["state_path"].with_name(f".{ctx['state_path'].name}.{ctx['txid']}.tmp")
    state_tmp.write_text(json.dumps(new_state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(state_tmp.resolve(), ctx["state_path"].resolve())


def _publish_failure(ctx, journal, new_state, exc, state_replaced=False):
    if state_replaced or _state_digest(ctx["state_path"]) == _sha_json(new_state):
        try:
            _write_journal_status(ctx, journal, "committed")
        except OSError:
            pass
        return {
            "status": "committed_cleanup_pending",
            "transaction_id": ctx["txid"],
            "warnings": [f"post_commit:{exc}"],
        }
    rollback_error = _rollback_outputs_from_disk(ctx, journal)
    if rollback_error:
        return {
            "status": "recovery_required",
            "transaction_id": ctx["txid"],
            "errors": [rollback_error],
        }
    cleanup_errors = _cleanup_failed_transaction(ctx, journal)
    if cleanup_errors:
        return {
            "status": "recovery_required",
            "transaction_id": ctx["txid"],
            "errors": cleanup_errors,
        }
    return {"status": "rejected", "errors": [f"publish_failed:{exc}"], "transaction_id": ctx["txid"]}


def _publish_cleanup_success(ctx, backups, new_state):
    root = _root()
    cleanup_errors = []
    for _page_key, backup in backups:
        try:
            backup.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"backup:{exc}")
    try:
        shutil.rmtree(root / ctx["staging_root"])
    except FileNotFoundError:
        pass
    except OSError as exc:
        cleanup_errors.append(f"staging:{exc}")
    for parent in (
        root / "reports/incremental/staging" / ctx["txid"],
        root / "reports/incremental/backups" / ctx["txid"],
    ):
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
    if cleanup_errors:
        return {
            "status": "committed_cleanup_pending",
            "request_id": ctx["request_id"],
            "transaction_id": ctx["txid"],
            "generation": new_state["generation"],
            "warnings": [f"cleanup:{error}" for error in cleanup_errors],
        }
    try:
        _journal_path(ctx["txid"]).unlink(missing_ok=True)
    except OSError as exc:
        return {
            "status": "committed_cleanup_pending",
            "request_id": ctx["request_id"],
            "transaction_id": ctx["txid"],
            "generation": new_state["generation"],
            "warnings": [f"cleanup:{exc}"],
        }
    return {
        "status": "committed",
        "request_id": ctx["request_id"],
        "transaction_id": ctx["txid"],
        "generation": new_state["generation"],
    }


def _publish(ctx, new_state):
    journal = _journal_payload(ctx, new_state)
    state_replaced = False
    try:
        backups = _publish_backups(ctx)
        _write_journal_status(ctx, journal, "prepared")
        _publish_provenance(ctx)
        _publish_outputs(ctx)
        _write_journal_status(ctx, journal, "pages_replaced")
        _publish_state(ctx, new_state)
        state_replaced = True
        _write_journal_status(ctx, journal, "committed")
    except Exception as exc:  # noqa: BLE001 - transaction boundary
        return _publish_failure(ctx, journal, new_state, exc, state_replaced)
    return _publish_cleanup_success(ctx, backups, new_state)


def _replay_load_state(ctx):
    state_path = Path(ctx["state_path"])
    if not state_path.is_file():
        return None, None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    build = state.get("build") or {}
    if build.get("request_id") != ctx["request_id"] or build.get("version") != 2 or not build.get("transaction_id"):
        return None, None
    return state, build


def _replay_identity(state, build):
    if (
        state.get("generation") != build.get("generation")
        or state.get("graph", {}).get("generation") != state.get("generation")
    ):
        return "replay_generation_mismatch"
    token = build.get("execution_token")
    if not isinstance(token, str) or not token:
        return "replay_identity_mismatch"
    expected_txid = _canonical_sha({"request_id": build["request_id"], "execution_token": token})
    if build.get("transaction_id") != expected_txid:
        return "replay_identity_mismatch"
    return None


def _replay_provenance_members(provenance_dir, manifest_sha256, page_key):
    manifest_path = provenance_dir / "manifest.json"
    if not manifest_path.is_file():
        return f"replay_provenance_mismatch:{page_key}"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return f"replay_provenance_mismatch:{page_key}"
    if _canonical_sha(manifest) != manifest_sha256:
        return f"replay_provenance_mismatch:{page_key}"
    listed = {}
    for item in manifest.get("files") or []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return f"replay_provenance_mismatch:{page_key}"
        listed[item["path"]] = item.get("sha256")
    actual = {}
    for path in provenance_dir.rglob("*"):
        if path.is_file() and path.relative_to(provenance_dir).as_posix() != "manifest.json":
            actual[path.relative_to(provenance_dir).as_posix()] = path.read_bytes()
    if set(actual) != set(listed):
        return f"replay_provenance_mismatch:{page_key}"
    for relative, payload in actual.items():
        if _sha_bytes(payload) != listed[relative]:
            return f"replay_provenance_mismatch:{page_key}"
    return None


def _replay_page(ctx, page_key, build, registry, expected_txid):
    entry = build["pages"][page_key]
    registered = registry.get(page_key)
    response = entry.get("response") or {}
    output = entry.get("output") or {}
    provenance = entry.get("provenance") or {}
    if registered is None or response.get("path") != registered["response"] or output.get("path") != registered["output"]:
        return f"replay_registered_target_mismatch:{page_key}"
    if _hash_bytes(_root() / registered["response"]) != response.get("sha256"):
        return f"replay_response_mismatch:{page_key}"
    if _hash_bytes(_root() / registered["output"]) != output.get("sha256"):
        return f"replay_output_mismatch:{page_key}"
    expected_dir = f"reports/incremental/provenance/{expected_txid}/{_slug(page_key)}"
    if provenance.get("dir") != expected_dir:
        return f"replay_provenance_owner_mismatch:{page_key}"
    return _replay_provenance_members(_root() / expected_dir, provenance.get("manifest_sha256"), page_key)


def _replay_result(ctx, *, lock_held=False):
    if not lock_held and _lock_path().exists():
        return {"status": "lock_busy", "transaction_id": ctx.get("request_id", "")}
    state, build = _replay_load_state(ctx)
    if state is None:
        return None
    journals = _journal_list()
    if journals:
        return {"status": "recovery_required", "errors": [f"journal_unresolved:{name}" for name in journals]}
    baseline, errors = inc.load_baseline(Path(ctx["state_path"]))
    if baseline is None:
        return {"status": "rejected", "errors": errors or ["state_invalid"]}
    identity_error = _replay_identity(state, build)
    if identity_error:
        return {"status": "rejected", "errors": [identity_error]}
    pages = build.get("pages")
    if not isinstance(pages, dict) or set(pages) != set(ctx["build_set"]):
        return {"status": "rejected", "errors": ["replay_page_set_mismatch"]}
    try:
        inputs = inc.load_inputs(ctx["inputs_path"])
    except (OSError, UnicodeDecodeError, inc.IncrementalError):
        return {"status": "rejected", "errors": ["replay_inputs_unreadable"]}
    registry = inc._page_registry(inputs)
    expected_txid = build["transaction_id"]
    for page_key in ctx["build_set"]:
        page_error = _replay_page(ctx, page_key, build, registry, expected_txid)
        if page_error:
            return {"status": "rejected", "errors": [page_error]}
    return {
        "status": "already_committed",
        "request_id": build["request_id"],
        "transaction_id": build["transaction_id"],
    }


def _final_cas_sources(ctx):
    baseline, errors = _load_state(ctx["state_path"])
    if baseline is None:
        return None, {"status": "plan_outdated", "errors": errors or ["state_unavailable"]}
    if baseline.get("generation") != ctx["baseline"].get("generation"):
        return None, {"status": "plan_outdated", "errors": ["baseline_generation_mismatch"]}
    try:
        live_inputs = inc.load_inputs(ctx["inputs_path"])
        live_observed = inc.fingerprint_inputs(live_inputs)
        disk_plan = json.loads(ctx["plan_path"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, inc.IncrementalError) as exc:
        return None, {"status": "rejected", "errors": [f"final_cas_unreadable:{exc}"]}
    if live_observed != ctx["plan"].get("observed"):
        return None, {"status": "plan_outdated", "errors": ["observed_fingerprint_mismatch"]}
    if disk_plan != ctx["plan"]:
        return None, {"status": "plan_mismatch", "errors": ["plan_changed_after_staging"]}
    return live_inputs, None


def _final_cas_plan(ctx, live_inputs):
    authoritative = inc.build_plan(live_inputs, ctx["state_path"])
    if (
        authoritative.get("generation") != ctx["plan"].get("generation")
        or authoritative.get("baseline_generation") != ctx["plan"].get("baseline_generation")
        or inc._plan_decision_fields(authoritative) != inc._plan_decision_fields(ctx["plan"])
    ):
        return {"status": "plan_mismatch", "errors": ["plan_mismatch_after_staging"]}
    return None


def _final_cas_pages(ctx, new_state, staged, live_inputs):
    registry = inc._page_registry(live_inputs)
    build_pages = (new_state.get("build") or {}).get("pages") or {}
    if set(build_pages) != set(ctx["build_set"]):
        return {"status": "rejected", "errors": ["final_cas_page_set_mismatch"]}
    for page_key in ctx["build_set"]:
        frozen_hash = _hash_bytes(staged[page_key]["frozen"])
        observed_hash = _observed_candidate_hash(ctx["plan"].get("observed") or {}, registry[page_key]["bundle_key"], page_key)
        live_hash = _hash_bytes(_root() / registry[page_key]["response"])
        build_hash = (build_pages[page_key].get("response") or {}).get("sha256")
        if not frozen_hash or frozen_hash != observed_hash or frozen_hash != live_hash or frozen_hash != build_hash:
            return {"status": "rejected", "errors": [f"frozen_candidate_binding:{page_key}"]}
    return None


def _final_cas_generation(ctx, new_state):
    build = new_state.get("build") or {}
    if build.get("parent_generation") != ctx["baseline"].get("generation"):
        return {"status": "plan_outdated", "errors": ["final_parent_generation_mismatch"]}
    if build.get("plan_generation") != ctx["plan"].get("generation"):
        return {"status": "plan_mismatch", "errors": ["final_plan_generation_mismatch"]}
    if build.get("generation") != new_state.get("generation"):
        return {"status": "rejected", "errors": ["final_state_generation_mismatch"]}
    return None


def _final_cas(ctx, new_state, staged):
    live_inputs, error = _final_cas_sources(ctx)
    if error:
        return error
    error = _final_cas_plan(ctx, live_inputs)
    if error:
        return error
    error = _final_cas_pages(ctx, new_state, staged, live_inputs)
    if error:
        return error
    return _final_cas_generation(ctx, new_state)
def commit_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None):
    paths_plan = None
    paths_inputs = None
    if Path(state_path).resolve() != (_root() / "reports/incremental/state.json").resolve():
        return {"status": "rejected", "errors": ["state_path_not_supported"]}
    # Replay detection must run before authoritative plan comparison: after a
    # successful commit the plan is intentionally outdated relative to new state.
    try:
        paths_plan = inc._ensure_file_inside(plan_path, "plan")
        paths_inputs = inc._ensure_file_inside(inputs_path, "inputs")
        state_resolved = Path(state_path).resolve()
        inputs = inc.load_inputs(paths_inputs)
        plan = json.loads(paths_plan.read_text(encoding="utf-8"))
        candidates, candidate_errors = _validate_candidates(inputs, plan, candidate_manifest_path, None)
        if not candidate_errors:
            payload = _request_payload(inputs, state_resolved, paths_inputs, plan, candidates)
            request_id = _canonical_sha(payload)
            try:
                state_probe = json.loads(state_resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                state_probe = {}
            build_probe = state_probe.get("build") or {}
            if build_probe.get("request_id") == request_id:
                handle, lock_error = _acquire_lock(request_id)
                if lock_error:
                    return lock_error
                try:
                    replay = _replay_result(
                        {
                            "state_path": state_resolved,
                            "inputs_path": paths_inputs,
                            "request_id": request_id,
                            "build_set": _managed_scope(inputs, plan),
                        },
                        lock_held=True,
                    )
                    if replay is not None:
                        return replay
                finally:
                    _release_lock(handle)
    except (OSError, ValueError, KeyError, inc.IncrementalError, json.JSONDecodeError):
        pass

    report = precheck_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path)
    if report["action"] == "no_op":
        return {"status": "no_op", "errors": []}
    if report["action"] == "reject":
        return {"status": report.pop("commit_status", "rejected"), "errors": report["errors"]}
    if report["action"] == "single_page":
        return single.commit_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path)

    paths_plan = inc._ensure_file_inside(plan_path, "plan")
    paths_inputs = inc._ensure_file_inside(inputs_path, "inputs")
    state_path = Path(state_path).resolve()
    candidate_manifest_path = inc._ensure_file_inside(candidate_manifest_path, "candidate_manifest")
    inputs = inc.load_inputs(paths_inputs)
    plan = json.loads(paths_plan.read_text(encoding="utf-8"))
    candidates, errors = _validate_candidates(inputs, plan, candidate_manifest_path, None)
    if errors:
        return {"status": "rejected", "errors": errors}
    request_payload = _request_payload(inputs, state_path, paths_inputs, plan, candidates)
    request_id = _canonical_sha(request_payload)
    token = f"{os.getpid()}-{_sha_bytes(os.urandom(16))[:16]}"
    txid = _canonical_sha({"request_id": request_id, "execution_token": token})
    ctx = {
        "inputs": inputs,
        "plan": plan,
        "candidates": candidates,
        "build_set": report["build_set"],
        "state_path": state_path,
        "inputs_path": paths_inputs,
        "plan_path": paths_plan,
        "candidate_manifest_path": candidate_manifest_path,
        "request_payload": request_payload,
        "request_id": request_id,
        "token": token,
        "txid": txid,
        "staging_root": _staging_root(txid, token),
    }
    handle, lock_error = _acquire_lock(txid)
    if lock_error:
        return lock_error
    try:
        recheck = _precheck(inputs, state_path, paths_plan, paths_inputs, candidate_manifest_path)
        if recheck["action"] != "multi_page":
            return {"status": recheck.get("commit_status", "rejected"), "errors": recheck["errors"]}
        baseline, errors = _load_state(state_path)
        if errors:
            return {"status": "rejected", "errors": errors}
        ctx["baseline"] = baseline
        staged, stage_error = _stage_all(ctx)
        if stage_error:
            return stage_error
        ctx["staged"] = staged
        new_state = _build_state(ctx, staged)
        final_error = _final_cas(ctx, new_state, staged)
        if final_error:
            shutil.rmtree(_root() / ctx["staging_root"], ignore_errors=True)
            return final_error
        result = _publish(ctx, new_state)
        if result.get("status") == "committed":
            result["request_id"] = request_id
            result["transaction_id"] = txid
        return result
    finally:
        _release_lock(handle)


def recover_transaction(txid: Optional[str] = None):
    """I1/I2 最小恢复：先实现 v2 journal 的成功回滚/清理与基本拒绝。"""
    from app import rebuild_multi_recovery as recovery

    return recovery.recover_transaction(txid)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.rebuild_multi")
    parser.add_argument("command", choices=["precheck", "commit", "recover"])
    parser.add_argument("--plan", default=str(inc.DEFAULT_PLAN))
    parser.add_argument("--state", default=str(inc.DEFAULT_STATE))
    parser.add_argument("--inputs", default=str(inc.DEFAULT_INPUTS))
    parser.add_argument("--candidate-manifest")
    parser.add_argument("--txid")
    args = parser.parse_args(argv)
    candidate = Path(args.candidate_manifest) if args.candidate_manifest else None
    if args.command == "precheck":
        report = precheck_rebuild(Path(args.plan), Path(args.state), Path(args.inputs), candidate)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "commit":
        result = commit_rebuild(Path(args.plan), Path(args.state), Path(args.inputs), candidate)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in ("committed", "already_committed", "committed_cleanup_pending", "no_op") else 1
    result = recover_transaction(args.txid)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""B203 contract gate：core / compat / old / all / freeze。

用法：
  python scripts/check_b203_contracts.py core
  python scripts/check_b203_contracts.py compat
  python scripts/check_b203_contracts.py old
  python scripts/check_b203_contracts.py all
  python scripts/check_b203_contracts.py freeze --run-id <id>
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests/b203/contract_manifest.json"
SNAPSHOT_DIR = ROOT / "snapshots"
SNAPSHOT_PATH = SNAPSHOT_DIR / "current.json"
LOG_ROOT = ROOT / "logs/contract-check"
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
GREEN_K = "test_g0 or test_t1b_g1 or test_c1 or test_c2_g or test_governance_gate"
RED_K = "not test_t1b_g1 and not test_c2_g"
RED_MESSAGE = "Failed: B203_API_MISSING: app.rebuild_multi"
MANAGED_BASES = [
    "tests/b203",
    "openspec/changes/016-multi-page-commit",
    "docs/b203-review-protocol.md",
    "docs/b203-backlog.md",
    "docs/b203-gpt-review-contract.md",
    "scripts/check_b203_contracts.py",
    "app/rebuild_multi.py",
    "app/rebuild_multi_recovery.py",
]


def logdir(run_id):
    path = LOG_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def managed_files():
    result = []
    for base in MANAGED_BASES:
        path = ROOT / base
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            result.extend(
                item for item in path.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"
            )
    return sorted({item.relative_to(ROOT).as_posix() for item in result})


def run_pytest(args, log, junit=None, timeout=None):
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if junit is not None:
        command += ["--junitxml", str(junit)]
    command += args
    with open(log, "w", encoding="utf-8") as handle:
        result = subprocess.run(
            command, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT, env=ENV, timeout=timeout,
        )
    return result.returncode, Path(log).read_text(encoding="utf-8")


def collect_nodeids(text):
    return {
        line.strip()
        for line in text.splitlines()
        if "::" in line and not line.strip().startswith(("=", "FAILED", "ERROR"))
    }


def parse_junit(path):
    results = {}
    for case in ET.parse(path).iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        file_attr = case.get("file")
        relative = file_attr.replace("\\", "/") if file_attr else classname.replace(".", "/") + ".py"
        nodeid = f"{relative}::{name}"
        outcome = "passed"
        message = ""
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[child.tag]
                message = child.get("message") or ("skipped" if child.tag == "skipped" else "")
        results[nodeid] = {"outcome": outcome, "message": message}
    return results


def classify_results(results, expected, *, message=None, require_pass=True):
    errors = []
    expected_set = set(expected)
    actual = set(results)
    missing = expected_set - actual
    extra = actual - expected_set
    if missing:
        errors.append(f"missing nodeids: {sorted(missing)[:5]}")
    if extra:
        errors.append(f"extra nodeids: {sorted(extra)[:5]}")
    for nodeid in sorted(expected_set & actual):
        item = results[nodeid]
        if require_pass:
            if item["outcome"] != "passed":
                errors.append(f"{nodeid}: {item['outcome']}")
        else:
            if item["outcome"] != "failed":
                errors.append(f"{nodeid}: expected failed, got {item['outcome']}")
            elif message is not None and item["message"].strip() != message:
                errors.append(f"{nodeid}: unexpected message {item['message']!r}")
    return errors


def partition_errors(collect_set, green_set, red_set, label):
    errors = []
    collect_set = set(collect_set)
    green_set = set(green_set)
    red_set = set(red_set)
    overlap = green_set & red_set
    if overlap:
        errors.append(f"{label}: green/red overlap: {sorted(overlap)[:5]}")
    union = green_set | red_set
    missing = collect_set - union
    extra = union - collect_set
    if missing:
        errors.append(f"{label}: collected but unclassified: {sorted(missing)[:5]}")
    if extra:
        errors.append(f"{label}: classified but not collected: {sorted(extra)[:5]}")
    return errors


def validate_snapshot(data, manifest, managed):
    errors = []
    if not isinstance(data, dict):
        return ["snapshot is not an object"]
    snapshot_id = data.get("snapshot_id")
    if not snapshot_id:
        errors.append("snapshot_id missing")
    if snapshot_id != manifest.get("current_snapshot_id"):
        errors.append(f"snapshot_id {snapshot_id!r} != manifest current {manifest.get('current_snapshot_id')!r}")
    parent = data.get("parent_snapshot_id")
    if parent == snapshot_id:
        errors.append("parent_snapshot_id must not equal snapshot_id")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files must be a non-empty list")
        return errors
    paths = {item.get("path") for item in files if isinstance(item, dict)}
    if None in paths:
        errors.append("file entry missing path")
    managed = set(managed)
    missing = sorted(managed - paths)
    extra = sorted(paths - managed)
    if missing:
        errors.append(f"snapshot missing managed files: {missing[:5]}")
    if extra:
        errors.append(f"snapshot has unmanaged files: {extra[:5]}")
    return errors


def validate_history_chain(manifest, snapshot_dir, current_data):
    errors = []
    chain = list(manifest.get("snapshot_chain") or [])
    current_id = manifest.get("current_snapshot_id")
    snapshot_dir = Path(snapshot_dir)
    if not chain:
        errors.append("snapshot_chain is empty")
        return errors
    if current_id != chain[-1]:
        errors.append(f"current_snapshot_id {current_id!r} != chain tail {chain[-1]!r}")
    current_parent = current_data.get("parent_snapshot_id")
    expected_parent = chain[-2] if len(chain) > 1 else None
    if current_parent != expected_parent:
        errors.append(f"current parent {current_parent!r} != expected {expected_parent!r}")
    for index, snapshot_id in enumerate(chain):
        named = snapshot_dir / f"{snapshot_id}.json"
        if not named.exists():
            errors.append(f"named snapshot missing: {snapshot_id}")
            continue
        try:
            data = json.loads(named.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"named snapshot unreadable: {snapshot_id}: {exc}")
            continue
        if data.get("snapshot_id") != snapshot_id:
            errors.append(f"named snapshot identity mismatch: {snapshot_id}")
        parent = chain[index - 1] if index > 0 else None
        if data.get("parent_snapshot_id") != parent:
            errors.append(f"named snapshot parent mismatch: {snapshot_id}")
        if snapshot_id == current_id and data != current_data:
            errors.append("current copy differs from named snapshot")
    return errors


def plan_freeze_parent(manifest, run_id, snapshot_dir=None):
    snapshot_dir = Path(snapshot_dir or SNAPSHOT_DIR)
    if (snapshot_dir / f"{run_id}.json").exists():
        raise ValueError(f"snapshot id already exists: {run_id}")
    chain = list(manifest.get("snapshot_chain") or [])
    parent = chain[-1] if chain else None
    if parent == run_id:
        raise ValueError("parent snapshot id must not equal new snapshot id")
    if parent and not (snapshot_dir / f"{parent}.json").exists():
        raise ValueError(f"parent snapshot file missing: {parent}")
    return parent


def check_hashes():
    if not SNAPSHOT_PATH.exists():
        print(f"[FAIL hash] {SNAPSHOT_PATH} missing")
        return False
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[FAIL hash] snapshot/manifest unreadable: {exc}")
        return False
    errors = validate_snapshot(data, manifest, managed_files())
    errors.extend(validate_history_chain(manifest, SNAPSHOT_DIR, data))
    for item in data.get("files") or []:
        path = ROOT / item.get("path", "")
        if not path.exists() or sha256(path) != item.get("sha256"):
            errors.append(f"hash mismatch: {item.get('path')}")
    if errors:
        for error in errors:
            print(f"[FAIL hash] {error}")
        return False
    print(f"[hash] PASS {len(data['files'])} files")
    return True


def check_core(run_dir):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = manifest["expected"]
    ok = check_hashes()
    if manifest.get("phase") == "implementation":
        code, text = run_pytest(["tests/b203"], run_dir / "collect.log", run_dir / "collect.xml")
        results = parse_junit(run_dir / "collect.xml")
        errors = classify_results(results, expected["collect"])
        print(f"[impl] exit={code} actual={len(results)} errors={errors[:2]}")
        return ok and code == 0 and not errors

    expected_partition = partition_errors(expected["collect"], expected["green"], expected["red"], "expected")
    if expected_partition:
        print(f"[FAIL partition] {expected_partition}")
        ok = False

    code, text = run_pytest(["tests/b203", "--collect-only"], run_dir / "collect.log")
    actual = set(collect_nodeids(text))
    collect_ok = actual == set(expected["collect"])
    print(f"[collect] exit={code} actual={len(actual)} expected={len(expected['collect'])}")
    ok = ok and code == 0 and collect_ok

    code, text = run_pytest(["tests/b203", "-k", GREEN_K], run_dir / "green.log", run_dir / "green.xml")
    green = parse_junit(run_dir / "green.xml")
    green_errors = classify_results(green, expected["green"])
    print(f"[green] exit={code} actual={len(green)} errors={green_errors[:2]}")
    ok = ok and code == 0 and not green_errors

    code, text = run_pytest(
        [
            "tests/b203/test_multi_entry_contract.py",
            "tests/b203/test_t1b_contract.py",
            "tests/b203/test_recovery_entry_contract.py",
            "-k",
            RED_K,
        ],
        run_dir / "red.log",
        run_dir / "red.xml",
    )
    red = parse_junit(run_dir / "red.xml")
    red_errors = classify_results(red, expected["red"], message=RED_MESSAGE, require_pass=False)
    print(f"[red] exit={code} actual={len(red)} errors={red_errors[:2]}")
    actual_partition = partition_errors(actual, set(green), set(red), "actual")
    if actual_partition:
        print(f"[FAIL partition] {actual_partition}")
    ok = ok and code == 1 and not red_errors and not actual_partition
    return ok


def check_compat(run_dir):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    files = sorted(str(p) for p in (ROOT / "tests/b203/compat").glob("probe_*.py"))
    if not files:
        print("[FAIL compat] no probe_*.py found")
        return False
    code, text = run_pytest(files, run_dir / "compat.log", run_dir / "compat.xml")
    results = parse_junit(run_dir / "compat.xml")
    errors = classify_results(results, manifest["expected"]["compat"])
    print(f"[compat] exit={code} actual={len(results)} errors={errors[:2]}")
    return code == 0 and not errors


def check_old(run_dir):
    code, text = run_pytest(["--ignore=tests/b203"], run_dir / "old_regression.log")
    print(f"[old] exit={code}")
    return code == 0


def freeze(run_id):
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    parent = plan_freeze_parent(manifest, run_id)
    manifest["snapshot_chain"] = list(manifest.get("snapshot_chain") or []) + [run_id]
    manifest["current_snapshot_id"] = run_id
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files = managed_files()
    snapshot = {
        "format_version": 3,
        "snapshot_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "parent_snapshot_id": parent,
        "parent_snapshot_file": f"snapshots/{parent}.json" if parent else None,
        "historical_source_complete": parent == "b203-review-audit-20261015-selected",
        "project_root": str(ROOT),
        "managed_roots": MANAGED_BASES,
        "files": [{"path": rel, "sha256": sha256(ROOT / rel)} for rel in files],
    }
    target = SNAPSHOT_DIR / f"{run_id}.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[freeze] {target} + current.json ({len(files)} files, parent={parent})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scope", choices=["core", "compat", "old", "all", "freeze"])
    parser.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.scope == "freeze":
        freeze(args.run_id)
        return
    run_dir = logdir(args.run_id)
    checks = {
        "core": [check_core],
        "compat": [check_compat],
        "old": [check_old],
        "all": [check_core, check_compat, check_old],
    }[args.scope]
    ok = True
    for check in checks:
        ok = check(run_dir) and ok
    print(f"[verdict] {'PASS' if ok else 'FAIL'} scope={args.scope} run={args.run_id}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

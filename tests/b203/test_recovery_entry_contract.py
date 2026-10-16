# -*- coding: utf-8 -*-
"""B203 T1-C2：公共 recover 有限组测试资产（当前预期 API 缺失）。"""
import importlib
import json
from pathlib import Path

import pytest

from . import recovery_support as rs
from . import support


def load_recover_api():
    try:
        return importlib.import_module("app.rebuild_multi")
    except ModuleNotFoundError as exc:
        if exc.name != "app.rebuild_multi":
            raise
        pytest.fail("B203_API_MISSING: app.rebuild_multi", pytrace=False)


def _journal_page(case, key):
    return next(page for page in case["journal"]["pages"] if page["page_key"] == key)


def _install_sentinels(case):
    root = case["root"]
    owned = root / "reports/incremental/staging" / case["txid"] / case["token"] / "owned.bin"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_bytes(b"owned staging content")
    sentinels = [
        f"reports/incremental/staging/{case['txid']}/owner-sentinel.txt",
        f"reports/incremental/staging/{case['txid']}/other-token/sentinel.txt",
        "reports/incremental/staging/other-transaction/token/sentinel.txt",
        "reports/incremental/backups/other-transaction/sentinel.txt",
        "reports/incremental/provenance/other-transaction/sentinel.txt",
    ]
    for relative in sentinels:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must remain unchanged")


def _remove_last_backup(case):
    key = case["keys"][-1]
    path = case["root"] / case["backups"][key]["path"]
    path.unlink(missing_ok=True)


def _prepare(scenario, tmp_path, monkeypatch):
    if scenario == "RC07":
        case = rs.make_recovery_case(tmp_path, monkeypatch, old_equal=("a",), conservative_full=True)
    elif scenario == "RC08":
        case = rs.make_recovery_case(tmp_path, monkeypatch, missing_old=("c",))
    elif scenario == "RC15":
        case = rs.clone_with_token(rs.make_recovery_case(tmp_path, monkeypatch), "token-rec")
    else:
        case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)

    expected_outputs = {key: case["old_outputs"][key] for key in case["keys"]}
    expected_state = case["old_state_bytes"]
    provenance_removed = True
    expected_error = None

    if scenario == "RC01":
        rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
        for key in (case["page_keys"]["a"], case["page_keys"]["b"]):
            (case["root"] / case["registry"][key]["output"]).write_bytes(case["new_outputs"][key])
    elif scenario == "RC02":
        rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
    elif scenario == "RC03":
        rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
        expected_state = case["new_state_bytes"]
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
        provenance_removed = False
    elif scenario == "RC04":
        rs.materialize_layout(case, state_variant="old", outputs="old", provenance="partial", backups="full")
    elif scenario == "RC05":
        rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
        _remove_last_backup(case)
        expected_error = f"journal_backup_invalid:{case['journal_name']}"
    elif scenario == "RC06":
        rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
        key = case["page_keys"]["c"]
        (case["root"] / case["registry"][key]["output"]).write_bytes(b"third-party")
        expected_error = f"journal_resource_conflict:{case['journal_name']}"
    elif scenario == "RC07":
        rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
        for key in (case["page_keys"]["a"], case["page_keys"]["b"]):
            (case["root"] / case["registry"][key]["output"]).write_bytes(case["new_outputs"][key])
    elif scenario == "RC08":
        rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
        key = case["page_keys"]["c"]
        (case["root"] / case["registry"][key]["output"]).write_bytes(case["new_outputs"][key])
    elif scenario == "RC09":
        rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
        _remove_last_backup(case)
    elif scenario == "RC10":
        rs.materialize_layout(
            case, state_variant="new", outputs="new", provenance="full", backups="full", status="committed"
        )
        _remove_last_backup(case)
        expected_state = case["new_state_bytes"]
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
        provenance_removed = False
    elif scenario == "RC11":
        rs.materialize_layout(case, state_variant="new", outputs="new", provenance="missing", backups="full")
        expected_state = case["new_state_bytes"]
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
        provenance_removed = False
        expected_error = f"journal_resource_conflict:{case['journal_name']}"
    elif scenario in ("RC12", "RC13"):
        rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
        journal_path = case["root"] / "reports/incremental/journal" / case["journal_name"]
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if scenario == "RC12":
            journal["pages"][0]["response"]["sha256"] = "0" * 64
            expected_error = f"journal_projection_mismatch:{case['journal_name']}"
        else:
            journal["staging_root"] = f"reports/incremental/staging/{case['txid']}"
            expected_error = f"journal_resource_ownership:{case['journal_name']}"
        journal_path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
    elif scenario == "RC14":
        rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
        case["plan"].unlink(missing_ok=True)
        expected_state = case["new_state_bytes"]
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
        provenance_removed = False
    elif scenario == "RC15":
        rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
        expected_state = case["new_state_bytes"]
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
        provenance_removed = False
    else:
        raise AssertionError(scenario)

    _install_sentinels(case)
    _assert_prepared(case, scenario)
    case["_expected_state"] = expected_state
    case["_expected_outputs"] = expected_outputs
    case["_provenance_removed"] = provenance_removed
    case["_expected_error"] = expected_error
    case["_before_files"] = support.snapshot_files(case["root"])
    case["_before_dirs"] = support.snapshot_dirs(case["root"])
    case["_before"] = rs.snapshot_recovery_resources(case)
    return case


def _assert_prepared(case, scenario):
    root = case["root"]
    committed = scenario in ("RC03", "RC10", "RC11", "RC14", "RC15")
    expected_state = case["new_state_bytes"] if committed else case["old_state_bytes"]
    assert case["state"].read_bytes() == expected_state

    expected_outputs = {key: case["old_outputs"][key] for key in case["keys"]}
    if scenario in ("RC02", "RC03", "RC05", "RC06", "RC10", "RC11", "RC12", "RC13", "RC14", "RC15"):
        expected_outputs = {key: case["new_outputs"][key] for key in case["keys"]}
    elif scenario == "RC01":
        for key in (case["page_keys"]["a"], case["page_keys"]["b"]):
            expected_outputs[key] = case["new_outputs"][key]
    elif scenario == "RC07":
        for key in (case["page_keys"]["a"], case["page_keys"]["b"]):
            expected_outputs[key] = case["new_outputs"][key]
    elif scenario == "RC08":
        expected_outputs[case["page_keys"]["c"]] = case["new_outputs"][case["page_keys"]["c"]]
    if scenario == "RC06":
        expected_outputs[case["page_keys"]["c"]] = b"third-party"
    for key, payload in expected_outputs.items():
        output = root / case["registry"][key]["output"]
        assert (output.read_bytes() if output.exists() else None) == payload

    journal_path = root / "reports/incremental/journal" / case["journal_name"]
    disk = json.loads(journal_path.read_text(encoding="utf-8"))
    if scenario == "RC10":
        assert disk["status"] == "committed"
        expected_journal = dict(case["journal"])
        expected_journal["status"] = "committed"
        assert disk == expected_journal
    elif scenario == "RC12":
        expected = json.loads(json.dumps(case["journal"]))
        expected["pages"][0]["response"]["sha256"] = "0" * 64
        assert disk == expected
        with pytest.raises(AssertionError) as caught:
            rs.validate_journal_projection(case, disk)
        assert str(caught.value) == "response sha256 mismatch"
    elif scenario == "RC13":
        expected = json.loads(json.dumps(case["journal"]))
        expected["staging_root"] = f"reports/incremental/staging/{case['txid']}"
        assert disk == expected
        with pytest.raises(AssertionError) as caught:
            rs.validate_journal_projection(case, disk)
        assert str(caught.value) == "staging owner mismatch"
    else:
        assert disk == case["journal"]

    for index, key in enumerate(case["keys"]):
        backup = root / case["backups"][key]["path"]
        if case["old_outputs"][key] is None:
            assert not backup.exists()
        elif scenario in ("RC05", "RC09", "RC10") and index == len(case["keys"]) - 1:
            assert not backup.exists()
        else:
            assert backup.read_bytes() == case["old_outputs"][key]

    if scenario == "RC04":
        for key in case["scope"]:
            missing = rs.assert_provenance_subset(root, _journal_page(case, key), case["provenance"][key]["members"])
            assert missing
    elif scenario == "RC11":
        for key in case["scope"]:
            assert not (root / case["provenance"][key]["dir"]).exists()
    else:
        for key in case["scope"]:
            support.verify_provenance(root, _journal_page(case, key), case["snapshots"][key])

    owned = root / case["journal"]["staging_root"]
    assert owned.is_dir() and any(path.is_file() for path in owned.rglob("*"))
    sentinels = [
        f"reports/incremental/staging/{case['txid']}/owner-sentinel.txt",
        f"reports/incremental/staging/{case['txid']}/other-token/sentinel.txt",
        "reports/incremental/staging/other-transaction/token/sentinel.txt",
        "reports/incremental/backups/other-transaction/sentinel.txt",
        "reports/incremental/provenance/other-transaction/sentinel.txt",
    ]
    for relative in sentinels:
        assert (root / relative).read_bytes() == b"must remain unchanged"
    if scenario == "RC07":
        assert case["plan_data"]["full_required"] is True
        key = case["page_keys"]["a"]
        assert case["old_outputs"][key] == case["new_outputs"][key]
    if scenario == "RC08":
        assert case["old_outputs"][case["page_keys"]["c"]] is None
    if scenario == "RC14":
        assert not case["plan"].exists()


def _assert_recovery_required(case, result, before):
    assert result.get("status") == "recovery_required", result
    errors = result.get("errors")
    assert isinstance(errors, list) and errors
    assert all(isinstance(item, str) for item in errors)
    assert case["_expected_error"] in errors
    assert rs.snapshot_recovery_resources(case) == before


def _assert_cleanup_directories(case, before_dirs, removed_roots, after_dirs):
    from pathlib import PurePosixPath

    before_dirs = set(before_dirs)
    after_dirs = set(after_dirs)
    removed_roots = set(removed_roots)
    required_absent = {
        directory
        for directory in before_dirs
        if any(directory == root or directory.startswith(root + "/") for root in removed_roots)
    }
    optional_empty_parents = {"reports/incremental/journal"}
    optional_empty_parents.update(
        str(PurePosixPath(case["backups"][key]["path"]).parent)
        for key in case["scope"]
    )
    if case["_provenance_removed"]:
        optional_empty_parents.update(
            str(PurePosixPath(case["provenance"][key]["dir"]).parent)
            for key in case["scope"]
        )

    assert not (after_dirs & required_absent), "owned cleanup directories remain"
    required_present = before_dirs - required_absent - optional_empty_parents
    assert required_present <= after_dirs, "protected directory removed"
    assert after_dirs <= before_dirs - required_absent, "unexpected directory added"


def _assert_ok(case, result):
    assert result.get("status") == "ok", result
    assert result.get("resolved") == 1, result
    assert case["state"].read_bytes() == case["_expected_state"]
    for key, payload in case["_expected_outputs"].items():
        output = case["root"] / case["registry"][key]["output"]
        if payload is None:
            assert not output.exists()
        else:
            assert output.read_bytes() == payload
    for key in case["scope"]:
        directory = case["root"] / case["provenance"][key]["dir"]
        if case["_provenance_removed"]:
            assert not directory.exists()
        else:
            support.verify_provenance(case["root"], _journal_page(case, key), case["snapshots"][key])

    snapshot = case.get("_before")
    assert snapshot is not None
    before_files = {
        relative: payload
        for relative, payload in snapshot.items()
        if relative != "__dirs__" and payload is not rs.MISSING
    }
    before_dirs = set(snapshot.get("__dirs__", []))
    expected_files = dict(before_files)
    journal_relative = (
        (case["root"] / "reports/incremental/journal" / case["journal_name"])
        .relative_to(case["root"])
        .as_posix()
    )
    state_relative = case["state"].relative_to(case["root"]).as_posix()
    expected_files.pop(journal_relative, None)
    for key in case["scope"]:
        expected_files.pop(case["backups"][key]["path"], None)
    staging_root = case["journal"]["staging_root"]
    allowed_dir_prefixes = {staging_root}
    for relative in list(expected_files):
        if relative == staging_root or relative.startswith(staging_root + "/"):
            expected_files.pop(relative)
    if case["_provenance_removed"]:
        for key in case["scope"]:
            provenance_dir = case["provenance"][key]["dir"]
            allowed_dir_prefixes.add(provenance_dir)
            for relative in list(expected_files):
                if relative == provenance_dir or relative.startswith(provenance_dir + "/"):
                    expected_files.pop(relative)
    expected_files[state_relative] = case["_expected_state"]
    for key, payload in case["_expected_outputs"].items():
        relative = case["registry"][key]["output"]
        if payload is None:
            expected_files.pop(relative, None)
        else:
            expected_files[relative] = payload
    actual_files = support.snapshot_files(case["root"])
    assert actual_files == expected_files

    _assert_cleanup_directories(
        case, before_dirs, allowed_dir_prefixes, support.snapshot_dirs(case["root"])
    )

@pytest.mark.parametrize(
    "scenario",
    ["RC01", "RC02", "RC03", "RC04", "RC05", "RC06", "RC07", "RC08", "RC09", "RC10", "RC11", "RC12", "RC13", "RC14", "RC15"],
)
def test_c2_public_recover(scenario, tmp_path, monkeypatch):
    case = _prepare(scenario, tmp_path, monkeypatch)
    before = case["_before"]
    api = load_recover_api()
    result = api.recover_transaction(case["txid"])
    if scenario in ("RC05", "RC06", "RC11", "RC12", "RC13"):
        _assert_recovery_required(case, result, before)
    else:
        _assert_ok(case, result)
        after_first = rs.snapshot_recovery_resources(case)
        second = api.recover_transaction()
        assert second["status"] == "ok"
        assert second["resolved"] == 0
        assert rs.snapshot_recovery_resources(case) == after_first


def _apply_expected_cleanup(case):
    root = case["root"]
    for key, payload in case["_expected_outputs"].items():
        output = root / case["registry"][key]["output"]
        if payload is None:
            output.unlink(missing_ok=True)
        else:
            output.write_bytes(payload)
    (root / "reports/incremental/journal" / case["journal_name"]).unlink()
    owned = root / case["journal"]["staging_root"]
    if owned.exists():
        import shutil

        shutil.rmtree(owned)
    for key in case["scope"]:
        (root / case["backups"][key]["path"]).unlink(missing_ok=True)
        if case["_provenance_removed"]:
            import shutil

            shutil.rmtree(root / case["provenance"][key]["dir"])


def test_c2_g_ok_assertion_resource_boundaries(tmp_path, monkeypatch):
    case = _prepare("RC04", tmp_path, monkeypatch)
    sibling = case["root"] / "reports/incremental/staging" / case["txid"] / "audit-sibling/sentinel.txt"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(b"preserve sibling")
    case["_before"] = rs.snapshot_recovery_resources(case)
    _apply_expected_cleanup(case)
    _assert_ok(case, {"status": "ok", "resolved": 1})

    owned = case["root"] / case["journal"]["staging_root"] / "owned.bin"
    owned.parent.mkdir(parents=True)
    owned.write_bytes(b"owned staging content")
    with pytest.raises(AssertionError):
        _assert_ok(case, {"status": "ok", "resolved": 1})


@pytest.mark.parametrize("damage", ["output", "backup", "journal"])
def test_c2_g_prepare_rejects_materialized_damage(damage, tmp_path, monkeypatch):
    materialize = rs.materialize_layout

    def materialize_and_damage(case, *args, **kwargs):
        result = materialize(case, *args, **kwargs)
        root = case["root"]
        if damage == "output":
            last = case["keys"][-1]
            (root / case["registry"][last]["output"]).write_bytes(b"unexpected output")
        elif damage == "backup":
            key = case["keys"][0]
            (root / case["backups"][key]["path"]).write_bytes(b"unexpected backup")
        else:
            journal = root / "reports/incremental/journal" / case["journal_name"]
            data = json.loads(journal.read_bytes())
            data["request_id"] = "0" * 64
            journal.write_text(json.dumps(data), encoding="utf-8")
        return result

    monkeypatch.setattr(rs, "materialize_layout", materialize_and_damage)
    with pytest.raises(AssertionError):
        _prepare("RC05" if damage == "backup" else "RC01", tmp_path, monkeypatch)


@pytest.mark.parametrize("parent", ["backup_tx", "provenance_tx", "journal_root"])
def test_c2_g_optional_empty_cleanup_parents(parent, tmp_path, monkeypatch):
    case = _prepare("RC04", tmp_path, monkeypatch)
    _apply_expected_cleanup(case)
    _assert_ok(case, {"status": "ok", "resolved": 1})
    root = case["root"]
    if parent == "backup_tx":
        directory = root / "reports/incremental/backups" / case["txid"]
    elif parent == "provenance_tx":
        directory = root / "reports/incremental/provenance" / case["txid"]
    else:
        directory = root / "reports/incremental/journal"
    assert directory.is_dir() and not any(directory.iterdir())
    directory.rmdir()
    _assert_ok(case, {"status": "ok", "resolved": 1})

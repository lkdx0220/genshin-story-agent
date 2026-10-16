"""Verify the existing allowance to remove empty cleanup parent directories."""

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent / "probe_c2_residual.py"
spec = importlib.util.spec_from_file_location("c2_residual_for_parent_checks", SOURCE)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


@pytest.mark.parametrize("parent", ["backup_tx", "provenance_tx", "journal_root"])
def test_empty_cleanup_parent_may_be_removed(parent, tmp_path, monkeypatch):
    case = previous.prepared("RC04", tmp_path, monkeypatch)
    previous.target(case)
    previous.batch._assert_ok(case, {"status": "ok", "resolved": 1})
    root = case["root"]
    if parent == "backup_tx":
        directory = root / "reports/incremental/backups" / case["txid"]
    elif parent == "provenance_tx":
        directory = root / "reports/incremental/provenance" / case["txid"]
    else:
        directory = root / "reports/incremental/journal"
    assert directory.is_dir() and not any(directory.iterdir())
    directory.rmdir()
    previous.batch._assert_ok(case, {"status": "ok", "resolved": 1})

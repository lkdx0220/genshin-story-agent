"""Reuse the original 30 checks with corrected successful-target assembly."""

import importlib.machinery
import importlib.util
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "probe_independent_c2.py.orig"
spec = importlib.util.spec_from_file_location(
    "c2_original_probe_adapted", SOURCE, loader=importlib.machinery.SourceFileLoader("c2_original_probe_adapted", str(SOURCE))
)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def corrected_target(case):
    root = case["root"]
    case["_before"] = legacy.rs.snapshot_recovery_resources(case)
    case["_before_files"] = legacy.batch.support.snapshot_files(root)
    case["_before_dirs"] = legacy.batch.support.snapshot_dirs(root)
    assert case["state"].read_bytes() == case["_expected_state"]
    (root / "reports/incremental/journal" / case["journal_name"]).unlink()
    shutil.rmtree(root / case["journal"]["staging_root"])
    for key in case["scope"]:
        (root / case["backups"][key]["path"]).unlink(missing_ok=True)


legacy._materialize_expected_new_cleanup = corrected_target
for name in dir(legacy):
    if name.startswith("test_"):
        globals()[name] = getattr(legacy, name)

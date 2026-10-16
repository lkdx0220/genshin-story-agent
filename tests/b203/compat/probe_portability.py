"""GOV-R02：canonical 探针必须绑定当前 checkout。"""
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]


def test_imported_helpers_resolve_to_current_checkout():
    import importlib

    for name in ("b203.support", "b203.recovery_support", "app.incremental"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(PROJECT), (name, str(path), str(PROJECT))


def test_no_compat_source_hardcodes_project_path():
    compat = Path(__file__).resolve().parent
    for path in sorted(compat.glob("probe_*")):
        if path.name == "probe_portability.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "C:/Users/24701/Desktop" not in text, path.name
        assert "C:\\Users\\24701\\Desktop" not in text, path.name

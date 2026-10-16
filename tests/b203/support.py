# -*- coding: utf-8 -*-
"""B203 T1-A 三页真实夹具与独立断言 helper。"""
import hashlib
import json
import shutil
from pathlib import Path

from app import canonical
from app import incremental as inc
import ingest
import wiki_lint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTITY_ID = "char:traveler"
DATA_MODE = "fixture"
SCHEMA = "wiki/__schema__/entity-template.md"
PROMPT = "wiki/__schema__/ingest-traveler.prompt.md"

PAGES = (
    {
        "key": "a",
        "page_key": "responses/a.md|fixture|char:traveler",
        "bundle": "traveler-fixture",
        "root": "tests/fixtures/traveler/raw",
        "response": "responses/a.md",
        "output": "reports/fixtures/a.md",
        "prompt": PROMPT,
    },
    {
        "key": "b",
        "page_key": "responses/b.md|fixture|char:traveler",
        "bundle": "traveler-conflict-fixture",
        "root": "tests/fixtures/traveler-conflict/raw",
        "response": "responses/b.md",
        "output": "reports/fixtures/b.md",
        "prompt": PROMPT,
    },
    {
        "key": "c",
        "page_key": "responses/c.md|fixture|char:traveler",
        "bundle": "traveler-fixture",
        "root": "tests/fixtures/traveler/raw",
        "response": "responses/c.md",
        "output": "reports/fixtures/c.md",
        "prompt": PROMPT,
    },
)

ID_MAP = {
    "identity:traveler": "identity:traveler-p3",
    "name:traveler": "name:traveler-p3",
    "name:aether": "name:aether-p3",
    "name:lumine": "name:lumine-p3",
    "event:common": "event:common-p3",
    "event:aether": "event:aether-p3",
    "event:lumine": "event:lumine-p3",
}


def C(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def project_root_id(root):
    return sha256_bytes(str(Path(root).resolve()).encode("utf-8"))


def snapshot_files(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result

def snapshot_dirs(root):
    root = Path(root)
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}

def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def replace_equal_strings(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_equal_strings(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: replace_equal_strings(item, mapping) for key, item in value.items()}
    return value


def build_c_response(a_text):
    data, body = ingest.parse_page(a_text)
    data = replace_equal_strings(data, ID_MAP)
    front = json.dumps(data, ensure_ascii=False, indent=2)
    return "---\n" + front + "\n---\n" + body


def _copy_base(root):
    for relative in (
        "wiki/__schema__",
        "lint",
        "tests/fixtures/traveler",
        "tests/fixtures/traveler-conflict",
    ):
        shutil.copytree(PROJECT_ROOT / relative, root / relative)
    (root / "docs").mkdir()
    shutil.copy2(PROJECT_ROOT / "docs/schema-lock.json", root / "docs/schema-lock.json")


def _config():
    return {
        "format_version": 1,
        "inputs": [
            {
                "bundle_key": "traveler-fixture",
                "root": "tests/fixtures/traveler/raw",
                "data_mode": DATA_MODE,
                "managed_pages": [
                    {
                        "page_key": "responses/a.md|fixture|char:traveler",
                        "entity_id": ENTITY_ID,
                        "response": "responses/a.md",
                        "output": "reports/fixtures/a.md",
                    },
                    {
                        "page_key": "responses/c.md|fixture|char:traveler",
                        "entity_id": ENTITY_ID,
                        "response": "responses/c.md",
                        "output": "reports/fixtures/c.md",
                    },
                ],
                "sources": ["source:traveler-fixture"],
                "profile_ids": [ENTITY_ID],
                "prompt": PROMPT,
            },
            {
                "bundle_key": "traveler-conflict-fixture",
                "root": "tests/fixtures/traveler-conflict/raw",
                "data_mode": DATA_MODE,
                "managed_pages": [
                    {
                        "page_key": "responses/b.md|fixture|char:traveler",
                        "entity_id": ENTITY_ID,
                        "response": "responses/b.md",
                        "output": "reports/fixtures/b.md",
                    }
                ],
                "sources": ["source:traveler-conflict"],
                "profile_ids": [ENTITY_ID],
                "prompt": PROMPT,
            },
        ],
    }


def _patch_roots(monkeypatch, root, config_path):
    monkeypatch.setattr(inc, "REPO_ROOT", root)
    monkeypatch.setattr(inc, "DEFAULT_INPUTS", config_path)
    monkeypatch.setattr(ingest, "ROOT", root)
    monkeypatch.setattr(ingest, "ENTITY_PROFILE_DIR", root / "wiki/__schema__/entities")
    monkeypatch.setattr(wiki_lint, "ROOT", root)
    monkeypatch.setattr(canonical, "REPO_ROOT", root)


def assert_error_member(errors, expected):
    if not isinstance(errors, list) or expected not in errors:
        raise AssertionError(f"expected exact diagnostic {expected!r}, got {errors!r}")


def assert_allowed_changes(before, after, allowed_files, allowed_prefixes):
    allowed_files = set(allowed_files)
    removed = set(before) - set(after)
    if removed:
        raise AssertionError(f"files removed: {sorted(removed)}")
    for relative, data in before.items():
        if relative in allowed_files:
            continue
        if after[relative] != data:
            raise AssertionError(f"file changed without permission: {relative}")
    for relative in set(after) - set(before):
        if not any(relative.startswith(prefix + "/") for prefix in allowed_prefixes):
            raise AssertionError(f"new file outside allowed provenance: {relative}")


def _expected_snapshots(root, item):
    manifest = json.loads((root / item["root"] / "manifest.json").read_text(encoding="utf-8"))
    file_by_source = {source["source_id"]: source["file"] for source in manifest["sources"]}
    bundle = ingest.load_bundle(root / item["root"])
    result = []
    for source in bundle["sources"]:
        artifact = source["artifact_path"]
        raw = (root / item["root"] / file_by_source[source["source_id"]]).read_bytes()
        result.append(
            {
                "source_id": source["source_id"],
                "path": artifact,
                "sha256": source["snapshot_sha256"],
                "bytes": raw,
            }
        )
    return result


def _ingest_pages(root, inputs):
    expected = {}
    reports = {}
    snapshots = {}
    for item in inputs["inputs"]:
        item_snapshots = _expected_snapshots(root, item)
        for page in item["managed_pages"]:
            text, bundle = ingest.ingest(
                root / item["root"],
                root / SCHEMA,
                response_file=root / page["response"],
                provider="response-file",
                output=root / page["output"],
                prompt_path=root / item["prompt"],
                expected_entity_id=page["entity_id"],
            )
            data, _body = ingest.parse_page(text)
            if data.get("entity_id") != page["entity_id"]:
                raise AssertionError("fixture entity mismatch")
            if ingest.lint(text, bundle) != [f"L{i}" for i in range(1, 9)]:
                raise AssertionError("fixture lint failed")
            provenance = (root / page["output"]).parent / "_provenance" / sha256_text(text)[:16]
            report_path = provenance / "ingest-report.json"
            if not report_path.is_file():
                raise AssertionError(f"fixture ingest report missing: {report_path}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            reports[page["page_key"]] = {
                "publication": report.get("publication"),
                "report_path": str(report_path.relative_to(root)),
                "page_status": data.get("page_status"),
                "entity_id": data.get("entity_id"),
                "data_mode": data.get("data_mode"),
                "output_sha256": sha256_file(root / page["output"]),
                "provenance_dir": str(provenance.relative_to(root)),
            }
            expected[page["page_key"]] = (root / page["output"]).read_bytes()
            snapshots[page["page_key"]] = item_snapshots
    return expected, reports, snapshots

def make_multi_case(tmp_path, monkeypatch, dirty=("a", "b")):
    root = tmp_path / "project"
    root.mkdir()
    _copy_base(root)
    (root / "responses").mkdir(parents=True)
    a_bytes = (PROJECT_ROOT / "tests/fixtures/traveler-response.md").read_bytes()
    b_bytes = (PROJECT_ROOT / "tests/fixtures/traveler-conflict-response.md").read_bytes()
    c_bytes = build_c_response(a_bytes.decode("utf-8")).encode("utf-8")
    (root / "responses/a.md").write_bytes(a_bytes)
    (root / "responses/b.md").write_bytes(b_bytes)
    (root / "responses/c.md").write_bytes(c_bytes)
    config_path = root / "config/incremental-inputs.json"
    write_json(config_path, _config())
    _patch_roots(monkeypatch, root, config_path)

    inputs = inc.load_inputs(config_path)
    expected_outputs, reports, snapshots = _ingest_pages(root, inputs)
    for page_key, data in expected_outputs.items():
        page = next(
            p for item in inputs["inputs"] for p in item["managed_pages"] if p["page_key"] == page_key
        )
        (root / page["output"]).write_bytes(data)

    observed = inc.fingerprint_inputs(inputs)
    graph = inc.collect_dependencies(inputs)
    if graph["diagnostics"]:
        raise AssertionError(f"fixture graph diagnostics: {graph['diagnostics']}")
    state = inc.make_baseline(observed, "fixture-parent", graph)
    state_path = root / "reports/incremental/state.json"
    write_json(state_path, state)
    loaded, errors = inc.load_baseline(state_path)
    if loaded is None:
        raise AssertionError(f"fixture baseline invalid: {errors}")

    clean_plan = inc.build_plan(inputs, state_path)
    if clean_plan["dirty"] or clean_plan["full_required"] or clean_plan["blocked"] or clean_plan["unknown"]:
        raise AssertionError(f"fixture not clean: {clean_plan}")

    for page in inputs["inputs"]:
        for managed in page["managed_pages"]:
            key = next(p["key"] for p in PAGES if p["page_key"] == managed["page_key"])
            if key in dirty:
                (root / managed["output"]).write_bytes(f"stale-{key}".encode("utf-8"))

    plan = inc.build_plan(inputs, state_path)
    plan_path = root / "reports/incremental/plan.json"
    write_json(plan_path, plan)

    manifest = {"format_version": 1, "candidates": []}
    for page in inputs["inputs"]:
        for managed in page["managed_pages"]:
            key = next(p["key"] for p in PAGES if p["page_key"] == managed["page_key"])
            if key in dirty:
                manifest["candidates"].append(
                    {"page_key": managed["page_key"], "response": managed["response"]}
                )
    manifest["candidates"].sort(key=lambda item: item["page_key"])
    manifest_path = root / "candidate.json"
    write_json(manifest_path, manifest)

    registry = {}
    for item in inputs["inputs"]:
        for managed in item["managed_pages"]:
            registry[managed["page_key"]] = {
                "bundle_key": item["bundle_key"],
                "root": item["root"],
                "entity_id": managed["entity_id"],
                "data_mode": item["data_mode"],
                "response": managed["response"],
                "output": managed["output"],
                "prompt": item["prompt"],
            }
    return {
        "root": root,
        "config": config_path,
        "state": state_path,
        "plan": plan_path,
        "manifest": manifest_path,
        "inputs": inputs,
        "registry": registry,
        "expected_outputs": expected_outputs,
        "reports": reports,
        "snapshots": snapshots,
        "original_graph": graph,
        "clean_plan": clean_plan,
        "plan_data": plan,
        "parent_generation": "fixture-parent",
        "page_keys": {p["key"]: p["page_key"] for p in PAGES},
    }


def _strict_string_list(value, field):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssertionError(f"{field} must be list[str]")
    return sorted(set(value))


def _authoritative_scope(case, plan_data):
    inputs = case["inputs"]["inputs"]
    if plan_data.get("full_required") is True:
        return sorted(
            managed["page_key"] for item in inputs for managed in item["managed_pages"]
        )
    dirty = plan_data.get("dirty")
    if not isinstance(dirty, dict):
        raise AssertionError("dirty must be dict")
    return sorted(dirty)


def request_payload(case, plan_data, candidates):
    if not isinstance(plan_data.get("observed"), dict):
        raise AssertionError("observed must be dict")
    if not isinstance(plan_data.get("full_required"), bool):
        raise AssertionError("full_required must be bool")
    if not isinstance(plan_data.get("blocked"), list) or not all(
        isinstance(item, str) for item in plan_data.get("blocked")
    ):
        raise AssertionError("blocked must be list[str]")
    if not isinstance(plan_data.get("unknown"), list) or not all(
        isinstance(item, str) for item in plan_data.get("unknown")
    ):
        raise AssertionError("unknown must be list[str]")

    scope = _authoritative_scope(case, plan_data)
    candidate_by_page = {item["page_key"]: item for item in candidates}
    if len(candidate_by_page) != len(candidates):
        raise AssertionError("duplicate candidate page_key")
    if sorted(candidate_by_page) != scope:
        raise AssertionError("candidates must equal authoritative scope")
    affected = _strict_string_list(plan_data.get("affected_pages", []), "affected_pages")

    pages = []
    for page_key in scope:
        managed = next(
            p
            for item in case["inputs"]["inputs"]
            for p in item["managed_pages"]
            if p["page_key"] == page_key
        )
        candidate = candidate_by_page[page_key]
        pages.append(
            {
                "page_key": managed["page_key"],
                "response": managed["response"],
                "output": managed["output"],
                "candidate_sha256": sha256_file(case["root"] / candidate["response"]),
            }
        )
    dirty = plan_data["dirty"]
    normalized_dirty = {}
    for key in sorted(dirty):
        reasons = dirty[key]
        if not isinstance(key, str) or not isinstance(reasons, list) or not all(
            isinstance(item, str) for item in reasons
        ):
            raise AssertionError("dirty must be dict[str, list[str]]")
        normalized_dirty[key] = list(reasons)
    return {
        "project_root_id": project_root_id(case["root"]),
        "protocol": "b4-02-multi-commit-v1",
        "inputs": {"path": "config/incremental-inputs.json", "sha256": sha256_file(case["config"])},
        "state": "reports/incremental/state.json",
        "plan_generation": plan_data.get("generation"),
        "parent_generation": case["parent_generation"],
        "page_keys": scope,
        "pages": sorted(pages, key=lambda item: item["page_key"]),
        "execution": {
            "observed": plan_data["observed"],
            "dirty": normalized_dirty,
            "full_required": plan_data["full_required"],
            "blocked": sorted(set(plan_data["blocked"])),
            "unknown": sorted(set(plan_data["unknown"])),
            "affected_pages": affected,
        },
    }


def expected_provenance_dir(case, transaction_id, page_key):
    slug = hashlib.sha256(page_key.encode("utf-8")).hexdigest()[:16]
    return f"reports/incremental/provenance/{transaction_id}/{slug}"


def _load_provenance(directory, provenance):
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "b4-02-provenance-v1":
        raise AssertionError("bad provenance protocol")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise AssertionError("provenance files must be non-empty list")
    paths = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise AssertionError("bad provenance file entry")
        paths.append(item["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise AssertionError("provenance files must be sorted and unique")
    if "manifest.json" in paths:
        raise AssertionError("manifest.json must not be a member")
    actual = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative == "manifest.json":
            continue
        if path.is_symlink():
            raise AssertionError("provenance symlink not allowed")
        actual[relative] = path.read_bytes()
    if set(actual) != set(paths):
        raise AssertionError("provenance members do not match actual files")
    required = {"ingest-report.json", "ingest-prompt.txt", "sources.json", "spans.jsonl", "paragraph-index.json"}
    if not required <= set(paths):
        raise AssertionError(f"provenance missing required evidence: {sorted(required - set(paths))}")
    manifest_hashes = {}
    for item in files:
        data = actual[item["path"]]
        if not data:
            raise AssertionError(f"provenance evidence empty: {item['path']}")
        if sha256_bytes(data) != item["sha256"]:
            raise AssertionError(f"provenance member hash mismatch: {item['path']}")
        manifest_hashes[item["path"]] = item["sha256"]
    if sha256_bytes(C(manifest)) != provenance["manifest_sha256"]:
        raise AssertionError("provenance manifest sha256 mismatch")
    return manifest_hashes, actual


def _verify_provenance_snapshots(directory, actual, expected_snapshots, manifest_hashes):
    if not expected_snapshots:
        raise AssertionError("expected snapshots must be provided")
    sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    source_by_id = {source["source_id"]: source for source in sources}
    for expected in expected_snapshots:
        source = source_by_id.get(expected["source_id"])
        if source is None:
            raise AssertionError(f"sources.json missing source: {expected['source_id']}")
        if source.get("artifact_path") != expected["path"]:
            raise AssertionError(f"artifact_path mismatch for {expected['source_id']}")
        if source.get("snapshot_sha256") != expected["sha256"]:
            raise AssertionError(f"snapshot_sha256 mismatch for {expected['source_id']}")
        path = expected["path"]
        if path not in actual:
            raise AssertionError(f"raw snapshot missing: {path}")
        if actual[path] != expected["bytes"]:
            raise AssertionError(f"raw snapshot bytes mismatch: {path}")
        if manifest_hashes.get(path) != expected["sha256"]:
            raise AssertionError(f"raw snapshot not bound in manifest: {path}")
    report = json.loads((directory / "ingest-report.json").read_text(encoding="utf-8"))
    if report.get("publication") != "draft_only":
        raise AssertionError("provenance ingest report is not draft_only")


def verify_provenance(root, page_entry, expected_snapshots):
    provenance = page_entry["provenance"]
    directory = Path(root) / provenance["dir"]
    if not directory.is_dir():
        raise AssertionError(f"provenance dir missing: {directory}")
    manifest_hashes, actual = _load_provenance(directory, provenance)
    _verify_provenance_snapshots(directory, actual, expected_snapshots, manifest_hashes)


def _assert_build_identity(case, state, build, result, expected_scope, candidates):
    loaded, errors = inc.load_baseline(case["state"])
    if loaded is None:
        raise AssertionError(f"state invalid: {errors}")
    if state.get("generation") == case["parent_generation"]:
        raise AssertionError("generation did not advance")
    if build.get("parent_generation") != case["parent_generation"]:
        raise AssertionError("parent_generation mismatch")
    if build.get("plan_generation") != case["plan_data"]["generation"]:
        raise AssertionError("plan_generation mismatch")
    if build.get("generation") != state["generation"]:
        raise AssertionError("build generation mismatch")
    if state.get("graph", {}).get("generation") != state["generation"]:
        raise AssertionError("graph generation mismatch")
    ordered = sorted(expected_scope)
    if list(build.get("pages") or []) != ordered:
        raise AssertionError(f"page order/scope mismatch: {list(build.get('pages') or [])}")
    for entry in (build.get("pages") or {}).values():
        if entry.get("generation") != state["generation"]:
            raise AssertionError("page generation mismatch")
    request_id = expected_request_id(case, case["plan_data"], candidates)
    if build.get("request_id") != request_id or result.get("request_id") != request_id:
        raise AssertionError("request_id mismatch")
    transaction_id = expected_transaction_id(request_id, build.get("execution_token"))
    if build.get("transaction_id") != transaction_id or result.get("transaction_id") != transaction_id:
        raise AssertionError("transaction_id mismatch")
    return ordered


def _assert_page_entries(case, result, build, ordered):
    allowed = {"reports/incremental/state.json"}
    provenance_dirs = set()
    for page_key in ordered:
        managed = case["registry"][page_key]
        entry = build["pages"][page_key]
        if entry.get("response", {}).get("path") != managed["response"]:
            raise AssertionError("response path mismatch")
        if entry.get("output", {}).get("path") != managed["output"]:
            raise AssertionError("output path mismatch")
        if entry["response"].get("sha256") != sha256_file(Path(case["root"]) / managed["response"]):
            raise AssertionError("response sha256 mismatch")
        if entry["output"].get("sha256") != sha256_file(Path(case["root"]) / managed["output"]):
            raise AssertionError("output sha256 mismatch")
        if (Path(case["root"]) / managed["output"]).read_bytes() != case["expected_outputs"][page_key]:
            raise AssertionError("output bytes mismatch")
        expected_dir = expected_provenance_dir(case, result["transaction_id"], page_key)
        if entry["provenance"].get("dir") != expected_dir:
            raise AssertionError("provenance dir mismatch")
        provenance_dirs.add(expected_dir)
        verify_provenance(case["root"], entry, case["snapshots"][page_key])
        allowed.add(managed["output"])
    return allowed, provenance_dirs


def _assert_multi_cleanup(case, state, ordered):
    if (Path(case["root"]) / "reports/incremental/commit.lock").exists():
        raise AssertionError("lock left behind")
    if list((Path(case["root"]) / "reports/incremental/journal").glob("*")):
        raise AssertionError("journal left behind")
    for name in ("staging", "backups"):
        directory = Path(case["root"]) / "reports/incremental" / name
        if directory.exists() and any(directory.rglob("*")):
            raise AssertionError(f"{name} residue")
    current = inc.build_plan(case["inputs"], case["state"])
    if current["dirty"] or current["full_required"] or current["blocked"] or current["unknown"]:
        raise AssertionError(f"plan not clean: {current}")
    page_nodes = {
        tuple(node["key"])
        for node in state.get("graph", {}).get("nodes") or []
        if isinstance(node.get("key"), (list, tuple)) and node["key"][:1] == ["page"]
    }
    for page_key in ordered:
        if ("page", page_key) not in page_nodes:
            raise AssertionError(f"graph missing page node: {page_key}")


def assert_multi_success(case, result, before_files, expected_scope, candidates):
    if result.get("status") != "committed":
        raise AssertionError(f"commit not committed: {result}")
    state = json.loads(Path(case["state"]).read_text(encoding="utf-8"))
    if state.get("format_version") != 1:
        raise AssertionError("state format_version must be 1")
    build = state.get("build")
    if not isinstance(build, dict) or build.get("version") != 2:
        raise AssertionError("build.version must be 2")
    ordered = _assert_build_identity(case, state, build, result, expected_scope, candidates)
    allowed, provenance_dirs = _assert_page_entries(case, result, build, ordered)
    assert_allowed_changes(before_files, snapshot_files(case["root"]), allowed, provenance_dirs)
    _assert_multi_cleanup(case, state, ordered)


def staging_injection_target(response_file, output, root, target_hash):
    if not response_file or not output:
        return False
    try:
        data = Path(response_file).read_bytes()
    except OSError:
        return False
    if sha256_bytes(data) != target_hash:
        return False
    staging = (Path(root) / "reports/incremental/staging").resolve()
    target = Path(output).resolve()
    return target == staging or staging in target.parents


def assert_staging_injection(trace, errors, page_key, expected_error):
    if "A_OK" not in trace:
        raise AssertionError("A page was not validated before injection")
    if "B_HIT" not in trace:
        raise AssertionError("staging injection never hit")
    if trace.index("A_OK") > trace.index("B_HIT"):
        raise AssertionError("B injection happened before A completed")
    assert_error_member(errors, expected_error)


def expected_request_id(case, plan_data, candidates):
    return sha256_bytes(C(request_payload(case, plan_data, candidates)))


def expected_transaction_id(request_id, execution_token):
    return sha256_bytes(C({"request_id": request_id, "execution_token": execution_token}))

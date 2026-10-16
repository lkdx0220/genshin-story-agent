# -*- coding: utf-8 -*-
"""B203 T1-C C1：恢复 journal/disk fixture 与独立断言 helper。"""
import hashlib
import json
import shutil
from pathlib import Path

from . import support

MISSING = object()
SCENARIO_ROOT = Path("C:/Users/24701/Documents/Codex/2026-10-14/l-t/work/b203-t1c-fixture")
TOKEN = "t1c-token-01"
NEW_GENERATION = "fixture-new"
BUILT_AT = "2026-10-15T00:00:00+00:00"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_state(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def state_digest(payload):
    return sha256_bytes(canonical_state(payload))


def page_slug(page_key):
    return hashlib.sha256(page_key.encode("utf-8")).hexdigest()[:16]


def _dump_state(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _read_state(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_map(directory):
    result = {}
    directory = Path(directory)
    if not directory.exists():
        return result
    for path in directory.rglob("*"):
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            result[relative] = path.read_bytes()
    return result


def _write_provenance(directory, members, manifest=None, mutate=None):
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    data = dict(members)
    if mutate:
        data = mutate(data)
    for relative, payload in data.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    if manifest is None:
        files = [
            {"path": relative, "sha256": sha256_bytes(payload)}
            for relative, payload in sorted(data.items())
        ]
        manifest = {"protocol": "b4-02-provenance-v1", "files": files}
    (directory / "manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    return manifest


def make_recovery_case(tmp_path, monkeypatch, old_equal=(), missing_old=(), conservative_full=False):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    root = case["root"]
    inputs = case["inputs"]
    keys = sorted(case["page_keys"].values())
    old_state_bytes = case["state"].read_bytes()
    if conservative_full:
        graph = support.inc.collect_dependencies(inputs)
        dependency = ("page", case["page_keys"]["b"])
        for index in range(35):
            consumer = ("index", f"t1c-depth-{index}")
            graph["nodes"].append({"key": consumer, "type": "index"})
            graph["edges"].append({"consumer": consumer, "dependency": dependency})
            dependency = consumer
        baseline = support.inc.make_baseline(json.loads(old_state_bytes)["inputs"], "fixture-parent", graph)
        old_state_bytes = _dump_state(baseline)
        case["state"].write_bytes(old_state_bytes)
    old_equal = {case["page_keys"].get(item, item) for item in old_equal}
    missing_old = {case["page_keys"].get(item, item) for item in missing_old}

    old_outputs = {}
    for key in keys:
        output = root / case["registry"][key]["output"]
        if key in old_equal:
            output.write_bytes(case["expected_outputs"][key])
            old_outputs[key] = case["expected_outputs"][key]
        elif key in missing_old:
            output.unlink(missing_ok=True)
            old_outputs[key] = None
        else:
            old_outputs[key] = output.read_bytes()

    # Real planner uses the unchanged successful parent baseline.
    plan_data = support.inc.build_plan(inputs, case["state"])
    support.write_json(case["plan"], plan_data)
    if plan_data.get("full_required"):
        scope = sorted(case["page_keys"].values())
    else:
        scope = sorted(plan_data["dirty"])
    candidates = [
        {"page_key": key, "response": case["registry"][key]["response"]}
        for key in scope
    ]
    support.write_json(case["manifest"], {"format_version": 1, "candidates": candidates})
    request_payload = support.request_payload(case, plan_data, candidates)
    request_id = support.sha256_bytes(support.C(request_payload))
    token = TOKEN
    txid = support.expected_transaction_id(request_id, token)

    provenance = {}
    backups = {}
    for key in scope:
        source = root / case["reports"][key]["provenance_dir"]
        members = _file_map(source)
        members.pop("manifest.json", None)
        files = [
            {"path": relative, "sha256": sha256_bytes(payload)}
            for relative, payload in sorted(members.items())
        ]
        manifest = {"protocol": "b4-02-provenance-v1", "files": files}
        provenance[key] = {
            "dir": f"reports/incremental/provenance/{txid}/{page_slug(key)}",
            "members": members,
            "manifest": manifest,
            "manifest_sha256": sha256_bytes(support.C(manifest)),
        }
        old_bytes = old_outputs[key]
        backups[key] = {
            "path": f"reports/incremental/backups/{txid}/{page_slug(key)}.bak",
            "bytes": old_bytes,
            "sha256": None if old_bytes is None else sha256_bytes(old_bytes),
        }

    for key in keys:
        (root / case["registry"][key]["output"]).write_bytes(case["expected_outputs"][key])
    new_observed = support.inc.fingerprint_inputs(inputs)
    new_graph = support.inc.collect_dependencies(inputs)
    new_state = support.inc.make_baseline(new_observed, NEW_GENERATION, new_graph)

    page_entries = {}
    for key in scope:
        managed = case["registry"][key]
        old_bytes = old_outputs[key]
        page_entries[key] = {
            "response": {"path": managed["response"], "sha256": support.sha256_file(root / managed["response"])},
            "output": {
                "path": managed["output"],
                "sha256": sha256_bytes(case["expected_outputs"][key]),
            },
            "backup": {"path": backups[key]["path"], "sha256": backups[key]["sha256"]},
            "provenance": {
                "dir": provenance[key]["dir"],
                "manifest_sha256": provenance[key]["manifest_sha256"],
                "members": provenance[key]["manifest"]["files"],
            },
            "generation": NEW_GENERATION,
        }
    new_state["build"] = {
        "version": 2,
        "request_id": request_id,
        "transaction_id": txid,
        "execution_token": token,
        "parent_generation": "fixture-parent",
        "plan_generation": plan_data["generation"],
        "generation": NEW_GENERATION,
        "built_at": BUILT_AT,
        "pages": {key: page_entries[key] for key in scope},
    }
    new_state_bytes = _dump_state(new_state)

    for key in keys:
        output = root / case["registry"][key]["output"]
        if old_outputs[key] is None:
            output.unlink(missing_ok=True)
        else:
            output.write_bytes(old_outputs[key])
    case["state"].write_bytes(old_state_bytes)

    journal = {
        "format_version": 2,
        "protocol": "b4-02-multi-commit-v1",
        "project_root_id": support.project_root_id(root),
        "request_payload": request_payload,
        "request_id": request_id,
        "execution_token": token,
        "transaction_id": txid,
        "plan_generation": plan_data["generation"],
        "parent_generation": "fixture-parent",
        "inputs": {"path": "config/incremental-inputs.json", "sha256": support.sha256_file(case["config"])},
        "state": {
            "path": "reports/incremental/state.json",
            "old_sha256": state_digest(json.loads(old_state_bytes)),
            "new_sha256": state_digest(new_state),
        },
        "staging_root": f"reports/incremental/staging/{txid}/{token}",
        "created_at": BUILT_AT,
        "status": "prepared",
        "pages": [
            {
                "page_key": key,
                "response": dict(page_entries[key]["response"]),
                "output": {
                    "path": case["registry"][key]["output"],
                    "old_exists": old_outputs[key] is not None,
                    "old_sha256": None if old_outputs[key] is None else sha256_bytes(old_outputs[key]),
                    "new_sha256": sha256_bytes(case["expected_outputs"][key]),
                },
                "backup": dict(page_entries[key]["backup"]),
                "provenance": dict(page_entries[key]["provenance"]),
            }
            for key in scope
        ],
    }
    return {
        **case,
        "plan_data": plan_data,
        "keys": keys,
        "scope": scope,
        "request_payload": request_payload,
        "request_id": request_id,
        "token": token,
        "txid": txid,
        "journal_name": f"{txid}.json",
        "journal": journal,
        "old_state_bytes": old_state_bytes,
        "new_state_bytes": new_state_bytes,
        "new_state": new_state,
        "old_outputs": old_outputs,
        "new_outputs": {key: case["expected_outputs"][key] for key in keys},
        "provenance": provenance,
        "backups": backups,
    }

def _clear_transaction_paths(case):
    root = case["root"]
    for relative in (
        "reports/incremental/journal",
        "reports/incremental/staging",
        "reports/incremental/backups",
        "reports/incremental/provenance",
    ):
        path = root / relative
        if path.exists():
            shutil.rmtree(path)


def materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full", status="prepared", journal_override=None):
    root = case["root"]
    _clear_transaction_paths(case)
    if state_variant == "old":
        case["state"].write_bytes(case["old_state_bytes"])
    elif state_variant == "new":
        case["state"].write_bytes(case["new_state_bytes"])
    elif state_variant == "missing":
        case["state"].unlink(missing_ok=True)
    elif state_variant == "corrupt":
        case["state"].write_bytes(b"{invalid")
    else:
        raise AssertionError(state_variant)

    for key in case["keys"]:
        output = root / case["registry"][key]["output"]
        if outputs == "old":
            if case["old_outputs"][key] is None:
                output.unlink(missing_ok=True)
            else:
                output.write_bytes(case["old_outputs"][key])
        elif outputs == "new":
            output.write_bytes(case["new_outputs"][key])
        elif outputs == "mixed":
            if key == case["page_keys"]["c"]:
                if case["old_outputs"][key] is None:
                    output.unlink(missing_ok=True)
                else:
                    output.write_bytes(case["old_outputs"][key])
            else:
                output.write_bytes(case["new_outputs"][key])
        elif outputs == "missing":
            output.unlink(missing_ok=True)
        else:
            raise AssertionError(outputs)

    for key in case["scope"]:
        spec = case["provenance"][key]
        directory = root / spec["dir"]
        if provenance == "full":
            _write_provenance(directory, spec["members"], spec["manifest"])
        elif provenance == "partial":
            partial = dict(list(sorted(spec["members"].items()))[:-1])
            _write_provenance(directory, partial, spec["manifest"])
        elif provenance == "missing":
            if directory.exists():
                shutil.rmtree(directory)
        elif provenance == "third":
            _write_provenance(directory, spec["members"], spec["manifest"])
            (directory / "third-party.txt").write_bytes(b"third party")
        else:
            raise AssertionError(provenance)

    for key in case["scope"]:
        backup = root / case["backups"][key]["path"]
        if backups == "full":
            if case["backups"][key]["bytes"] is not None:
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(case["backups"][key]["bytes"])
        elif backups == "missing":
            backup.unlink(missing_ok=True)
        elif backups == "corrupt":
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(b"corrupt backup")
        else:
            raise AssertionError(backups)

    journal = dict(journal_override or case["journal"])
    journal["status"] = status
    journal_path = root / "reports/incremental/journal" / case["journal_name"]
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_bytes(json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
    return journal_path

def snapshot_recovery_resources(case):
    root = case["root"]
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    result["__dirs__"] = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    )
    tracked = [case["state"], root / "reports/incremental/journal" / case["journal_name"]]
    tracked.extend(root / case["registry"][key]["output"] for key in case["keys"])
    tracked.extend(root / case["provenance"][key]["dir"] for key in case["scope"])
    tracked.extend(root / case["backups"][key]["path"] for key in case["scope"])
    tracked.append(root / "reports/incremental/staging" / case["txid"] / case["token"])
    for path in tracked:
        relative = path.relative_to(root).as_posix()
        if relative not in result:
            result[relative] = MISSING
    return result

def assert_layout_matches(case, expected):
    actual = snapshot_recovery_resources(case)
    if actual != expected:
        raise AssertionError(f"layout mismatch: {actual!r} != {expected!r}")


def assert_provenance_subset(root, entry, expected_members):
    provenance = entry["provenance"]
    expected_files = [
        {"path": path, "sha256": sha256_bytes(payload)}
        for path, payload in sorted(expected_members.items())
    ]
    if provenance.get("members") != expected_files:
        raise AssertionError("journal provenance members mismatch")
    expected_manifest = {"protocol": "b4-02-provenance-v1", "files": expected_files}
    if provenance.get("manifest_sha256") != sha256_bytes(support.C(expected_manifest)):
        raise AssertionError("journal provenance manifest sha256 mismatch")
    directory = Path(root) / provenance["dir"]
    if not directory.exists():
        return set(expected_members)
    actual = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise AssertionError("provenance symlink not allowed")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative == "manifest.json":
                continue
            actual[relative] = path.read_bytes()
    for relative, payload in actual.items():
        if relative not in expected_members:
            raise AssertionError(f"extra provenance member: {relative}")
        if payload != expected_members[relative]:
            raise AssertionError(f"rewritten provenance member: {relative}")
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssertionError(f"trusted manifest unreadable: {exc}") from exc
        if manifest != expected_manifest:
            raise AssertionError("root manifest differs from trusted manifest")
        if sha256_bytes(support.C(manifest)) != provenance["manifest_sha256"]:
            raise AssertionError("root manifest sha256 mismatch")
    return set(expected_members) - set(actual)

def assert_recovery_preserved(before, after):
    if before != after:
        raise AssertionError("recovery resources changed")


def derive_staging_root(case):
    return f"reports/incremental/staging/{case['txid']}/{case['token']}"


def assert_staging_owner(case, relative):
    expected = derive_staging_root(case)
    if relative != expected:
        raise AssertionError(f"staging owner mismatch: {relative} != {expected}")


def assert_registered_output(case, key, relative):
    expected = case["registry"][key]["output"]
    if relative != expected:
        raise AssertionError(f"registered output mismatch: {relative} != {expected}")


def assert_not_protected(case, relative):
    root = case["root"]
    if relative == "reports/incremental/state.json":
        raise AssertionError("state is protected")
    for key in case["keys"]:
        if relative == case["registry"][key]["response"]:
            raise AssertionError("response is protected")
    for prefix in ("wiki/__schema__", "tests/fixtures/", "docs/", "lint/", "config/"):
        if relative.startswith(prefix):
            raise AssertionError(f"protected path: {relative}")


def validate_request_identity(case, request_payload, request_id, txid):
    recomputed = support.sha256_bytes(support.C(request_payload))
    if recomputed != request_id:
        raise AssertionError("request_id mismatch")
    if support.expected_transaction_id(request_id, case["token"]) != txid:
        raise AssertionError("transaction_id mismatch")


def clone_with_token(case, token):
    import copy

    variant = copy.deepcopy(case)
    txid = support.expected_transaction_id(case["request_id"], token)
    variant["token"] = token
    variant["txid"] = txid
    variant["journal_name"] = f"{txid}.json"
    provenance = {}
    backups = {}
    for key in case["scope"]:
        provenance[key] = dict(case["provenance"][key])
        provenance[key]["dir"] = f"reports/incremental/provenance/{txid}/{page_slug(key)}"
        backups[key] = dict(case["backups"][key])
        backups[key]["path"] = f"reports/incremental/backups/{txid}/{page_slug(key)}.bak"
    variant["provenance"] = provenance
    variant["backups"] = backups
    journal = variant["journal"]
    journal["execution_token"] = token
    journal["transaction_id"] = txid
    journal["staging_root"] = f"reports/incremental/staging/{txid}/{token}"
    for page in journal["pages"]:
        key = page["page_key"]
        page["provenance"]["dir"] = provenance[key]["dir"]
        page["backup"]["path"] = backups[key]["path"]
    state = json.loads(variant["new_state_bytes"])
    state["build"]["execution_token"] = token
    state["build"]["transaction_id"] = txid
    for key, entry in state["build"]["pages"].items():
        entry["provenance"]["dir"] = provenance[key]["dir"]
    journal["state"]["new_sha256"] = state_digest(state)
    variant["new_state"] = state
    variant["new_state_bytes"] = _dump_state(state)
    return variant


def _validate_journal_header(case, journal):
    if journal.get("format_version") != 2 or journal.get("protocol") != "b4-02-multi-commit-v1":
        raise AssertionError("journal version/protocol mismatch")
    if journal.get("project_root_id") != support.project_root_id(case["root"]):
        raise AssertionError("project_root_id mismatch")
    if journal["request_id"] != support.sha256_bytes(support.C(journal["request_payload"])):
        raise AssertionError("request_id mismatch")
    if journal["transaction_id"] != support.expected_transaction_id(journal["request_id"], journal["execution_token"]):
        raise AssertionError("transaction_id mismatch")


def _validate_journal_projection_fields(case, journal):
    payload = journal["request_payload"]
    if payload["state"] != journal["state"]["path"]:
        raise AssertionError("state projection mismatch")
    if payload["inputs"] != journal["inputs"]:
        raise AssertionError("inputs projection mismatch")
    if payload["plan_generation"] != journal["plan_generation"]:
        raise AssertionError("plan generation projection mismatch")
    if payload["parent_generation"] != journal["parent_generation"]:
        raise AssertionError("parent generation projection mismatch")
    if payload["page_keys"] != [page["page_key"] for page in journal["pages"]]:
        raise AssertionError("page projection mismatch")
    if journal["plan_generation"] != case["plan_data"]["generation"]:
        raise AssertionError("plan_generation mismatch")
    if journal["parent_generation"] != case["parent_generation"]:
        raise AssertionError("parent_generation mismatch")
    if journal["staging_root"] != derive_staging_root(case):
        raise AssertionError("staging owner mismatch")


def _validate_state_summary(case, journal):
    if journal["state"]["path"] != "reports/incremental/state.json":
        raise AssertionError("state path mismatch")
    if journal["state"]["old_sha256"] != state_digest(json.loads(case["old_state_bytes"])):
        raise AssertionError("old state digest mismatch")
    actual_new_state = json.loads(case["new_state_bytes"])
    if journal["state"]["new_sha256"] != state_digest(actual_new_state):
        raise AssertionError("new state digest mismatch")
    if actual_new_state.get("build", {}).get("pages", {}).keys() != {page["page_key"] for page in journal["pages"]}:
        raise AssertionError("new state build pages mismatch")


def _validate_journal_page(case, page, expected_payload, payload):
    key = page["page_key"]
    managed = case["registry"][key]
    if expected_payload is None:
        raise AssertionError("payload page missing")
    if page["response"]["path"] != managed["response"]:
        raise AssertionError("response path mismatch")
    if page["response"]["sha256"] != support.sha256_file(Path(case["root"]) / managed["response"]):
        raise AssertionError("response sha256 mismatch")
    if expected_payload["output"] != page["output"]["path"]:
        raise AssertionError("payload output projection mismatch")
    if page["output"]["path"] != managed["output"]:
        raise AssertionError("output path mismatch")
    old_bytes = case["old_outputs"][key]
    if not isinstance(page["output"]["old_exists"], bool):
        raise AssertionError("old_exists type mismatch")
    if page["output"]["old_exists"] != (old_bytes is not None):
        raise AssertionError("old_exists mismatch")
    if page["output"]["old_sha256"] != (None if old_bytes is None else sha256_bytes(old_bytes)):
        raise AssertionError("old output sha mismatch")
    if page["output"]["new_sha256"] != sha256_bytes(case["new_outputs"][key]):
        raise AssertionError("new output sha mismatch")
    if page["backup"]["path"] != case["backups"][key]["path"]:
        raise AssertionError("backup path mismatch")
    if page["backup"]["sha256"] != case["backups"][key]["sha256"]:
        raise AssertionError("backup sha mismatch")
    if page["provenance"]["dir"] != case["provenance"][key]["dir"]:
        raise AssertionError("provenance owner mismatch")
    if page["provenance"]["manifest_sha256"] != case["provenance"][key]["manifest_sha256"]:
        raise AssertionError("provenance digest mismatch")
    expected_members = [
        {"path": path, "sha256": sha256_bytes(payload)}
        for path, payload in sorted(case["provenance"][key]["members"].items())
    ]
    if page["provenance"]["members"] != expected_members:
        raise AssertionError("provenance members mismatch")


def _validate_journal_pages(case, journal, payload):
    payload_pages = {page["page_key"]: page for page in payload["pages"]}
    for page in journal["pages"]:
        _validate_journal_page(case, page, payload_pages.get(page["page_key"]), payload)


def validate_journal_projection(case, journal=None):
    journal = journal or case["journal"]
    _validate_journal_header(case, journal)
    _validate_journal_projection_fields(case, journal)
    _validate_state_summary(case, journal)
    _validate_journal_pages(case, journal, journal["request_payload"])
    validate_state_build(case, json.loads(case["new_state_bytes"]))
    return True


def validate_state_build(case, state):
    build = state.get("build")
    if not isinstance(build, dict) or build.get("version") != 2:
        raise AssertionError("build.version contract missing")
    for field in (
        "request_id",
        "transaction_id",
        "execution_token",
        "parent_generation",
        "plan_generation",
        "generation",
        "built_at",
        "pages",
    ):
        if field not in build:
            raise AssertionError(f"build missing field: {field}")
    if build["request_id"] != case["request_id"]:
        raise AssertionError("build.request_id mismatch")
    if build["transaction_id"] != case["txid"]:
        raise AssertionError("build.transaction_id mismatch")
    if build["execution_token"] != case["token"]:
        raise AssertionError("build.execution_token mismatch")
    if build["parent_generation"] != case["parent_generation"]:
        raise AssertionError("build.parent_generation mismatch")
    if build["plan_generation"] != case["plan_data"]["generation"]:
        raise AssertionError("build.plan_generation mismatch")
    if build["generation"] != NEW_GENERATION:
        raise AssertionError("build.generation mismatch")
    if set(build["pages"]) != set(case["scope"]):
        raise AssertionError("build.pages scope mismatch")
    for key in case["scope"]:
        entry = build["pages"][key]
        managed = case["registry"][key]
        if entry["response"]["path"] != managed["response"]:
            raise AssertionError("build.response path mismatch")
        if entry["response"]["sha256"] != support.sha256_file(Path(case["root"]) / managed["response"]):
            raise AssertionError("build.response sha256 mismatch")
        if entry["output"]["path"] != managed["output"]:
            raise AssertionError("build.output path mismatch")
        if entry["output"]["sha256"] != sha256_bytes(case["new_outputs"][key]):
            raise AssertionError("build.output sha256 mismatch")
        if entry.get("generation") != build["generation"]:
            raise AssertionError("build page generation mismatch")
        if entry["provenance"]["dir"] != case["provenance"][key]["dir"]:
            raise AssertionError("build.provenance dir mismatch")
        if entry["provenance"]["manifest_sha256"] != case["provenance"][key]["manifest_sha256"]:
            raise AssertionError("build.provenance manifest mismatch")
    if state.get("generation") != build["generation"]:
        raise AssertionError("state/build generation mismatch")
    if state.get("graph", {}).get("generation") != build["generation"]:
        raise AssertionError("graph/build generation mismatch")

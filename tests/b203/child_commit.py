# -*- coding: utf-8 -*-
"""T1-D 子进程提交入口：真实 os._exit / 锁竞争用。"""
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))


def patch_roots(root: Path):
    from app import canonical
    from app import incremental as inc
    import ingest
    import wiki_lint

    inc.REPO_ROOT = root
    inc.DEFAULT_INPUTS = root / "config/incremental-inputs.json"
    ingest.ROOT = root
    ingest.ENTITY_PROFILE_DIR = root / "wiki/__schema__/entities"
    wiki_lint.ROOT = root
    canonical.REPO_ROOT = root


def main():
    root = Path(sys.argv[1]).resolve()
    mode = sys.argv[2]
    patch_roots(root)
    from app import rebuild_multi as api

    plan = root / "reports/incremental/plan.json"
    state = root / "reports/incremental/state.json"
    config = root / "config/incremental-inputs.json"
    manifest = root / "candidate.json"

    inputs = api.inc.load_inputs(config)
    plan_data = json.loads(plan.read_text(encoding="utf-8"))
    scope = sorted(api._managed_scope(inputs, plan_data))
    registry = api.inc._page_registry(inputs)
    outputs = [ (root / registry[key]["output"]).resolve() for key in scope ]
    state_resolved = state.resolve()

    real_replace = os.replace
    replaced_outputs = []

    def wrapped_replace(source, destination):
        destination = Path(destination).resolve()
        result = real_replace(source, destination)
        if destination in outputs:
            replaced_outputs.append(destination)
            if mode == "middle" and len(replaced_outputs) == 2:
                os._exit(77)
            if mode == "all_pages" and len(replaced_outputs) == len(outputs):
                os._exit(77)
        elif mode == "state" and destination == state_resolved:
            os._exit(77)
        return result

    api.os.replace = wrapped_replace

    if mode == "hold_lock":
        original_final = api._final_cas
        ready = root / "held.flag"
        release = root / "release.flag"
        for path in (ready, release):
            path.unlink(missing_ok=True)

        def wait_final(ctx, new_state, staged):
            ready.write_text("held", encoding="utf-8")
            deadline = time.time() + 30
            while not release.exists():
                if time.time() > deadline:
                    raise RuntimeError("release timeout")
                time.sleep(0.05)
            return original_final(ctx, new_state, staged)

        api._final_cas = wait_final

    try:
        result = api.commit_rebuild(plan, state, config, manifest)
    except BaseException as exc:  # noqa: BLE001 - child reports exact failure
        print(json.dumps({"status": "exception", "error": repr(exc)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

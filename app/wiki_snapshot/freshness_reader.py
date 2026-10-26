# -*- coding: utf-8 -*-
"""S5-C bounded two-entity pinned local reader, derived from accepted S4-B."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from types import MappingProxyType
from collections import namedtuple

FRESHNESS_SCOPE = "pinned_local_read_snapshot"
_Binding = namedtuple("_Binding", "entity_id page_key page registry span_index", defaults=(
    "wiki/__schema__/version-registry.json", "raw/wanderer/span_index.json"))


_BINDINGS = MappingProxyType({
    "char:wanderer": _Binding("char:wanderer", "raw/wanderer/response_candidate.md|production|char:wanderer", "wiki/characters/wanderer.md"),
    "char:nahida": _Binding("char:nahida", "raw/nahida/response_candidate.md|production|char:nahida", "wiki/characters/nahida.md"),
})

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class FreshnessReader:
    def __init__(self, root: Path, hooks: Optional[Dict[str, Callable[..., Any]]] = None, *, entity_id: str = "char:wanderer"):
        if not isinstance(entity_id, str) or entity_id not in _BINDINGS:
            raise ValueError("unsupported entity_id")
        self._binding = _BINDINGS[entity_id]
        self._paths = MappingProxyType({"page": self._binding.page, "registry": self._binding.registry, "span_index": self._binding.span_index})
        self.root_arg = Path(root)
        self.root = self.root_arg.resolve()
        self.hooks: Dict[str, Callable[..., Any]] = dict(hooks or {})
        self._cache: Dict[str, Any] = {}
        self.read_attempts: list = []

    def _failure(self, status: str, reason_code: str, reason_resource: Optional[str]) -> dict:
        return {
            "status": status,
            "reason_code": reason_code,
            "reason_resource": reason_resource,
            "answer": None,
            "hits": [],
            "freshness_scope": FRESHNESS_SCOPE,
            "read_snapshot": None,
            "cache_reused": False,
        }

    def _hook(self, name: str, **kwargs) -> None:
        fn = self.hooks.get(name)
        if fn is not None:
            fn(**kwargs)

    def _within_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
        except OSError:
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None:
            try:
                if is_junction():
                    return True
            except OSError:
                return True
        return False

    def _has_link_component(self, path: Path) -> bool:
        """True if root itself or any path component from root to path is a link."""
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return True
        if self._is_link(self.root_arg):
            return True
        current = self.root
        for part in relative.parts:
            current = current / part
            if self._is_link(current):
                return True
        return False

    def _root_invalid(self) -> bool:
        lexical = self.root_arg.absolute()
        return any(self._is_link(p) for p in (lexical, *lexical.parents)) or not self.root_arg.exists()

    def _read_bytes(self, path: Path, resource: str) -> bytes:
        self.read_attempts.append({"resource": resource, "path": str(path)})
        self._hook("before_resource_read", resource=resource, path=path)
        return path.read_bytes()

    def _resolve_identity_path(self, identity_path: Path) -> Optional[Path]:
        candidate = Path(identity_path)
        if ".." in candidate.parts:
            raise ValueError("identity path traversal")
        if not candidate.is_absolute():
            candidate = self.root / candidate
        if not candidate.exists():
            return None
        if self._has_link_component(candidate) or not self._within_root(candidate):
            raise ValueError("identity path invalid")
        return candidate

    def _resolve_resource(self, rel_path: str, resource: str) -> Path:
        if not isinstance(rel_path, str) or rel_path != self._paths[resource]:
            raise ValueError("resource path invalid")
        candidate = self.root / rel_path
        if self._has_link_component(candidate) or not self._within_root(candidate):
            raise ValueError("resource path invalid")
        return candidate

    def _identity_schema_ok(self, identity: Any) -> bool:
        if not isinstance(identity, dict):
            return False
        if type(identity.get("format_version")) is not int or identity.get("format_version") != 1:
            return False
        if identity.get("kind") != "s4b_lab_read_identity":
            return False
        if identity.get("scope") != FRESHNESS_SCOPE:
            return False
        if identity.get("page_key") != self._binding.page_key:
            return False
        if identity.get("entity_id") != self._binding.entity_id:
            return False
        if not isinstance(identity.get("snapshot_id"), str) or not identity["snapshot_id"].strip():
            return False
        authority = identity.get("authority")
        if not isinstance(authority, dict) or not isinstance(authority.get("kind"), str) or not authority["kind"].strip():
            return False
        resources = identity.get("resources")
        if not isinstance(resources, dict) or set(resources) != set(self._paths):
            return False
        for name, expected_path in self._paths.items():
            item = resources.get(name)
            if not isinstance(item, dict):
                return False
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                return False
            digest = item.get("sha256")
            if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                return False
        return True

    def _load_identity(self, identity_path: Path, trusted_identity_sha256: str) -> tuple:
        if not isinstance(trusted_identity_sha256, str):
            return False, self._failure("unknown", "identity_untrusted", "identity"), None, None
        trusted = trusted_identity_sha256.strip().lower()
        if not HEX64.fullmatch(trusted):
            return False, self._failure("unknown", "identity_untrusted", "identity"), None, None
        try:
            resolved = self._resolve_identity_path(identity_path)
        except ValueError:
            return False, self._failure("unknown", "path_invalid", "identity"), None, None
        if resolved is None:
            return False, self._failure("unknown", "identity_missing", "identity"), None, None
        try:
            raw = self._read_bytes(resolved, "identity")
        except OSError:
            return False, self._failure("unknown", "identity_missing", "identity"), None, None
        if hashlib.sha256(raw).hexdigest() != trusted:
            return False, self._failure("unknown", "identity_untrusted", "identity"), None, None
        try:
            identity = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, self._failure("unknown", "identity_invalid", "identity"), None, None
        if not self._identity_schema_ok(identity):
            return False, self._failure("unknown", "identity_invalid", "identity"), None, None
        return True, None, identity, raw

    def query(
        self,
        question: str,
        *,
        identity_path: Path,
        trusted_identity_sha256: str,
        snapshot: Optional[dict] = None,
        top_k: int = 6,
    ) -> dict:
        from app import canonical, versioning

        if self._root_invalid():
            return self._failure("unknown", "path_invalid", "identity")

        ok, failure, identity, identity_raw = self._load_identity(identity_path, trusted_identity_sha256)
        if not ok:
            return failure
        trusted = trusted_identity_sha256.strip().lower()
        resources = identity["resources"]

        try:
            paths = {name: self._resolve_resource(resources[name]["path"], name) for name in self._paths}
        except ValueError:
            return self._failure("unknown", "path_invalid", "identity")

        captured: Dict[str, bytes] = {}
        for name in ("page", "registry", "span_index"):
            try:
                captured[name] = self._read_bytes(paths[name], name)
            except OSError:
                return self._failure("unknown", "resource_unreadable", name)

        for name in ("page", "registry", "span_index"):
            if hashlib.sha256(captured[name]).hexdigest() != resources[name]["sha256"]:
                return self._failure("stale", "resource_changed", name)

        self._hook("after_capture", captured=dict(captured))

        try:
            span_rows = json.loads(captured["span_index"].decode("utf-8"))
            if not isinstance(span_rows, list):
                raise ValueError("span_index must be a list")
            known_span_ids = []
            span_urls = {}
            for row in span_rows:
                if not isinstance(row, dict) or not isinstance(row.get("span_id"), str):
                    raise ValueError("span_index row invalid")
                span_id = row["span_id"]
                if not span_id or span_id in known_span_ids:
                    raise ValueError("duplicate or empty span_id")
                known_span_ids.append(span_id)
                url = row.get("url")
                if url is not None and not isinstance(url, str):
                    raise ValueError("span URL invalid")
                span_urls[span_id] = url
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return self._failure("unknown", "resource_invalid", "span_index")

        try:
            registry = json.loads(captured["registry"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure("unknown", "resource_invalid", "registry")
        try:
            versioning.validate_registry(registry, known_span_ids=known_span_ids)
        except versioning.VersionRegistryError:
            return self._failure("unknown", "resource_invalid", "registry")

        try:
            page_text = captured["page"].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            return self._failure("unknown", "resource_invalid", "page")

        cache_key = (self._binding.entity_id, trusted)
        cache_reused = False
        cached = self._cache.get(cache_key)
        if cached is not None:
            page = cached["page"]
            index = cached["index"]
            registry = cached["registry"]
            registry_fingerprint = cached["registry_fingerprint"]
            cache_reused = True
        else:
            try:
                page = canonical.CanonicalPage(paths["page"], page_text, registry=registry)
                if page.entity_id != self._binding.entity_id or page.data.get("data_mode") != "production":
                    raise ValueError("captured page entity/mode mismatch")
                for fact in page.facts:
                    if any(span not in span_urls or not span_urls[span] for span in fact.evidence):
                        raise ValueError("page cites span absent from captured index")
                # Citation URLs are bound to the captured span resource, never a disk reload.
                page.span_url = {span: url for span, url in span_urls.items() if url}
            except (ValueError, json.JSONDecodeError):
                return self._failure("unknown", "resource_invalid", "page")
            try:
                index = canonical.CanonicalIndex([page], registry=registry)
            except ValueError:
                return self._failure("unknown", "resource_invalid", "page")
            registry_fingerprint = index.registry_fingerprint or versioning.registry_fingerprint(registry)
            self._cache[cache_key] = {
                "page": page,
                "index": index,
                "registry": registry,
                "registry_fingerprint": registry_fingerprint,
            }

        hits = index.search(question, top_k=top_k, snapshot=snapshot or {})
        answer = index.answer(question, top_k=top_k, snapshot=snapshot or {})

        serialized_hits = []
        for page_obj, fact in hits:
            item = dataclasses.asdict(fact)
            item["entity_id"] = page_obj.entity_id
            item["evidence_urls"] = [page_obj.span_url.get(span_id) for span_id in fact.evidence]
            serialized_hits.append(item)

        return {
            "status": "fresh",
            "reason_code": "fresh_verified",
            "reason_resource": None,
            "answer": answer,
            "hits": copy.deepcopy(serialized_hits),
            "freshness_scope": FRESHNESS_SCOPE,
            "read_snapshot": {
                "snapshot_id": identity["snapshot_id"],
                "identity_sha256": trusted,
                "page_key": identity["page_key"],
                "page_sha256": resources["page"]["sha256"],
                "registry_sha256": resources["registry"]["sha256"],
                "span_index_sha256": resources["span_index"]["sha256"],
                "registry_fingerprint": registry_fingerprint,
            },
            "cache_reused": cache_reused,
        }

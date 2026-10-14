# -*- coding: utf-8 -*-
"""Canonical Wiki 页运行时接入（Wave1 最小垂直切片）。

读取 ``wiki/characters/*.md``：
- JSON-compatible YAML front matter；
- 结构化记录中的 evidence span；
- 正文脚注定义中的 span -> URL 映射。

对外提供：
- ``CanonicalPage``：单个 canonical 页；
- ``CanonicalIndex``：多页检索；
- ``canonical_query(question)``：返回带 citation 的候选回答。

本模块只依赖标准库，不依赖 ingest.py / wiki_lint.py，方便在运行时长期使用。
缺少 canonical 页时返回显式缺口，不抛异常。
"""

from __future__ import annotations

import copy
import functools
import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = REPO_ROOT / "wiki" / "characters"
_REGISTRY_UNSET = object()

_FOOTNOTE_RE = re.compile(
    r"^\[\^source:(?P<url>[^\]]+)\]: \[原文\]\((?P<link>[^)]+)\) — (?P<span>span:[a-z0-9_-]+)$",
    re.M,
)
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
_STOP = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "及",
    "有",
    "没有",
    "吗",
    "呢",
    "吧",
    "什么",
    "怎么",
    "为什么",
    "哪些",
    "哪个",
    "如何",
    "是否",
    "这个",
    "那个",
    "一下",
    "一个",
    "我们",
    "你们",
    "他们",
    "他",
    "她",
    "它",
    "我",
    "你",
    "不",
    "无",
    "存在",
    "完全",
    "全不",
    "不存",
    "在的",
    "的角",
    "角色",
    "色问",
    "问题",
    "是什",
    "为什",
}


def _tokens(text: str) -> List[str]:
    """中文按字/双字切，英文/数字按词切；用于无依赖的轻量匹配。"""
    out: List[str] = []
    for match in _TOKEN_RE.finditer(text or ""):
        token = match.group(0)
        if re.match(r"[\u4e00-\u9fff]", token):
            if len(token) == 1:
                out.append(token)
            else:
                out.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            out.append(token.lower())
    return [t for t in out if t and t not in _STOP]


def _parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("canonical page needs JSON front matter starting with ---")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("canonical page front matter closing delimiter missing")
    data = json.loads(parts[0][4:])
    if not isinstance(data, dict):
        raise ValueError("canonical page front matter must be an object")
    return data, parts[1]


def _collect_evidence(obj: Any) -> List[str]:
    """递归收集对象里的 evidence.source_span_id，保持顺序去重。"""
    found: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "evidence" in value and isinstance(value["evidence"], list):
                for item in value["evidence"]:
                    if isinstance(item, dict) and item.get("source_span_id"):
                        found.append(item["source_span_id"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    seen = set()
    result = []
    for span_id in found:
        if span_id not in seen:
            seen.add(span_id)
            result.append(span_id)
    return result


@dataclass
class Fact:
    kind: str
    text: str
    evidence: List[str] = field(default_factory=list)
    status: str = "candidate"
    identity_version_ids: List[str] = field(default_factory=list)
    story_scope: Dict[str, Any] = field(default_factory=dict)
    version: Dict[str, Any] = field(default_factory=dict)
    version_id: Optional[str] = None
    conflict_id: Optional[str] = None
    target_ref: Optional[str] = None
    attribute_id: Optional[str] = None
    attribute_key: Optional[str] = None
    attribute_value: Any = None
    observed_at: List[Dict[str, Any]] = field(default_factory=list)
    player_choice_scope: List[str] = field(default_factory=list)
    player_choice_missing: bool = False
    config_error: bool = False
    record_id: Optional[str] = None
    parent_identity: Dict[str, Any] = field(default_factory=dict)


REQUIRED_CHOICE_PROFILE_IDS = {"char:traveler"}


def _load_player_choice_profile_options(entity_id: str):
    """返回 (options, error)。错误不降级为非选择实体。"""
    if not isinstance(entity_id, str) or not entity_id:
        return [], "invalid entity_id for choice profile"
    slug = entity_id.replace(":", "_")
    path = REPO_ROOT / "wiki" / "__schema__" / "entities" / (slug + ".json")
    if not path.exists():
        if entity_id in REQUIRED_CHOICE_PROFILE_IDS:
            return [], "required choice profile missing: " + entity_id
        return [], None
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], "choice profile unreadable: " + str(exc)
    if not isinstance(profile, dict) or profile.get("entity_id") != entity_id:
        return [], "choice profile entity_id mismatch"
    options = profile.get("player_choice_options")
    required = entity_id in REQUIRED_CHOICE_PROFILE_IDS
    if options is None:
        if required:
            return [], "required choice profile needs exactly aether/lumine options"
        return [], None
    if not isinstance(options, list) or (required and len(options) != 2):
        return [], "choice profile options must be exactly aether/lumine for required profiles"
    seen_ids = set()
    seen_aliases = set()
    for option in options:
        if not isinstance(option, dict):
            return [], "choice profile option must be an object"
        choice_id = option.get("choice_id")
        label = option.get("label")
        aliases = option.get("aliases")
        if choice_id not in ("aether", "lumine") or choice_id in seen_ids:
            return [], "choice profile option id invalid/duplicate"
        seen_ids.add(choice_id)
        if not isinstance(label, str) or not label.strip() or not re.search(r"[一-鿿]", label):
            return [], "choice profile label must be non-empty Chinese text"
        if not isinstance(aliases, list) or not aliases:
            return [], "choice profile aliases required"
        for alias in aliases:
            if (
                not isinstance(alias, str)
                or not alias.strip()
                or not re.search(r"[一-鿿]", alias)
                or alias in seen_aliases
            ):
                return [], "choice profile alias must be non-empty Chinese and unique"
            seen_aliases.add(alias)
    if required and seen_ids != {"aether", "lumine"}:
        return [], "required choice profile must contain aether and lumine"
    return options, None


class CanonicalPage:
    """单个 canonical 页的解析结果。"""

    def __init__(self, path: Path, text: str, registry: Optional[Dict[str, Any]] = None):
        self.path = Path(path)
        self.registry = registry
        self.data, self.body = _parse_front_matter(text)
        page_options = self.data.get("player_choice_options")
        profile_options, profile_error = _load_player_choice_profile_options(str(self.data.get("entity_id") or ""))
        self.player_choice_error = profile_error
        if page_options is not None:
            if not isinstance(page_options, list):
                self.player_choice_error = "page player_choice_options must be an array"
                self.player_choice_options = []
            else:
                if profile_options and page_options != profile_options:
                    self.player_choice_error = "page player_choice_options does not match profile"
                self.player_choice_options = list(page_options)
        else:
            self.player_choice_options = list(profile_options or [])
        self.has_player_choice = bool(self.player_choice_options) or bool(self.player_choice_error)
        self.span_url: Dict[str, str] = {}
        for match in _FOOTNOTE_RE.finditer(self.body):
            self.span_url[match.group("span")] = match.group("url")
        self.facts: List[Fact] = []
        self._build_facts()

    @classmethod
    def from_path(cls, path: Path, registry: Optional[Dict[str, Any]] = None) -> "CanonicalPage":
        return cls(Path(path), Path(path).read_text(encoding="utf-8"), registry=registry)

    @property
    def entity_id(self) -> str:
        return str(self.data.get("entity_id") or "")

    @property
    def canonical_name(self) -> str:
        return str(self.data.get("canonical_name") or "")

    @property
    def aliases(self) -> List[str]:
        return [str(x) for x in self.data.get("aliases") or []]

    @staticmethod
    def _span_ids(raw_evidence):
        span_ids = []
        for item in raw_evidence:
            if isinstance(item, dict):
                if item.get("source_span_id"):
                    span_ids.append(item["source_span_id"])
            elif isinstance(item, str) and item:
                span_ids.append(item)
        return span_ids

    def _fact_version_id(self, version_obj):
        if self.registry is None or not (version_obj.get("introduced_in") or version_obj.get("label")):
            return None
        try:
            from app.versioning import VersionRegistryError, resolve_version

            return resolve_version(
                numeric=version_obj.get("introduced_in"),
                label=version_obj.get("label"),
                registry=self.registry,
            ).version_id
        except VersionRegistryError:
            return None

    def _add_fact(
        self,
        facts,
        kind,
        text,
        obj=None,
        evidence=None,
        status=None,
        identity_ids=None,
        story_scope=None,
        version=None,
        conflict_id=None,
        target_ref=None,
        attribute_id=None,
        attribute_key=None,
        attribute_value=None,
        observed_at=None,
        player_choice_scope=None,
        parent_identity=None,
    ):
        obj = obj or {}
        status = status or obj.get("status") or "candidate"
        if status == "rejected":
            return
        raw_evidence = evidence if evidence is not None else _collect_evidence(obj)
        span_ids = self._span_ids(raw_evidence)
        record_keys = (
            "event_id",
            "relation_id",
            "memory_record_id",
            "state_id",
            "attribute_id",
            "name_id",
            "conflict_id",
            "alternative_id",
        )
        player_choice_missing = bool(
            self.has_player_choice and any(key in obj for key in record_keys) and "player_choice_scope" not in obj
        )
        record_id = next((str(obj.get(key)) for key in record_keys if obj.get(key)), None)
        observed_at = list(observed_at if observed_at is not None else obj.get("observed_at") or [])
        version_obj = dict(version or obj.get("version") or {})
        version_id = self._fact_version_id(version_obj)
        facts.append(
            Fact(
                kind=kind,
                text=text,
                evidence=span_ids,
                status=status,
                identity_version_ids=list(identity_ids or obj.get("identity_version_ids") or []),
                story_scope=dict(story_scope or obj.get("story_scope") or {}),
                version=version_obj,
                version_id=version_id,
                conflict_id=conflict_id,
                target_ref=target_ref,
                attribute_id=attribute_id,
                attribute_key=attribute_key,
                attribute_value=attribute_value,
                observed_at=observed_at,
                player_choice_scope=list(player_choice_scope or obj.get("player_choice_scope") or []),
                player_choice_missing=player_choice_missing,
                config_error=self.player_choice_error is not None,
                record_id=record_id,
                parent_identity=dict(parent_identity or {}),
            )
        )

    def _build_identity_facts(self):
        for identity in self.data.get("identity_versions") or []:
            if identity.get("status") == "rejected":
                continue
            label = str(identity.get("label") or "")
            allowed_names = [n for n in identity.get("names") or [] if n.get("status") != "rejected"]
            common_names = [n for n in allowed_names if "player_choice_scope" in n and not n.get("player_choice_scope")]
            missing_names = [n for n in allowed_names if "player_choice_scope" not in n]
            scoped: Dict[tuple, List[Dict[str, Any]]] = {}
            for name in allowed_names:
                scope = tuple(name.get("player_choice_scope") or [])
                if scope:
                    scoped.setdefault(scope, []).append(name)
            if not scoped and (not missing_names or not self.has_player_choice):
                self._add_fact(
                    self.facts,
                    "identity",
                    label,
                    identity,
                    evidence=identity.get("evidence") or [],
                    identity_ids=[identity.get("identity_version_id")],
                )
            else:
                if common_names:
                    self._add_fact(
                        self.facts,
                        "identity",
                        label + "（共同）",
                        identity,
                        evidence=identity.get("evidence") or [],
                        identity_ids=[identity.get("identity_version_id")],
                        player_choice_scope=[],
                    )
                for scope in scoped:
                    self._add_fact(
                        self.facts,
                        "identity",
                        label + f"（{'/'.join(scope)}）",
                        identity,
                        evidence=identity.get("evidence") or [],
                        identity_ids=[identity.get("identity_version_id")],
                        player_choice_scope=list(scope),
                    )
            for name in allowed_names:
                observed = list(name.get("observed_at") or identity.get("observed_at") or [])
                self._add_fact(
                    self.facts,
                    "name",
                    f"名称：{name.get('value')}（{label}）",
                    name,
                    evidence=name.get("evidence") or [],
                    identity_ids=[identity.get("identity_version_id")],
                    observed_at=observed,
                    parent_identity=identity,
                )

    def _build_event_facts(self):
        for event in self.data.get("events") or []:
            version_obj = event.get("version") or {}
            version = version_obj.get("introduced_in") or version_obj.get("label")
            self._add_fact(
                self.facts,
                "event",
                f"[版本 {version}] {event.get('title', '')}：{event.get('description', '')}",
                event,
            )

    def _build_relation_facts(self):
        for relation in self.data.get("relations") or []:
            self._add_fact(self.facts, "relation", str(relation.get("statement") or ""), relation)

    def _build_memory_facts(self):
        for memory in self.data.get("memory_records") or []:
            self._add_fact(self.facts, "memory", str(memory.get("content") or ""), memory)

    def _build_affiliation_facts(self):
        for state in self.data.get("affiliation_states") or []:
            scope_label = (state.get("story_scope") or {}).get("label") or "未限定范围"
            if state.get("state") == "unknown":
                self._add_fact(
                    self.facts,
                    "affiliation_gap",
                    f"[数据缺口] 组织归属未确认（{scope_label}）；空数组不表示无组织。",
                    state,
                    evidence=[],
                )
            elif state.get("organization_ids"):
                self._add_fact(
                    self.facts,
                    "affiliation",
                    f"组织归属（{scope_label}）：" + "、".join(map(str, state.get("organization_ids") or [])),
                    state,
                )

    @staticmethod
    def _explicit_choice_scope(*objects):
        for obj in objects:
            if isinstance(obj, dict) and "player_choice_scope" in obj:
                scope = obj.get("player_choice_scope")
                return list(scope) if isinstance(scope, list) else []
        return []

    def _build_conflict_facts(self):
        for conflict in self.data.get("conflicts") or []:
            if conflict.get("status") == "rejected":
                continue
            self._add_fact(
                self.facts,
                "conflict",
                "[争议] " + str(conflict.get("summary") or ""),
                conflict,
                evidence=[],
                status=conflict.get("status") or "open",
                conflict_id=conflict.get("conflict_id"),
                target_ref=conflict.get("target_ref"),
                player_choice_scope=self._explicit_choice_scope(conflict),
            )
            for alternative in conflict.get("alternatives") or []:
                proposed = alternative.get("proposed_value")
                choice_scope = self._explicit_choice_scope(
                    alternative, proposed if isinstance(proposed, dict) else None, conflict
                )
                self._add_fact(
                    self.facts,
                    "conflict_alternative",
                    "[争议候选] " + str(alternative.get("statement") or ""),
                    alternative,
                    evidence=alternative.get("evidence") or [],
                    status=conflict.get("status") or "open",
                    conflict_id=conflict.get("conflict_id"),
                    target_ref=conflict.get("target_ref"),
                    player_choice_scope=choice_scope,
                )

    def _build_attribute_facts(self):
        for record in self.data.get("attribute_records") or []:
            key = record.get("key")
            value = record.get("value")
            label = record.get("value_label") or value
            key_label = {"element": "元素", "weapon": "武器", "nation": "国家"}.get(key, key)
            self._add_fact(
                self.facts,
                "attribute",
                f"属性：{key_label}({key})={label}",
                record,
                evidence=record.get("evidence") or [],
                identity_ids=record.get("identity_version_ids") or [],
                version=record.get("version") or {},
                attribute_id=record.get("attribute_id"),
                attribute_key=key,
                attribute_value=value,
                observed_at=record.get("observed_at") or [],
                player_choice_scope=record.get("player_choice_scope") or [],
            )

    def _build_facts(self) -> None:
        self.facts = []
        self._build_identity_facts()
        self._build_event_facts()
        self._build_relation_facts()
        self._build_memory_facts()
        self._build_affiliation_facts()
        self._build_attribute_facts()
        self._build_conflict_facts()
        self.facts = [fact for fact in self.facts if fact.text.strip()]


class CanonicalIndex:
    """多 canonical 页的轻量检索索引。"""

    _INTENT_BOOSTS = {
        "身份": {"identity", "name"},
        "名称": {"identity", "name"},
        "别名": {"identity", "name"},
        "关系": {"relation"},
        "组织": {"relation", "affiliation", "affiliation_gap"},
        "愚人众": {"relation", "affiliation", "affiliation_gap"},
        "执行官": {"relation"},
        "时间线": {"event"},
        "事件": {"event"},
        "剧情": {"event", "memory"},
        "版本": {"event"},
        "世界树": {"event", "memory"},
        "登神": {"event"},
        "邪眼": {"event", "relation"},
        "记忆": {"memory"},
        "失忆": {"memory"},
        "回忆": {"memory"},
        "冲突": {"conflict", "conflict_alternative"},
        "属性": {"attribute"},
        "元素": {"attribute"},
        "武器": {"attribute"},
        "国家": {"attribute"},
    }

    def __init__(self, pages: List[CanonicalPage], registry: Any = _REGISTRY_UNSET):
        self.pages = pages
        self.registry_unavailable = False
        if registry is _REGISTRY_UNSET:
            try:
                from app import versioning

                registry = versioning.load_registry()
            except Exception:
                registry = None
                self.registry_unavailable = True
        elif registry is None:
            self.registry_unavailable = True
        else:
            registry = copy.deepcopy(registry)
        self.registry = registry
        self.registry_fingerprint = None
        self.registry_revision = None
        if self.registry:
            from app import versioning

            self.registry_fingerprint = versioning.registry_fingerprint(self.registry)
            self.registry_revision = self.registry.get("registry_revision")

    @classmethod
    def from_dir(cls, directory: Optional[Path] = None, include_fixture: bool = False) -> "CanonicalIndex":
        root = Path(directory) if directory else CANONICAL_DIR
        try:
            from app import versioning

            registry = versioning.load_registry()
        except Exception:
            registry = None
        pages = []
        if root.exists():
            for path in sorted(root.glob("*.md")):
                if path.name.lower() == "readme.md":
                    continue
                try:
                    page = CanonicalPage.from_path(path, registry=registry)
                    if not include_fixture and page.data.get("data_mode") == "fixture":
                        continue
                    pages.append(page)
                except Exception:
                    continue
        return cls(pages, registry=registry)

    def registry_snapshot(self) -> Dict[str, Any]:
        return {
            "registry_revision": self.registry_revision,
            "registry_fingerprint": self.registry_fingerprint,
        }

    def alias_ambiguities(self) -> Dict[str, List[str]]:
        """返回跨 canonical 页重名/共用称呼：name -> [entity_id, ...]。"""
        mapping: Dict[str, set] = {}
        for page in self.pages:
            for name in [page.canonical_name] + page.aliases:
                name = str(name).strip()
                if name:
                    mapping.setdefault(name, set()).add(page.entity_id)
        return {name: sorted(ids) for name, ids in mapping.items() if len(ids) > 1}

    @staticmethod
    def _historical_question(question: str) -> bool:
        return any(term in question for term in ("历史", "曾经", "过去", "以前", "旧", "前生", "当时", "before"))

    @staticmethod
    def _scope_applies(scope: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
        scope = scope or {}
        event_id = snapshot.get("story_event_id")
        position = snapshot.get("position")
        from_id = scope.get("from_event_id")
        to_id = scope.get("to_event_id")
        if not event_id or not (from_id or to_id):
            return True
        if position == "before":
            return not (to_id != event_id or from_id == event_id)
        if position in ("after", "at"):
            return not (from_id != event_id or to_id == event_id)
        return True

    @classmethod
    def _version_status(cls, version: Dict[str, Any], snapshot: Dict[str, Any], registry: Any = _REGISTRY_UNSET) -> str:
        version = version or {}
        has_range = any(version.get(key) for key in ("valid_from", "valid_from_label", "valid_to", "valid_to_label"))
        if not has_range:
            # introduced_in / label 只表示首次公开，不是有效期过滤。
            return "applicable"
        if not (snapshot.get("game_version") or snapshot.get("game_version_label")):
            return "applicable"
        try:
            from app.versioning import VersionRegistryError, load_registry, range_contains, resolve_version

            if registry is _REGISTRY_UNSET:
                registry = load_registry()
            elif registry is None:
                return "unresolved"
            value = resolve_version(
                numeric=snapshot.get("game_version"),
                label=snapshot.get("game_version_label"),
                registry=registry,
            )
            lower = None
            upper = None
            if version.get("valid_from") or version.get("valid_from_label"):
                lower = resolve_version(
                    numeric=version.get("valid_from"),
                    label=version.get("valid_from_label"),
                    registry=registry,
                )
            if version.get("valid_to") or version.get("valid_to_label"):
                upper = resolve_version(
                    numeric=version.get("valid_to"),
                    label=version.get("valid_to_label"),
                    registry=registry,
                )
        except VersionRegistryError:
            return "unresolved"
        result = range_contains(value, lower, upper)
        if result is None:
            return "unresolved"
        return "applicable" if result else "excluded"

    @classmethod
    def _version_applies(
        cls, version: Dict[str, Any], snapshot: Dict[str, Any], registry: Any = _REGISTRY_UNSET
    ) -> bool:
        return cls._version_status(version, snapshot, registry=registry) == "applicable"

    @staticmethod
    def _player_choice_applies(scope: List[str], snapshot: Dict[str, Any]) -> str:
        from app.choice_contract import choice_status

        return choice_status(scope, snapshot, has_choice=True, field_present=True)

    @classmethod
    def _fact_status(cls, fact: Fact, snapshot: Dict[str, Any], registry: Any = _REGISTRY_UNSET) -> str:
        from app.choice_contract import APPLICABLE, observations_status

        snapshot = snapshot or {}
        statuses = []
        identity_id = snapshot.get("identity_version_id")
        if identity_id and fact.identity_version_ids and identity_id not in fact.identity_version_ids:
            statuses.append("excluded")
        if (fact.story_scope.get("from_event_id") or fact.story_scope.get("to_event_id")) and not cls._scope_applies(
            fact.story_scope, snapshot
        ):
            statuses.append("excluded")
        statuses.append(cls._version_status(fact.version, snapshot, registry=registry))
        parent = fact.parent_identity or {}
        if parent:
            statuses.append(cls._version_status(parent.get("version") or {}, snapshot, registry=registry))
            parent_scope = parent.get("story_scope") or {}
            if (parent_scope.get("from_event_id") or parent_scope.get("to_event_id")) and not cls._scope_applies(
                parent_scope, snapshot
            ):
                statuses.append("excluded")
            if parent.get("observed_at"):
                statuses.append(observations_status(parent["observed_at"], snapshot, registry=registry))
        if fact.config_error:
            statuses.append("excluded")
        if fact.player_choice_missing:
            statuses.append("unresolved")
        else:
            statuses.append(cls._player_choice_applies(fact.player_choice_scope, snapshot))
        if fact.observed_at:
            statuses.append(observations_status(fact.observed_at, snapshot, registry=registry))
        if "excluded" in statuses:
            return "excluded"
        if "unresolved" in statuses:
            return "unresolved"
        return APPLICABLE

    @classmethod
    def _fact_applies(cls, fact: Fact, snapshot: Dict[str, Any], registry: Any = _REGISTRY_UNSET) -> bool:
        return cls._fact_status(fact, snapshot, registry=registry) == "applicable"

    def registry_gaps(self, snapshot: Dict[str, Any]) -> List[str]:
        gaps = []
        snapshot = snapshot or {}
        for page in self.pages:
            for fact in page.facts:
                if fact.status in ("rejected", "superseded") or not fact.version:
                    continue
                if self._fact_status(fact, snapshot, registry=self.registry) == "excluded":
                    continue
                identity_id = snapshot.get("identity_version_id")
                if identity_id and fact.identity_version_ids and identity_id not in fact.identity_version_ids:
                    continue
                if not self._scope_applies(fact.story_scope, snapshot):
                    continue
                if self._version_status(fact.version, snapshot, registry=self.registry) == "unresolved":
                    label = (
                        fact.version.get("label")
                        or fact.version.get("valid_from_label")
                        or fact.version.get("valid_to_label")
                        or ""
                    )
                    gaps.append(f"{page.entity_id}: {fact.text[:40]}（{label or '版本未知'}）")
        return sorted(set(gaps))

    def _observation_matches(self, observation: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
        snapshot = snapshot or {}
        if observation.get("identity_version_id") not in (None, snapshot.get("identity_version_id")):
            return False
        if observation.get("story_event_id") not in (None, snapshot.get("story_event_id")):
            return False
        if observation.get("position") not in (None, snapshot.get("position")):
            return False
        if observation.get("player_choice") not in (None, snapshot.get("player_choice")):
            return False
        obs_numeric = observation.get("game_version")
        obs_label = observation.get("game_version_label")
        snap_numeric = snapshot.get("game_version")
        snap_label = snapshot.get("game_version_label")
        if obs_numeric or obs_label:
            if not (snap_numeric or snap_label):
                return False
            try:
                from app.versioning import VersionRegistryError, compare_versions, resolve_version

                observed = resolve_version(numeric=obs_numeric, label=obs_label, registry=self.registry)
                queried = resolve_version(numeric=snap_numeric, label=snap_label, registry=self.registry)
            except VersionRegistryError:
                return False
            return compare_versions(observed, queried) == 0
        return True

    def _attribute_status(self, fact: Fact, snapshot: Dict[str, Any]) -> str:
        if fact.status in ("rejected", "superseded"):
            return "excluded"
        return self._fact_status(fact, snapshot, registry=self.registry)

    def player_choice_gaps(self, snapshot: Dict[str, Any], question: Optional[str] = None) -> List[str]:
        snapshot = snapshot or {}
        question = question or ""
        question_chars = {char for char in question if not char.isspace()}
        relevant_entities = set()
        for page in self.pages:
            names = [page.canonical_name] + page.aliases
            if any(name and name in question for name in names):
                relevant_entities.add(page.entity_id)
        gaps = []
        for page in self.pages:
            if relevant_entities and page.entity_id not in relevant_entities:
                continue
            for fact in page.facts:
                if fact.status in ("rejected", "superseded"):
                    continue
                if not (fact.player_choice_scope or fact.player_choice_missing or fact.config_error):
                    continue
                if not fact.player_choice_missing and not fact.config_error and snapshot.get("player_choice"):
                    if self._fact_status(fact, snapshot, registry=self.registry) != "unresolved":
                        continue
                identity_id = snapshot.get("identity_version_id")
                if identity_id and fact.identity_version_ids and identity_id not in fact.identity_version_ids:
                    continue
                if (
                    fact.story_scope.get("from_event_id") or fact.story_scope.get("to_event_id")
                ) and not self._scope_applies(fact.story_scope, snapshot):
                    continue
                if self._version_status(fact.version, snapshot, registry=self.registry) == "excluded":
                    continue
                if fact.observed_at:
                    from app.choice_contract import observations_status

                    obs_status = observations_status(fact.observed_at, snapshot, registry=self.registry)
                    if obs_status == "excluded":
                        continue
                if question_chars and not (question_chars & set(fact.text)):
                    continue
                cites = "".join(
                    f"[原文]({page.span_url[span_id]})" for span_id in fact.evidence if span_id in page.span_url
                )
                if fact.config_error:
                    gaps.append(f"{page.entity_id}: {page.player_choice_error}（{fact.text[:40]}）")
                elif fact.player_choice_missing:
                    gaps.append(f"{page.entity_id}: {fact.text[:40]}（未分类：缺少 player_choice_scope）{cites}")
                else:
                    if snapshot.get("player_choice"):
                        gaps.append(f"{page.entity_id}: {fact.text[:40]}（适用性未解析）{cites}")
                    else:
                        gaps.append(
                            f"{page.entity_id}: {fact.text[:40]}（选择范围：{','.join(fact.player_choice_scope)}）{cites}"
                        )
        return sorted(set(gaps))

    def choice_candidate_lines(self, snapshot: Optional[Dict[str, Any]], question: Optional[str] = None) -> List[str]:
        snapshot = snapshot or {}
        if snapshot.get("player_choice"):
            return []
        question = question or ""
        question_chars = {char for char in question if not char.isspace()}
        relevant_entities = set()
        for page in self.pages:
            names = [page.canonical_name] + page.aliases
            if any(name and name in question for name in names):
                relevant_entities.add(page.entity_id)
        groups: Dict[str, List[str]] = {"aether": [], "lumine": [], "unclassified": []}
        for page in self.pages:
            if relevant_entities and page.entity_id not in relevant_entities:
                continue
            for fact in self._aggregate_facts(page.facts):
                if fact.status in ("rejected", "superseded"):
                    continue
                if fact.config_error or fact.player_choice_missing:
                    groups["unclassified"].append(
                        f"{fact.text[:50]}（{page.player_choice_error or '缺少 player_choice_scope'}）"
                    )
                    continue
                if not fact.player_choice_scope:
                    continue
                if question_chars and not (question_chars & set(fact.text)):
                    continue
                citations = "".join(
                    f"[原文]({page.span_url[span_id]})" for span_id in fact.evidence if span_id in page.span_url
                )
                for choice_id in fact.player_choice_scope:
                    trial = dict(snapshot)
                    trial["player_choice"] = choice_id
                    if self._fact_status(fact, trial, registry=self.registry) == "excluded":
                        continue
                    groups.setdefault(choice_id, []).append(f"{fact.text[:50]}{citations}")
        lines = []
        for choice_id in ("aether", "lumine"):
            for text in sorted(set(groups.get(choice_id) or []))[:3]:
                lines.append(f"> [条件候选] 玩家选择 {choice_id} 时：{text}")
        for text in sorted(set(groups.get("unclassified") or []))[:3]:
            lines.append(f"> [未分类] {text}")
        return lines

    def attribute_projection(self, snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        groups: Dict[str, Dict[str, Any]] = {}
        for page in self.pages:
            for fact in page.facts:
                if fact.kind != "attribute":
                    continue
                group_key = f"{page.entity_id}:{fact.attribute_key}"
                group = groups.setdefault(group_key, {"values": set(), "unresolved": False, "unknown": False})
                status = self._attribute_status(fact, snapshot)
                if status == "applicable":
                    if fact.attribute_value is None:
                        group["unknown"] = True
                    else:
                        group["values"].add(str(fact.attribute_value))
                elif status == "unresolved":
                    group["unresolved"] = True
        result: Dict[str, Dict[str, Any]] = {}
        for group_key, group in groups.items():
            values = sorted(group["values"])
            if len(values) > 1:
                result[group_key] = {"status": "ambiguous", "value": None, "values": values}
            elif group["unresolved"]:
                result[group_key] = {"status": "unresolved", "value": None, "values": values}
            elif len(values) == 1:
                result[group_key] = {"status": "applicable", "value": values[0], "values": values}
            elif group["unknown"]:
                result[group_key] = {"status": "unknown", "value": None, "values": []}
            else:
                result[group_key] = {"status": "not_applicable", "value": None, "values": []}
        return result

    def attribute_ambiguities(self, snapshot: Optional[Dict[str, Any]]) -> List[str]:
        if snapshot is None:
            return []
        grouped: Dict[tuple, set] = {}
        for page in self.pages:
            for fact in page.facts:
                if fact.kind != "attribute" or fact.attribute_value is None:
                    continue
                if fact.status == "rejected" or fact.status == "superseded":
                    continue
                if not self._fact_applies(fact, snapshot, registry=self.registry):
                    continue
                grouped.setdefault((page.entity_id, fact.attribute_key), set()).add(str(fact.attribute_value))
        warnings = []
        for (entity_id, key), values in grouped.items():
            if len(values) > 1:
                warnings.append(
                    f"> [属性歧义] {entity_id} 的 {key} 在当前快照下有多个互斥值：{', '.join(sorted(values))}；不得静默选一个。"
                )
        return sorted(warnings)

    @staticmethod
    def _score_fact(fact: Fact, page_names: List[str], question: str, q_tokens, intent_kinds) -> float:
        f_tokens = Counter(_tokens(fact.text + " " + " ".join(page_names)))
        score = float(sum(min(count, f_tokens.get(token, 0)) for token, count in q_tokens.items()))
        if any(name and name in question for name in page_names):
            score += 5.0
        if fact.kind in intent_kinds:
            score += 2.0
        if fact.kind == "affiliation_gap" and intent_kinds.intersection({"affiliation", "affiliation_gap"}):
            score += 1.0
        return score

    def _passes_filters(self, fact: Fact, question: str, snapshot, historical: bool, include_superseded: bool) -> bool:
        if fact.status == "superseded" and not (historical or include_superseded):
            return False
        if not self._fact_applies(fact, snapshot or {}, registry=self.registry):
            return False
        if fact.kind == "attribute" and self._attribute_status(fact, snapshot or {}) != "applicable":
            return False
        return True

    @staticmethod
    def _aggregate_facts(facts: List[Fact]) -> List[Fact]:
        grouped: Dict[tuple, Fact] = {}
        for fact in facts:
            key = (
                fact.kind,
                fact.record_id or fact.text,
                fact.text,
                tuple(fact.player_choice_scope),
                tuple(fact.identity_version_ids),
                json.dumps(fact.story_scope, sort_keys=True, ensure_ascii=False),
                json.dumps(fact.version, sort_keys=True, ensure_ascii=False),
                fact.status,
                json.dumps(fact.observed_at, sort_keys=True, ensure_ascii=False),
                json.dumps(fact.parent_identity, sort_keys=True, ensure_ascii=False),
            )
            if key not in grouped:
                grouped[key] = fact
            else:
                existing = grouped[key]
                merged = list(existing.evidence)
                for span_id in fact.evidence:
                    if span_id not in merged:
                        merged.append(span_id)
                grouped[key] = replace(existing, evidence=merged)
        return list(grouped.values())

    def search(
        self,
        question: str,
        top_k: int = 6,
        min_score: float = 2.0,
        snapshot: Optional[Dict[str, Any]] = None,
        include_superseded: bool = False,
    ) -> List[Tuple[CanonicalPage, Fact]]:
        question = str(question or "").strip()
        if not question or not self.pages:
            return []
        historical = self._historical_question(question)
        q_tokens = Counter(_tokens(question))
        intent_kinds = set()
        for keyword, kinds in self._INTENT_BOOSTS.items():
            if keyword in question:
                intent_kinds.update(kinds)
        scored: List[Tuple[float, CanonicalPage, Fact]] = []
        for page in self.pages:
            page_names = [page.canonical_name] + page.aliases
            for fact in self._aggregate_facts(page.facts):
                if not self._passes_filters(fact, question, snapshot, historical, include_superseded):
                    continue
                score = self._score_fact(fact, page_names, question, q_tokens, intent_kinds)
                if score >= min_score:
                    scored.append((score, page, fact))
        scored.sort(key=lambda item: (-item[0], item[1].entity_id, item[2].text))
        return [(page, fact) for _, page, fact in scored[: max(1, int(top_k))]]

    def answer(
        self, question: str, top_k: int = 6, snapshot: Optional[Dict[str, Any]] = None, include_superseded: bool = False
    ) -> str:
        choice = (snapshot or {}).get("player_choice")
        if choice not in (None, "aether", "lumine"):
            return "> [选择冲突] snapshot.player_choice 非法；只允许 aether / lumine。"
        warnings = []
        for page in self.pages:
            if page.player_choice_error:
                warnings.append(f"> [配置错误] {page.entity_id}: {page.player_choice_error}")
        for name, ids in self.alias_ambiguities().items():
            if name and name in question:
                warnings.append(f"> [歧义] 「{name}」在多个实体中出现：{', '.join(ids)}；不得默认归一。")
        warnings.extend(self.attribute_ambiguities(snapshot))
        warnings.extend(self.choice_candidate_lines(snapshot, question=question))
        for gap in self.player_choice_gaps(snapshot or {}, question=question)[:5]:
            warnings.append(f"> [选择缺口] {gap}")
        hits = self.search(question, top_k=top_k, snapshot=snapshot, include_superseded=include_superseded)
        if not hits:
            message = (
                "canonical 知识库没有匹配当前快照的可引用记录。" if snapshot else "canonical 知识库没有可引用的记录。"
            )
            if snapshot is not None:
                gaps = self.registry_gaps(snapshot)
                if gaps:
                    message += "\n> [版本注册表缺口] 以下记录因版本映射/顺序未解析，无法确认：" + "；".join(gaps[:5])
            if warnings:
                return "\n".join(warnings + [message])
            return message
        lines = ["【canonical 候选证据（未人工复核）】"] + warnings
        used: Dict[Tuple[str, str], str] = {}
        seen_text = set()
        for page, fact in hits:
            dedupe_key = (
                page.entity_id,
                fact.kind,
                fact.record_id or fact.text,
                tuple(fact.player_choice_scope),
                tuple(fact.identity_version_ids),
                json.dumps(fact.story_scope, sort_keys=True, ensure_ascii=False),
                json.dumps(fact.version, sort_keys=True, ensure_ascii=False),
                fact.status,
                json.dumps(fact.observed_at, sort_keys=True, ensure_ascii=False),
                json.dumps(fact.parent_identity, sort_keys=True, ensure_ascii=False),
            )
            if dedupe_key in seen_text:
                continue
            seen_text.add(dedupe_key)
            labels = []
            for span_id in fact.evidence:
                url = page.span_url.get(span_id)
                if url:
                    labels.append(f"[^source:{url}]")
                    used[(url, span_id)] = page.entity_id
            scope_label = (fact.story_scope or {}).get("label")
            prefix = f"[{fact.status}]" + (f"（{scope_label}）" if scope_label else "")
            if fact.player_choice_scope:
                prefix += f"[选择:{'/'.join(fact.player_choice_scope)}]"
            conflict = f"（{fact.conflict_id} / {fact.target_ref}）" if fact.conflict_id else ""
            lines.append(f"- {prefix}{conflict} {fact.text}" + "".join(labels))
        if len(lines) == 1:
            return "canonical 知识库没有可引用的记录。"
        lines.append("")
        for url, span_id in sorted(used):
            lines.append(f"[^source:{url}]: [原文]({url}) — {span_id}")
        return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def default_index() -> CanonicalIndex:
    return CanonicalIndex.from_dir(CANONICAL_DIR)


def canonical_query(
    question: str,
    top_k: int = 6,
    snapshot: Optional[Dict[str, Any]] = None,
    player_choice: Optional[str] = None,
) -> str:
    """查询本地 canonical Wiki 候选页，返回带来源 citation 的证据文本。"""
    if player_choice is not None and player_choice not in ("aether", "lumine"):
        return "> [选择冲突] 非法 player_choice；只允许 aether / lumine。"
    merged = dict(snapshot or {})
    snapshot_choice = merged.get("player_choice")
    if snapshot_choice not in (None, "aether", "lumine"):
        return "> [选择冲突] snapshot.player_choice 非法；只允许 aether / lumine。"
    if player_choice is not None:
        if snapshot_choice and snapshot_choice != player_choice:
            return "> [选择冲突] 结构化参数与 snapshot.player_choice 不一致；请先明确改选规则。"
        merged["player_choice"] = player_choice
    return default_index().answer(question, top_k=top_k, snapshot=merged)

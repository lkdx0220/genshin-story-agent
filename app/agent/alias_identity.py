"""Request-local, fully consuming alias identity gate (no model or Wiki IO)."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from uuid import uuid4

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

GRAMMAR_VERSION = "alias-identity-grammar-v1"
GRAMMAR_EXTENSIONS = ()
_PREFIXES = ("", "请问", "请问，")
_PREDICATES = ("是谁", "指谁", "是哪位", "是什么人")
_REFERENCES = re.compile("他们|她们|它们|他|她|它|这位|那位|前者|后者|上述|刚才")
_FORBIDDEN = re.compile(r"""[\s\x00-\x1f\x7f"'「」『』“”‘’]""")
_RESET = (
    "rewritten_query",
    "alias_notes",
    "alias_pairs",
    "alias_binding",
    "wiki_route",
    "wiki_evidence",
    "count_result",
    "final_response",
    "plan_exit_reason",
    "execution_plan",
    "intent_labels",
    "execution_mode",
    "termination_kind",
    "route_reason",
    "accepted_aliases",
    "route_diagnostics",
)


def _digest(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def initialize_request(state):
    """Run once at the graph entry; reducer-compatible removal, never in loops."""
    result = dict.fromkeys(_RESET)
    result.update(
        request_nonce=uuid4().hex,
        original_query=state.get("user_query"),
        user_query=state.get("user_query"),
        raw_query=state.get("raw_query"),
        conversation_history=deepcopy(state.get("conversation_history")),
        conversation_summary=deepcopy(state.get("conversation_summary")),
        run_id=state.get("run_id"),
        messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES)],
        coref_changed=False,
        unresolved_reference=False,
        iteration=0,
        plan_retry=0,
        fast_iteration=0,
    )
    return result


def _valid_map(alias_map):
    return (
        isinstance(alias_map, dict)
        and bool(alias_map)
        and all(
            isinstance(a, str) and a and isinstance(c, str) and c
            for a, c in alias_map.items()
        )
    )


def _subject_parses(subject, aliases):
    """Bounded dynamic programming: keep two parses to detect ambiguity."""
    if not subject:
        return []
    parses = {len(subject): [()]}
    for pos in range(len(subject) - 1, -1, -1):
        found = []
        for alias in aliases:
            if not subject.startswith(alias, pos):
                continue
            end = pos + len(alias)
            tails = [()] if end == len(subject) else []
            if end + 1 < len(subject) and subject[end] in ("和", "、"):
                tails = parses.get(end + 1, [])
            for tail in tails:
                value = (alias, *tail)
                if value not in found:
                    found.append(value)
                if len(found) == 2:
                    break
            if len(found) == 2:
                break
        if found:
            parses[pos] = found
    return parses.get(0, [])


def parse_identity(question, alias_map):
    """Return one complete nonself parse, or None; never longest-match guess."""
    if (
        not isinstance(question, str)
        or not question
        or len(question) > 5000
        or _FORBIDDEN.search(question)
        or not _valid_map(alias_map)
    ):
        return None
    text = question[:-1] if question.endswith(("?", "？")) else question
    aliases = tuple(a for a, c in alias_map.items() if a != c)
    results = []
    for prefix in _PREFIXES:
        if not text.startswith(prefix):
            continue
        body = text[len(prefix) :]
        for predicate in _PREDICATES:
            if body.endswith(predicate):
                results.extend(_subject_parses(body[: -len(predicate)], aliases))
        for label in ("外号", "绰号"):
            if body.startswith(label) and body.endswith("的角色是谁"):
                alias = body[len(label) : -len("的角色是谁")]
                if alias in aliases:
                    results.append((alias,))
    # Distinct derivations are ambiguous even when they yield the same names.
    return results[0] if len(results) == 1 else None


def has_unresolved_reference(question, pairs):
    """Conservative lexical flag; exclude only complete accepted alias spans."""
    if not isinstance(question, str) or not isinstance(pairs, (list, tuple)):
        return True
    spans = []
    for pair in pairs:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not pair[0]
        ):
            return True
        start = 0
        while (pos := question.find(pair[0], start)) >= 0:
            spans.append((pos, pos + len(pair[0])))
            start = pos + len(pair[0])
    return any(
        not any(a <= m.start() and m.end() <= b for a, b in spans)
        for m in _REFERENCES.finditer(question)
    )


def make_alias_binding(state, question, pairs, alias_map):
    if (
        not isinstance(state.get("request_nonce"), str)
        or not _valid_map(alias_map)
        or not isinstance(pairs, list)
    ):
        return None
    accepted = []
    for pair in pairs:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(x, str) for x in pair)
            or alias_map.get(pair[0]) != pair[1]
        ):
            return None
        accepted.append(list(pair))
    return {
        "request_nonce": state["request_nonce"],
        "question_digest": _digest(question),
        "alias_map_digest": _digest(alias_map),
        "accepted_pairs": accepted,
    }


def _current_aliases(state, alias_map):
    raw, original, current = (
        state.get(k) for k in ("raw_query", "original_query", "user_query")
    )
    if (
        not isinstance(raw, str)
        or not isinstance(original, str)
        or raw.strip(" ") != original.strip(" ")
        or current != original
        or state.get("coref_changed") is not False
        or state.get("unresolved_reference") is not False
    ):
        return None
    # Controls/Unicode whitespace cannot disappear in the entry sanitizer.
    if _FORBIDDEN.search(raw.strip(" ")):
        return None
    subjects = parse_identity(original.strip(" "), alias_map)
    pairs = state.get("alias_pairs")
    expected = make_alias_binding(state, current, pairs, alias_map)
    if subjects is None or expected is None or state.get("alias_binding") != expected:
        return None
    if has_unresolved_reference(current, pairs):
        return None
    accepted = {tuple(p) for p in expected["accepted_pairs"]}
    if not all((a, alias_map[a]) in accepted for a in subjects):
        return None
    return [(a, alias_map[a]) for a in subjects]


def alias_identity_gate(state):
    from app.progress import _cancel_events
    from character_aliases import ALIAS_MAP

    event = _cancel_events.get(state.get("run_id"))
    if event is not None and event.is_set():
        return {
            "termination_kind": "cancelled",
            "route_reason": "cancelled",
            "final_response": "[回答已中断]",
        }
    pairs = _current_aliases(state, ALIAS_MAP)
    if pairs is None:
        return {}
    answer = (
        "；".join(f"“{alias}”指的是{canonical}" for alias, canonical in pairs) + "。"
    )
    return {
        "final_response": answer,
        "termination_kind": "alias_identity",
        "route_reason": "alias_identity_complete",
        "accepted_aliases": pairs,
        "route_diagnostics": {
            "grammar_version": GRAMMAR_VERSION, "wiki_validation": "not_applicable"
        },
    }

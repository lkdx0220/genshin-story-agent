"""Explicit evidence messages/state; no fake ToolMessage or new LLM instance."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from .store import store_from_environment, strict_json
from . import validation_diagnostics as diagnostic

MESSAGE_ID = 'local-wiki-evidence-current'
DATA_NAME = 'local_wiki_source'
_PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts" / "system"


def _load_prompt_file(name: str) -> str:
    """Load a prompt owned by prompts/system; absence is a startup error."""
    return _PROMPT_ROOT.joinpath(name).read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


BRIDGE_INSTRUCTION = _load_prompt_file("answer_wiki_bridge.txt")


def _contains_external_evidence_claim(text):
    external = r'https?://|www\.|(?:bilibili|miyoushe|hoyolab)\.(?:com|cn)'
    wiki_names = r'bilibili|哔哩哔哩|B站|米游社|hoyolab'
    source_markers = r'来源|证据|原文|根据|参见|出自|source|reference'
    return (re.search(r'\[[^\]\n]*\]\(\s*(?:https?://|www\.)', text, re.IGNORECASE)
            or re.search(r'\[\^source:', text, re.IGNORECASE)
            or any(re.search(source_markers, line, re.IGNORECASE)
                   and re.search(external + '|' + wiki_names, line, re.IGNORECASE)
                   for line in text.splitlines()))


def _answer_source_corpus(state, messages):
    from .canonical_bridge import _looks_canonical, gather
    # Only the current request's actual call table can authorize canonical
    # quotes. A rejected ToolMessage cannot reenter via the legacy corpus.
    texts = source_texts([m for m in messages if isinstance(m, ToolMessage) and not _looks_canonical(m)])
    canonical = gather(state)
    diagnostic.evidence(canonical)
    texts.extend(entry['quote'] for entry in canonical['entries'])
    source_names = set()
    if available(state.get('wiki_evidence')):
        current = retrieve_for_state(state)
        if not available(current) or current['snapshot_id'] != state['wiki_evidence']['snapshot_id']:
            return texts, source_names, '[证据已变化] 本轮回答未展示，请重新查询。'
        texts.extend(item['text'] for item in current['sources'])
        source_names.update(_normalized(item['title']) for item in current['sources'])
        source_names.update(_normalized(alias) for item in current['sources']
                            for entity in item['entities'] for alias in entity['aliases'])
    return texts, source_names, None


def _quotation_validation_failure(text, corpus, source_names, question):
    from .canonical_bridge import quoted_spans
    quotes, balanced = quoted_spans(text)
    if not balanced:
        return '[回答未通过原文校验] 引文边界不完整。'
    for quote in (q for q in quotes if len(q) >= 4):
        if _normalized(quote) in source_names:
            continue  # A verified task/entity name is not fabricated dialogue.
        fragments = re.split(r'[.．]{2,}|…+', quote)
        if any(_normalized(fragment) not in corpus and _normalized(fragment) not in question
               for fragment in fragments if _normalized(fragment)):
            return '[回答未通过原文校验] 引文不在本轮本地剧情证据中。'
    return None


def _quote_inert_urls(text):
    parts = re.split(r'(`[^`\n]*`)', text)
    literal = ''.join(part if i % 2 else re.sub(r'\bhttps?://[^\s`<>，。；）)]+',
                                             lambda match: '`' + match.group(0) + '`', part)
                      for i, part in enumerate(parts))
    return literal


def enabled():
    return os.environ.get('LLM_WIKI_MODE', 'off') == 'agent_evidence'


def is_source_message(message):
    return isinstance(message, HumanMessage) and message.name == DATA_NAME and message.id == MESSAGE_ID


def source_messages(messages):
    return [m for m in messages if isinstance(m, ToolMessage) or is_source_message(m)]


def source_texts(messages):
    """Return raw grounding text; JSON framing and metadata are never facts.

    Named Wiki messages must still match the pinned, freshly rebuilt source.
    A source name/id alone does not authorize arbitrary user-supplied JSON.
    Original ToolMessage text remains unchanged for legacy consumers.
    """
    texts = []
    for message in messages:
        if isinstance(message, ToolMessage):
            from .canonical_bridge import grounding_texts
            canonical = grounding_texts(message)
            if canonical is None:
                texts.append(str(message.content or ''))
            else:
                texts.extend(canonical)
        elif enabled() and is_source_message(message):
            try:
                package = strict_json(message.content)
                if not isinstance(package, dict) or not available(package):
                    continue
                store = store_from_environment(os.environ)
                bundle = store.load()
                if (package.get('snapshot_id') == store.trusted_sha256
                        and package['sources'] == [bundle]):
                    texts.append(bundle['text'])
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                # Invalid/stale data cannot ground a quotation. Answer's
                # freshness boundary separately reports the source failure.
                continue
    return texts


def retrieve_for_state(state):
    if not enabled():
        return None
    question = state.get('user_query', '')
    history = state.get('conversation_history') or []
    context = history[-1].get('user', '') if history else ''
    try:
        return store_from_environment(os.environ).query(question, context=context)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return {'status': 'unavailable', 'coverage': 'unknown', 'sources': [], 'topic_ids': [],
                'reason_code': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}


def available(package):
    return bool(package and package.get('status') == 'available' and package.get('sources'))


def data_message(package):
    return HumanMessage(id=MESSAGE_ID, name=DATA_NAME, content=json.dumps(package, ensure_ascii=False))


def prepared_plan_state(state):
    package = retrieve_for_state(state)
    had_source = any(is_source_message(m) for m in state.get('messages', []))
    if package is None and not had_source and state.get('wiki_evidence') is None:
        return state
    # Never reuse a prior turn's source message. Caller exposes a state update
    # even on miss/error, and reattaches the current package after legacy code.
    clean = [m for m in state.get('messages', []) if not is_source_message(m)]
    update = {'messages': clean, 'wiki_evidence': package, '_wiki_had_source': had_source,
              '_wiki_cleanup': True}
    if enabled():
        # route_after_tools must return to the same Planner/Answer path, even
        # when the original assessment picked L1 before this opt-in adapter.
        update['execution_mode'] = 'L2'
    return dict(state, **update)


def attach_result(result, state):
    package = state.get('wiki_evidence')
    if package is None and not state.get('_wiki_cleanup') and not enabled():
        return result
    messages = [m for m in result.get('messages', []) if not is_source_message(m)]
    if available(package) and not (result.get('final_response') and not messages):
        # The original AIMessage must remain last for route_after_plan/tools.
        messages.insert(0, data_message(package))
    elif state.get('_wiki_had_source'):
        messages.insert(0, RemoveMessage(id=MESSAGE_ID))
    update = {'messages': messages, 'wiki_evidence': package}
    if enabled():
        update['execution_mode'] = 'L2'
    return dict(result, **update)


def context_for_plan(state, messages, base_prompt, *, pseudo_note='', recent_turns=3):
    """Rebuild missing Planner context without splitting AI/tool result groups.

    Legacy Planner normally returns only its newest AI message. On a later
    tool iteration its initial prompt/history therefore need reconstruction.
    Context is local to the call; only the source's stable ID is persisted.
    """
    if not enabled():
        return messages
    clean = [m for m in messages if not is_source_message(m)]
    has_prompt = any(isinstance(m, SystemMessage) and base_prompt in _message_text(m) for m in clean)
    if not has_prompt:
        prompt = base_prompt + (state.get('alias_notes') or '')
        if pseudo_note:
            prompt += '\n\n' + pseudo_note
        if state.get('conversation_summary'):
            prompt += '\n\n先前对话摘要（非事实来源）：' + state['conversation_summary']
        prefix = [SystemMessage(content=[{'type': 'text', 'text': prompt,
                                         'cache_control': {'type': 'ephemeral'}}])]
        for turn in (state.get('conversation_history') or [])[-recent_turns:]:
            prefix.extend([HumanMessage(content=turn['user']),
                           AIMessage(content=turn['assistant'])])
        prefix.append(HumanMessage(content=state.get('user_query', '')))
        clean = prefix + clean
    if available(state.get('wiki_evidence')):
        first = clean[0]
        content = first.content
        if isinstance(content, str):
            first = first.model_copy(update={'content': content + ('' if BRIDGE_INSTRUCTION in content else BRIDGE_INSTRUCTION)})
        else:
            blocks = [dict(block) for block in content]
            if not any(BRIDGE_INSTRUCTION in str(block.get('text', '')) for block in blocks):
                blocks.append({'type': 'text', 'text': BRIDGE_INSTRUCTION})
            first = first.model_copy(update={'content': blocks})
        clean[0] = first
        # Before the whole conversation, never inside AI(tool_calls)->Tools.
        clean.insert(1, data_message(state['wiki_evidence']))
    from .canonical_bridge import plan_messages
    return plan_messages(state, clean)


def context_for_answer(state, messages):
    package = state.get('wiki_evidence')
    clean = [m for m in messages if not is_source_message(m)]
    from .canonical_bridge import project_messages
    clean, canonical = project_messages(state, clean)
    if not enabled() or not available(package):
        return clean, bool(enabled() and canonical['entries'])
    current = retrieve_for_state(state)
    if not available(current) or current != package:
        raise ValueError('evidence_changed_before_answer')
    from .canonical_bridge import ANSWER_INSTRUCTION, gather
    info = gather(state)
    references = [entry for entry in info['entries'] if entry.get('source_kind') == 'bundle']
    catalog = SystemMessage(content=ANSWER_INSTRUCTION + '\n'
                            + json.dumps({'schema_version': 'bundle-answer-citations-v1',
                                          'local_evidence': references}, ensure_ascii=False))
    return clean + [catalog, data_message(current)], True


def _current_literal_records(state):
    """Collect calls and results after the original actual-user boundary."""
    from .canonical_bridge import _user_boundary

    messages = list(state.get('messages', []))
    boundary = _user_boundary(messages)
    calls = [(call.get('id'), call.get('name'), index)
             for index, message in enumerate(messages) if index > boundary and isinstance(message, AIMessage)
             for call in message.tool_calls or []]
    results = [(index, message) for index, message in enumerate(messages)
               if index > boundary and isinstance(message, ToolMessage)]
    return calls, results


def _literal_call_table(calls, results):
    """Require one result for each unique exact, nonblank call identity."""
    from collections import Counter

    if any(not isinstance(identity, str) or not identity.strip() for identity, _, _ in calls):
        return None
    call_counts = Counter(identity for identity, _, _ in calls)
    result_counts = Counter(message.tool_call_id for _, message in results)
    if not results or call_counts != result_counts or any(count != 1 for count in call_counts.values()):
        return None
    return {identity: (name, index) for identity, name, index in calls}


def _render_literal_results(results, declared):
    """Validate each association and preserve ToolMessage-order rendering."""
    from .canonical_bridge import _looks_canonical

    parts = []
    for index, message in results:
        name, position = declared[message.tool_call_id]
        if (not name or name == 'canonical_query' or position >= index or _looks_canonical(message)
                or (message.name and message.name != name)):
            return None
        content = str(message.content).strip()
        if content:
            parts.append(content)
    return '\n\n'.join(parts)


def _exact_current_tool_rendering(text, state, witness):
    """Confirm a node-issued literal rendering, not source-file identity."""
    if not isinstance(witness, str) or not witness or text != witness:
        return False
    calls, results = _current_literal_records(state)
    declared = _literal_call_table(calls, results)
    if declared is None:
        return False
    rendered = _render_literal_results(results, declared)
    return rendered is not None and text == rendered


@diagnostic.validation_boundary("validate_answer")
def validate_answer(text, state, messages=(), *, literal_tool_answer=None):
    """Structural source boundary, not a general semantic-entailment checker."""
    if not enabled():
        return text
    # An ordinary URL may be mentioned as an inert literal. External links and
    # external evidence claims are forbidden; local KB is the only fact source.
    if _contains_external_evidence_claim(text):
        diagnostic.boundary_rejection('external_evidence_claim', text)
        return '[回答未通过来源校验] 请依据本地剧情原文重新回答。'
    texts, source_names, rejection = _answer_source_corpus(state, messages)
    if rejection is not None:
        diagnostic.boundary_rejection('evidence_changed', rejection)
        return rejection
    # Original deterministic metadata previews may intentionally stop inside a
    # quotation. Only their exact current rendering can waive these heuristics;
    # generated text, source identity and all final citation checks stay intact.
    if texts and not _exact_current_tool_rendering(text, state, literal_tool_answer):
        corpus = _normalized('\n'.join(texts))
        question = _normalized(state.get('user_query', ''))
        diagnostic.quote_comparisons(text, texts, source_names, state.get('user_query', ''), corpus, question)
        rejection = _quotation_validation_failure(text, corpus, source_names, question)
        if rejection is not None:
            return rejection
    literal = _quote_inert_urls(text)
    from .canonical_bridge import finalize
    return finalize(literal, state, literal_tool_answer=literal_tool_answer)


def _normalized(value):
    return re.sub(r'[\W_]+', '', value, flags=re.UNICODE)


def _message_text(message):
    if isinstance(message.content, str):
        return message.content
    return '\n'.join(block.get('text', '') if isinstance(block, dict) else str(block)
                     for block in message.content)

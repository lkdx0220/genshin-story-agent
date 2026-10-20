"""Canonical tool provenance consumers; original executor and tool API stay intact.

No tool result or source URL is trusted merely because it is a ToolMessage.
Only exact local records authorized by canonical_local can enter the Answer.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

SCHEMA = 'canonical-local-evidence-v1'
ROOT = Path(__file__).resolve().parents[2]
STATES = {'bound', 'partial', 'missing', 'stale'}
PLAN_INSTRUCTION = '''
【canonical 本地出处协议】
canonical 的 original_text 仅供定位线索，不是可答事实；只使用 local_evidence 中已核原库。
工具执行 trace 的 success 不代表出处 bound。local_provenance_status 为 partial、missing、
stale 或未知时，继续按原有工具和预算补查本地 RAG，不重复同一已执行工具及相同参数。
预算结束或补查仍无证据时说明具体缺口，不能把“候选未绑定”写成整个知识库未收录。
'''
ANSWER_INSTRUCTION = '''
【canonical 引用覆盖规则：仅本模式】
本轮 canonical 的旧 URL/source 脚注规则由此覆盖：仅引用当前程序提供的 L1/L2 等本地别名，
格式如 [^L1]。你仍自行组织自然中文、比较与分析，可准确引用提供的原文；不得编造原话。
不要输出脚注定义、来源路径、旧 source 脚注或外站证据；脚注原文由程序装配。
不能使用未绑定的 canonical 候选文本、旧 URL、Planner 猜测作为事实。缺口不作结论，
保留提瓦特剧情的奇幻语境和请求/完成、主体/否定/阶段区别，不加无关免责声明。
'''


def _canonical_call_table(messages):
    boundary = _user_boundary(messages)
    calls = {}
    positions = {}
    request_ids = {}
    duplicates = set()
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                identity = call.get('id')
                if not isinstance(identity, str) or not identity:
                    continue
                if identity in calls:
                    duplicates.add(identity)
                calls[identity] = call
                positions[identity] = index
                request_ids[identity] = message.additional_kwargs.get('local_canonical_run_id')
    counts = Counter(m.tool_call_id for m in messages if isinstance(m, ToolMessage))
    return calls, positions, request_ids, duplicates, counts, boundary


def _canonical_binding_error(state, message, index, table):
    calls, positions, request_ids, duplicates, counts, boundary = table
    identity = message.tool_call_id
    call = calls.get(identity)
    is_call = bool(call and call.get('name') == 'canonical_query')
    if (not is_call or identity in duplicates or counts[identity] != 1
            or positions.get(identity, index) >= index
            or positions.get(identity, -1) <= boundary
            or not isinstance(state.get('run_id'), str) or not state['run_id']
            or request_ids.get(identity) != state['run_id']
            or not identity or getattr(message, 'name', None) not in (None, 'canonical_query')):
        return {'code': 'call_binding_invalid', 'detail': '工具调用身份缺失、重复或不匹配。'}
    return None


def _collect_canonical_envelope(envelope, tool_call_id):
    records, gaps = [], []
    status = envelope.get('local_provenance_status')
    if status not in STATES:
        status = 'missing'
    if status in {'partial', 'missing', 'stale'}:
        for hit in envelope.get('hits', []):
            if hit.get('status') != 'bound':
                gaps.append(hit['status_reason'])
        if not any(h.get('status') != 'bound' for h in envelope.get('hits', [])):
            gaps.append(envelope['status_reason'])
    if status in {'bound', 'partial'}:
        for evidence in envelope['local_evidence']:
            records.append(dict(evidence, provenance=dict(evidence['provenance'], tool_call_id=tool_call_id)))
    return records, gaps


def _merge_citation_records(records, gaps, canonical_ids, present):
    unique = {}
    for entry in records:
        identity = entry['local_citation_id']
        comparison = dict(entry['provenance'])
        comparison.pop('tool_call_id', None)
        if identity in unique:
            old = dict(unique[identity]['provenance'])
            old.pop('tool_call_id', None)
            if old != comparison or unique[identity]['quote'] != entry['quote']:
                gaps.append({'code': 'citation_collision', 'detail': '本地引用身份冲突。'})
                return {'present': True, 'status': 'missing', 'entries': [],
                        'gaps': gaps, 'canonical_ids': canonical_ids}
        else:
            unique[identity] = entry
    entries = []
    for number, identity in enumerate(sorted(unique), 1):
        entries.append(dict(unique[identity], alias=f'L{number}'))
    status = 'partial' if entries and gaps else 'bound' if entries else 'missing'
    if not entries and any(g.get('code') in {'source_changed', 'source_stale', 'stale_source', 'source_file_changed'} for g in gaps):
        status = 'stale'
    return {'present': present, 'status': status, 'entries': entries,
            'gaps': gaps, 'canonical_ids': canonical_ids}


def _wiki_package_has_supplement(package):
    return package.get('status') == 'available' and package.get('sources')


def _supplement_call_table(messages):
    calls = [(call.get('id'), call.get('name'), index)
             for index, message in enumerate(messages) if isinstance(message, AIMessage)
             for call in message.tool_calls or []]
    counts = Counter(identity for identity, _, _ in calls)
    results = Counter(message.tool_call_id for message in messages if isinstance(message, ToolMessage))
    names = {identity:(name, index) for identity, name, index in calls}
    boundary = _user_boundary(messages)
    return counts, results, names, boundary


def _is_current_noncanonical_supplement(message, index, table):
    counts, results, names, boundary = table
    identity = message.tool_call_id
    name, position = names.get(identity, (None, -1))
    text = str(message.content or '').strip()
    if (name and name != 'canonical_query' and boundary < position < index
            and counts[identity] == 1 and results[identity] == 1
            and not _looks_canonical(message)
            and text and not text.startswith(('[系统', '工具执行出错', '未找到', '未收录', '无匹配', '不存在', 'No match', 'not found'))):
        return True
    return False


def enabled():
    return os.environ.get('LLM_WIKI_MODE', 'off') == 'agent_evidence'


def _looks_canonical(message):
    text = str(message.content or '')
    return (getattr(message, 'name', None) == 'canonical_query'
            or SCHEMA in text[:300] or 'local_provenance_status' in text[:500])


def _validated(raw):
    from .canonical_local import validate_envelope
    return validate_envelope(str(raw), ROOT)


def stamp_plan_result(result, state):
    """Bind actual Planner-produced calls to the API's fresh request run_id.

    This is program metadata, not a model parameter or a query-layer field.
    Original off/finite messages and tool signatures remain unchanged.
    """
    run_id = state.get('run_id')
    if not enabled() or not isinstance(run_id, str) or not run_id:
        return result
    previous_calls = {call.get('id') for message in state.get('messages', [])
                      if isinstance(message, AIMessage) for call in message.tool_calls or []}
    messages = []
    for message in result.get('messages', []):
        if (isinstance(message, AIMessage)
                and any(c.get('name') == 'canonical_query' for c in message.tool_calls or [])
                and all(c.get('id') not in previous_calls for c in message.tool_calls or [])):
            metadata = dict(message.additional_kwargs, local_canonical_run_id=run_id)
            message = message.model_copy(update={'additional_kwargs':metadata})
        messages.append(message)
    return dict(result, messages=messages)


def _user_boundary(messages):
    # The named Wiki data message is a program source, not a new user turn.
    return max((index for index, message in enumerate(messages)
                if isinstance(message, HumanMessage)
                and not (message.name == 'local_wiki_source'
                         and message.id == 'local-wiki-evidence-current')), default=-1)


def gather(state):
    """Rebuild the current invocation table; never borrow prior-turn history."""
    messages = list(state.get('messages', []))
    table = _canonical_call_table(messages)
    calls = table[0]
    records, gaps, canonical_ids = [], [], set()
    present = False
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            continue
        identity = message.tool_call_id
        call = calls.get(identity)
        is_call = bool(call and call.get('name') == 'canonical_query')
        if not is_call and not _looks_canonical(message):
            continue
        present = True
        canonical_ids.add(identity)
        error = _canonical_binding_error(state, message, index, table)
        if error is not None:
            gaps.append(error)
            continue
        envelope = _validated(message.content)
        bound, missing = _collect_canonical_envelope(envelope, identity)
        records.extend(bound)
        gaps.extend(missing)
    return _merge_citation_records(records, gaps, canonical_ids, present)


def plan_messages(state, messages):
    if not enabled():
        return messages
    info = gather(dict(state, messages=messages))
    if not info['present']:
        return messages
    # The protocol groups stay intact. The original JSON remains internal to
    # Planner; the explicit assessment overrides executor's generic success.
    status = json.dumps({'status':info['status'], 'gaps':info['gaps']},ensure_ascii=False)
    return [SystemMessage(content=PLAN_INSTRUCTION+'\n本轮程序判定：'+status)] + list(messages)


def project_messages(state, messages):
    """Remove all original canonical candidate bodies from the Answer input."""
    if not enabled():
        return messages, {'present': False, 'status': 'missing', 'entries': [],
                          'gaps': [], 'canonical_ids': set()}
    info = gather(state)
    if not info['present']:
        return messages, info
    projected = []
    for message in messages:
        if isinstance(message, ToolMessage) and (message.tool_call_id in info['canonical_ids'] or _looks_canonical(message)):
            entries = [e for e in info['entries'] if e['provenance']['tool_call_id'] == message.tool_call_id]
            content = json.dumps({'schema_version': 'canonical-answer-projection-v1',
                                  'local_provenance_status': info['status'],
                                  'local_evidence': entries, 'gaps': info['gaps']},ensure_ascii=False)
            projected.append(message.model_copy(update={'content':content}))
        elif isinstance(message, AIMessage) and not message.tool_calls:
            # A Planner summary may paraphrase unbound candidate facts.
            # Original summaries remain in state/audit, never Answer grounding.
            continue
        elif isinstance(message, AIMessage):
            projected.append(message.model_copy(update={'content':''}))
        else:
            projected.append(message)
    return projected, info


def grounding_texts(message):
    """None means unrelated original tool; [] means canonical has no facts."""
    if not enabled() or not isinstance(message, ToolMessage):
        return None
    text = str(message.content or '')
    if 'canonical-answer-projection-v1' in text[:300]:
        # Projection is used only inside one actual Answer call. Reconstruct
        # the query envelope (without original_text) for exact source checks.
        try:
            data = json.loads(text)
            quotes = []
            from .canonical_local import verify_evidence
            for record in data['local_evidence']:
                checked = dict(record)
                checked.pop('alias', None)
                checked['provenance'] = {k:v for k,v in checked['provenance'].items() if k != 'tool_call_id'}
                verify_evidence(checked, ROOT)
                quotes.append(checked['quote'])
            return quotes
        except (OSError, ValueError, TypeError, KeyError):
            return []
    if _looks_canonical(message):
        # Raw envelopes need the actual current state/call table. They never
        # ground a legacy helper that has received only a list of tool texts.
        return []
    return None


def _has_supplement(state):
    package = state.get('wiki_evidence') or {}
    if _wiki_package_has_supplement(package):
        return True
    messages = state.get('messages', [])
    table = _supplement_call_table(messages)
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            continue
        if _is_current_noncanonical_supplement(message, index, table):
            return True
    return False


def finalize(text, state):
    """Validate local-ID closure, then assemble original quotes and gap status."""
    if not enabled():
        return text
    info = gather(state)
    if not info['present']:
        # Any unknown local reference must still fail closed.
        if re.search(r'\[\^L[^\]]*\]', text):
            return '[回答未通过来源校验] 本地引用不属于本轮证据。'
        return text
    if text.startswith(('[回答未通过', '[证据已变化]', '[回答生成失败]', '[回答已中断]')):
        return text
    if re.search(r'^\s*\[\^[^\]]+\]:', text, re.MULTILINE):
        return '[回答未通过来源校验] 脚注定义只能由程序生成。'
    supplied = {entry['alias']:entry for entry in info['entries']}
    used = re.findall(r'\[\^([^\]]+)\]', text)
    if any(alias not in supplied for alias in used):
        return '[回答未通过来源校验] 本地引用不属于本轮证据。'
    if supplied and not used and not _has_supplement(state):
        return '[回答未通过来源校验] 回答未选择本轮本地引用。'
    if not supplied and not _has_supplement(state):
        return '【未覆盖的部分】本轮尚未取得可核对的本地原文，无法据此回答；这不表示整个知识库未收录。'
    # gather() above reopens original sources immediately before assembly.
    # The local map and finalization never trust the model's own definitions.
    lines = [text]
    for alias in dict.fromkeys(used):
        record = supplied[alias]
        pageid = record['row_pageid']['pageid']
        prov = record['provenance']
        quote = record['quote'].replace('\r\n','\n').replace('\r','\n')
        # Escape Markdown/HTML in program-owned source display; the stored
        # original quote and its hash/range are not normalized or changed.
        quote = quote.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        quote = re.sub(r'([\\`*_{}\[\]()#!|])',r'\\\1',quote)
        quote = quote.replace('\n','\n    ')
        lines.append(f'[^{alias}]: 本地原库 · pageid {pageid} · 字符区间 [{prov["start"]}, {prov["end"]})\n    '+quote)
    if info['status'] in {'partial', 'missing', 'stale'}:
        lines.append('【未覆盖的部分】部分候选资料尚未取得可核对的本地原文，或原文已经变化；这些未核实内容未用于本次回答。')
    return '\n\n'.join(lines)


def quoted_spans(text):
    """Balanced nested quotations, including original multiline dialogue."""
    closing = {'「':'」', '“':'”', '"':'"'}
    stack = []
    start = None
    spans = []
    for index, char in enumerate(text):
        if index and text[index-1] == '\\':
            continue
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                spans.append(text[start+1:index])
                start = None
        elif char in closing:
            if not stack:
                start = index
            stack.append(closing[char])
    return spans, not stack

"""Scoped story cast counting from pinned records and the original evidence API."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import re

from app.entity_retrieval import story_body
from .contracts import (NUMERIC, VERSIONS, Snapshot, _validate_links, _validate_locations, _validate_numbers,
                        _validate_status, canonical_bytes, evidence_location,
                        new_result, require, snapshot_identity, snapshot_value, validate_verdict)
from .evidence import (ALIAS_SHA256, AliasSnapshot, _cast_members,
                       _metadata_completeness, member_evidence)

from .snapshot_reads import GraphEntries, SnapshotReadContext

GRAPH_SHA256 = 'b1c611dadf99de98fcf180e8b2b62733b58747191dde720ded5a52504ce0b417'
ROOT = Path(__file__).resolve().parents[2]
_LOGGER = logging.getLogger(__name__)
_UNCERTAINTY = ('unknown_member_count', 'conflict_member_count',
                'unresolved_name_count', 'unenumerated_member_count')
_SPEAKER = re.compile(r'^([^\s:：<>「“"\[【]{1,40})[:：]')
_QUESTIONS = re.compile(
    r'(?P<title>.+?)(?:有多少(?:角色|人物)参与(?:[,，]分别是谁)?|有哪些人物出场)')
_AUDIT_PREFIX = 'cast-audit:'


def _safe_title(value):
    value = str(value or '').strip()
    if (len(value) > 120 or re.search(r'[\x00-\x1f\\/]|[0-9a-f]{64}|\.json\b', value)):
        return '指定剧情'
    return value or '尚未明确的剧情'


def _blank(request, title='', status='missing', reason='资料不足'):
    result = new_result(request, status=status, gaps=[reason])
    result.update(contract_version='count_result_v1.3', error_code=None, unit='人',
                  cast_breakdown=None, cast_uncertainty=dict.fromkeys(_UNCERTAINTY))
    level = request.get('scope_level', 'activity')
    result['normalized_scope'].update(
        scope_level=level, scope_id=request.get('entry_id') or 'unresolved',
        identity_basis='stable_id', region_mode='not_applicable',
        population_basis=f'本地《{_safe_title(title)}》剧情范围内可唯一识别的剧情人物（含具名NPC，不限可玩角色）')
    return result


def parse_story_query(query):
    """Accept a complete small language, without deleting scope qualifiers."""
    if not isinstance(query, str):
        return None
    text = re.sub(r'^(?:请问|请|帮我)\s*', '', query.strip(), count=1).rstrip('？?。！!').strip()
    match = _QUESTIONS.fullmatch(text)
    if not match:
        return None
    title = match['title'].strip()
    if title.startswith('《') and title.endswith('》'):
        title = title[1:-1]
    elif any(c in title for c in '《》'):
        return None
    if not title or re.search(r'只算|仅|除了|除去|不含|合并|跨幕|第.{0,5}[幕章]|可玩角色|版本|之后|之前|[()（）]', title):
        return None
    return title


class _InputFailure(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _load_source(root, graph_sha256, *, _reads=None):
    try:
        graph = Snapshot.read(root, 'kb_vectors/wiki_entry_graph.json', graph_sha256)
    except OSError as exc:
        raise _InputFailure('source_unavailable') from exc
    except (ValueError, UnicodeError) as exc:
        code = 'source_snapshot_unverifiable' if str(exc).startswith(('source_sha_', 'source_expected_', 'source_escape')) else 'source_data_invalid'
        raise _InputFailure(code) from exc
    if _reads is not None:
        try:
            return graph, _reads.graph_entries(graph)
        except ValueError as exc:
            raise _InputFailure('source_data_invalid') from exc
    data = graph.data()
    if not (isinstance(data, dict) and isinstance(data.get('entries'), list)
            and all(isinstance(e, dict) and isinstance(e.get('entry_id'), str) for e in data['entries'])):
        raise _InputFailure('source_data_invalid')
    return graph, data['entries']


def _load_aliases(root, alias_sha256, *, _reads=None):
    try:
        aliases = AliasSnapshot.read(root, alias_sha256, _reads=_reads)
        aliases.identity()
        return aliases
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise _InputFailure('alias_snapshot_unverifiable') from exc


def _unavailable(request, title, status, reason, graph=None, aliases=None, error_code=None, *, _reads=None):
    result = _blank(request, title, status, reason)
    _bind_available(result, graph, aliases, _reads=_reads)
    result['error_code'] = error_code
    if status == 'unsupported':
        result['unsupported_reason'] = reason
    return _finish(result)


def _count_story_cast(request, *, root=ROOT, graph_sha256=GRAPH_SHA256, alias_sha256=ALIAS_SHA256, _reads=None):
    """Trusted Python callers can select frozen test sources; state cannot."""
    title = ''
    graph = aliases = None
    try:
        require(set(request) == {'tool', 'entry_id', 'scope_level'}
                and request['tool'] == 'count_story_cast', 'story_request')
        if request['scope_level'] not in {'activity', 'task'}:
            return _unavailable(request, title, 'unsupported', '本批仅支持单activity/task')
        graph, entries = _load_source(root, graph_sha256, _reads=_reads)
        matches = [(i, e) for i, e in _scan(entries) if e.get('entry_id') == request['entry_id']]
        if not matches:
            return _unavailable(request, title, 'missing', '未找到精确剧情条目', graph, aliases, _reads=_reads)
        if len(matches) != 1:
            return _ambiguous(request, title, matches, graph, aliases, _reads=_reads)
        index, entry = matches[0]
        if isinstance(entries, GraphEntries):
            entry = entries.copy(index)
        title = entry.get('title', '')
        if entry.get('entry_type') != request['scope_level']:
            return _unavailable(request, title, 'unsupported', '条目类型与请求范围不一致', graph, aliases, _reads=_reads)
        aliases = _load_aliases(root, alias_sha256, _reads=_reads)
        return _count_bound(request, index, entry, graph, entries, aliases, _reads=_reads)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError) as exc:
        _LOGGER.info('story_cast_error', extra={'story_cast_error': type(exc).__name__ + ':' + str(exc)})
        return _input_error(request, title, graph, aliases, exc, _reads=_reads)


def _story_result_for_query(query, *, root=ROOT, graph_sha256=GRAPH_SHA256, alias_sha256=ALIAS_SHA256, _reads=None):
    title = parse_story_query(query)
    if title is None:
        return None
    request = {'tool': 'count_story_cast', 'entry_id': 'unresolved', 'scope_level': 'activity'}
    graph = aliases = None
    if title in {'这个活动', '这个任务', '这个剧情', '该活动', '该任务'}:
        return _unavailable(request, '', 'missing', '需要明确本轮剧情标题')
    try:
        graph, entries = _load_source(root, graph_sha256, _reads=_reads)
        matches = [(i, e) for i, e in _scan(entries)
                   if e.get('entry_type') in {'activity', 'task'}
                   and (e.get('title') == title or title in e.get('aliases', []))]
        if not matches:
            return _unavailable(request, title, 'missing', '未找到精确剧情标题', graph, aliases, _reads=_reads)
        if len(matches) != 1:
            return _ambiguous(request, title, matches, graph, aliases, _reads=_reads)
        index, entry = matches[0]
        if isinstance(entries, GraphEntries):
            entry = entries.copy(index)
        request.update(entry_id=entry['entry_id'], scope_level=entry['entry_type'])
        aliases = _load_aliases(root, alias_sha256, _reads=_reads)
        return _count_bound(request, index, entry, graph, entries, aliases, _reads=_reads)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError) as exc:
        _LOGGER.info('story_cast_error', extra={'story_cast_error': type(exc).__name__ + ':' + str(exc)})
        return _input_error(request, title, graph, aliases, exc, _reads=_reads)


def _input_error(request, title, graph, aliases, exc, *, _reads=None):
    code = exc.code if isinstance(exc, _InputFailure) else 'internal_validation_failed'
    if _reads is not None and _reads._state != 'open':
        graph = aliases = None
    return _unavailable(request, title, 'error', '源或语义校验失败', graph, aliases, code, _reads=_reads if graph is not None else None)


def _bind_available(result, graph, aliases, *, _reads=None):
    """Attach only snapshots that actually passed verification, including errors."""
    if graph is not None:
        result['source_snapshots'] = [snapshot_identity(graph, _reads)]
    if aliases is not None:
        result['alias_snapshot'] = aliases.identity()


def _ambiguous(request, title, matches, graph, aliases, *, _reads=None):
    result = _blank(request, title, 'ambiguous', '同标题或身份对应多个条目')
    _bind_available(result, graph, aliases, _reads=_reads)
    result['ambiguities'] = [{'input': title or '指定剧情',
                              'candidates': sorted({_safe_title(e.get('title')) +
                                                    ('（活动）' if e.get('entry_type') == 'activity' else '（任务）')
                                                    for _, e in matches}),
                              'reason': '需明确剧情范围，未选择首条'}]
    return _finish(result)


def _speaker_names(entry):
    body = story_body(entry)
    if '[任务概述]' in body or '[前情提要]' in body:
        return []
    result, depth, fenced, offset = [], 0, False, 0
    for line in body.splitlines(keepends=True):
        if line.strip().startswith('```'):
            fenced = not fenced
        match = _SPEAKER.match(line)
        if match and not fenced and depth == 0:
            result.append((match[1], {'start': offset, 'end': offset + len(line.rstrip('\r\n')),
                                      'normalizer_version': 'story-body-existing-speaker-exact-v1'}))
        depth = max(0, depth + line.count('“') + line.count('「') - line.count('”') - line.count('」'))
        offset += len(line)
    return result


def _type_records(entries, aliases):
    records = []
    for index, entry in _scan(entries):
        if entry.get('entry_type') not in {'character', 'npc'}:
            continue
        name, _ = aliases.normalize(entry.get('title', ''))
        if name:
            if isinstance(entries, GraphEntries):
                entry = entries.copy(index)
            records.append({'canonical_member': name, 'original_pointer': '/entries/' + str(index),
                            'entry': entry})
    return records


def _derive(graph, index, entry, types, *, _reads=None):
    """Keep the original record unchanged; never manufacture its missing fields."""
    payload = {'origin': dict(snapshot_identity(graph, _reads), json_pointer='/entries/' + str(index)),
               'entry': entry, 'type_records': types, 'adapter_version': 'exact-entry-projection-v1'}
    raw = canonical_bytes(payload)
    path = 'derived/story-cast/' + graph.sha256 + '/' + str(index) + '.json'
    return Snapshot(path, hashlib.sha256(raw).hexdigest(), raw)


def _collect(entry, source, aliases, scope, *, _reads=None):
    cast = entry.get('cast')
    _validate_cast_field(cast)
    names = defaultdict(list)
    for index, raw in enumerate(cast or []):
        names[raw].append(evidence_location(source, '/entry/cast/' + str(index), _reads=_reads))
    field = 'story_text' if entry.get('story_text') else 'full_text'
    for raw, span in _speaker_names(entry):
        names[raw].append(evidence_location(source, '/entry/' + field, span, _reads=_reads))
    candidates, unresolved = defaultdict(list), []
    for raw, locations in sorted(names.items()):
        name, ambiguity = aliases.normalize(raw)
        if name:
            candidates[name].append({'raw': raw, 'locations': locations})
        else:
            unresolved.append({'raw': raw, 'locations': locations, 'candidates': ambiguity})
    packets = {name: [{'source_id': snapshot_identity(source, _reads)['source_id'], 'entry_pointer': '/entry',
                      'verdict': member_evidence(entry, name, scope=scope, source=source,
                                                entry_pointer='/entry', aliases=aliases, _reads=_reads)}]
               for name in sorted(candidates)}
    return packets, candidates, unresolved


def _validate_cast_field(cast):
    if not (cast is None or (isinstance(cast, list) and all(isinstance(n, str) and n.strip() for n in cast))):
        raise _InputFailure('source_data_invalid')


def merge_member_evidence(packets, *, scope, snapshots, aliases, _reads=None):
    """One shared merge core. Verify original verdicts against pinned source bytes.

    Public collection supplies a single exact entry. Multiple independent source
    packets here are internal acceptance inputs, not a new public linking API.
    """
    checks = {}
    for name, rows in sorted(packets.items()):
        require(aliases.normalize(name)[0] == name and bool(rows), 'cast_packet_identity')
        checks[name] = [_verify_packet(name, row, scope, snapshots, aliases, _reads=_reads) for row in rows]
    return _partition(checks), checks


def _verify_packet(name, packet, scope, snapshots, aliases, *, _reads=None):
    require(set(packet) == {'source_id', 'entry_pointer', 'verdict'}, 'cast_packet_keys')
    source = snapshots[packet['source_id']]
    snapshot_identity(source, _reads)
    entry = snapshot_value(source, packet['entry_pointer'], _reads)
    require(entry['entry_id'] == scope['scope_id'], 'cast_packet_entry')
    expected = member_evidence(entry, name, scope=scope, source=source,
                               entry_pointer=packet['entry_pointer'], aliases=aliases, _reads=_reads)
    require(canonical_bytes(expected) == canonical_bytes(packet['verdict']), 'cast_packet_verdict')
    require(expected['canonical_member'] == name and expected['scope'] == scope, 'cast_packet_scope')
    return expected


def _partition(checks):
    groups = {decision: [] for decision in ('include', 'exclude', 'unknown', 'conflict')}
    for name, verdicts in sorted(checks.items()):
        decisions = {v['decision'] for v in verdicts}
        require(not {'include', 'exclude'} <= decisions, 'cast_unlinked_opposing_sources')
        if 'conflict' in decisions:
            decision = 'conflict'
        elif 'include' in decisions:
            decision = 'include'
        elif 'exclude' in decisions:
            decision = 'exclude'
        else:
            decision = 'unknown'
        groups[decision].append(name)
    return groups


def _metadata_complete(entry, scope, aliases):
    parsed_names, parsed = _cast_members(entry, aliases)
    complete, parse_ok = _metadata_completeness(entry, scope, parsed_names, aliases)
    return (parsed and parse_ok and complete and entry.get('cast_scope') == scope['scope_level']
            and 'metadata' in (entry.get('cast_source') or '').split('+'))


def _count_bound(request, index, entry, graph, entries, aliases, *, _reads=None):
    scope = {'scope_level': request['scope_level'], 'scope_id': request['entry_id']}
    source = _derive(graph, index, entry, _type_records(entries, aliases), _reads=_reads)
    if _reads is not None:
        _reads.bind(source)
        _reads.lock_execution(scope, aliases)
    packets, candidates, unresolved = _collect(entry, source, aliases, scope, _reads=_reads)
    complete = _metadata_complete(entry, scope, aliases)
    return aggregate_story_cast(request, title=entry.get('title'), packets=packets,
        candidates=candidates, unresolved=unresolved, aliases=aliases,
        snapshots={snapshot_identity(s, _reads)['source_id']: s for s in (graph, source)},
        complete=complete, field_present=isinstance(entry.get('cast'), list),
        type_source=source, _reads=_reads)


def aggregate_story_cast(request, *, title, packets, candidates, unresolved, aliases,
                         snapshots, complete=False, field_present=None, type_source=None, _reads=None):
    """Actual count entry's aggregation; partial batches remain explicit lower bounds."""
    if _reads is None:
        return _aggregate_owned(request, title, packets, candidates, unresolved, aliases, snapshots,
                                complete, field_present, type_source)
    scope = {'scope_level': request['scope_level'], 'scope_id': request['entry_id']}
    if complete:
        _verify_complete_collection(type_source, packets, candidates, unresolved, aliases, scope, _reads=_reads)
    groups, checks = merge_member_evidence(packets, scope=scope, snapshots=snapshots, aliases=aliases, _reads=_reads)
    exact = complete and not (groups['unknown'] or groups['conflict'] or unresolved)
    status = 'complete' if exact else ('partial' if groups['include'] or groups['conflict'] else 'missing')
    result = _blank(request, title, status)
    result['source_snapshots'] = sorted([snapshot_identity(s, _reads) for s in snapshots.values()], key=lambda s: s['source_id'])
    result['alias_snapshot'] = aliases.identity()
    result['count_basis'] = 'exact_in_snapshot' if exact else ('lower_bound' if status == 'partial' else 'not_available')
    result['gaps'] = [] if exact else ['缺少同范围完整性或身份闭合证明']
    result['cast_uncertainty'] = dict(zip(_UNCERTAINTY, (len(groups['unknown']), len(groups['conflict']),
                                                       len(unresolved), 0 if exact else None), strict=True))
    result['normalized_scope']['normalized_members'] = sorted(candidates)
    _add_confirmed(result, checks, groups)
    audit = {'scope': scope, 'checks': checks, 'packets': packets, 'candidates': dict(candidates),
             'unresolved_names': unresolved, 'groups': groups}
    _record_aggregate_audit(result, audit, field_present, exact)
    if status in NUMERIC:
        result['count'] = result['denominator']['matched'] = len(result['members'])
        result['cast_breakdown'] = _breakdown(result, type_source, _reads=_reads) if type_source is not None else None
    bound = dict(snapshots, **{snapshot_identity(aliases.snapshot, _reads)['source_id']: aliases.snapshot})
    return _finish(result, bound, _reads=_reads)


def _record_aggregate_audit(result, audit, field_present, exact):
    authoritative = any(v['evidence_kind'] == 'metadata' for rows in audit['checks'].values() for v in rows)
    result['denominator'].update(total_in_source=1, considered=1, with_field=None if field_present is None else int(field_present),
                                 missing_field=None if field_present is None else int(not field_present), with_authoritative_data=int(authoritative),
                                 unresolved=0 if exact else 1)
    if not exact:
        result['gaps'].append(_AUDIT_PREFIX + canonical_bytes(audit).decode('utf-8').strip())
    _LOGGER.info('story_cast_evidence', extra={'story_cast_audit': audit})


def _verify_complete_collection(source, packets, candidates, unresolved, aliases, scope, *, _reads=None):
    require(source is not None, 'cast_complete_source')
    snapshot_identity(source, _reads)
    entry = snapshot_value(source, '/entry', _reads)
    require(entry['entry_id'] == scope['scope_id'] and entry['entry_type'] == scope['scope_level'],
            'cast_complete_scope')
    require(_metadata_complete(entry, scope, aliases), 'cast_complete_metadata')
    actual = _collect(entry, source, aliases, scope, _reads=_reads)
    require(canonical_bytes(actual) == canonical_bytes((packets, candidates, unresolved)),
            'cast_complete_collection')


def _add_confirmed(result, checks, groups):
    for name in groups['include']:
        verdicts = [v for v in checks[name] if v['decision'] == 'include']
        eid = 'cast:' + hashlib.sha256(canonical_bytes(verdicts)).hexdigest()
        locations = [loc for v in verdicts for loc in v['evidence_location']]
        result['members'].append({'member_id': name, 'display_name': _safe_title(name), 'evidence_ids': [eid]})
        result['evidence'].append({'evidence_id': eid, 'subject_id': name, 'kind': 'member_verdict', 'member_checks': verdicts,
                                   'locations': locations})
    for name in groups['conflict']:
        result['conflicts'].append({'subject_id': name, 'scope': checks[name][0]['scope'],
                                    'member_checks': checks[name], 'reason': '同范围证据冲突，未计人数'})
    result['members'].sort(key=lambda m: m['member_id'])
    result['evidence'].sort(key=lambda e: e['evidence_id'])
    result['evidence_ids'] = [e['evidence_id'] for e in result['evidence']]


def _breakdown(result, source, *, _reads=None):
    types = defaultdict(set)
    proofs = defaultdict(list)
    for i, row in enumerate(snapshot_value(source, '/type_records', _reads)):
        types[row['canonical_member']].add(row['entry']['entry_type'])
        proofs[row['canonical_member']].append(evidence_location(source, '/type_records/' + str(i) + '/entry/entry_type', _reads=_reads))
    if not result['members'] or any(len(types[m['member_id']]) != 1 for m in result['members']):
        return None
    for row in result['evidence']:
        row['locations'].extend(proofs[row['subject_id']])
    return {'playable': sum(types[m['member_id']] == {'character'} for m in result['members']),
            'npc': sum(types[m['member_id']] == {'npc'} for m in result['members'])}


def render_story_result(result):
    head = result['normalized_scope']['population_basis'] + '；仅据本库收录资料。'
    status = result['status']
    if status not in NUMERIC:
        labels = {'missing': '资料或明确范围不足，暂不能给出人数。', 'ambiguous': '存在同名剧情，请明确活动或任务范围。',
                  'unsupported': '暂不支持该范围的人数统计。', 'error': '统计资料暂不可用，请稍后重试。'}
        return head + '\n' + labels[status] + _uncertainty_text(result)
    count = result['count']
    quantity = f'已确认至少{count}人' if status == 'partial' else f'本库该范围收录{count}人'
    names = '、'.join(m['display_name'] for m in result['members']) or '暂无无冲突的已确认人物'
    text = head + '\n' + quantity + '。\n' + names + '。' + _uncertainty_text(result) + _conflict_text(result)
    breakdown = result['cast_breakdown']
    if breakdown is not None:
        text += f'\n已确认者中：可玩角色{breakdown["playable"]}人，具名NPC{breakdown["npc"]}人。'
    return text


def _conflict_text(result):
    return '\n冲突人物未计入已确认人数；不能据此认定无人出场。' if result['conflicts'] else ''


def _uncertainty_text(result):
    if result['status'] == 'error':
        return ''
    u = result['cast_uncertainty']
    parts = []
    for key, suffix in [('unknown_member_count', '名已识别人物参与证据不足'),
                        ('conflict_member_count', '名人物证据冲突'),
                        ('unresolved_name_count', '个名称待核')]:
        if u[key]:
            parts.append(str(u[key]) + suffix)
    if u['unenumerated_member_count'] is None:
        parts.append('资料可能还漏收其他人物，数量未知')
    return '\n说明：' + '；'.join(parts) + '。' if parts else ''


def _validate_uncertainty(result, snapshots, *, _reads=None):
    u = result['cast_uncertainty']
    require(set(u) == set(_UNCERTAINTY), 'cast_uncertainty_keys')
    require(all(v is None or (type(v) is int and v >= 0) for v in u.values()), 'cast_uncertainty_values')
    require(u['unenumerated_member_count'] in (0, None), 'cast_unenumerated')
    require(u['conflict_member_count'] in (None, len(result['conflicts'])), 'cast_conflict_count')
    if result['status'] in {'complete', 'zero'}:
        require(all(v == 0 for v in u.values()), 'cast_exact_uncertainty')
    if result['status'] == 'error':
        require(all(v is None for v in u.values()), 'cast_error_uncertainty')
    audits = [g[len(_AUDIT_PREFIX):] for g in result['gaps'] if g.startswith(_AUDIT_PREFIX)]
    require(len(audits) <= 1, 'cast_audit_duplicate')
    if audits:
        audit = json.loads(audits[0])
        scope = {'scope_level': result['request']['scope_level'], 'scope_id': result['request']['entry_id']}
        require(audit['scope'] == scope, 'cast_audit_scope')
        groups = _partition(audit['checks'])
        require(set(groups['include']) == {m['member_id'] for m in result['members']}, 'cast_audit_members')
        require(len(groups['unknown']) == u['unknown_member_count'], 'cast_unknown_count')
        require(len(audit['unresolved_names']) == u['unresolved_name_count'], 'cast_unresolved_names')
        require(set(groups['conflict']) == {c['subject_id'] for c in result['conflicts']}, 'cast_audit_conflicts')
        _validate_audit_sources(result, audit, groups, scope, snapshots, _reads=_reads)


def _validate_audit_sources(result, audit, groups, scope, snapshots, *, _reads=None):
    if snapshots is not None:
        alias_id = result['alias_snapshot']['path'] + '@' + result['alias_snapshot']['sha256']
        merged, actual_checks = merge_member_evidence(audit['packets'], scope=scope, snapshots=snapshots,
                                                      aliases=AliasSnapshot(snapshots[alias_id], _reads), _reads=_reads)
        require(merged == groups and actual_checks == audit['checks'], 'cast_audit_replay')
    sources = {s['source_id']: s for s in result['source_snapshots']}
    for name, verdicts in audit['checks'].items():
        for verdict in verdicts:
            validate_verdict(verdict)
            require(verdict['canonical_member'] == name and verdict['scope'] == scope, 'cast_audit_identity')
            _validate_locations(verdict['evidence_location'], sources, snapshots, _reads=_reads)
    for row in audit['unresolved_names']:
        _validate_locations(row['locations'], sources, snapshots, _reads=_reads)


def validate_story_result(result, snapshots=None, *, _reads=None):
    if _reads is None and snapshots:
        return _validate_owned(result, snapshots)
    request = result['request']
    require(set(result) == set(_blank(request)), 'story_result_keys')
    require(result['contract_version'] == 'count_result_v1.3' and result['tool'] == 'count_story_cast'
            and request['tool'] == result['tool'], 'story_version')
    require(set(request) == {'tool', 'entry_id', 'scope_level'}, 'story_request_keys')
    require(result['unit'] == '人' and result['render_policy'] == 'direct_verbatim', 'story_unit_render')
    require(result['versions'] == VERSIONS, 'story_versions')
    require(result['normalized_scope']['region_mode'] == 'not_applicable', 'story_region_mode')
    _validate_bound_members(result)
    _validate_numbers(result)
    _validate_status(result)
    _validate_links(result, snapshots, _reads=_reads)
    _validate_uncertainty(result, snapshots, _reads=_reads)
    _validate_story_state(result)
    b = result['cast_breakdown']
    if b is not None:
        require(set(b) == {'playable', 'npc'} and all(type(v) is int and v >= 0 for v in b.values()), 'cast_breakdown')
        require(result['status'] in NUMERIC and sum(b.values()) == result['count'], 'cast_breakdown_sum')
        _validate_type_proofs(result, snapshots, _reads=_reads)
    require(result['answer_text_hint'] == render_story_result(result), 'story_noncanonical_render')
    return result


def _validate_story_state(result):
    status = result['status']
    if status == 'partial' and result['count'] == 0:
        require(bool(result['conflicts']), 'cast_partial_zero_without_conflict')
    if status in NUMERIC:
        require(isinstance(result['alias_snapshot'], dict), 'cast_numeric_alias')
    if status == 'error':
        require(result['error_code'] in {'alias_snapshot_unverifiable', 'source_unavailable',
                'source_snapshot_unverifiable', 'source_data_invalid', 'internal_validation_failed'}, 'cast_error_code')
        require(not result['conflicts'] and all(v is None for v in result['denominator'].values()), 'cast_error_counts')
        if result['error_code'] == 'alias_snapshot_unverifiable':
            require(result['alias_snapshot'] is None, 'cast_error_alias')
    else:
        require(result['error_code'] is None, 'cast_nonerror_code')


def _validate_bound_members(result):
    request = result['request']
    scope = {'scope_level': request['scope_level'], 'scope_id': request['entry_id']}
    normalized = result['normalized_scope']
    require(all(normalized[k] == v for k, v in scope.items()), 'cast_normalized_scope')
    for row in result['evidence']:
        require(bool(row['member_checks']), 'cast_empty_member_checks')
        for verdict in row['member_checks']:
            require(verdict['canonical_member'] == row['subject_id'], 'cast_member_identity')
            require(verdict['scope'] == scope and verdict['entry_id'] == request['entry_id'], 'cast_member_scope')
    for conflict in result['conflicts']:
        _validate_conflict_identity(conflict, scope)


def _validate_conflict_identity(conflict, scope):
    require(conflict['scope'] == scope, 'cast_conflict_scope')
    for verdict in conflict['member_checks']:
        require(verdict['scope'] == scope and verdict['entry_id'] == scope['scope_id'], 'cast_conflict_verdict_scope')
        require(verdict['canonical_member'] == conflict['subject_id'], 'cast_conflict_identity')


def _validate_type_proofs(result, snapshots, *, _reads=None):
    expected = {'playable': 0, 'npc': 0}
    for row in result['evidence']:
        proofs = [loc for loc in row['locations']
                  if re.fullmatch(r'/type_records/[0-9]+/entry/entry_type', loc['json_pointer'])]
        require(bool(proofs), 'cast_type_proof_missing')
        if snapshots is not None:
            kinds = {snapshot_value(snapshots[loc['source_id']], loc['json_pointer'], _reads) for loc in proofs}
            require(len(kinds) == 1 and kinds <= {'character', 'npc'}, 'cast_type_proof_conflict')
            expected['playable' if kinds == {'character'} else 'npc'] += 1
    if snapshots is not None:
        require(expected == result['cast_breakdown'], 'cast_type_proof_count')


def _finish(result, snapshots=None, *, _reads=None):
    result = deepcopy(result)
    result['answer_text_hint'] = render_story_result(result)
    return validate_story_result(result, snapshots, _reads=_reads)


def _scan(entries):
    if isinstance(entries, GraphEntries):
        return entries.scan(('entry_id', 'entry_type', 'title', 'aliases'))
    return enumerate(entries)


def _context(root, original, kind, snapshots):
    return SnapshotReadContext(root=root, original_request=original, entry_kind=kind,
                               expected_sources=snapshots)


def _call_owned(original, kind, root, graph_sha256, alias_sha256, core):
    reads = _context(root, original, kind, {'kb_vectors/wiki_entry_graph.json': graph_sha256,
                                           'kb_vectors/entity_alias_map.json': alias_sha256})
    try:
        pending = core(_reads=reads)
        if pending is not None and pending['status'] != 'error':
            try:
                reads.seal()
            except ValueError as exc:
                return _input_error(pending['request'], '', None, None, exc)
        return pending
    finally:
        reads.close()


def count_story_cast(request, *, root=ROOT, graph_sha256=GRAPH_SHA256, alias_sha256=ALIAS_SHA256):
    request = deepcopy(request)
    def core(*, _reads):
        return _count_story_cast(request, root=root, graph_sha256=graph_sha256,
                                 alias_sha256=alias_sha256, _reads=_reads)
    return _call_owned(request, 'structured', root, graph_sha256, alias_sha256, core)


def story_result_for_query(query, *, root=ROOT, graph_sha256=GRAPH_SHA256, alias_sha256=ALIAS_SHA256):
    if parse_story_query(query) is None:
        return None
    def core(*, _reads):
        return _story_result_for_query(query, root=root, graph_sha256=graph_sha256,
                                       alias_sha256=alias_sha256, _reads=_reads)
    return _call_owned(query, 'natural_language', root, graph_sha256, alias_sha256, core)


def _standalone_context(request, kind, snapshots, aliases):
    sources = dict(snapshots)
    if aliases is not None:
        sources['alias'] = aliases.snapshot
    reads = _context(ROOT, request, kind, {s.path: s.sha256 for s in sources.values()})
    try:
        for source in sources.values():
            reads.bind(source)
        bound_aliases = AliasSnapshot(aliases.snapshot, reads) if aliases is not None else None
        if bound_aliases is not None:
            scope = {'scope_level': request['scope_level'], 'scope_id': request['entry_id']}
            reads.lock_execution(scope, bound_aliases)
        return reads, bound_aliases
    except Exception:
        reads.close()
        raise


def _aggregate_owned(request, title, packets, candidates, unresolved, aliases, snapshots,
                      complete, field_present, type_source):
    reads, aliases = _standalone_context(request, 'aggregate', snapshots, aliases)
    try:
        pending = aggregate_story_cast(request, title=title, packets=packets, candidates=candidates,
            unresolved=unresolved, aliases=aliases, snapshots=snapshots, complete=complete,
            field_present=field_present, type_source=type_source, _reads=reads)
        reads.seal()
        return pending
    finally:
        reads.close()


def _validate_owned(result, snapshots):
    identity = result['alias_snapshot']
    aliases = AliasSnapshot(snapshots[identity['path'] + '@' + identity['sha256']]) if identity else None
    reads, _ = _standalone_context(result['request'], 'validate', snapshots, aliases)
    try:
        pending = validate_story_result(result, snapshots, _reads=reads)
        reads.seal()
        return pending
    finally:
        reads.close()

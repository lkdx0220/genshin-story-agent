"""Snapshot identities, result invariants and deterministic presentation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

DENOMINATORS = ('total_in_source', 'with_field', 'missing_field', 'considered',
                'with_authoritative_data', 'unresolved', 'matched')
STATUSES = frozenset({'complete', 'zero', 'partial', 'missing', 'ambiguous', 'unsupported', 'error'})
NUMERIC = frozenset({'complete', 'zero', 'partial'})
VERSIONS = {'evidence': 'evidence_contract_v1.1', 'count_policy': 'count-policy-draft-v1.1',
            'serialization': 'sorted-json-utf8-lf-v1'}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


def json_pointer(*parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def resolve_pointer(value, pointer):
    return _resolve_pointer(value, pointer)


def _resolve_pointer(value, pointer, *, arrays=(list,), objects=(dict,)):
    require(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/')), 'pointer_absolute')
    if pointer == '':
        return value
    for part in pointer.split('/')[1:]:
        require(re.search(r'~(?![01])', part) is None, 'pointer_escape')
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, arrays):
            require(re.fullmatch(r'0|[1-9][0-9]*', key) is not None, 'pointer_array_index')
            value = value[int(key)]
        else:
            require(isinstance(value, objects), 'pointer_container')
            value = value[key]
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate_json_key:' + key)
        value[key] = item
    return value


@dataclass(frozen=True)
class Snapshot:
    path: str
    sha256: str
    raw: bytes

    @classmethod
    def read(cls, root, relative, trusted_sha256):
        rel = PurePosixPath(relative)
        require(not rel.is_absolute() and '..' not in rel.parts and '\\' not in relative
                and ':' not in relative, 'source_path')
        require(re.fullmatch('[0-9a-f]{64}', trusted_sha256 or '') is not None, 'source_expected_sha')
        root = Path(root).resolve()
        path = root.joinpath(*rel.parts).resolve()
        require(path.is_relative_to(root), 'source_escape')
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        require(actual == trusted_sha256, 'source_sha_mismatch:' + relative)
        snapshot = cls(relative, actual, raw)
        snapshot.data()
        return snapshot

    def data(self):
        return json.loads(self.raw.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: require(False, 'nonfinite_json'))

    def identity(self):
        require(hashlib.sha256(self.raw).hexdigest() == self.sha256, 'snapshot_identity')
        return {'source_id': self.path + '@' + self.sha256, 'path': self.path, 'sha256': self.sha256}


def snapshot_identity(snapshot, _reads=None):
    return snapshot.identity() if _reads is None else _reads.identity(snapshot)


def snapshot_value(snapshot, pointer='', _reads=None):
    return (resolve_pointer(snapshot.data(), pointer) if _reads is None
            else _reads.copy_at(snapshot, pointer))


def evidence_location(snapshot, pointer, cleaned_span=None, *, _reads=None):
    if _reads is None:
        resolve_pointer(snapshot.data(), pointer)
    else:
        _reads.check_pointer(snapshot, pointer)
    return {'source_id': snapshot_identity(snapshot, _reads)['source_id'], 'json_pointer': pointer,
            'cleaned_span': deepcopy(cleaned_span)}


def new_result(request, *, status='missing', gaps=()):
    tool = request.get('tool', 'count_catalog')
    return {'contract_version': 'count_result_v1.1', 'tool': tool, 'request': deepcopy(request),
            'status': status, 'count': None, 'count_basis': 'not_available', 'unit': '品种',
            'normalized_scope': {'scope_level': 'catalog', 'scope_id': request.get('catalog', 'specialty'),
                'identity_basis': 'stable_id', 'population_basis': '本地固定快照中的区域特产品种',
                'region_mode': 'exact_source_value', 'normalized_members': []},
            'denominator': dict.fromkeys(DENOMINATORS), 'members': [], 'evidence_ids': [],
            'evidence': [], 'source_snapshots': [], 'alias_snapshot': None, 'unsupported_reason': None,
            'ambiguities': [], 'gaps': list(gaps), 'conflicts': [], 'answer_text_hint': '',
            'render_policy': 'direct_verbatim', 'versions': dict(VERSIONS)}


def render_result(result):
    if result.get('tool') == 'count_story_cast' and result.get('contract_version') == 'count_result_v1.3':
        from .story_cast import render_story_result
        return render_story_result(result)
    status = result['status']
    region = result['request'].get('filters', {}).get('region') or '全部地区'
    # Public spelling only; request/evidence retain the exact source classification.
    region = {'挪德卡莱': '挪得卡莱'}.get(region, region)
    normalized = result['normalized_scope']
    label = region if normalized['scope_level'] == 'catalog' else normalized['scope_level'] + ':' + normalized['scope_id']
    scope = f'本库固定快照（{label}），非游戏全量'
    if status in NUMERIC:
        label = '已确认下限' if status == 'partial' else '收录'
        names = '、'.join(m['display_name'] for m in result['members']) or '无已确认成员'
        d = result['denominator']
        return (f'{scope}：{label}{result["count"]}{result["unit"]}。\n{names}\n'
                f'分母：源记录{d["total_in_source"]}，有分类{d["with_field"]}，'
                f'缺分类{d["missing_field"]}，检查{d["considered"]}，'
                f'权威字段{d["with_authoritative_data"]}，未决{d["unresolved"]}，匹配{d["matched"]}。\n'
                + _render_sources(result) + _render_gaps(result))
    labels = {'missing': '缺少统计条件', 'ambiguous': '存在歧义，不能给出有效数字',
              'error': '统计源或校验失败，不能给出有效数字', 'unsupported': '首批不支持该统计'}
    return scope + '：' + labels[status] + '。' + _render_gaps(result)


def _render_sources(result):
    return '依据：知识库收录的分类资料。' if result['source_snapshots'] else ''


def _render_gaps(result):
    # Diagnostic payloads stay intact internally. Never interpolate raw IDs,
    # pointers, exceptions or caller-provided reasons into a public answer.
    public = {
        'error': '统计资料暂不可用，请稍后重试。',
        'missing': '请明确地区，或明确选择本库全部地区。',
        'ambiguous': '资料或地区选择存在歧义，请指定单一地区；资料冲突需核实后再统计。',
        'unsupported': '本入口仅支持本库全部地区或蒙德、璃月、稻妻、须弥、枫丹、纳塔、挪得卡莱、至冬的区域特产；暂不统计子地区及复杂限定。',
        'partial': '仍有未确认资料，已确认数量仅为下限。',
    }
    return '\n说明：' + public[result['status']] if result['status'] in public else ''


def _unique_rows(rows, key):
    values = [r[key] for r in rows]
    require(len(set(values)) == len(values), 'duplicate_' + key)
    require(values == sorted(values), 'unsorted_' + key)
    return {r[key]: r for r in rows}


def _validate_numbers(result):
    d = result['denominator']
    require(set(d) == set(DENOMINATORS), 'denominator_keys')
    require(all(v is None or (type(v) is int and v >= 0) for v in d.values()), 'denominator_values')
    count = result['count']
    require(d['matched'] == count, 'matched_count')
    if result['status'] in NUMERIC:
        require(type(count) is int and count >= 0 and count == len(result['members']), 'count_members')
    else:
        require(count is None and not result['members'] and not result['evidence'], 'nonnumeric_members')
    if all(d[k] is not None for k in ('total_in_source', 'with_field', 'missing_field')):
        require(d['with_field'] + d['missing_field'] == d['total_in_source'], 'field_denominator')
    if d['considered'] is not None:
        require(d['total_in_source'] is None or d['considered'] <= d['total_in_source'], 'considered_total')
        require(all(d[k] is None or d[k] <= d['considered'] for k in ('with_authoritative_data', 'unresolved')),
                'subdenominator')


def _validate_status(result):
    status, count, d = result['status'], result['count'], result['denominator']
    require(status in STATUSES, 'status')
    if status in {'complete', 'zero'}:
        require(not result['conflicts'] and not result['gaps'] and d['unresolved'] == 0
                and d['missing_field'] in (0, None), 'incomplete_exact')
        require(result['count_basis'] == 'exact_in_snapshot', 'exact_basis')
        require((status == 'zero' and count == 0) or (status == 'complete' and count > 0), 'zero_complete')
    elif status == 'partial':
        require(result['count_basis'] == 'lower_bound' and result['gaps'], 'partial_diagnostic')
    else:
        require(result['count_basis'] == 'not_available', 'unavailable_basis')
    if status in {'error', 'missing'}:
        require(bool(result['gaps']), 'missing_diagnostic')
    if status == 'ambiguous':
        require(bool(result['ambiguities']), 'ambiguity_diagnostic')
    if status == 'unsupported':
        require(bool(result['unsupported_reason']), 'unsupported_diagnostic')


def validate_verdict(verdict):
    require(verdict['evidence_contract_version'] == 'evidence_contract_v1.1', 'evidence_version')
    require(verdict['decision'] in {'include', 'exclude', 'unknown', 'conflict'}, 'decision')
    require(verdict['evidence_kind'] in {'metadata', 'speaker_lines', 'mention_only',
                                        'activity_inherited', 'missing', 'conflicting'}, 'evidence_kind')
    c = verdict['coverage']
    require(set(c) == {'scope', 'cast_source', 'complete', 'field_parse_ok'} and c['scope'] == verdict['scope'], 'coverage_scope')
    require(type(c['complete']) is bool and type(c['field_parse_ok']) is bool, 'coverage_boolean')
    if verdict['decision'] in {'include', 'exclude', 'conflict'}:
        require(bool(verdict['evidence_location']), 'verdict_location')
    if verdict['decision'] == 'include':
        require(verdict['evidence_kind'] in {'metadata', 'speaker_lines'}, 'include_kind')
    if verdict['decision'] == 'conflict':
        require(verdict['evidence_kind'] == 'conflicting', 'conflict_kind')
    if verdict['decision'] == 'exclude':
        require(c['complete'] and c['field_parse_ok'] and 'metadata' in (c['cast_source'] or '').split('+'), 'exclude_coverage')
        require(verdict['evidence_kind'] == 'metadata', 'exclude_kind')
    if verdict['evidence_kind'] != 'metadata':
        require(not c['complete'], 'nonmetadata_complete')


def _validate_links(result, snapshots, *, _reads=None):
    members = _unique_rows(result['members'], 'member_id')
    evidence = _unique_rows(result['evidence'], 'evidence_id')
    sources = _unique_rows(result['source_snapshots'], 'source_id')
    for source in sources.values():
        _validate_source(source)
    require(result['evidence_ids'] == sorted(set(result['evidence_ids'])), 'evidence_id_set')
    used = set()
    for member in members.values():
        require(bool(member['evidence_ids']), 'member_without_evidence')
        for eid in member['evidence_ids']:
            require(eid in evidence and evidence[eid]['subject_id'] == member['member_id'], 'member_evidence')
            used.add(eid)
    require(used == set(evidence) == set(result['evidence_ids']), 'evidence_union')
    for row in evidence.values():
        require(bool(row['locations']), 'evidence_location')
        for verdict in row['member_checks']:
            validate_verdict(verdict)
            require(verdict['decision'] == 'include', 'counted_noninclude')
            _validate_locations(verdict['evidence_location'], sources, snapshots, _reads=_reads)
        _validate_locations(row['locations'], sources, snapshots, _reads=_reads)
    _validate_conflicts(result, members, sources, snapshots, _reads=_reads)


def _validate_source(source):
    require(set(source) == {'source_id', 'path', 'sha256'}, 'source_keys')
    path = PurePosixPath(source['path'])
    require(not path.is_absolute() and '..' not in path.parts and ':' not in source['path']
            and '\\' not in source['path'], 'source_relative_path')
    require(re.fullmatch('[0-9a-f]{64}', source['sha256']) is not None, 'source_sha')
    require(source['source_id'] == source['path'] + '@' + source['sha256'], 'source_id')


def _validate_locations(locations, sources, snapshots, *, _reads=None):
    for loc in locations:
        require(loc['source_id'] in sources, 'location_source')
        require(re.fullmatch(r'(?:/(?:[^~/]|~[01])*)*', loc['json_pointer']) is not None, 'pointer_syntax')
        span = loc['cleaned_span']
        if span is not None:
            require(type(span['start']) is int and type(span['end']) is int
                    and 0 <= span['start'] <= span['end'] and bool(span['normalizer_version']), 'cleaned_span')
        if snapshots is not None:
            require(loc['source_id'] in snapshots, 'snapshot_unavailable')
            snap = snapshots[loc['source_id']]
            require(snapshot_identity(snap, _reads) == sources[loc['source_id']], 'source_identity')
            if _reads is None:
                resolve_pointer(snap.data(), loc['json_pointer'])
            else:
                _reads.check_pointer(snap, loc['json_pointer'])


def _validate_conflicts(result, members, sources, snapshots, *, _reads=None):
    seen = set()
    for conflict in result['conflicts']:
        sid = conflict['subject_id']
        require(sid not in members and sid not in seen, 'conflict_member_overlap')
        seen.add(sid)
        require(conflict['scope']['scope_level'] in {'activity', 'act', 'task'}, 'conflict_scope')
        require(any(v['decision'] == 'conflict' for v in conflict['member_checks']), 'conflict_verdict')
        for verdict in conflict['member_checks']:
            validate_verdict(verdict)
            require(verdict['scope'] == conflict['scope'], 'conflict_verdict_scope')
            _validate_locations(verdict['evidence_location'], sources, snapshots, _reads=_reads)
    if seen:
        require(result['status'] == 'partial' and result['denominator']['unresolved'] >= 1, 'conflict_partial')


def validate_result(result, snapshots=None):
    if result.get('tool') == 'count_story_cast' and result.get('contract_version') == 'count_result_v1.3':
        from .story_cast import validate_story_result
        return validate_story_result(result, snapshots)
    require(set(result) == set(new_result(result['request'])), 'result_keys')
    require(result['contract_version'] == 'count_result_v1.1' and result['versions'] == VERSIONS, 'version')
    require(result['tool'] == result['request']['tool'], 'tool_request')
    require(result['render_policy'] == 'direct_verbatim', 'render_policy')
    _validate_numbers(result)
    _validate_status(result)
    _validate_links(result, snapshots)
    require(result['answer_text_hint'] == render_result(result), 'noncanonical_render')
    return result


def assemble_result(result, *, snapshots=None):
    """Assemble already selected evidence; this does not traverse story entries."""
    result = deepcopy(result)
    conflict_ids = {c['subject_id'] for c in result['conflicts']}
    result['members'] = sorted((m for m in result['members'] if m['member_id'] not in conflict_ids), key=lambda m: m['member_id'])
    result['evidence'] = sorted((e for e in result['evidence'] if e['subject_id'] not in conflict_ids), key=lambda e: e['evidence_id'])
    result['source_snapshots'].sort(key=lambda s: s['source_id'])
    result['conflicts'].sort(key=lambda c: (c['subject_id'], c['scope']['scope_id']))
    result['evidence_ids'] = sorted(e['evidence_id'] for e in result['evidence'])
    for member in result['members']:
        member['evidence_ids'] = sorted(set(member['evidence_ids']))
    if conflict_ids:
        result.update(status='partial', count_basis='lower_bound')
        result['gaps'] = sorted(set(result['gaps'] + ['冲突主体未计入，已确认数量仅为下限']))
    if result['status'] in NUMERIC:
        result['count'] = len(result['members'])
        result['denominator']['matched'] = result['count']
    result['answer_text_hint'] = render_result(result)
    return validate_result(result, snapshots)

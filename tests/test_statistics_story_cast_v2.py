"""scope=v2: preserved literal cases plus FO original-primitive aggregation cases."""
from copy import deepcopy
import hashlib
import json

import pytest

from app.statistics.contracts import canonical_bytes, validate_result
from app.statistics.story_cast import count_story_cast, story_result_for_query


@pytest.fixture
def source(tmp_path):
    people = ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛', '壬', '癸', '子', '丑']
    root = tmp_path / 'source'
    (root / 'kb_vectors').mkdir(parents=True)
    aliases = {'version': 'test-v1', 'canonical_to_variants': {n: [] for n in people},
               'variant_to_canonical': {'小甲': '甲'}, 'conflict_notes': {'同名': {'candidates': ['甲', '乙']}}}
    ap = root / 'kb_vectors/entity_alias_map.json'
    ap.write_bytes(canonical_bytes(aliases))
    base = {'entry_id': 'story-1', 'title': '测试活动', 'entry_type': 'activity',
            'cast': ['甲', '乙'], 'cast_source': 'metadata', 'cast_scope': 'activity',
            'story_text': '', 'full_text': '', 'metadata': {'出场人物': ['甲', '乙']},
            'coverage': {'scope': {'scope_level': 'activity', 'scope_id': 'story-1'},
                         'field_parse_ok': True, 'complete': True},
            'story_placeholder': False, 'cast_absent': False, 'aliases': []}
    request = {'tool': 'count_story_cast', 'entry_id': 'story-1', 'scope_level': 'activity'}

    def run(*, entry=None, extra=(), query=None, request_override=None):
        entry = deepcopy(base if entry is None else entry)
        graph = {'entries': [entry, *deepcopy(list(extra))]}
        gp = root / 'kb_vectors/wiki_entry_graph.json'
        gp.write_bytes(canonical_bytes(graph))
        kw = dict(root=root, graph_sha256=hashlib.sha256(gp.read_bytes()).hexdigest(),
                  alias_sha256=hashlib.sha256(ap.read_bytes()).hexdigest())
        if query is not None:
            return story_result_for_query(query, **kw)
        return count_story_cast(request if request_override is None else request_override, **kw)
    return base, request, run, people


def ids(result):
    return sorted(row['member_id'] for row in result['members'])


def test_c01_count_list_set(source):
    _, _, run, _ = source
    results = [run(query=q) for q in ['《测试活动》有多少角色参与', '《测试活动》有哪些人物出场', '《测试活动》有多少人物参与，分别是谁']]
    assert all(r['status'] == 'complete' and r['count'] == 2 for r in results)
    assert all(ids(r) == ['乙', '甲'] for r in results)
    assert all('含具名NPC' in r['answer_text_hint'] and '测试活动' in r['answer_text_hint'] for r in results)


def test_c02_scope_and_population(source):
    base, request, run, _ = source
    assert run(request_override=dict(request, scope_level='act'))['status'] == 'unsupported'
    for q in ['《测试活动》第二幕里只算璃月可玩角色', '《测试活动》只算可玩角色有多少人参与']:
        assert run(query=q) is None
    base['cast'] = ['甲', '骑士团众人'];base['metadata']['出场人物'] = list(base['cast'])
    r = run(entry=base)
    assert r['status'] == 'missing' and r['count'] is None
    assert r['cast_uncertainty']['unresolved_name_count'] == 1


def test_c03_duplicate_titles(source):
    base, _, run, _ = source
    second = dict(base, entry_id='story-2')
    r = run(extra=[second], query='《测试活动》有多少角色参与')
    assert r['status'] == 'ambiguous' and r['count'] is None and not r['members']


def test_c05_population_no_topk(source):
    base, _, run, people = source
    base['cast'] = people; base['metadata']['出场人物'] = people
    r = run(entry=base)
    assert r['count'] == 12 and ids(r) == sorted(people)
    assert all(n in r['answer_text_hint'] for n in people)


def test_c06_inherited_population(source):
    base, _, run, _ = source
    base.update(cast_source='activity_inherited')
    r = run(entry=base)
    assert r['status'] == 'missing' and r['count'] is None


def test_c07_alias_identity(source):
    base, _, run, _ = source
    base['cast'] = ['甲', '小甲'];base['metadata']['出场人物'] = list(base['cast'])
    assert run(entry=base)['count'] == 1
    base['cast'] = ['同名'];base['metadata']['出场人物'] = ['同名']
    r = run(entry=base)
    assert r['count'] is None and r['cast_uncertainty']['unresolved_name_count'] == 1


def test_c08_unregistered_member(source):
    base, _, run, _ = source
    base.update(cast=['甲', '新NPC'], story_text='甲：你好。')
    base['metadata']['出场人物'] = list(base['cast'])
    r = run(entry=base)
    assert r['status'] == 'partial' and ids(r) == ['甲']
    assert r['cast_uncertainty']['unresolved_name_count'] == 1


@pytest.mark.parametrize('text,expected', [('甲：台词。', ['甲']), ('有人提到甲。', []), ('>甲：引用。', []), ('```\n甲：代码引用\n```', []), ('“\n甲：引文。\n”', []), ('[前情提要]\n甲：上回。', []), ('[任务概述]\n甲：概述。', [])])
def test_c09_evidence_levels(source, text, expected):
    base, _, run, _ = source
    base.update(cast=[], cast_source='speaker_lines', story_text=text);base.pop('coverage')
    r = run(entry=base)
    assert ids(r) == expected
    assert r['status'] == ('partial' if expected else 'missing')


def test_c10_conflict_priority(source):
    base, _, run, _ = source
    base['story_text'] = '丙：我在这里。'
    r = run(entry=base)
    assert r['status'] == 'partial' and ids(r) == ['乙', '甲']
    assert [c['subject_id'] for c in r['conflicts']] == ['丙']
    assert r['cast_uncertainty']['conflict_member_count'] == 1


def test_c11_lower_bound(source):
    base, _, run, _ = source
    base.update(cast=[], cast_source='speaker_lines', story_text='甲：一。\n乙：二。\n丙：三。');base.pop('coverage')
    r = run(entry=base)
    assert r['count'] == 3 and r['status'] == 'partial'
    assert r['cast_uncertainty']['unenumerated_member_count'] is None
    assert '至少3人' in r['answer_text_hint']


def test_c12_zero_missing_conflict(source):
    base, _, run, _ = source
    base['cast'] = [];base['metadata']['出场人物'] = []
    # Original member_evidence cannot prove a complete empty metadata field.
    assert run(entry=base)['status'] == 'missing'
    base['story_text'] = '丙：我在这里。'
    r = run(entry=base)
    assert r['status'] == 'partial' and r['count'] == 1
    # A malformed source is error, never a fabricated zero.
    base['cast'] = 123
    assert run(entry=base)['status'] == 'error'


def test_c13_denominator_units(source):
    base, _, run, people = source
    base['cast'] = people;base['metadata']['出场人物'] = people
    r = run(entry=base)
    assert r['denominator']['considered'] == 1 and r['denominator']['matched'] == 12
    assert r['count'] == len(r['members']) == 12


@pytest.mark.parametrize('mutation', ['count', 'denominator', 'evidence', 'overlap', 'unknown'])
def test_c14_result_invariants(source, mutation):
    _, _, run, _ = source
    r = deepcopy(run())
    if mutation == 'count':r['count'] = 999
    if mutation == 'denominator':r['denominator'].pop('unresolved')
    if mutation == 'evidence':r['members'][0]['evidence_ids'] = ['dangling']
    if mutation == 'overlap':r['cast_uncertainty']['conflict_member_count'] = 1
    if mutation == 'unknown':r['cast_uncertainty']['unknown_member_count'] = -1
    with pytest.raises((ValueError, KeyError)):
        validate_result(r)


@pytest.mark.parametrize('kind', ['complete', 'partial', 'missing', 'ambiguous', 'unsupported', 'error'])
def test_c17_public_domain(source, kind):
    base, request, run, _ = source
    if kind == 'partial':base.pop('coverage')
    if kind == 'missing':base.update(cast=[], story_text='');base.pop('coverage')
    if kind == 'error':base['cast'] = 99
    extra=[dict(base, entry_id='story-2')] if kind == 'ambiguous' else []
    if kind == 'ambiguous':r=run(entry=base, extra=extra, query='《测试活动》有多少角色参与')
    else:r=run(entry=base, request_override=dict(request,scope_level='act') if kind=='unsupported' else request)
    assert r['status'] == kind
    text=r['answer_text_hint']
    assert all(t not in text for t in ['story-1', 'kb_vectors', '.json', 'source_id', '区域特产', '请明确地区'])
    assert all(s['sha256'] not in text for s in r['source_snapshots'])


def test_c19_source_adapter_proof(source):
    base, _, run, _ = source
    base.pop('metadata');base.pop('coverage')
    r=run(entry=base)
    assert r['status']=='partial' and r['count']==2
    assert r['cast_uncertainty']['unenumerated_member_count'] is None
    base['cast_scope']='task'
    assert run(entry=base)['status']=='missing'


def test_c21_unknown_member_sets(source):
    base, _, run, _ = source
    base.update(cast=['甲'],cast_source='activity_inherited',story_text='乙：本段。\n丙：本段。')
    # Wrong-scope source never turns its apparent speakers into confirmed people.
    r=run(entry=base)
    assert r['status']=='missing' and r['count'] is None
    assert r['cast_uncertainty']['unknown_member_count']==3


def test_c22_unenumerated_and_names(source):
    base, _, run, _ = source
    base.update(cast=['新NPC','新NPC','另一人','群体'],cast_source='speaker_lines',story_text='甲：你好。')
    r=run(entry=base)
    assert r['count']==1 and r['cast_uncertainty']['unresolved_name_count']==3
    assert r['cast_uncertainty']['unenumerated_member_count'] is None
    assert '3个名称待核' in r['answer_text_hint']


def test_c23_authoritative_type_breakdown(source):
    _, _, run, _ = source
    r=run(extra=[{'entry_id':'person-a','title':'甲','entry_type':'character'}, {'entry_id':'person-b','title':'乙','entry_type':'npc'}])
    assert r['cast_breakdown']=={'playable':1,'npc':1}


def test_c24_missing_type_no_guess(source):
    _, _, run, _ = source
    assert run()['cast_breakdown'] is None
    r=run(extra=[{'entry_id':'person-a','title':'甲','entry_type':'character'}])
    assert r['count']==2 and r['cast_breakdown'] is None


@pytest.fixture
def original_fm():
    """Reconstruct original-helper inputs from accepted serialized raw bytes."""
    import os
    from pathlib import Path

    from app.statistics.contracts import Snapshot
    from app.statistics.evidence import AliasSnapshot, member_evidence

    batch = Path(__file__).resolve().parents[3]
    source = batch.parent / 'story-cast-fm-spec-20261025-01/run'
    data = json.loads((source / ('member-input-r02-' + os.environ['LLM_WIKI_MODE'] + '.json')).read_bytes())
    alias = data['alias_source']
    aliases = AliasSnapshot(Snapshot(alias['path'], alias['sha256'], bytes.fromhex(data['alias_source_raw_hex'])))
    snapshots, packets = {}, {}
    for row in data['rows']:
        identity = row['source']
        snapshot = Snapshot(identity['path'], identity['sha256'], bytes.fromhex(row['source_raw_hex']))
        snapshots[identity['source_id']] = snapshot
        actual = member_evidence(row['entry'], row['canonical_member'], scope=row['scope'],
                                 source=snapshot, entry_pointer=row['entry_pointer'], aliases=aliases)
        assert canonical_bytes(actual) == canonical_bytes(row['verdict'])
        packets[row['canonical_member']] = [{'verdict': actual, 'source_id': identity['source_id'],
                                             'entry_pointer': row['entry_pointer']}]
    request = {'tool': 'count_story_cast', 'entry_id': data['rows'][0]['scope']['scope_id'], 'scope_level': 'task'}
    return request, packets, snapshots, aliases


def aggregate_original(original_fm, selected=None):
    from app.statistics.story_cast import aggregate_story_cast

    request, packets, snapshots, aliases = original_fm
    if selected is not None:
        packets = {name: packets[name] for name in selected}
    return aggregate_story_cast(request, title='固定合成剧情', packets=packets,
        snapshots=snapshots, aliases=aliases, candidates={n: [] for n in packets},
        unresolved=[], complete=False)


def test_c21_original_5_2_1_1_shared_aggregate(original_fm, tmp_path):
    result = aggregate_original(original_fm)
    assert result['contract_version'] == 'count_result_v1.3'
    assert result['status'] == 'partial' and result['count'] == result['denominator']['matched'] == 5
    assert ids(result) == sorted(['甲', '乙', '丙', '丁', '戊'])
    assert result['cast_uncertainty'] == {'unknown_member_count': 2, 'conflict_member_count': 1,
                                         'unresolved_name_count': 0, 'unenumerated_member_count': None}
    assert [c['subject_id'] for c in result['conflicts']] == ['辛']
    assert '壬' not in ids(result) and '2名已识别人物' in result['answer_text_hint']
    assert set(result['denominator']) == {'total_in_source', 'with_field', 'missing_field', 'considered',
                                          'with_authoritative_data', 'unresolved', 'matched'}
    assert result['denominator']['considered'] == result['denominator']['unresolved'] == 1
    (tmp_path / 'c21-actual-aggregate.json').write_bytes(canonical_bytes(result))


def test_c12_original_conflict_only_incomplete_batch(original_fm, tmp_path):
    result = aggregate_original(original_fm, ['辛'])
    assert result['status'] == 'partial' and result['count'] == 0 and not result['members']
    assert result['count_basis'] == 'lower_bound' and len(result['conflicts']) == 1
    assert '冲突人物未计入' in result['answer_text_hint'] and '不能据此认定无人出场' in result['answer_text_hint']
    assert result['cast_uncertainty']['unenumerated_member_count'] is None
    (tmp_path / 'c12-incomplete-batch.json').write_bytes(canonical_bytes(result))


def test_c12_partial_zero_without_conflict_rejected(original_fm):
    from app.statistics.story_cast import render_story_result, validate_story_result

    result = deepcopy(aggregate_original(original_fm, ['辛']))
    result['conflicts'] = []
    result['cast_uncertainty']['conflict_member_count'] = 0
    result['gaps'] = ['资料未闭合']
    result['answer_text_hint'] = render_story_result(result)
    with pytest.raises(ValueError, match='cast_partial_zero_without_conflict'):
        validate_story_result(result)


@pytest.mark.parametrize('mutation', ['scope', 'identity', 'pointer', 'verdict', 'source_bytes'])
def test_c21_shared_core_rejects_unbound_packets(original_fm, mutation):
    from app.statistics.contracts import Snapshot
    from app.statistics.story_cast import merge_member_evidence

    request, original, original_snapshots, aliases = original_fm
    packets, snapshots = deepcopy(original), dict(original_snapshots)
    packet = packets['甲'][0]
    if mutation == 'scope':
        packet['verdict']['scope']['scope_id'] = 'another'
    if mutation == 'identity':
        packet['verdict']['canonical_member'] = '乙'
    if mutation == 'pointer':
        packet['entry_pointer'] = '/not-there'
    if mutation == 'verdict':
        packet['verdict']['decision'] = 'unknown'
    if mutation == 'source_bytes':
        old = snapshots[packet['source_id']]
        snapshots[packet['source_id']] = Snapshot(old.path, old.sha256, old.raw + b' ')
    with pytest.raises((ValueError, KeyError)):
        merge_member_evidence(packets, scope={'scope_level': 'task', 'scope_id': request['entry_id']},
                              snapshots=snapshots, aliases=aliases)


def test_c12_public_retains_auxiliary_not_fake_zero(source):
    base, _, run, _ = source
    base['cast'] = ['甲']
    base['metadata']['出场人物'] = ['甲']
    base['story_text'] = '乙：本范围有效台词。'
    result = run(entry=base)
    assert result['status'] == 'partial' and result['count'] == 1 and ids(result) == ['甲']
    assert [c['subject_id'] for c in result['conflicts']] == ['乙']


def test_c21_public_count_and_list_use_one_merge(source, monkeypatch):
    import app.statistics.story_cast as module

    original, calls = module.merge_member_evidence, []
    def observed(*args, **kwargs):
        calls.append(kwargs['scope'])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'merge_member_evidence', observed)
    _, _, run, _ = source
    one = run(query='《测试活动》有多少角色参与')
    two = run(query='《测试活动》有哪些人物出场')
    assert ids(one) == ids(two) == ['乙', '甲'] and len(calls) >= 2


@pytest.mark.parametrize('stage', ['source_absent', 'source_sha', 'source_json', 'source_structure',
                                    'alias_absent', 'alias_sha', 'alias_json', 'alias_structure'])
def test_c14_v13_error_matrix(tmp_path, stage):
    from app.statistics.story_cast import count_story_cast

    root = tmp_path / 'bad-source'
    (root / 'kb_vectors').mkdir(parents=True)
    gp, ap = root / 'kb_vectors/wiki_entry_graph.json', root / 'kb_vectors/entity_alias_map.json'
    gp.write_bytes(canonical_bytes({'entries': [{'entry_id': 'one', 'title': '剧情', 'entry_type': 'task'}]}))
    ap.write_bytes(canonical_bytes({'version': 'x', 'canonical_to_variants': {}, 'variant_to_canonical': {}, 'conflict_notes': {}}))
    if stage.endswith('json'):
        (gp if stage.startswith('source') else ap).write_bytes(b'{bad')
    if stage.endswith('structure'):
        (gp if stage.startswith('source') else ap).write_bytes(b'{}')
    gsha, asha = hashlib.sha256(gp.read_bytes()).hexdigest(), hashlib.sha256(ap.read_bytes()).hexdigest()
    if stage == 'source_absent': gp.unlink()
    if stage == 'alias_absent': ap.unlink()
    if stage == 'source_sha': gsha = '0' * 64
    if stage == 'alias_sha': asha = '0' * 64
    result = count_story_cast({'tool': 'count_story_cast', 'entry_id': 'one', 'scope_level': 'task'},
                              root=root, graph_sha256=gsha, alias_sha256=asha)
    expected = {'source_absent': 'source_unavailable', 'source_sha': 'source_snapshot_unverifiable',
                'source_json': 'source_data_invalid', 'source_structure': 'source_data_invalid'}
    assert result['status'] == 'error' and result['count'] is None
    assert result['error_code'] == expected.get(stage, 'alias_snapshot_unverifiable')
    assert result['alias_snapshot'] is None
    assert all(v is None for v in result['denominator'].values())
    assert all(v is None for v in result['cast_uncertainty'].values())
    assert not result['evidence'] and not result['conflicts']


def test_c14_no_unnecessary_alias_access(source, monkeypatch):
    import app.statistics.story_cast as module

    _, _, run, _ = source
    def denied(*args, **kwargs):
        raise AssertionError('unnecessary_alias_read')
    monkeypatch.setattr(module, '_load_aliases', denied)
    assert run(query='未知剧情有多少角色参与')['status'] == 'missing'
    assert run(query='这个活动有多少角色参与')['status'] == 'missing'


def test_c14_all_actual_results_match_reviewed_schema(source):
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / 'reviewed-change/count_result_v1.3-story-cast.schema.json').read_bytes())
    base, request, run, _ = source
    results = [run(), run(query='未知剧情有多少角色参与'), run(request_override=dict(request, scope_level='act'))]
    base.pop('coverage')
    results.append(run(entry=base))
    base['cast'] = 99
    results.append(run(entry=base))
    for result in results:
        jsonschema.Draft202012Validator(schema).validate(result)


def test_c06_complete_flag_without_source_proof_rejected(original_fm):
    from app.statistics.story_cast import aggregate_story_cast

    request, packets, snapshots, aliases = original_fm
    with pytest.raises(ValueError, match='cast_complete_source'):
        aggregate_story_cast(request, title='固定合成剧情', packets=packets,
            snapshots=snapshots, aliases=aliases, candidates={n: [] for n in packets},
            unresolved=[], complete=True)


def test_c06_complete_collection_cannot_drop_member(source, monkeypatch):
    import app.statistics.story_cast as module

    original = module.aggregate_story_cast
    def dropped(request, **kwargs):
        kwargs['packets'] = dict(kwargs['packets'])
        kwargs['packets'].pop('甲')
        return original(request, **kwargs)
    monkeypatch.setattr(module, 'aggregate_story_cast', dropped)
    _, _, run, _ = source
    result = run()
    assert result['status'] == 'error' and result['error_code'] == 'internal_validation_failed'
    assert result['count'] is None


def test_c14_alias_array_is_error(tmp_path):
    root = tmp_path / 'array-alias'
    (root / 'kb_vectors').mkdir(parents=True)
    graph = canonical_bytes({'entries': [{'entry_id': 'one', 'entry_type': 'task'}]})
    aliases = b'[]'
    (root / 'kb_vectors/wiki_entry_graph.json').write_bytes(graph)
    (root / 'kb_vectors/entity_alias_map.json').write_bytes(aliases)
    result = count_story_cast({'tool': 'count_story_cast', 'entry_id': 'one', 'scope_level': 'task'},
        root=root, graph_sha256=hashlib.sha256(graph).hexdigest(), alias_sha256=hashlib.sha256(aliases).hexdigest())
    assert result['status'] == 'error' and result['error_code'] == 'alias_snapshot_unverifiable'
    assert result['alias_snapshot'] is None and result['count'] is None


def test_c14_conflict_verdict_scope_must_match(original_fm):
    from app.statistics.story_cast import render_story_result, validate_story_result

    result = deepcopy(aggregate_original(original_fm, ['辛']))
    verdict = result['conflicts'][0]['member_checks'][0]
    verdict['scope'] = dict(verdict['scope'], scope_id='wrong')
    result['answer_text_hint'] = render_story_result(result)
    with pytest.raises(ValueError, match='cast_conflict_verdict_scope'):
        validate_story_result(result)

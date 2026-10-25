"""Integration negatives use actual evidence/replay, never hand-filled verdicts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.statistics import contracts, evidence, story_cast as story
from app.statistics.snapshot_reads import SnapshotReadContext
from test_statistics_snapshot_reads import context, snap


@pytest.fixture
def real_inputs(tmp_path):
    root = tmp_path / 'source'
    (root / 'kb_vectors').mkdir(parents=True)
    entry = {'entry_id': 'e', 'entry_type': 'activity', 'title': '固定活动', 'cast': ['甲'],
             'cast_source': 'metadata', 'cast_scope': 'activity', 'story_text': '', 'metadata': {'出场人物': ['甲']}}
    alias = {'version': 'test-v1', 'canonical_to_variants': {'甲': [], '乙': []},
             'variant_to_canonical': {'小甲': '甲'}, 'conflict_notes': {}}
    types = [{'entry_id': 'p', 'entry_type': 'character', 'title': '甲'}]
    graph = contracts.canonical_bytes({'entries': [entry, *types]})
    aliases = contracts.canonical_bytes(alias)
    (root / 'kb_vectors/wiki_entry_graph.json').write_bytes(graph)
    (root / 'kb_vectors/entity_alias_map.json').write_bytes(aliases)
    kw = {'root': root, 'graph_sha256': hashlib.sha256(graph).hexdigest(),
          'alias_sha256': hashlib.sha256(aliases).hexdigest()}
    return entry, alias, kw


def test_r06_r07_r26_actual_different_members_and_polluted_entry(tmp_path):
    reads = context(tmp_path)
    entry = {'entry_id': 'e', 'entry_type': 'activity', 'cast_scope': 'activity',
             'cast': ['甲'], 'cast_source': 'metadata', 'metadata': {'出场人物': ['甲']}, 'story_text': ''}
    source = snap({'entry': entry})
    aliases = evidence.AliasSnapshot(snap({'version': 'test', 'canonical_to_variants': {'甲': [], '乙': []},
                                         'variant_to_canonical': {}, 'conflict_notes': {}}, 'alias.json'), reads)
    reads.bind(source)
    reads.bind(aliases.snapshot)
    scope = {'scope_level': 'activity', 'scope_id': 'e'}
    reads.lock_execution(scope, aliases)
    first = evidence.member_evidence(entry, '甲', scope=scope, source=source, entry_pointer='/entry', aliases=aliases, _reads=reads)
    assert reads.lookup(source, reads.execution_key())['hit'] is True
    second = evidence.member_evidence(entry, '乙', scope=scope, source=source, entry_pointer='/entry', aliases=aliases, _reads=reads)
    assert first['decision'] == 'include' and second['decision'] == 'unknown'
    bad = deepcopy(entry)
    bad['cast'].append('乙')
    with pytest.raises(ValueError, match='entry_source_mismatch'):
        evidence.member_evidence(bad, '乙', scope=scope, source=source, entry_pointer='/entry', aliases=aliases, _reads=reads)
    reads.seal()
    reads.close()


def test_r14_seal_failure_releases_no_numeric_answer(real_inputs, monkeypatch):
    _, _, kw = real_inputs
    original = SnapshotReadContext.seal
    calls = []
    def failed(self):
        calls.append(self)
        original(self)
        raise ValueError('injected_final_seal_failure')
    monkeypatch.setattr(SnapshotReadContext, 'seal', failed)
    result = story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    assert len(calls) == 1 and calls[0]._state == 'closed'
    assert result['status'] == 'error' and result['count'] is None
    assert not result['members'] and '至少' not in result['answer_text_hint']
    assert result['error_code'] == 'internal_validation_failed'


def test_r23_r28_structure_and_actual_replays(real_inputs, monkeypatch):
    _, _, kw = real_inputs
    parses, members, packets, audits, contexts = [], [], [], [], []
    def trace(obj, name, rows, summary):
        original = getattr(obj, name)
        def run(*args, **kwargs):
            result = original(*args, **kwargs)
            rows.append(summary(args, kwargs, result))
            return result
        monkeypatch.setattr(obj, name, run)
    trace(contracts.Snapshot, 'data', parses, lambda a, k, v: a[0].path)
    trace(story, 'member_evidence', members, lambda a, k, v: (a[1], v['decision']))
    trace(story, '_verify_packet', packets, lambda a, k, v: v['decision'])
    trace(story, '_validate_audit_sources', audits, lambda a, k, v: k['_reads'])
    trace(story, '_context', contexts, lambda a, k, v: v)
    result = story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    assert result['count'] == 1 and result['status'] == 'partial'
    assert parses.count('kb_vectors/wiki_entry_graph.json') == 2
    assert parses.count('kb_vectors/entity_alias_map.json') == 2
    assert len([p for p in parses if p.startswith('derived/')]) == 1
    assert len(members) == 3 and len(packets) == 2 and len(audits) == 1
    assert all(v == ('甲', 'include') for v in members)
    assert len(contexts) == 1 and contexts[0]._state == 'closed' and contexts[0]._bindings == {}


@pytest.mark.parametrize('changed', ['count', 'member', 'evidence', 'audit'])
def test_r06_r19_result_corruption_rejected(real_inputs, changed, monkeypatch):
    _, _, kw = real_inputs
    sources = {}
    original = story._finish
    def capture(result, snapshots=None, **kwargs):
        if snapshots:
            sources.update(snapshots)
        return original(result, snapshots, **kwargs)
    monkeypatch.setattr(story, '_finish', capture)
    result = story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    bad = deepcopy(result)
    if changed == 'count':
        bad['count'] = 99
    elif changed == 'member':
        bad['members'][0]['member_id'] = '乙'
    elif changed == 'evidence':
        bad['evidence'][0]['member_checks'][0]['decision'] = 'unknown'
    else:
        audit = json.loads(bad['gaps'][1][len('cast-audit:'):])
        audit['checks']['甲'][0]['reason'] = 'made up'
        bad['gaps'][1] = 'cast-audit:' + contracts.canonical_bytes(audit).decode().strip()
    with pytest.raises((ValueError, KeyError)):
        story.validate_story_result(bad, sources)


def test_r20_fixture_full_bytes_match_legacy(real_inputs):
    _, _, kw = real_inputs
    result = story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    legacy = Path(__file__).resolve().parents[2] / 'legacy'
    code = ('import sys,json;from app.statistics.story_cast import story_result_for_query;'
            'from app.statistics.contracts import canonical_bytes;'
            'k=json.loads(sys.argv[1]);sys.stdout.buffer.write(canonical_bytes('
            'story_result_for_query("《固定活动》有多少角色参与",**k)))')
    args = dict(kw, root=str(kw['root']))
    child = subprocess.run([sys.executable, '-B', '-c', code, json.dumps(args)], cwd=legacy,
                           capture_output=True, check=True)
    assert child.stdout == contracts.canonical_bytes(result)


def test_r21_alias_and_public_snapshot_default_remain_independent(real_inputs):
    _, _, kw = real_inputs
    aliases = evidence.AliasSnapshot.read(kw['root'], kw['alias_sha256'])
    assert aliases._reads is None
    assert aliases.normalize('小甲') == ('甲', [])
    one = aliases.snapshot.data()
    one['variant_to_canonical']['小甲'] = '乙'
    assert aliases.normalize('小甲') == ('甲', [])
    assert aliases.snapshot.identity()['sha256'] == kw['alias_sha256']


@pytest.mark.parametrize('source_kind', ['graph', 'alias', 'derived'])
@pytest.mark.parametrize('stage', ['bind', 'read', 'seal'])
def test_r14_each_source_tamper_error_mapping(real_inputs, monkeypatch, source_kind, stage):
    _, _, kw = real_inputs
    seen = []
    def matches(snapshot):
        return {'graph': snapshot.path.endswith('wiki_entry_graph.json'),
                'alias': snapshot.path.endswith('entity_alias_map.json'),
                'derived': snapshot.path.startswith('derived/')}[source_kind]
    name = {'bind': '_initial_binding', 'read': '_at', 'seal': 'seal'}[stage]
    original = getattr(SnapshotReadContext, name)
    def changed(self, *args, **kwargs):
        sources = [b.snapshot for b in self._bindings.values()] if stage == 'seal' else [args[0]]
        for source in sources:
            if matches(source) and not seen:
                seen.append(source)
                object.__setattr__(source, 'raw', b'{}')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SnapshotReadContext, name, changed)
    result = story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    assert len(seen) == 1
    assert result['status'] == 'error' and result['count'] is None and result['members'] == []
    assert '至少' not in result['answer_text_hint']
    if stage != 'seal' and source_kind == 'alias':
        assert result['error_code'] == 'alias_snapshot_unverifiable' and result['alias_snapshot'] is None
    if stage == 'seal':
        assert result['error_code'] == 'internal_validation_failed'


def test_r16_exception_closes_owned_context(real_inputs, monkeypatch):
    _, _, kw = real_inputs
    contexts = []
    original = story._context
    def context_capture(*a, **k):
        value = original(*a, **k)
        contexts.append(value)
        return value
    def interrupted(*a, **k):
        raise RuntimeError('controlled_fixture_exception')
    monkeypatch.setattr(story, '_context', context_capture)
    monkeypatch.setattr(story, '_count_bound', interrupted)
    with pytest.raises(RuntimeError, match='controlled_fixture_exception'):
        story.story_result_for_query('《固定活动》有多少角色参与', **kw)
    assert len(contexts) == 1 and contexts[0]._state == 'closed' and not contexts[0]._bindings

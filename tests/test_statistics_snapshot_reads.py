"""R01-R18/R25: public success controls before deliberate corruption."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from types import MappingProxyType
import weakref
import gc

import pytest

from app.statistics.contracts import Snapshot, canonical_bytes, resolve_pointer
from app.statistics.evidence import AliasSnapshot
from app.statistics.snapshot_reads import INPUT_FIELDS, SnapshotReadContext


def snap(value, path='fixture.json'):
    raw = canonical_bytes(value)
    return Snapshot(path, hashlib.sha256(raw).hexdigest(), raw)


def context(tmp_path, original=None):
    return SnapshotReadContext(root=tmp_path, original_request=original or {'scope': 'fixture'},
                               entry_kind='fixture', expected_sources={})


def locked(tmp_path):
    reads = context(tmp_path)
    source = snap({'entry': {'entry_id': 'e', 'cast': ['甲']}})
    aliases = AliasSnapshot(snap({'version': 'test-v1', 'canonical_to_variants': {'甲': [], '乙': []},
                                 'variant_to_canonical': {'小甲': '甲'}, 'conflict_notes': {}}, 'aliases.json'), reads)
    reads.bind(source)
    reads.bind(aliases.snapshot)
    reads.lock_execution({'scope_level': 'activity', 'scope_id': 'e'}, aliases)
    return reads, source, aliases


def test_r01_r03_public_json_and_pointer_equivalence(tmp_path):
    source = snap({'a/b': {'~key': [None, {'n': 2}]}, '': False})
    reads = context(tmp_path)
    for pointer in ['', '/', '/a~1b', '/a~1b/~0key', '/a~1b/~0key/1/n']:
        actual = reads.copy_at(source, pointer)
        expected = resolve_pointer(source.data(), pointer)
        assert actual == expected and type(actual) is type(expected)
    for pointer in ['x', '/a~2b', '/a~1b/~0key/01', '/a~1b/~0key/-1', '/a~1b/~0key/99', '//n']:
        with pytest.raises((ValueError, KeyError, IndexError)):
            reads.check_pointer(source, pointer)
    first = source.data()
    first['a/b']['~key'].append(3)
    assert source.data() != first
    reads.seal()
    reads.close()


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'{'])
def test_r02_strict_parser_rejection(tmp_path, raw):
    source = Snapshot('bad.json', hashlib.sha256(raw).hexdigest(), raw)
    reads = context(tmp_path)
    with pytest.raises((ValueError, UnicodeError)):
        reads.bind(source)
    assert reads._state == 'poisoned' and reads._bindings == {}
    reads.close()


def test_r04_r05_nested_copies_and_frozen_private_tree(tmp_path):
    reads, source, aliases = locked(tmp_path)
    entry = reads.copy_at(source, '/entry')
    entry['cast'].append('伪造')
    assert reads.copy_at(source, '/entry/cast') == ['甲']
    identity = reads.identity(source)
    identity['sha256'] = 'fake'
    assert reads.identity(source)['sha256'] == source.sha256
    names = reads.copy_at(aliases.snapshot, '/canonical_to_variants')
    names['甲'].append('坏别名')
    assert aliases.normalize('坏别名') == (None, [])
    tree = reads._bindings[id(source)].tree
    assert type(tree) is MappingProxyType
    with pytest.raises(TypeError):
        tree['entry']['cast'][0] = '伪造'
    reads.seal()
    reads.close()


def test_r08_r09_wrong_sha_and_foreign_object(tmp_path):
    reads, source, _ = locked(tmp_path)
    assert reads.lookup(source, reads.execution_key())['hit'] is True
    fake = Snapshot(source.path, source.sha256, b'{}')
    assert reads.lookup(fake, reads.execution_key())['hit'] is False
    with pytest.raises(ValueError, match='snapshot_identity'):
        reads.copy_at(fake)
    with pytest.raises(ValueError, match='poisoned'):
        reads.copy_at(source)
    reads.close()


@pytest.mark.parametrize('field,kind', [('raw', 'different'), ('raw', 'equal'), ('path', 'changed'),
                                       ('sha256', 'changed'), ('both', 'self_consistent')])
def test_r10_r11_warm_tamper_poison(tmp_path, field, kind):
    reads, source, _ = locked(tmp_path)
    key = reads.execution_key()
    assert reads.lookup(source, key)['hit'] is True
    if field == 'both':
        raw = b'{}'
        object.__setattr__(source, 'raw', raw)
        object.__setattr__(source, 'sha256', hashlib.sha256(raw).hexdigest())
    elif field == 'raw':
        raw = bytes(bytearray(source.raw)) if kind == 'equal' else b'{}'
        assert raw is not source.raw
        object.__setattr__(source, field, raw)
    else:
        object.__setattr__(source, field, 'changed')
    with pytest.raises(ValueError, match='reads_binding_changed'):
        reads.copy_at(source)
    assert reads._state == 'poisoned'
    with pytest.raises(ValueError):
        reads.bind(snap({'fresh': True}))
    reads.close()


@pytest.mark.parametrize('factory', [bytearray, memoryview])
def test_r12_mutable_raw_rejected(tmp_path, factory):
    source = snap({})
    object.__setattr__(source, 'raw', factory(source.raw))
    reads = context(tmp_path)
    with pytest.raises(ValueError, match='reads_native_fields'):
        reads.bind(source)
    reads.close()


def test_r13_seal_detects_last_read_tamper(tmp_path):
    reads, source, _ = locked(tmp_path)
    pending = reads.copy_at(source)
    object.__setattr__(source, 'raw', b'{}')
    with pytest.raises(ValueError, match='reads_binding_changed'):
        reads.seal()
    assert pending['entry']['cast'] == ['甲']
    reads.close()


def test_r15_new_request_reads_disk_and_rejects_old_pin(tmp_path):
    path = tmp_path / 'x.json'
    path.write_bytes(b'{"n":1}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source = Snapshot.read(tmp_path, 'x.json', digest)
    reads = context(tmp_path)
    assert reads.copy_at(source, '/n') == 1
    path.write_bytes(b'{"n":2}')
    assert reads.copy_at(source, '/n') == 1
    with pytest.raises(ValueError, match='source_sha_mismatch'):
        Snapshot.read(tmp_path, 'x.json', digest)
    reads.seal()
    reads.close()


def test_r16_closed_context_releases_and_rejects_generator(tmp_path):
    source = snap({'entries': [{'entry_id': 'e', 'cast': ['甲']}]})
    reads = context(tmp_path)
    rows = reads.scan_object_fields(source, '/entries', ('entry_id',))
    assert next(rows) == (0, {'entry_id': 'e'})
    exported = reads.copy_at(source, '/entries/0')
    reads.close()
    assert reads._bindings == {} and reads._execution is None
    assert exported['cast'] == ['甲']
    with pytest.raises(ValueError):
        reads.copy_at(source)


def test_r17_separate_requests_and_wrong_thread(tmp_path):
    reads, source, _ = locked(tmp_path)
    other = context(tmp_path)
    assert other.copy_at(source) == reads.copy_at(source)
    assert other._bindings[id(source)].tree is not reads._bindings[id(source)].tree
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError, match='reads_owner'):
            pool.submit(reads.copy_at, source).result()
    other.close()
    reads.close()


def test_r17_wrong_async_task(tmp_path):
    class NoIOEventLoop(asyncio.BaseEventLoop):
        """Real task scheduling without Windows socketpair initialization."""
        def _run_once(self):
            for _ in range(len(self._ready)):
                self._ready.popleft()._run()

    async def check():
        reads = context(tmp_path)
        source = snap({})
        reads.bind(source)
        async def foreign():
            with pytest.raises(ValueError, match='reads_owner'):
                reads.copy_at(source)
        await asyncio.create_task(foreign())
        reads.close()
    asyncio.run(check(), loop_factory=NoIOEventLoop)


@pytest.mark.parametrize('field', INPUT_FIELDS)
@pytest.mark.parametrize('mutation', ['delete', 'none', 'empty', 'unknown'])
def test_r25_each_missing_or_changed_key_must_miss(tmp_path, field, mutation):
    reads, source, _ = locked(tmp_path)
    valid = reads.execution_key()
    assert reads.lookup(source, valid)['hit'] is True
    broken = deepcopy(valid)
    if mutation == 'delete':
        del broken[field]
    else:
        broken[field] = {'none': None, 'empty': {}, 'unknown': 'unknown-version'}[mutation]
    before = reads.hits
    assert reads.lookup(source, broken)['hit'] is False
    assert reads.hits == before
    assert reads.lookup(source, valid)['hit'] is True
    assert reads.lookup(source, {})['hit'] is False
    reads.close()


def test_r18_r25_selection_lock_and_nonce(tmp_path):
    reads, source, aliases = locked(tmp_path)
    other, foreign_source, foreign_aliases = locked(tmp_path)
    assert reads.lookup(source, other.execution_key())['hit'] is False
    with pytest.raises(ValueError, match='reads_already_locked'):
        reads.lock_execution({'scope_level': 'activity', 'scope_id': 'e'}, aliases)
    with pytest.raises(ValueError, match='reads_scope_changed'):
        reads.check_semantic('甲', {'scope_level': 'activity', 'scope_id': 'other'}, aliases)
    with pytest.raises(ValueError, match='reads_semantic_input'):
        reads.check_semantic('甲', {'scope_level': 'activity', 'scope_id': 'e'}, foreign_aliases)
    other.close()
    reads.close()


def test_r05_conflict_candidates_and_location_are_detached(tmp_path):
    from app.statistics.contracts import evidence_location
    reads = context(tmp_path)
    source = snap({'entry': {'cast': ['甲']}})
    aliases = AliasSnapshot(snap({'version': 'test', 'canonical_to_variants': {'甲': [], '乙': []},
                                 'variant_to_canonical': {},
                                 'conflict_notes': {'同名': {'candidates': ['甲', '乙']}}}), reads)
    _, first = aliases.normalize('同名')
    first.append('伪造')
    assert aliases.normalize('同名') == (None, ['乙', '甲'])
    span = {'start': 0, 'end': 1}
    one = evidence_location(source, '/entry/cast/0', span, _reads=reads)
    one['cleaned_span']['start'] = 99
    assert evidence_location(source, '/entry/cast/0', span, _reads=reads)['cleaned_span']['start'] == 0
    assert span == {'start': 0, 'end': 1}
    reads.seal()
    reads.close()


def test_r15_same_size_same_mtime_does_not_reuse_old_disk(tmp_path):
    path = tmp_path / 'same.json'
    path.write_bytes(b'{"n":1}')
    old = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b'{"n":2}')
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert path.stat().st_size == old.st_size and path.stat().st_mtime_ns == old.st_mtime_ns
    with pytest.raises(ValueError, match='source_sha_mismatch'):
        Snapshot.read(tmp_path, 'same.json', digest)


def test_r16_generator_and_snapshot_lifetime(tmp_path):
    source = snap({'entries': [{'entry_id': 'a'}, {'entry_id': 'b'}]})
    ref = weakref.ref(source)
    reads = context(tmp_path)
    rows = reads.scan_object_fields(source, '/entries', ('entry_id',))
    assert next(rows) == (0, {'entry_id': 'a'})
    reads.close()
    with pytest.raises(ValueError, match='closed'):
        next(rows)
    del rows, source
    gc.collect()
    assert ref() is None


def test_r17_two_thread_owned_contexts_do_not_share_trees(tmp_path):
    from threading import Barrier
    barrier = Barrier(2)
    source = snap({'entry': {'cast': ['甲']}})
    def work(number):
        reads = context(tmp_path)
        first = reads.copy_at(source)
        first['entry']['cast'].append(str(number))
        barrier.wait(timeout=10)
        result = reads.copy_at(source)
        reads.seal()
        reads.close()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(work, [1, 2]))
    assert a == b == {'entry': {'cast': ['甲']}}


def test_r25_actual_mode_change_cannot_hit(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_WIKI_MODE', 'off')
    reads, source, _ = locked(tmp_path)
    key = reads.execution_key()
    assert reads.lookup(source, key)['hit'] is True
    before = reads.hits
    monkeypatch.setenv('LLM_WIKI_MODE', 'finite')
    with pytest.raises(ValueError, match='reads_mode_changed'):
        reads.lookup(source, key)
    assert reads.hits == before
    monkeypatch.setenv('LLM_WIKI_MODE', 'off')
    reads.close()

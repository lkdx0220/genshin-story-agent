"""Request-owned immutable inputs. Never retain evidence decisions or results."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident
from types import MappingProxyType

from .contracts import VERSIONS, Snapshot, _resolve_pointer, canonical_bytes, require

INPUT_FIELDS = (
    'request_nonce', 'entry_kind', 'trusted_root_namespace', 'source_expected_sha_set',
    'alias_table_sha256', 'alias_table_version', 'alias_normalizer_version',
    'contract_version', 'evidence_count_serialization_versions', 'original_request',
    'resolved_scope_id_and_level', 'per_call_member_and_scope',
    'story_body_rules_version_and_source_manifest', 'parser_adapter_source', 'actual_mode',
)


def _freeze(value):
    return _freeze_value(value)


def _freeze_value(value):
    if type(value) is dict:
        return MappingProxyType({k: _freeze_value(v) for k, v in value.items()})
    if type(value) is list:
        return tuple(_freeze_value(v) for v in value)
    return value


def _thaw(value):
    return _thaw_value(value)


def _thaw_value(value):
    if type(value) is MappingProxyType:
        return {k: _thaw_value(v) for k, v in value.items()}
    if type(value) is tuple:
        return [_thaw_value(v) for v in value]
    return value


def _task_identity():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _code_manifest():
    base = Path(__file__).resolve().parent
    paths = [base / n for n in ('snapshot_reads.py', 'contracts.py', 'evidence.py', 'story_cast.py')]
    paths.append(base.parent / 'entity_retrieval.py')
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@dataclass(frozen=True)
class _Binding:
    snapshot: Snapshot
    raw: bytes
    path: str
    sha256: str
    tree: object


class SnapshotReadContext:
    """Explicit single-call lifetime; all tree reads return detached data."""

    def __init__(self, *, root, original_request, entry_kind, expected_sources):
        self._thread = get_ident()
        self._task = _task_identity()
        self._state = 'open'
        self._bindings = {}
        self._nonce = uuid.uuid4().hex
        self._original = _freeze(_thaw(_freeze(original_request)))
        self._root = str(Path(root).resolve())
        self._entry_kind = entry_kind
        self._expected_sources = _freeze(dict(expected_sources))
        self._manifest = _freeze(_code_manifest())
        self._mode = os.environ.get('LLM_WIKI_MODE', 'off')
        require(self._mode in {'off', 'finite', 'agent_evidence'}, 'reads_mode')
        self._execution = None
        self._fingerprint = None
        self.hits = 0
        self.misses = 0

    def _open(self):
        require(get_ident() == self._thread and _task_identity() is self._task, 'reads_owner')
        require(self._state == 'open', 'reads_closed_or_poisoned')
        require(os.environ.get('LLM_WIKI_MODE', 'off') == self._mode, 'reads_mode_changed')

    def _guard(self, bound):
        self._open()
        snap = bound.snapshot
        valid = (type(snap.raw) is bytes and snap.raw is bound.raw
                 and type(snap.path) is str and snap.path == bound.path
                 and type(snap.sha256) is str and snap.sha256 == bound.sha256)
        if not valid:
            self._state = 'poisoned'
        require(valid, 'reads_binding_changed')

    def _initial_binding(self, snapshot):
        require(isinstance(snapshot, Snapshot), 'reads_snapshot_type')
        require(type(snapshot.raw) is bytes and type(snapshot.path) is str
                and type(snapshot.sha256) is str, 'reads_native_fields')
        raw, path, digest = snapshot.raw, snapshot.path, snapshot.sha256
        require(hashlib.sha256(raw).hexdigest() == digest, 'snapshot_identity')
        expected = self._expected_sources.get(path)
        require(expected is None or expected == digest, 'reads_expected_source')
        # Call the original strict parser; never substitute a permissive decoder.
        tree = _freeze(Snapshot.data(Snapshot(path, digest, raw)))
        bound = _Binding(snapshot, raw, path, digest, tree)
        self._guard(bound)
        return bound

    def bind(self, snapshot):
        self._open()
        try:
            bound = self._bindings.get(id(snapshot))
            if bound is None:
                bound = self._initial_binding(snapshot)
                self._bindings[id(snapshot)] = bound
            require(bound.snapshot is snapshot, 'reads_snapshot_identity')
            self._guard(bound)
        except (ValueError, TypeError, KeyError, UnicodeError):
            self._state = 'poisoned'
            raise

    def _get(self, snapshot):
        self.bind(snapshot)
        bound = self._bindings[id(snapshot)]
        if self._execution is not None:
            require(self.lookup(snapshot, self._execution)['hit'], 'reads_execution_miss')
        return bound

    def _at(self, snapshot, pointer):
        bound = self._get(snapshot)
        return _resolve_pointer(bound.tree, pointer, arrays=(tuple,), objects=(MappingProxyType,))

    def identity(self, snapshot):
        bound = self._get(snapshot)
        return {'source_id': bound.path + '@' + bound.sha256,
                'path': bound.path, 'sha256': bound.sha256}

    def check_pointer(self, snapshot, pointer):
        self._at(snapshot, pointer)

    def copy_at(self, snapshot, pointer=''):
        return _thaw(self._at(snapshot, pointer))

    def contains(self, snapshot, pointer, key):
        value = self._at(snapshot, pointer)
        require(type(value) is MappingProxyType, 'reads_object_required')
        return key in value

    def get_key(self, snapshot, pointer, key, default=None):
        value = self._at(snapshot, pointer)
        require(type(value) is MappingProxyType, 'reads_object_required')
        return _thaw(value[key]) if key in value else default

    def scan_object_fields(self, snapshot, pointer, fields):
        rows = self._at(snapshot, pointer)
        require(type(rows) is tuple, 'reads_array_required')
        for index, row in enumerate(rows):
            self._get(snapshot)
            require(type(row) is MappingProxyType, 'reads_object_required')
            yield index, {k: _thaw(row[k]) for k in fields if k in row}

    def graph_entries(self, snapshot):
        tree = self._at(snapshot, '')
        valid = type(tree) is MappingProxyType and type(tree.get('entries')) is tuple
        require(valid, 'source_data_invalid')
        require(all(type(e) is MappingProxyType and isinstance(e.get('entry_id'), str)
                    for e in tree['entries']), 'source_data_invalid')
        return GraphEntries(self, snapshot)

    def execution_inputs(self, scope, aliases):
        """Build binding metadata from owned sources; no caller-state trust tokens."""
        self._open()
        identity = aliases.identity()
        sources = dict(self._expected_sources)
        sources.update({v.path: v.sha256 for v in self._bindings.values()})
        return {
            'request_nonce': self._nonce, 'entry_kind': self._entry_kind,
            'trusted_root_namespace': self._root, 'source_expected_sha_set': sources,
            'alias_table_sha256': identity['sha256'], 'alias_table_version': identity['version'],
            'alias_normalizer_version': identity['normalizer_version'],
            'contract_version': 'count_result_v1.3',
            'evidence_count_serialization_versions': dict(VERSIONS),
            'original_request': _thaw(self._original), 'resolved_scope_id_and_level': dict(scope),
            'per_call_member_and_scope': {'policy': 'recompute_each_call', 'scope': dict(scope)},
            'story_body_rules_version_and_source_manifest': {
                'normalizer': 'story-body-existing-speaker-exact-v1', 'source': dict(self._manifest)},
            'parser_adapter_source': {'parser': 'Snapshot.data', 'adapter': 'exact-entry-projection-v1',
                                      'source': dict(self._manifest)},
            'actual_mode': self._mode,
        }

    def lock_execution(self, scope, aliases):
        self._open()
        require(self._execution is None, 'reads_already_locked')
        require(aliases._reads is self, 'reads_alias_context')
        inputs = self.execution_inputs(scope, aliases)
        require(all(inputs.get(k) for k in INPUT_FIELDS), 'reads_incomplete_inputs')
        self._fingerprint = hashlib.sha256(canonical_bytes(inputs)).hexdigest()
        self._execution = _freeze(inputs)

    def lookup(self, snapshot, key):
        """Qualification only. A hit never returns a decision or mutable tree."""
        self._open()
        bound = self._bindings.get(id(snapshot))
        hit = False
        if bound is not None and bound.snapshot is snapshot:
            self._guard(bound)
            # Mapping equality to the privately frozen key also rejects missing,
            # empty, changed and unknown fields; identity is only the fast case.
            comparable = _freeze(key) if type(key) is dict else key
            hit = self._fingerprint is not None and self._execution is not None and (
                comparable is self._execution or comparable == self._execution)
        self.hits += int(hit)
        self.misses += int(not hit)
        return {'hit': bool(hit)}

    def execution_key(self):
        self._open()
        require(self._execution is not None, 'reads_selection_only')
        return _thaw(self._execution)

    def check_semantic(self, member, scope, aliases):
        self._open()
        require(self._execution is not None, 'reads_selection_only')
        require(scope == self._execution['resolved_scope_id_and_level'], 'reads_scope_changed')
        require(aliases._reads is self and isinstance(member, str) and bool(member.strip()),
                'reads_semantic_input')
        identity = aliases.identity()
        require(identity['sha256'] == self._execution['alias_table_sha256']
                and identity['version'] == self._execution['alias_table_version'], 'reads_alias_changed')

    def seal(self):
        self._open()
        try:
            for bound in self._bindings.values():
                self._guard(bound)
                require(hashlib.sha256(bound.raw).hexdigest() == bound.sha256, 'reads_seal_sha')
            for bound in self._bindings.values():
                self._guard(bound)
        except ValueError:
            self._state = 'poisoned'
            raise

    def close(self):
        require(get_ident() == self._thread and _task_identity() is self._task, 'reads_owner')
        self._bindings.clear()
        self._execution = None
        self._fingerprint = None
        self._original = None
        self._manifest = None
        self._task = None
        self._state = 'closed'


@dataclass(frozen=True)
class GraphEntries:
    """Private indexed scan facade, never exported in count results."""
    reads: SnapshotReadContext
    snapshot: Snapshot

    def scan(self, fields):
        return self.reads.scan_object_fields(self.snapshot, '/entries', fields)

    def copy(self, index):
        return self.reads.copy_at(self.snapshot, '/entries/' + str(index))

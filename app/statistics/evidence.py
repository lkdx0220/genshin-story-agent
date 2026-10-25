"""Per-member evidence in an explicitly bound local source scope."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.entity_retrieval import story_body
from .contracts import (Snapshot, evidence_location, json_pointer, require,
                        snapshot_identity, snapshot_value, validate_verdict)

ALIAS_SHA256 = '363ef7f1a06f87a84590bb7619f5a3ce26cb1bd3e5d613367bc0a507f4e9b1ab'
NORMALIZER = 'story-body-existing-speaker-exact-v1'


@dataclass(frozen=True)
class AliasSnapshot:
    snapshot: Snapshot
    _reads: object = field(default=None, repr=False, compare=False)

    @classmethod
    def read(cls, root, trusted_sha256=ALIAS_SHA256, *, _reads=None):
        value = cls(Snapshot.read(root, 'kb_vectors/entity_alias_map.json', trusted_sha256), _reads)
        data = snapshot_value(value.snapshot, '', _reads)
        require(all(isinstance(data.get(k), dict)
                    for k in ('variant_to_canonical', 'canonical_to_variants', 'conflict_notes')),
                'alias_schema')
        return value

    def identity(self):
        snapshot_identity(self.snapshot, self._reads)
        return {'path': self.snapshot.path, 'sha256': self.snapshot.sha256,
                'version': str(snapshot_value(self.snapshot, '/version', self._reads)), 'normalizer_version': 'exact-alias-v1'}

    def normalize(self, raw, canonical_id=None):
        require(isinstance(raw, str) and bool(raw.strip()), 'member_name')
        if self._reads is not None:
            return self._normalize_selected(raw.strip(), canonical_id)
        name, data = raw.strip(), self.snapshot.data()
        canonicals = data['canonical_to_variants']
        if canonical_id is not None:
            require(canonical_id in canonicals, 'unconfirmed_canonical_id')
            return canonical_id, []
        if name in data['conflict_notes']:
            return None, sorted(data['conflict_notes'][name]['candidates'])
        if name in canonicals:
            return name, []
        mapped = data['variant_to_canonical'].get(name)
        require(mapped is None or isinstance(mapped, str), 'alias_mapping')
        return mapped, []

    def _normalize_selected(self, name, canonical_id):
        reads, snap = self._reads, self.snapshot
        if canonical_id is not None:
            require(reads.contains(snap, '/canonical_to_variants', canonical_id), 'unconfirmed_canonical_id')
            return canonical_id, []
        if reads.contains(snap, '/conflict_notes', name):
            row = reads.copy_at(snap, json_pointer('conflict_notes', name))
            return None, sorted(row['candidates'])
        if reads.contains(snap, '/canonical_to_variants', name):
            return name, []
        mapped = reads.get_key(snap, '/variant_to_canonical', name)
        require(mapped is None or isinstance(mapped, str), 'alias_mapping')
        return mapped, []


def _cast_members(entry, aliases):
    cast = entry.get('cast')
    if not isinstance(cast, list) or not all(isinstance(n, str) and n.strip() for n in cast):
        return set(), False
    normalized = [aliases.normalize(n)[0] for n in cast]
    return {n for n in normalized if n}, all(normalized)


def _speaker_spans(body, target, aliases):
    spans, offset, quote_depth, fenced = [], 0, 0, False
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('```'):
            fenced = not fenced
        match = re.match(r'^([^\s:：<>「“"\[【]{1,40})[:：]', line)
        if match and not fenced and quote_depth == 0:
            if aliases.normalize(match[1])[0] == target:
                spans.append({'start': offset, 'end': offset + len(line.rstrip('\r\n')), 'normalizer_version': NORMALIZER})
        quote_depth = max(0, quote_depth + line.count('“') + line.count('「') - line.count('”') - line.count('」'))
        offset += len(line)
    return spans


def _scope_matches(entry, scope):
    if scope['scope_level'] == 'act':
        return False
    entry_level = 'activity' if entry.get('entry_type') == 'activity' else 'task'
    return (entry.get('entry_type') in {'activity', 'task'} and entry_level == scope['scope_level']
            and entry.get('cast_scope') == scope['scope_level'] and entry['entry_id'] == scope['scope_id'])


def _metadata_completeness(entry, scope, parsed_names, aliases):
    proof = entry.get('coverage') or {}
    if not isinstance(proof, dict) or proof.get('scope') != scope:
        return False, False
    field = (entry.get('metadata') or {}).get('出场人物')
    if not isinstance(field, list) or not all(isinstance(n, str) and n.strip() for n in field):
        return False, False
    names = [aliases.normalize(n)[0] for n in field]
    parse_ok = proof.get('field_parse_ok') is True and all(names) and set(names) == parsed_names
    complete = parse_ok and proof.get('complete') is True and not entry.get('cast_absent')
    complete = complete and not entry.get('story_placeholder') and bool(field)
    return bool(complete), bool(parse_ok)


def _verdict(entry, name, scope, decision, kind, locations, coverage, reason):
    if kind != 'metadata':
        coverage = dict(coverage, complete=False)
    verdict = {'evidence_contract_version': 'evidence_contract_v1.1', 'entry_id': entry['entry_id'],
               'canonical_member': name, 'scope': dict(scope), 'decision': decision,
               'evidence_kind': kind, 'evidence_location': locations, 'reason': reason, 'coverage': coverage}
    validate_verdict(verdict)
    return verdict


def member_evidence(entry, canonical_member, *, scope, source, entry_pointer, aliases, canonical_id=None, _reads=None):
    """Require a hash-bound source and exact entry pointer; do not swallow I/O errors.

    Coverage is not inferred from graph metadata alone. Exclusion additionally
    requires the actual parsed page field and its matching completeness record.
    """
    if _reads is not None:
        _reads.check_semantic(canonical_member, scope, aliases)
    snapshot_identity(source, _reads)
    aliases.identity()
    require(entry == snapshot_value(source, entry_pointer, _reads), 'entry_source_mismatch')
    require(isinstance(entry.get('entry_id'), str) and bool(entry['entry_id']), 'entry_id')
    require(set(scope) == {'scope_level', 'scope_id'} and scope['scope_level'] in {'activity', 'act', 'task'}, 'scope')
    target, ambiguity = aliases.normalize(canonical_member, canonical_id)
    name = target or canonical_member.strip()
    cast_source = entry.get('cast_source')
    require(cast_source is None or isinstance(cast_source, str), 'cast_source')
    coverage = {'scope': dict(scope), 'cast_source': cast_source, 'complete': False, 'field_parse_ok': False}
    if not target:
        reason = '人物别名存在歧义：' + '、'.join(ambiguity) if ambiguity else '人物身份无精确权威映射'
        return _verdict(entry, name, scope, 'unknown', 'missing', [], coverage, reason)
    return _bound_evidence(entry, name, scope, source, entry_pointer, aliases, coverage, _reads=_reads)


def _bound_evidence(entry, target, scope, source, pointer, aliases, coverage, *, _reads=None):
    parts = set((entry.get('cast_source') or '').split('+'))
    if not _scope_matches(entry, scope) or 'activity_inherited' in parts:
        kind = 'activity_inherited' if 'activity_inherited' in parts or entry.get('cast_scope') == 'activity' else 'missing'
        return _verdict(entry, target, scope, 'unknown', kind, [], coverage, '来源范围不等于请求范围；首版不支持幕级映射')
    cast, metadata, complete, locations = _metadata_evidence(entry, scope, source, pointer, aliases, coverage, _reads=_reads)
    body, speaking = _speaking_evidence(entry, target, source, pointer, aliases, _reads=_reads)
    decision, kind, evidence, reason = _choose_evidence(target, cast, metadata, complete, locations, body, speaking)
    return _verdict(entry, target, scope, decision, kind, evidence, coverage, reason)


def _metadata_evidence(entry, scope, source, pointer, aliases, coverage, *, _reads=None):
    parts = set((entry.get('cast_source') or '').split('+'))
    cast, parsed = _cast_members(entry, aliases)
    metadata = 'metadata' in parts and parsed
    complete, parse_ok = _metadata_completeness(entry, scope, cast, aliases)
    coverage.update(complete=bool(metadata and complete), field_parse_ok=parse_ok)
    locations = [evidence_location(source, pointer + '/cast', _reads=_reads)] if metadata else []
    if metadata and complete:
        locations.extend(evidence_location(source, pointer + suffix, _reads=_reads) for suffix in ('/metadata/出场人物', '/coverage'))
    return cast, metadata, complete, locations


def _speaking_evidence(entry, target, source, pointer, aliases, *, _reads=None):
    field = 'story_text' if entry.get('story_text') else 'full_text'
    body = story_body(entry)
    if '[任务概述]' in body or '[前情提要]' in body:
        body = ''
    spans = _speaker_spans(body, target, aliases)
    speaking = [evidence_location(source, pointer + '/' + field, span, _reads=_reads) for span in spans]
    return body, speaking


def _choose_evidence(target, cast, metadata, complete, locations, body, speaking):
    if metadata and complete and target not in cast and speaking:
        return 'conflict', 'conflicting', locations + speaking, '同范围完整metadata与实际说话人矛盾'
    if metadata and target in cast:
        return 'include', 'metadata', locations, '同范围metadata名单明确包含该成员'
    if speaking:
        return 'include', 'speaker_lines', speaking, '净化正文中精确身份的实际说话人'
    if metadata and complete:
        return 'exclude', 'metadata', locations, '本地该范围完整字段未列此人；不推出游戏中不存在'
    return 'unknown', 'mention_only' if target in body else 'missing', [], '仅提及或缺完整权威名单，不能判定参与或排除'

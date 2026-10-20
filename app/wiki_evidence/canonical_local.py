"""Strict local provenance for the opt-in canonical evidence transport.

Mappings are reviewed, finite links from unchanged canonical record bytes to
the original local knowledge base. An external URL never creates a mapping.
This module has no config, provider, tool-runtime or environment imports.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 'canonical-local-evidence-v1'
SOURCE_PATH = 'content_data/quests_魔神任务.json'
SOURCE_SHA256 = '9d64bec603d6254a52e3264382e019b0c3458126ea20c09ca281aa8455e07950'
HEX = re.compile(r'[0-9a-f]{64}')
REASON_CODES = frozenset({
    'binding_verified', 'partial_bindings', 'mapping_missing', 'no_hits',
    'source_missing', 'source_changed', 'source_invalid', 'invalid_envelope',
    'unknown_state', 'source_stale',
})
REASONS = {
    'binding_verified': '本次命中均已绑定并复核本地原库。',
    'partial_bindings': '仅部分命中已绑定；未覆盖部分须继续原 RAG 补查并向用户说明。',
    'mapping_missing': '未建立该记录与本地原库的显式绑定；须继续原 RAG 补查。',
    'no_hits': '没有可绑定命中；须继续原 RAG 补查。',
    'source_missing': '获授权原库文件不可读取；须继续原 RAG 补查。',
    'source_changed': '获授权原库字节已漂移；不得使用旧引用。',
    'source_invalid': '原库结构、定位或绑定区间不一致；不得使用旧引用。',
    'invalid_envelope': 'canonical 本地证据协议无效；须继续原 RAG 补查。',
    'unknown_state': '未知来源状态按 missing 处理；须继续原 RAG 补查。',
    'source_stale': '命中绑定已失效；须继续原 RAG 补查。',
}

# (canonical record ID, exact canonical fact UTF-8 SHA) -> original-KB spans.
# A span is (row, pageid, text SHA, start, end, quote SHA). These links were
# checked against the frozen local source, not reconstructed from old URLs.
TRUSTED_BINDINGS = {
    ('rel:scaramouche-fatui-sixth',
     'e98a4eb4c6bdd1485e29c47c06d4c45aedf98ed8e7f52102ff9c51fe15322471'): [
        (173, 164965, '07253808a863e6fdc14b44b71b6952e1a0bf0b89d7e6864a49e1fb0344cb76c3',
         556, 594, 'fa7afac7c507a05947dedf53fb11070d276a928a3ce7c5e2557fdad6a4f2b14e'),
    ],
    ('memory:nahida-backup',
     'c881d0f4a9ea971e94b9c6cbf5188ff684b8a468e0e5a63698a5867875785e77'): [
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         7815, 8104, 'f2e5c4c0a5794f617184ce7ba5a00f87fac2a4d07e0b43d8ada6966543948e46'),
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         8902, 9115, '6191ff1b90257f0b09ac837cf10efb74b83d605787cebef7c644d183b5a7403e'),
    ],
    ('memory:wanderer-memory-loss',
     'cd0b5b2873cb63536ba741f3cf0f3d7936b9b4c9d493490b476826f7e8e3e798'): [
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         11107, 11167, '21a6439b928d58792edc3ef48b6a84922ace82df88d5c618c7665dbe7674d16f'),
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         12187, 12350, '77a8204b10749224bf80828cd8569862d9ac14bc386f0b12e2f3931209c2d409'),
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         13160, 13204, '36b06729eb33e10e65a239b382015b033cf32c405e7f12b07dd7e94f632e2844'),
    ],
    ('memory:wanderer-regain',
     '07b240d463ad65012fea8cf4445da1cce83a02ac5a2c7c0ea5099a8fef8a96fe'): [
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         16615, 16727, 'ffa45c8b19e194bfedba005a29c0ae1a235f9bd2ef88746506d5f869c541ee97'),
        (215, 143592, 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779',
         17171, 17419, '2937204d9991a4c6329204de90646037aead67a90ff2b59b82e138e60d6c4741'),
    ],
}


def _parse_envelope_header(raw, progress):
    if not isinstance(raw, str):
        raise TypeError('not_string')
    data = _strict_json(raw)
    _fields(data, {'schema_version', 'original_text', 'local_provenance_status',
                   'status_reason', 'hits', 'local_evidence'})
    if data['schema_version'] != SCHEMA_VERSION or not isinstance(data['original_text'], str):
        raise ValueError('version')
    progress['original_text'] = data['original_text']
    if data['local_provenance_status'] not in {'bound', 'partial', 'missing', 'stale'}:
        return data, None, True
    _reason_valid(data['status_reason'])
    if not isinstance(data['hits'], list) or not isinstance(data['local_evidence'], list):
        raise TypeError('arrays')
    trusted = {binding_key(*pair): specs for pair, specs in TRUSTED_BINDINGS.items()}
    return data, trusted, False


def _validate_envelope_hits(data, trusted, by_key):
    for hit in data['hits']:
        _fields(hit, {'binding_key', 'status', 'status_reason', 'local_citation_ids'})
        key = hit['binding_key']
        if not isinstance(key, str) or not key or key in by_key:
            raise ValueError('hit_key')
        if hit['status'] not in {'bound', 'missing', 'stale'}:
            return True
        _reason_valid(hit['status_reason'])
        allowed_reasons = {
            'bound': {'binding_verified'},
            'missing': {'mapping_missing'},
            'stale': {'source_missing', 'source_changed', 'source_invalid'},
        }
        if hit['status_reason']['code'] not in allowed_reasons[hit['status']]:
            raise ValueError('hit_reason')
        _unique_strings(hit['local_citation_ids'], nonempty=hit['status'] == 'bound')
        if (hit['status'] != 'bound' and hit['local_citation_ids']) or (hit['status'] == 'bound' and key not in trusted):
            raise ValueError('hit_binding')
        by_key[key] = hit
    return False


def _validate_envelope_entries(data, by_key, kb_root):
    status, code = _aggregate(data['hits'])
    if (status != data['local_provenance_status']
            or data['status_reason']['code'] != code):
        raise ValueError('aggregate')
    if status in {'missing', 'stale'} and data['local_evidence']:
        raise ValueError('unbound_evidence')
    rows = _safe_source(kb_root) if data['local_evidence'] else None
    by_id = {}
    for entry in data['local_evidence']:
        _verified_entry(entry, rows)
        for key in entry['binding_keys']:
            if key not in by_key or by_key[key]['status'] != 'bound':
                raise ValueError('untrusted_mapping')
        if entry['local_citation_id'] in by_id:
            raise ValueError('evidence_mismatch')
        by_id[entry['local_citation_id']] = entry
    return by_id


def _validate_envelope_closure(by_key, by_id, trusted):
    for key, hit in by_key.items():
        expected_ids = []
        if hit['status'] == 'bound':
            for spec in trusted[key]:
                row, pageid, text_sha, start, end, quote_sha = spec
                expected_ids.append(stable_id(SOURCE_PATH, SOURCE_SHA256, row,
                                             pageid, text_sha, start, end, quote_sha))
            if set(hit['local_citation_ids']) != set(expected_ids):
                raise ValueError('incomplete_fact')
        for citation_id in hit['local_citation_ids']:
            if citation_id not in by_id or key not in by_id[citation_id]['binding_keys']:
                raise ValueError('closure')
    for citation_id, entry in by_id.items():
        if any(citation_id not in by_key[key]['local_citation_ids'] for key in entry['binding_keys']):
            raise ValueError('reverse_closure')


def _envelope_validation_failure(exc, original_text, by_key):
    if str(exc) in {'source_missing', 'source_changed', 'source_invalid'} and by_key:
        # A formerly valid envelope does not silently become valid after a
        # final-display read detects source drift. Preserve an explicit gap.
        failed = _empty(original_text)
        failed['hits'] = [
            {'binding_key': key,
             'status': 'stale' if hit['status'] == 'bound' else hit['status'],
             'status_reason': _reason(str(exc)) if hit['status'] == 'bound' else hit['status_reason'],
             'local_citation_ids': []}
            for key, hit in by_key.items()
        ]
        status, code = _aggregate(failed['hits'])
        failed.update(local_provenance_status=status, status_reason=_reason(code))
        return failed
    return _empty(original_text)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def stable_id(file, file_sha, row, pageid, text_sha, start, end, quote_sha) -> str:
    """Content identity; current-call permission is separately checked by consumer."""
    if (not isinstance(file, str) or not file or '\\' in file
            or PurePosixPath(file).is_absolute() or '..' in PurePosixPath(file).parts
            or PurePosixPath(file).as_posix() != file
            or any(not isinstance(value, str) or not HEX.fullmatch(value)
                   for value in (file_sha, text_sha, quote_sha))
            or any(type(value) is not int for value in (row, pageid, start, end))
            or row < 0 or pageid < 0 or not 0 <= start < end):
        raise ValueError('citation_identity_invalid')
    return 'loc:' + digest(_canonical([
        file, file_sha, row, pageid, text_sha, start, end, quote_sha,
    ]).encode('utf-8'))


def binding_key(record_id: str, fact_text_sha256: str) -> str:
    return 'can:' + digest(_canonical([record_id, fact_text_sha256]).encode('utf-8'))


def _reason(code: str) -> dict:
    return {'code': code, 'detail': REASONS[code]}


def _empty(original_text: str, code: str = 'invalid_envelope') -> dict:
    return {'schema_version': SCHEMA_VERSION, 'original_text': original_text,
            'local_provenance_status': 'missing', 'status_reason': _reason(code),
            'hits': [], 'local_evidence': []}


def _strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate_key')
            out[key] = value
        return out

    def constant(_):
        raise ValueError('non_finite_json')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _safe_source(kb_root: Path):
    root = Path(kb_root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('source_invalid')
    path = root / SOURCE_PATH
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or part.is_junction():
            raise ValueError('source_invalid')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError('source_missing') from exc
    if digest(raw) != SOURCE_SHA256:
        raise ValueError('source_changed')
    rows = _strict_json(raw)
    if not isinstance(rows, list):
        # The query boundary exposes one stable source-diagnostic exception.
        raise ValueError('source_invalid')  # noqa: TRY004
    return rows


def _span_evidence(rows: list, spec: tuple, key: str) -> dict:
    row_index, pageid, text_sha, start, end, quote_sha = spec
    try:
        row = rows[row_index]
        text = row['text']
        if (not isinstance(text, str) or type(row['pageid']) is not int
                or row['pageid'] != pageid
                or digest(text.encode('utf-8')) != text_sha
                or not 0 <= start < end <= len(text)):
            raise ValueError('source_invalid')
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('source_invalid') from exc
    quote = text[start:end]
    if digest(quote.encode('utf-8')) != quote_sha:
        raise ValueError('source_invalid')
    return {
        'binding_keys': [key],
        'local_citation_id': stable_id(SOURCE_PATH, SOURCE_SHA256, row_index,
                                     pageid, text_sha, start, end, quote_sha),
        'row_pageid': {'row': row_index, 'pageid': pageid},
        'quote': quote, 'quote_sha': quote_sha, 'status': 'bound',
        'provenance': {'file': SOURCE_PATH, 'file_sha': SOURCE_SHA256,
                       'text_sha': text_sha, 'start': start, 'end': end},
    }


def _aggregate(hits: list) -> tuple[str, str]:
    if not hits:
        return 'missing', 'no_hits'
    states = {hit['status'] for hit in hits}
    if states == {'bound'}:
        return 'bound', 'binding_verified'
    if 'bound' in states:
        return 'partial', 'partial_bindings'
    if 'stale' in states:
        return 'stale', 'source_stale'
    return 'missing', 'mapping_missing'


def build_envelope(original_text: str, hits, kb_root: Path) -> dict:
    """Build a finite envelope from actual canonical ``(page, Fact)`` hits.

    Facts without an exact record/text mapping remain explicit missing hits.
    One fact's full span set is atomic: incomplete support is never bound.
    """
    result = _empty(str(original_text))
    rows = None
    source_error = None
    evidence_by_id = {}
    seen = set()
    for _page, fact in hits:
        pair = (fact.record_id, digest(fact.text.encode('utf-8')))
        key = binding_key(*pair)
        if key in seen:
            continue
        seen.add(key)
        hit = {'binding_key': key, 'status': 'missing',
               'status_reason': _reason('mapping_missing'), 'local_citation_ids': []}
        specs = TRUSTED_BINDINGS.get(pair)
        if specs is not None:
            try:
                if rows is None:
                    if source_error:
                        raise ValueError(source_error)
                    try:
                        rows = _safe_source(kb_root)
                    except (ValueError, UnicodeError) as exc:
                        source_error = str(exc) if str(exc) in REASON_CODES else 'source_invalid'
                        raise ValueError(source_error) from exc
                entries = [_span_evidence(rows, spec, key) for spec in specs]
                for entry in entries:
                    citation_id = entry['local_citation_id']
                    if citation_id in evidence_by_id:
                        evidence_by_id[citation_id]['binding_keys'].append(key)
                    else:
                        evidence_by_id[citation_id] = entry
                    hit['local_citation_ids'].append(citation_id)
                hit.update(status='bound', status_reason=_reason('binding_verified'))
            except (ValueError, UnicodeError) as exc:
                code = str(exc) if str(exc) in REASON_CODES else 'source_invalid'
                hit.update(status='stale', status_reason=_reason(code))
        result['hits'].append(hit)
    status, code = _aggregate(result['hits'])
    result.update(local_provenance_status=status, status_reason=_reason(code),
                  local_evidence=list(evidence_by_id.values()))
    return result


def dumps_envelope(envelope: dict) -> str:
    return _canonical(envelope)


def _fields(obj, required):
    if not isinstance(obj, dict) or set(obj) != set(required):
        raise ValueError('fields')


def _reason_valid(reason):
    _fields(reason, {'code', 'detail'})
    if (reason['code'] not in REASON_CODES or not isinstance(reason['detail'], str)
            or reason['detail'] != REASONS[reason['code']]):
        raise ValueError('reason')


def _unique_strings(value, *, nonempty=False):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise ValueError('strings')


def _verified_entry(entry: dict, rows: list) -> dict:
    _fields(entry, {'binding_keys', 'local_citation_id', 'row_pageid',
                    'quote', 'quote_sha', 'status', 'provenance'})
    _unique_strings(entry['binding_keys'], nonempty=True)
    _fields(entry['row_pageid'], {'row', 'pageid'})
    _fields(entry['provenance'], {'file', 'file_sha', 'text_sha', 'start', 'end'})
    selector, provenance = entry['row_pageid'], entry['provenance']
    if (any(type(selector[k]) is not int for k in selector)
            or any(type(provenance[k]) is not int for k in ('start', 'end'))
            or not isinstance(provenance['file'], str)
            or PurePosixPath(provenance['file']).as_posix() != SOURCE_PATH
            or provenance['file'] != SOURCE_PATH
            or any(not isinstance(value, str) or not HEX.fullmatch(value)
                   for value in (provenance['file_sha'], provenance['text_sha'], entry['quote_sha']))
            or provenance['file_sha'] != SOURCE_SHA256
            or not isinstance(entry['quote'], str) or entry['status'] != 'bound'):
        raise ValueError('evidence_type')
    spec = (selector['row'], selector['pageid'], provenance['text_sha'],
            provenance['start'], provenance['end'], entry['quote_sha'])
    trusted = {binding_key(*pair): specs for pair, specs in TRUSTED_BINDINGS.items()}
    if any(key not in trusted or spec not in trusted[key] for key in entry['binding_keys']):
        raise ValueError('untrusted_mapping')
    expected = _span_evidence(rows, spec, entry['binding_keys'][0])
    expected['binding_keys'] = entry['binding_keys']
    if entry != expected:
        raise ValueError('evidence_mismatch')
    return entry


def verify_evidence(entry: dict, kb_root: Path) -> dict:
    """Recheck one unadorned query record immediately before answer display.

    Consumer-only alias/tool_call_id must be removed by the caller, whose own
    validator checks those execution identities. Raises on any disagreement.
    """
    return _verified_entry(entry, _safe_source(kb_root))


def validate_envelope(raw: str, kb_root: Path) -> dict:
    """Strictly parse and recheck actual source bytes, failing safely to missing.

    The caller retains the original ToolMessage for audit. This normalized
    return never authorizes an unknown state, malformed envelope or stale ID.
    ``tool_call_id`` is deliberately absent; only the consumer can bind it.
    """
    original_text = raw if isinstance(raw, str) else ''
    by_key = {}
    progress = {'original_text': original_text}
    try:
        data, trusted, unknown = _parse_envelope_header(raw, progress)
        if unknown:
            return _empty(progress['original_text'], 'unknown_state')
        if _validate_envelope_hits(data, trusted, by_key):
            return _empty(progress['original_text'], 'unknown_state')
        by_id = _validate_envelope_entries(data, by_key, kb_root)
        _validate_envelope_closure(by_key, by_id, trusted)
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, OSError) as exc:
        return _envelope_validation_failure(exc, progress['original_text'], by_key)

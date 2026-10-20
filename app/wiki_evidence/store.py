"""Deterministic compilation and externally pinned reading of a local topic.

The lexical index retrieves candidates, never classifies a question as answered.
The original Planner decides whether to supplement the complete source chapter.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SOURCE_SHA256 = '9d64bec603d6254a52e3264382e019b0c3458126ea20c09ca281aa8455e07950'
TEXT_SHA256 = 'fc7d0f96b83a215a678f7177300fe9fe0ac90c0109feee9e6eae2d7e667c6779'
SOURCE_PATH = 'content_data/quests_魔神任务.json'
HEX = re.compile(r'[0-9a-f]{64}')
TOPIC = 'topic:kb143592:memory'
ALIASES = ['流浪者', '散兵', '倾奇者', '纳西妲']
CUES = ['记忆', '记得', '经历', '过往', '遗忘', '世界树', '旅行者', '童话', '前生', '往事', '失忆', '监督']
# All ranges are Python Unicode half-open intervals in the pinned original text.
TIMELINE = [
    ('world_tree_change', 'records_changed', 7437, 7522),
    ('backup', 'before_restoration', 8862, 9116),
    ('unaware_of_past', 'before_restoration', 12187, 12350),
    ('watch_then_request', 'request_before_restoration', 16615, 16869),
    ('wandering_self', 'before_restoration', 16883, 17029),
    ('memory_return', 'restoration_and_after', 17148, 17421),
]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_safe(path: Path) -> bytes:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('evidence_path_invalid')
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or part.is_junction():
            raise ValueError('evidence_path_invalid')
    return path.read_bytes()


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def source_row(raw: bytes):
    if digest(raw) != SOURCE_SHA256:
        raise ValueError('source_changed')
    rows = strict_json(raw)
    row = rows[215]
    if (len(rows) != 230 or str(row['pageid']) != '143592'
            or row['title'] != '幕切——倾奇之末'
            or digest(row['text'].encode()) != TEXT_SHA256):
        raise ValueError('source_selector_changed')
    return row


def compile_topic(kb_root: Path) -> dict:
    """No generated answer/response, external URL or query-specific gold here."""
    row = source_row(read_safe(Path(kb_root) / SOURCE_PATH))
    text = row['text']
    spans = []
    for name, phase, start, end in TIMELINE:
        quote = text[start:end]
        quote_sha = digest(quote.encode())
        spans.append({'id': f'kb:143592:{start}:{end}:{quote_sha[:16]}',
                      'event': name, 'phase': phase, 'start': start, 'end': end,
                      'quote': quote, 'quote_sha256': quote_sha})
    return {'version': 1, 'topic_id': TOPIC, 'title': row['title'],
            'source': {'file': SOURCE_PATH, 'file_sha256': SOURCE_SHA256,
                       'row': 215, 'pageid': 143592, 'text_sha256': TEXT_SHA256},
            'retrieval': {'aliases': ALIASES, 'cues': CUES},
            'entities': [{'id': 'char:wanderer', 'aliases': ALIASES[:3]},
                         {'id': 'char:nahida', 'aliases': ALIASES[3:]}],
            'timeline': spans, 'text': text,
            'coverage': {'source': 'complete_chapter', 'knowledge_base': 'partial',
                         'answerability': 'planner_must_assess'}}


class EvidenceStore:
    def __init__(self, *, kb_root: Path, bundle_path: Path, trusted_sha256: str):
        self.kb_root = Path(kb_root)
        self.bundle_path = Path(bundle_path)
        if not isinstance(trusted_sha256, str) or not HEX.fullmatch(trusted_sha256):
            raise ValueError('evidence_trust_missing')
        self.trusted_sha256 = trusted_sha256

    def load(self) -> dict:
        raw = read_safe(self.bundle_path)
        if digest(raw) != self.trusted_sha256:
            raise ValueError('bundle_changed')
        bundle = strict_json(raw)
        # Rebuild from pinned raw bytes, checking every field and span, not just
        # the declared hashes. No stale cached text can pass a changed source.
        if bundle != compile_topic(self.kb_root):
            raise ValueError('bundle_invalid')
        return bundle

    def query(self, question: str, *, context: str = '') -> dict:
        bundle = self.load()
        search = question + '\n' + context
        index = bundle['retrieval']
        named = any(name in search for name in index['aliases'])
        relevant = any(cue in question for cue in index['cues'])
        if bundle['title'] not in question and not (named and relevant):
            return {'status': 'miss', 'coverage': 'unknown', 'sources': [], 'topic_ids': []}
        # A matching chapter is useful partial KB evidence, never a claim that
        # every subquestion is covered. Do not gate final facts by claim IDs.
        return {'status': 'available', 'coverage': 'partial', 'freshness': 'fresh',
                'snapshot_id': self.trusted_sha256, 'topic_ids': [bundle['topic_id']],
                'sources': [bundle], 'question': question,
                'limitations': ['One local chapter; Planner must assess remaining scope.']}


def store_from_environment(environ) -> EvidenceStore:
    path = environ.get('LLM_WIKI_EVIDENCE_CONFIG', '')
    expected = environ.get('LLM_WIKI_EVIDENCE_CONFIG_SHA256', '')
    if not HEX.fullmatch(expected):
        raise ValueError('evidence_config_trust_missing')
    raw = read_safe(Path(path))
    if digest(raw) != expected:
        raise ValueError('evidence_config_changed')
    config = strict_json(raw)
    if set(config) != {'version', 'kb_root', 'bundle_path', 'bundle_sha256'} or config['version'] != 1:
        raise ValueError('evidence_config_invalid')
    return EvidenceStore(kb_root=Path(config['kb_root']), bundle_path=Path(config['bundle_path']),
                         trusted_sha256=config['bundle_sha256'])

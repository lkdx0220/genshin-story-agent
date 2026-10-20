"""Opt-in finite Wiki routing before legacy model nodes; no provider imports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .wiki_snapshot.question_entry import parse_question
from .wiki_snapshot.readable_answer import TextQuestionReader

_ENTITIES = ('char:wanderer', 'char:nahida')
_HEX64 = re.compile(r'[0-9a-f]{64}')


def _require(condition, reason):
    if not condition:
        raise ValueError('wiki_config_invalid:' + reason)


def _absolute(value):
    _require(isinstance(value,str) and bool(value), 'path')
    p=Path(value)
    _require(p.is_absolute() and '..' not in p.parts and '\x00' not in value, 'path')
    return p


def load_snapshot_config(path, trusted_sha256):
    """Capture selected configuration once; digest is an external caller input."""
    p=_absolute(path)
    _require(isinstance(trusted_sha256,str) and _HEX64.fullmatch(trusted_sha256), 'trusted_sha256')
    raw=p.read_bytes()
    _require(hashlib.sha256(raw).hexdigest()==trusted_sha256, 'hash_mismatch')
    obj=json.loads(raw.decode('utf-8'))
    _require(isinstance(obj,dict) and set(obj)=={'version','entities'} and type(obj['version']) is int and obj['version']==1, 'schema')
    entries=obj['entities']
    _require(isinstance(entries,dict) and set(entries)==set(_ENTITIES), 'entities')
    parsed={}
    for entity,spec in entries.items():
        _require(isinstance(spec,dict) and set(spec)=={'root','identity','trusted_sha256'}, 'entry')
        root=_absolute(spec['root'])
        identity=spec['identity']
        _require(isinstance(identity,str) and bool(identity) and '\x00' not in identity, 'identity')
        identity=Path(identity)
        _require('..' not in identity.parts, 'identity')
        if identity.is_absolute():
            _require(identity==root or root in identity.parents, 'identity')
        _require(not identity.drive or identity.is_absolute(), 'identity')
        digest=spec['trusted_sha256']
        _require(isinstance(digest,str) and _HEX64.fullmatch(digest), 'identity_sha256')
        parsed[entity]={'root':root,'identity':identity,'trusted_sha256':digest}
    return parsed


class WikiRouter:
    def __init__(self, snapshots):
        self._snapshots={entity:dict(spec) for entity,spec in snapshots.items()}
        self._readers={entity:TextQuestionReader(spec['root'],entity_id=entity) for entity,spec in self._snapshots.items()}

    def node(self, state):
        """Return partial state updates; only migrated grammar owns a query."""
        question=state.get('user_query')
        entity=next((e for e in _ENTITIES if parse_question(question,e)['status'] in {'matched','needs_condition'}),None)
        if entity is None:
            return {'wiki_route':{'route':'legacy','reason':'unmigrated_question'}}
        spec=self._snapshots[entity]
        response=self._readers[entity].query(question,identity_path=spec['identity'],trusted_identity_sha256=spec['trusted_sha256'])
        result=response['envelope']['result']
        # add_messages is the original app reducer; [] would retain old tools.
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
        return {'final_response':response['text'],'messages':[RemoveMessage(id=REMOVE_ALL_MESSAGES)],
                'execution_mode':'WIKI',
                'wiki_route':{'route':'wiki','entity_id':entity,'freshness_status':result['freshness_status'],
                              'answer_status':result['answer_status'],'reason_code':result.get('reason_code'),
                              'scope':'pinned_local_snapshot_deterministic_candidate'}}


def router_from_environment():
    mode=os.environ.get('LLM_WIKI_MODE','off')
    _require(mode in {'off','finite','agent_evidence'}, 'mode')
    if mode in {'off','agent_evidence'}:
        return None
    path=os.environ.get('LLM_WIKI_CONFIG')
    digest=os.environ.get('LLM_WIKI_CONFIG_SHA256')
    return WikiRouter(load_snapshot_config(path,digest))

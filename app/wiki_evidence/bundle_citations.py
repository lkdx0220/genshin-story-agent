"""Citations from a freshly verified local chapter, without tool identities."""
from __future__ import annotations

import os

from .canonical_local import stable_id
from .store import store_from_environment


def current_package(state):
    """State is a claim; reopen the configured source before trusting it."""
    package = state.get('wiki_evidence')
    if not isinstance(package, dict) or package.get('status') != 'available':
        return None
    history = state.get('conversation_history') or []
    context = history[-1].get('user', '') if history else ''
    current = store_from_environment(os.environ).query(state.get('user_query', ''), context=context)
    if current != package:
        raise ValueError('bundle_package_changed')
    return current


def _span_record(bundle, span, snapshot_id):
    source = bundle['source']
    provenance = {'file': source['file'], 'file_sha': source['file_sha256'],
                  'text_sha': source['text_sha256'], 'start': span['start'],
                  'end': span['end'], 'tool_call_id': None}
    identity = stable_id(source['file'], source['file_sha256'], source['row'],
                         source['pageid'], source['text_sha256'], span['start'],
                         span['end'], span['quote_sha256'])
    # Separate authorization domains even when canonical uses the same span.
    return {'local_citation_id': 'bundle:' + identity, 'source_kind': 'bundle',
            'snapshot_id': snapshot_id, 'binding_keys': [],
            'row_pageid': {'row': source['row'], 'pageid': source['pageid']},
            'quote': span['quote'], 'quote_sha': span['quote_sha256'],
            'status': 'bound', 'provenance': provenance}


def collect(state):
    """Return records/gaps/presence; a partial chapter stays partial."""
    if os.environ.get('LLM_WIKI_MODE', 'off') != 'agent_evidence':
        return [], [], False
    try:
        package = current_package(state)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return [], [{'code': 'bundle_source_changed', 'detail': str(exc)}], True
    if package is None:
        return [], [], False
    records = [_span_record(bundle, span, package['snapshot_id'])
               for bundle in package['sources'] for span in bundle['timeline']]
    return records, [{'code': 'bundle_partial',
                      'detail': 'One local chapter; remaining question scope must be assessed.'}], True

"""Diagnostic observations of existing checks, never an alternative validator."""
from contextvars import ContextVar
from functools import wraps
import re

from .diagnostics import (abort_standalone, active_capture, begin_standalone,
                          finish_standalone, snapshot)

_VALIDATION = ContextVar('local_evidence_validation', default=None)


def observe(**values):
    current = _VALIDATION.get()
    if current is not None:
        current.update(snapshot(values))


def evidence(info):
    observe(evidence_snapshot=info['entries'], evidence_status=info['status'],
            evidence_gaps=info['gaps'])


def boundary_rejection(reason, presentation):
    observe(rejected_citations=[{'marker': None, 'reason': reason, 'quote': None,
                                'quote_head': None, 'compared_entry': None,
                                'difference': presentation}])


def quote_comparisons(text, texts, names, question, corpus, normalized_question):
    from .bridge import _normalized
    from .canonical_bridge import quoted_spans
    quotes, balanced = quoted_spans(text)
    rejected = []
    comparisons = []
    for quote in quotes:
        fragments = re.split(r'[.．]{2,}|…+', quote)
        checks = [{'fragment': fragment, 'normalized': _normalized(fragment),
                   'in_corpus': _normalized(fragment) in corpus,
                   'in_question': _normalized(fragment) in normalized_question}
                  for fragment in fragments if _normalized(fragment)]
        exempt = len(quote) < 4 or _normalized(quote) in names
        failed = not exempt and any(not c['in_corpus'] and not c['in_question'] for c in checks)
        comparisons.append({'quote': quote, 'exempt': exempt, 'fragments': checks,
                            'failed': failed})
        if failed:
            rejected.append({'marker': None, 'reason': 'quote_not_in_evidence',
                             'quote_head': quote[:120], 'quote': quote,
                             'compared_entry': None, 'comparison_scope': 'global_corpus',
                             'difference': 'Normalized fragment absent from full corpus and question; no per-alias attribution',
                             'fragments': checks})
    if not balanced:
        rejected.insert(0, {'marker': None, 'reason': 'unbalanced_quotation',
                            'quote': text, 'quote_head': text[:120], 'compared_entry': None,
                            'difference': 'Quotation boundaries are incomplete'})
    observe(quotation_comparisons=comparisons, rejected_citations=rejected,
            quotation_balanced=balanced, source_texts=texts, source_names=sorted(names),
            comparison_corpus=corpus, comparison_question=normalized_question,
            original_question=question)


def citation_comparisons(text, info, supplied, used, rejection):
    reasons = {
        '[证据已变化] 本轮回答未展示，请重新查询。': 'evidence_changed',
        '[回答未通过来源校验] 脚注定义只能由程序生成。': 'model_defined_footnote',
        '[回答未通过来源校验] 本地引用不属于本轮证据。': 'citation_not_in_current_evidence',
        '[回答未通过来源校验] 回答未选择本轮本地引用。': 'no_current_citation_selected',
    }
    reason = reasons.get(rejection, 'no_current_evidence')
    compared = [{'marker': f'[^{alias}]', 'entry': supplied.get(alias),
                 'identity_known': alias in supplied} for alias in dict.fromkeys(used)]
    rejected = []
    if rejection is not None:
        implicated = [a for a in dict.fromkeys(used) if a not in supplied]
        if reason == 'model_defined_footnote':
            implicated = re.findall(r'^\s*\[\^([^\]]+)\]:', text, re.MULTILINE)
        rejected = [{'marker': f'[^{a}]' if a is not None else None,
                     'reason': reason, 'quote_head': None, 'quote': None,
                     'compared_entry': supplied.get(a),
                     'difference': rejection} for a in implicated or [None]]
    observe(citation_comparisons=compared, rejected_citations=rejected,
            accepted_citations=[f'[^{a}]' for a in dict.fromkeys(used)] if rejection is None else [],
            citation_check='executed', evidence_snapshot=info['entries'])


def _result(presentation, current):
    if isinstance(presentation, dict):
        # The child finalizer's exact input and evidence are authoritative.
        combined = dict(current, **presentation)
        combined['validation_input'] = current['validation_input']
        combined['raw_draft'] = current['raw_draft']
        return combined
    result = dict(current, presentation=presentation)
    if presentation.startswith('[回答未通过原文校验]'):
        result['verdict'] = 'reject_quote'
    elif presentation.startswith(('[回答未通过来源校验]', '[证据已变化]', '【未覆盖的部分】本轮尚未取得')):
        result['verdict'] = 'reject_source'
    else:
        result['verdict'] = 'pass'
    if result['verdict'] != 'pass' and not result['rejected_citations']:
        result['rejected_citations'] = [{'marker': None, 'reason': 'boundary_rejection',
                                         'quote': None, 'quote_head': None,
                                         'compared_entry': None, 'difference': presentation}]
    return result


def validation_boundary(name):
    def decorate(function):
        @wraps(function)
        def structured(text, state, *args, **kwargs):
            owner = begin_standalone(state)
            current = {'raw_draft': text, 'raw_draft_origin': 'validation_input_without_model_observation',
                       'validation_input': text, 'evidence_snapshot': [],
                       'accepted_citations': [], 'rejected_citations': [],
                       'finalize_input': text if name == 'finalize' else None,
                       'finalize_reached': name == 'finalize', 'citation_check': 'not_reached'}
            calls = active_capture().data['model_calls']
            if calls:
                selected = next((c for c in reversed(calls) if c['status'] == 'completed'), calls[-1])
                current.update(raw_draft=snapshot(selected['raw_draft']), raw_draft_origin=selected['call_id'])
            token = _VALIDATION.set(current)
            try:
                value = _result(function(text, state, *args, **kwargs), current)
                capture = active_capture()
                event = dict(value, boundary=name, run_id=capture.data['run_id'],
                             capture_id=capture.identity)
                if capture.data['model_calls']:
                    calls = capture.data['model_calls']
                    selected = next((c for c in reversed(calls) if c['status'] == 'completed'), calls[-1])
                    event.update(raw_draft=selected['raw_draft'], raw_draft_origin=selected['call_id'])
                capture.data['validation_events'].append(snapshot(event))
                return finish_standalone(owner, value)
            except BaseException as error:
                abort_standalone(owner, error)
                raise
            finally:
                _VALIDATION.reset(token)
        return structured
    return decorate

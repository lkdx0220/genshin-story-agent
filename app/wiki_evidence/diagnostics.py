"""Local, complete Answer-call evidence; no provider configuration is serialized."""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import json
import logging
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

_ACTIVE = ContextVar('answer_diagnostic_capture', default=None)
_LOG = logging.getLogger(__name__)
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[2] / '.local' / 'answer-diagnostics'


def snapshot(value):
    """Preserve message structure and full content, never stringify a whole object."""
    if hasattr(value, 'model_dump'):
        return deepcopy(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {key: snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [snapshot(item) for item in value]
    if isinstance(value, set):
        return sorted(snapshot(item) for item in value)
    return deepcopy(value)


def active_capture():
    return _ACTIVE.get()


class Capture:
    def __init__(self, state):
        self.identity = uuid4().hex
        self.lock = Lock()
        self.data = {
            'schema_version': 'answer-diagnostics-v1', 'capture_id': self.identity,
            'run_id': state.get('run_id') or self.identity,
            'observed_mode': os.environ.get('LLM_WIKI_MODE', 'off'),
            'model_calls': [], 'model_inputs': [], 'raw_draft': None,
            'raw_draft_origin': 'no_model_call', 'validation_events': [],
            'stages': [], 'evidence_snapshot': [], 'finalize_input': None,
            'finalize_reached': False, 'verdict': None, 'presentation': None,
            'accepted_citations': [], 'rejected_citations': [],
            'observation_boundary': 'application stream/invoke calls; SDK HTTP retries are not individual observations',
        }

    def start_call(self, model, method, messages):
        with self.lock:
            call = {'call_id': f'{self.identity}:{len(self.data["model_calls"]) + 1}',
                    'method': method, 'model_name': getattr(model, 'model_name', None),
                    'model_inputs': snapshot(messages), 'outputs': [],
                    'raw_draft': '', 'status': 'started'}
            self.data['model_calls'].append(call)
        return call

    def persist(self):
        directory = Path(os.environ.get('LLM_WIKI_DIAGNOSTICS_DIR') or DEFAULT_DIRECTORY)
        destination = directory / f'{self.identity}.json'
        events = directory / f'{self.identity}.validation.jsonl'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            # UUID file names, exclusive creation, and per-call event files avoid
            # shared append interleaving across processes and repeated run_ids.
            payload = json.dumps(snapshot(self.data), ensure_ascii=False, indent=2)
            with destination.open('x', encoding='utf-8') as stream:
                stream.write(payload)
            with events.open('x', encoding='utf-8') as stream:
                for event in self.data['validation_events']:
                    stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        except (OSError, TypeError, ValueError) as error:
            # Evidence storage must not become another model retry or replace
            # the original answer. Full in-memory data remains on node output.
            self.data['capture_error'] = {'type': type(error).__name__,
                                          'reason': 'diagnostic_persistence_failed'}
            _LOG.error('Answer diagnostic persistence failed (%s); capture_id=%s',
                       type(error).__name__, self.identity)
            return {'status': 'error', 'capture_id': self.identity,
                    'error': self.data['capture_error']}
        return {'status': 'written', 'capture_id': self.identity,
                'json_path': str(destination), 'jsonl_path': str(events)}


def record_stage(name, **values):
    capture = active_capture()
    if capture is not None:
        with capture.lock:
            capture.data['stages'].append(snapshot(dict(stage=name, **values)))


def _output_content(output):
    return output if isinstance(output, str) else getattr(output, 'content', '')


def _record_output(call, output):
    call['outputs'].append(snapshot(output))
    content = _output_content(output)
    # Structured/multimodal content remains an ordered list of content blocks.
    if isinstance(content, str) and isinstance(call['raw_draft'], str):
        call['raw_draft'] += content
    else:
        previous = call['raw_draft']
        if not isinstance(previous, list):
            previous = [previous] if previous else []
        call['raw_draft'] = previous + snapshot(content if isinstance(content, list) else [content])


class ObservedModel:
    """A call-site proxy: preserve the chosen model and original retry helper."""
    def __init__(self, model, capture):
        self.model = model
        self.capture = capture

    def __getattr__(self, name):
        return getattr(self.model, name)

    def invoke(self, messages, *args, **kwargs):
        call = self.capture.start_call(self.model, 'invoke', messages)
        try:
            response = self.model.invoke(messages, *args, **kwargs)
            _record_output(call, response)
            call['status'] = 'completed'
            return response
        except BaseException as error:
            call.update(status='error', error_type=type(error).__name__)
            raise

    def stream(self, messages, *args, **kwargs):
        call = self.capture.start_call(self.model, 'stream', messages)
        try:
            for chunk in self.model.stream(messages, *args, **kwargs):
                _record_output(call, chunk)
                yield chunk
            call['status'] = 'completed'
        except BaseException as error:
            call.update(status='error', error_type=type(error).__name__)
            raise


def observed_model(model):
    capture = active_capture()
    return ObservedModel(model, capture) if capture is not None else model


def complete_capture(capture, result):
    data = capture.data
    validation = result.get('answer_validation')
    if validation is not None:
        data.update(snapshot(validation))
    data['presentation'] = result.get('final_response', data['presentation'])
    calls = data['model_calls']
    data['model_inputs'] = [{'call_id': c['call_id'], 'messages': c['model_inputs']} for c in calls]
    # Do not call a program fallback or assembled L3 sections a model draft.
    if calls:
        selected = next((c for c in reversed(calls) if c['status'] == 'completed'), calls[-1])
        data['initial_raw_draft'] = calls[0]['raw_draft']
        data['raw_draft'] = selected['raw_draft']
        data['raw_draft_origin'] = selected['call_id']
        data['raw_draft_selection'] = 'last completed application call; all attempts/parallel sections retained separately'
        data['model_drafts'] = [{'call_id': c['call_id'], 'raw_draft': c['raw_draft'],
                                 'status': c['status']} for c in calls]
    else:
        data['raw_draft'] = None
    storage = capture.persist()
    return dict(result, raw_draft=snapshot(data['raw_draft']),
                answer_diagnostics=snapshot(data), diagnostic_storage=storage)


def capture_answer(function=None, *, stage='answer', only_enabled=False):
    if function is None:
        return lambda wrapped: capture_answer(wrapped, stage=stage, only_enabled=only_enabled)
    @wraps(function)
    def recorded(state, *args, **kwargs):
        if only_enabled and os.environ.get('LLM_WIKI_MODE', 'off') != 'agent_evidence':
            return function(state, *args, **kwargs)
        capture = Capture(state)
        capture.data['stage'] = stage
        token = _ACTIVE.set(capture)
        try:
            result = function(state, *args, **kwargs)
            return complete_capture(capture, result)
        except BaseException as error:
            capture.data.update(execution_error=type(error).__name__)
            capture.persist()
            raise
        finally:
            _ACTIVE.reset(token)
    return recorded


def begin_standalone(state):
    if active_capture() is not None:
        return None
    capture = Capture(state)
    return capture, _ACTIVE.set(capture)


def finish_standalone(owner, result):
    if owner is None:
        return result
    capture, token = owner
    try:
        capture.data.update(snapshot(result))
        result['diagnostic_storage'] = capture.persist()
        return result
    finally:
        _ACTIVE.reset(token)


def abort_standalone(owner, error):
    if owner is not None:
        capture, token = owner
        try:
            capture.data['execution_error'] = type(error).__name__
            capture.persist()
        finally:
            _ACTIVE.reset(token)

"""Per-attempt provenance for Planner calls, without model or credential config."""
import time
from uuid import uuid4


def _reported_model(response):
    metadata = getattr(response, 'response_metadata', None) or {}
    identity = metadata.get('model_name') or metadata.get('model')
    return identity if isinstance(identity, str) and identity else 'unknown'


def _error_reason(error):
    code = getattr(error, 'status_code', None)
    return type(error).__name__ + (f':{code}' if isinstance(code, int) else '')


def _attempt(target, messages, emit, fields):
    started = time.perf_counter()
    emit('llm_attempt_start', dict(fields, final_model='unknown'))
    try:
        response = target.invoke(messages)
    except Exception as error:
        emit('llm_attempt_end', dict(fields, final_model='unknown', status='error',
                                    error_reason=_error_reason(error),
                                    duration_seconds=time.perf_counter() - started))
        raise
    emit('llm_attempt_end', dict(fields, final_model=_reported_model(response), status='success',
                                duration_seconds=time.perf_counter() - started,
                                usage=getattr(response, 'usage_metadata', None)))
    return response


class _FallbackAttempt:
    def __init__(self, target, emit, fields):
        self.target = target
        self.emit = emit
        self.fields = fields

    def invoke(self, messages):
        self.fields['attempt_index'] += 1
        return _attempt(self.target, messages, self.emit, dict(self.fields))


def invoke_planner(target, messages, *, fallback, retry, emit, role, iteration, run_id):
    """Keep attempts separate and identify the response that actually completed."""
    call_id = uuid4().hex
    started = time.perf_counter()
    fields = {'call_id': call_id, 'run_id': run_id, 'role': role, 'iteration': iteration,
              'requested_model': getattr(target, 'model_name', 'unknown'),
              'attempt_index': 1, 'fallback_reason': None, 'attempt_scope': 'node_invoke'}
    emit('llm_start', dict(fields, model=fields['requested_model'], final_model='unknown'))
    try:
        response = _attempt(target, messages, emit, fields)
    except Exception as error:
        if fallback is None:
            emit('llm_end', dict(fields, status='error', final_model='unknown',
                                 duration_seconds=time.perf_counter() - started))
            raise
        fields = dict(fields, fallback_reason=_error_reason(error),
                      requested_model=getattr(fallback, 'model_name', 'unknown'))
        observer = _FallbackAttempt(fallback, emit, fields)
        try:
            response = retry(messages, llm_instance=observer)
        except Exception as fallback_error:
            emit('llm_end', dict(fields, status='error', final_model='unknown',
                                 duration_seconds=time.perf_counter() - started))
            raise error from fallback_error
    emit('llm_end', dict(fields, status='success', final_model=_reported_model(response),
                         model=_reported_model(response),
                         tool_call_count=len(getattr(response, 'tool_calls', None) or []),
                         duration_seconds=time.perf_counter() - started))
    return response

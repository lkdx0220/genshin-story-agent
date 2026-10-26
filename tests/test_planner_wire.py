"""Observe actual SDK request bodies, without a network or app startup."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_context import tool_response
from app.planner_transport import PlannerReasoningChatOpenAI


TOOL = {'type': 'function', 'function': {'name': 'planner_probe', 'description': 'Protocol probe',
        'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}}}}


def completion(reasoning='THINKING', *, native=False):
    message = {'role': 'assistant', 'content': '```json\n[{"tool":"planner_probe","args":{"value":"ok"}}]\n```'}
    if reasoning is not None:
        message['reasoning_content'] = reasoning
    if native:
        message['tool_calls'] = [{'id': 'native-1', 'type': 'function',
                                 'function': {'name': 'planner_probe', 'arguments': '{"value":"ok"}'}}]
    return {'id': 'response-id', 'object': 'chat.completion', 'created': 0, 'model': 'response-model',
            'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if native else 'stop'}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 5, 'total_tokens': 8}}


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('adapter', [False, True])
def test_actual_sdk_wire_negative_and_positive(tmp_path, adapter, native):
    requests = []
    def hook(request):
        requests.append(json.loads(request.content))
    def respond(request):
        return httpx.Response(200, json=completion(native=native))
    cls = PlannerReasoningChatOpenAI if adapter else ChatOpenAI
    with httpx.Client(transport=httpx.MockTransport(respond), event_hooks={'request': [hook]}) as client:
        model = cls(model='configured-model', api_key='offline-placeholder', base_url='https://offline.invalid/v1',
                    http_client=client, max_retries=0).bind_tools([TOOL])
        user = HumanMessage(content='PROBE')
        first = model.invoke([user])
        assert bool(first.additional_kwargs.get('reasoning_content')) == adapter
        calls = first.tool_calls if native else [{'id': 'json-1', 'name': 'planner_probe', 'args': {'value': 'ok'}}]
        converted = tool_response(first, calls)
        assert converted.id == first.id and converted.usage_metadata == first.usage_metadata
        assert converted.response_metadata['model_name'] == 'response-model'
        before = copy.deepcopy(converted)
        model.invoke([user, converted, ToolMessage(content='OK',tool_call_id=calls[0]['id'])])
        assert converted == before
    assistant = requests[1]['messages'][1]
    assert ('reasoning_content' in assistant) == adapter
    assert 'reasoning_content' not in requests[1]
    if adapter:
        assert assistant['reasoning_content'] == 'THINKING'
    assert assistant['tool_calls'][0]['id'] == calls[0]['id']
    evidence = {'adapter': adapter, 'native': native, 'top_level_keys': sorted(requests[1]),
                'messages': [{'role': message['role'], 'keys': sorted(message),
                              'reasoning_characters': len(message.get('reasoning_content','')),
                              'reasoning_sha256': hashlib.sha256(message.get('reasoning_content','').encode()).hexdigest()
                              if 'reasoning_content' in message else None}
                             for message in requests[1]['messages']]}
    (tmp_path/'wire-keys.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')


@pytest.mark.parametrize('reasoning',[None,''])
def test_ordinary_history_does_not_invent_reasoning(reasoning):
    model = PlannerReasoningChatOpenAI(model='offline', api_key='offline-placeholder')
    result = model._create_chat_result(completion(reasoning))
    message = result.generations[0].message
    payload = model._get_request_payload([HumanMessage(content='question'),AIMessage(content='ordinary'),message])
    assert 'reasoning_content' not in payload['messages'][1]
    assert ('reasoning_content' in payload['messages'][2]) == (reasoning is not None)
    if reasoning is not None:
        assert payload['messages'][2]['reasoning_content'] == reasoning


def test_response_choice_reasoning_matches_each_choice():
    model = PlannerReasoningChatOpenAI(model='offline', api_key='offline-placeholder')
    raw = completion('FIRST')
    second = copy.deepcopy(raw['choices'][0])
    second.update(index=1)
    second['message']['reasoning_content']='SECOND'
    raw['choices'].append(second)
    result = model._create_chat_result(raw)
    assert [generation.message.additional_kwargs['reasoning_content'] for generation in result.generations] == ['FIRST','SECOND']


def test_invalid_reasoning_is_explicit_and_streaming_is_rejected():
    model = PlannerReasoningChatOpenAI(model='offline', api_key='offline-placeholder')
    with pytest.raises(ValueError,match='planner_reasoning_type_invalid'):
        model._create_chat_result(completion({'unexpected':'object'}))
    with pytest.raises(ValueError,match='planner_reasoning_type_invalid'):
        model._get_request_payload([AIMessage(content='bad',additional_kwargs={'reasoning_content':42})])
    with pytest.raises(ValueError,match='planner_transport_requires_nonstream_chat_completions'):
        model._get_request_payload([HumanMessage(content='probe')],stream=True)


def test_responses_api_is_rejected():
    model = PlannerReasoningChatOpenAI(model='offline',api_key='offline-placeholder',use_responses_api=True)
    with pytest.raises(ValueError,match='planner_transport_requires_nonstream_chat_completions'):
        model._get_request_payload([HumanMessage(content='probe')])

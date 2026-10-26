"""Explicit reasoning transport for non-streaming Planner Chat Completions."""
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI


class PlannerReasoningChatOpenAI(ChatOpenAI):
    """Retain response reasoning and echo it on its original assistant message."""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, raw['choices'], strict=True):
            reasoning = choice['message'].get('reasoning_content')
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise ValueError('planner_reasoning_type_invalid')
                generation.message.additional_kwargs['reasoning_content'] = reasoning
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if payload.get('stream') or 'messages' not in payload:
            raise ValueError('planner_transport_requires_nonstream_chat_completions')
        for message, encoded in zip(messages, payload['messages'], strict=True):
            if isinstance(message, AIMessage) and 'reasoning_content' in message.additional_kwargs:
                reasoning = message.additional_kwargs['reasoning_content']
                if not isinstance(reasoning, str):
                    raise ValueError('planner_reasoning_type_invalid')
                encoded['reasoning_content'] = reasoning
        return payload

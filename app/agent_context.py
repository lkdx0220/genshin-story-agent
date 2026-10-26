"""Ephemeral model context; state contains only explicit request deltas."""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def history_context(state, recent_turns, *, include_summary=False):
    messages = []
    if include_summary and state.get('conversation_summary'):
        messages.append(SystemMessage(content='先前对话摘要（非事实来源）：' + state['conversation_summary']))
    for turn in (state.get('conversation_history') or [])[-recent_turns:]:
        messages.extend([HumanMessage(content=turn['user']), AIMessage(content=turn['assistant'])])
    return messages


def build_context(state, deltas, base_prompt, *, recent_turns, profile, pseudo_note=''):
    """Compose a fresh prefix using a named formatting profile, never detection."""
    if profile not in {'fast', 'plan', 'ae_continuation'}:
        raise ValueError('context_profile_invalid')
    for message in deltas:
        runtime_hint = isinstance(message, SystemMessage) and message.name == 'planner_runtime_hint'
        if not isinstance(message, (AIMessage, ToolMessage)) and not runtime_hint:
            raise ValueError('context_requires_request_deltas')
    prompt = base_prompt + (state.get('alias_notes') or '')
    if pseudo_note:
        prompt += '\n\n' + pseudo_note
    summary = state.get('conversation_summary') or ''
    if summary:
        prompt += _summary_suffix(summary, profile)
    content = prompt if profile == 'fast' else [
        {'type': 'text', 'text': prompt, 'cache_control': {'type': 'ephemeral'}}]
    prefix = [SystemMessage(content=content)]
    prefix.extend(history_context(state, recent_turns))
    prefix.append(HumanMessage(content=state.get('user_query', '')))
    return prefix + [message.model_copy(deep=True) for message in deltas]


def _summary_suffix(summary, profile):
    if profile == 'ae_continuation':
        return '\n\n先前对话摘要（非事实来源）：' + summary
    suffix = '\n\n## 之前的对话摘要\n' + summary
    if profile == 'plan':
        suffix += '\n（以上是之前对话的摘要，供你理解上下文。请注意：如果用户当前问题与摘要中的话题相关，请结合上下文回答；如果不相关，请忽略。）'
    return suffix


def tool_response(response, calls):
    """Replace the call representation without dropping response protocol fields."""
    return response.model_copy(deep=True, update={'tool_calls': calls})

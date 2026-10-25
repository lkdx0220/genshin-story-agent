"""Pre-model specialty routing and explicit regional statistics exclusions."""
from __future__ import annotations

import hashlib
import logging
import os
import re
from uuid import uuid4

from .catalog import COLLECTIBLES_SHA256, catalog_request, count_catalog
from .contracts import VERSIONS, assemble_result, canonical_bytes, new_result
from .story_cast import GRAPH_SHA256, story_result_for_query

REGION_TABLE_VERSION = 'specialty-region-alias-v1'
# input, exact source prefix, public spelling. Tuples keep the runtime table immutable.
REGION_MAPPINGS = (
    ('蒙德', '蒙德', '蒙德'), ('璃月', '璃月', '璃月'),
    ('稻妻', '稻妻', '稻妻'), ('须弥', '须弥', '须弥'),
    ('枫丹', '枫丹', '枫丹'), ('纳塔', '纳塔', '纳塔'),
    ('挪得卡莱', '挪德卡莱', '挪得卡莱'), ('挪德卡莱', '挪德卡莱', '挪得卡莱'),
    ('至冬', '至冬', '至冬'),
)
REGIONS = tuple(row[0] for row in REGION_MAPPINGS)
_LOGGER = logging.getLogger(__name__)
_COUNT = r'(?:一共|总共)?有(?:多少|几)(?:种|个)?(?:区域)?特产'
_PLACE = r'(?P<region>[\u4e00-\u9fff、和与及/]{0,24}?)'
_QUERIES = tuple(re.compile(pattern) for pattern in (
    _PLACE + _COUNT,
    _PLACE + r'(?:区域)?特产数量是多少',
    _PLACE + r'(?:有哪些|有什么|都有哪些)(?:区域)?特产',
    r'(?:列出|列举)' + _PLACE + r'(?:全部|所有)?(?:区域)?特产(?:名单|列表)?',
    _PLACE + _COUNT + r'[,，]分别是什么',
))
_COMPLEX_SCOPE = re.compile(r'目前|现在|版本|除了|除去|不含|相比|比较|更多|最少|能|可|用于|获得|采集|路线|之后|之前|仅|只')


def region_table_identity():
    """Hash the actual runtime table, not a copied digest constant."""
    mappings = [dict(zip(('input', 'source_prefix', 'public_region'), row, strict=True))
                for row in sorted(REGION_MAPPINGS)]
    raw = canonical_bytes({'version': REGION_TABLE_VERSION, 'mappings': mappings})
    return {'version': REGION_TABLE_VERSION, 'alias_table_sha256': hashlib.sha256(raw).hexdigest()}
_REGION = ('(?:挪[德得]卡莱|蒙德|璃月|稻妻|须弥|枫丹|纳塔|至冬|霜月|提瓦特|'
           '龙脊雪山|雪山|渊下宫|层岩巨渊(?:·地下矿区)?|沉玉谷|鹤观|清籁岛|'
           '海祇岛|神无冢|八酝岛|鸣神岛|踏鞴砂|旧日之海|远古圣山|空之神殿|'
           '须弥沙漠|沙漠|雨林|什么地区|哪些地区|哪个地区|这个地区|某地区|这里)'
           '(?:地区|区域|境内)?')
_CATEGORY = ('(?:世界任务|魔神任务|传说任务|委托任务|隐藏任务|活动任务|部族纪闻|伴月纪闻|'
             '任务|特色料理|特殊料理|料理|菜谱|食谱|菜肴|怪物|敌人|魔物)')
_ASK = r'(?:一共|总共|总计)?(?:有多少|有几|有哪些|有什么|都有哪些)(?:种|个|道|类)?'
_REGIONAL_REQUESTS = [re.compile(pattern) for pattern in (
    rf'(?:在)?(?P<region>{_REGION})(?:的|里|内|中)?{_ASK}(?:区域)?(?P<category>{_CATEGORY})(?:数量|名单|列表)?',
    rf'(?P<region>{_REGION})(?:的|里|内|中)?(?:全部|所有)?(?P<category>{_CATEGORY})(?:{_ASK}|数量|名单|列表)',
    rf'(?:列出|列举|统计|统计一下|给我|告诉我)(?P<region>{_REGION})(?:的|里|内|中)?(?:全部|所有)?(?P<category>{_CATEGORY})(?:数量|名单|列表)?',
    rf'{_ASK}(?P<category>{_CATEGORY})(?:在|分布在|位于)(?P<region>{_REGION})',
)]


def regional_statistics_refusal(query):
    """This policy result is internal routing state, not a count tool result."""
    if not isinstance(query, str):
        return None
    query = re.sub(r'\s+', '', query).strip('？?。！!')
    query = re.sub(r'^(?:请问|请|帮我)(?:一下)?', '', query)
    for pattern in _REGIONAL_REQUESTS:
        match = pattern.fullmatch(query)
        if not match:
            continue
        category = match['category']
        if category in {'怪物', '敌人', '魔物'}:
            category, reason = '怪物', '许多怪物跨地区分布，当前没有可靠的地区统计口径'
        elif category in {'料理', '特色料理', '特殊料理', '菜谱', '食谱', '菜肴'}:
            category, reason = '料理', '当前不按地区整理料理'
        else:
            category, reason = '任务', '任务地区元数据尚未补齐'
        return {'status': 'unsupported', 'policy': 'regional_statistics_exclusion',
                'category': category, 'region': match['region'], 'count': None,
                'members': [], 'reason': reason}
    return None


def _normalize_query(query):
    # Only inter-token whitespace is allowed: never join characters inside a name.
    query = re.sub(r'\s+', ' ', query).strip().rstrip('？?。！!').strip()
    query = re.sub(r'^(?:请问|请|帮我)\s*', '', query, count=1)
    if re.match(r'^(?:请问|请|帮我)', query):
        return None
    # Spaces can separate a complete place from its suffix/verb, not place syllables.
    for token in REGIONS + ('地区', '区域', '境内', '的', '一共', '总共', '有多少', '有几',
                            '种', '个', '特产', '有哪些', '有什么', '都有哪些', '列出', '列举'):
        query = query.replace(token + ' ', token).replace(' ' + token, token)
    return query


def parse_statistics_query(query):
    if not isinstance(query, str):
        return None
    query = _normalize_query(query)
    if query is None:
        return None
    if query in {'本库全部特产有哪些', '本库全部地区有多少特产'}:
        return catalog_request()
    match = next((m for p in _QUERIES if (m := p.fullmatch(query))), None)
    if not match:
        return None
    return _region_request(re.sub(r'(?:地区|区域|境内)?的?$', '', match['region']))


def _region_request(region):
    if region in {'本库', '本库一共', '本库全部', '本库全部地区'}:
        return catalog_request()
    # The optional suffix removal above maps 本库全部地区 to 本库全部.
    if _COMPLEX_SCOPE.search(region):
        return None
    if region in REGIONS:
        return catalog_request(next(row[1] for row in REGION_MAPPINGS if row[0] == region))
    if not region or region in {'这里', '这个', '这个地区'}:
        return {'status': 'missing'}
    pieces = re.split(r'[、和与及/]', region)
    if len(pieces) > 1 and all(p in REGIONS for p in pieces):
        return {'status': 'ambiguous', 'regions': sorted(set(pieces))}
    return {'status': 'unsupported', 'region': region}


def _clarification(request, query):
    if request['status'] == 'missing':
        return assemble_result(new_result(catalog_request(), gaps=['请明确地区，或明确选择本库全部地区']))
    result = new_result(catalog_request(), status=request['status'])
    if request['status'] == 'ambiguous':
        result['ambiguities'] = [{'input': query, 'candidates': request['regions'],
                                 'reason': '不自动合并多个地区，请指定单一地区'}]
    else:
        result['unsupported_reason'] = '请指定蒙德、璃月、稻妻、须弥、枫丹、纳塔、挪得卡莱或至冬；暂不统计子地区'
    return assemble_result(result)


def _audit(state, route, reason, result=None):
    """Internal side channel only; no query, credentials or schema additions."""
    sources = (result or {}).get('source_snapshots', [])
    event = dict(region_table_identity(), request_id=state.get('run_id') or uuid4().hex,
                 mode=os.environ.get('LLM_WIKI_MODE', 'off'), route=route, reason=reason,
                 source_sha256=COLLECTIBLES_SHA256, source_identity_basis='configured_trusted_pin',
                 observed_source_sha256=[s['sha256'] for s in sources],
                 normalizer='specialty-finite-language-v1', serialization=VERSIONS['serialization'],
                 contract_version='count_result_v1.1')
    if result is not None and result.get('tool') == 'count_story_cast':
        event.update(source_sha256=GRAPH_SHA256, source_identity_basis='pinned_graph_plus_exact_projection',
                     normalizer='story-cast-finite-language-v1', contract_version=result['contract_version'],
                     cast_alias_snapshot=result['alias_snapshot'])
    _LOGGER.info('statistics_route %s', canonical_bytes(event).decode('utf-8').rstrip('\n'),
                 extra={'statistics_route': event})


def statistics_gate(state):
    """Ignore all prior count_result/final_response when deciding this request."""
    refusal = regional_statistics_refusal(state.get('user_query'))
    if refusal is not None:
        _audit(state, 'direct', 'regional_statistics_exclusion')
        return {'count_result': refusal, 'execution_mode': 'STATISTICS',
                'final_response': f'暂不支持按地区统计或列举{refusal["category"]}：{refusal["reason"]}。'}
    story_result = story_result_for_query(state.get('user_query'))
    if story_result is not None:
        return _direct_result(state, story_result)
    request = parse_statistics_query(state.get('user_query'))
    if request is None:
        _audit(state, 'fallback', 'no_full_match')
        return {'count_result': None}
    if 'status' in request:
        result = _clarification(request, state['user_query'])
    else:
        result = count_catalog(request)
    return _direct_result(state, result)


def _direct_result(state, result):
    # Local import avoids a circular import at module initialization.
    from app.agent.nodes import _get_direct_metadata_answer
    answer = _get_direct_metadata_answer([], state['user_query'], count_result=result)
    _audit(state, 'direct', result['status'], result)
    return {'count_result': result, 'final_response': answer, 'execution_mode': 'STATISTICS'}

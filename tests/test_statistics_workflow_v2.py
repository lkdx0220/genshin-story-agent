"""scope=v2: original graph/metadata consumer; external calls fail on invocation."""
import hashlib

import pytest

from app.statistics.contracts import canonical_bytes
from app.statistics.routing import statistics_gate
from test_statistics_workflow import _scl_run_pair
from test_statistics_workflow import test_legacy_consumers as legacy_consumers


pytest_plugins = ('test_statistics_workflow',)

def _cast_bound_runtime(tmp_path, monkeypatch, status):
    """Substitute the pinned source root only; actual gate/consumer/graph remain."""
    import app.statistics.routing as routing
    from app.statistics.story_cast import story_result_for_query
    folder=tmp_path/'cast-source';(folder/'kb_vectors').mkdir(parents=True)
    entry={'entry_id':'cast-1','title':'剧情测试','entry_type':'activity','cast':['甲'],
           'cast_source':'metadata','cast_scope':'activity','story_text':'','full_text':'',
           'metadata':{'出场人物':['甲']},'coverage':{'scope':{'scope_level':'activity','scope_id':'cast-1'},
           'complete':True,'field_parse_ok':True}}
    if status=='partial':entry.pop('coverage')
    if status=='missing':entry.update(cast=[]);entry.pop('coverage')
    if status=='error':entry['cast']=123
    entries=[entry]
    if status=='ambiguous':entries.append(dict(entry,entry_id='cast-2'))
    graph=canonical_bytes({'entries':entries})
    aliases=canonical_bytes({'version':'test','canonical_to_variants':{'甲':[]},'variant_to_canonical':{},'conflict_notes':{}})
    (folder/'kb_vectors/wiki_entry_graph.json').write_bytes(graph)
    (folder/'kb_vectors/entity_alias_map.json').write_bytes(aliases)
    def bound(query):
        return story_result_for_query(query,root=folder,graph_sha256=hashlib.sha256(graph).hexdigest(),alias_sha256=hashlib.sha256(aliases).hexdigest())
    monkeypatch.setattr(routing,'story_result_for_query',bound)


def test_c04_scope_and_fallback(runtime):
    from app.statistics.story_cast import parse_story_query
    query='《海灯节》第二幕里只算璃月可玩角色'
    state={'user_query':query,'count_result':{'count':999},'final_response':'old'}
    assert parse_story_query(query) is None
    assert statistics_gate(state)=={'count_result':None}
    assert state['user_query']==query
    result=statistics_gate({'user_query':'这个活动有多少角色参与'})['count_result']
    assert result['status']=='missing' and result['count'] is None


@pytest.mark.parametrize('status',['complete','partial','missing','ambiguous','error'])
def test_c15_real_workflow_direct(runtime,tmp_path,monkeypatch,status):
    _cast_bound_runtime(tmp_path,monkeypatch,status)
    result=runtime.workflow.create_agent_workflow().invoke({'user_query':'《剧情测试》有多少角色参与','run_id':'cast-current'})
    assert result['count_result']['status']==status
    assert result['final_response']==result['count_result']['answer_text_hint']
    assert runtime.counters['model_calls']==runtime.counters['tool_calls']==runtime.counters['network_calls']==0


def test_c16_injection_and_replay(runtime,tmp_path,monkeypatch):
    _cast_bound_runtime(tmp_path,monkeypatch,'partial')
    old={'count':999,'answer_text_hint':'unsafe'}
    final=runtime.workflow.create_agent_workflow().invoke({'user_query':'《剧情测试》有多少角色参与','count_result':old,'final_response':'unsafe'})
    assert final['count_result']['count']==1 and final['final_response']!='unsafe'
    # Existing byte/message replay harness uses this tree and the frozen baseline.
    _scl_run_pair(tmp_path,runtime.mode,'第二幕里只算璃月可玩角色的海灯节人物')


def test_c18_specialty_compatibility(runtime):
    result=runtime.workflow.create_agent_workflow().invoke({'user_query':'须弥有多少特产'})
    assert result['count_result']['contract_version']=='count_result_v1.1'
    assert result['count_result']['count']==9
    assert result['final_response']==result['count_result']['answer_text_hint']
    assert runtime.counters['model_calls']==runtime.counters['tool_calls']==runtime.counters['network_calls']==0


def test_c20_http_and_consumers(runtime,tmp_path):
    # Offline old-consumer substep only. Live HTTP/SSE remains unverified.
    legacy_consumers(runtime,tmp_path)

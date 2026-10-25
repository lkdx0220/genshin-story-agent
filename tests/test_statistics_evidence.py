"""Per-person scope, authoritative identity and adversarial speaker evidence."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import zlib

import jsonschema
import pytest

from app.entity_retrieval import story_body
from app.statistics.contracts import Snapshot, canonical_bytes, resolve_pointer
from app.statistics.evidence import AliasSnapshot, member_evidence

ROOT = Path(__file__).resolve().parents[1]
_FROZEN_SCHEMAS = {
    'evidence_contract_v1.schema.json': (
        'c-qxiOH<o05WX|RfADxX6vmK*BR%xeOJ_RM-kIPL_9hXLC0A0?rqulQ%1-={^{{!haA1JddbQ7QcXfY!6a?XkNLG-c@G1ybQs&}n'
        'GPx6sp6|&Qj4vlCM+-T*oL)}PXP1)$e^h-fu_RD_`?=%^wzG>_kU@3}Jb*1ufhO=V5Q?8loCFEm0!K>-l!#<40z?E0IQ-aP4rwiM'
        'msbMW?HwfYK+HMIflCZR9o?5CMXWD~6GkORiHwzWff;Ri6qh;_vJ_}xSK5Yq#v-bPs<T~;Q%y##laL(KWf(OF8Kz%>F6By{&U%s('
        'L>a{iBJtiK&zC3?R()lP={l=AusN)i5fj(~(N8MdH<=<{cZrIuT!pt-?ppEffkBf<tivvC-u_cWh9bJ7y{>V&r;=R%*<KMgd8JvT'
        'Il!t<O`Er(P?}<?5(|;<UB50={z5NCDJ0m)LDR7zrU}`kps4`AOxlL7Df>w^no8gW!OHoKs{JIcQ+<tTI;2Sk5~WBY-5WWeHSm~V'
        's!WYEc3Qe3W|U|d(WFU?=?XZObwxLk6QUQ-#*mUeXbWKpl6}wC$$~gX9d$x=eb!HXSFw1?rSt^b@Whs=1F7mxR=p4uUx~MI&M=ia'
        'N{qWDM^b%Px+T2%@abxzey(pGCi6cJ*VEbj^z8KPB=lUK5I_{tSmcOWE=P3tZDE2!&}Y;%r5i%R{&^8SMuZup5y5JMCJ~pe)6OlW'
        '%a_XOk%Lb~5NB1zOl=&6^*dE$>3_SMGM*uVf2-QHbVG+K_RJd2V*`Hu>Nz0Uh@0E1qir_%<~D_ff!%53T&pQZlXDr|K@|@acnY;W'
        'nQIt4!k$7KHohEya@vDk4oEn#_Hk3tVdNV(w)pfP*V*;{f`p9suI}}nEGHm!;sOIn;~a4TF<aZl0RK?s9As=z<^?g|kmecGyu+Gn'
        'V6zWxMuM9K?Hu4nA&U=F3QxCkBZzCTvk!MBB8<d>!j)f+=o?zvo4C<2U1M{Tx~^=yWvm2>(D8jQ%O4@7!H+|Qm%sL+Em83-XU3-G'
        'T$uE|$C$9~)2@4N>_MA>Q04#ooEXNT5E0Uyi&|{Miyk<5W=q?$Fi_&Q#i6%8ykwthH0(4SOCymhpySPUt|vpb+g26p!s2s5WY@*E'
        'UYKoi=JIC0I-*y~i}pFg67Ba9-o}I`obSe|&-RUN92&LOIDx-sg5(<*-n<6dz|&-Uy5Z=3!@p}3zCq!&^5)5C<$OaKcu5)cp5lI0'
        '@w}}JbcDB`-D4KB;?1Cm@lN;R4j&D*u59>6-LM+1j*ryuzhAm-tN'
    ),
}
_SCHEMA_PINS = {'evidence_contract_v1.schema.json':
    '8ac6551d35d6be717c4cf2235cc3f554c8501fd9a35e5abae5aba8e93fdf2d90'}


def schema_check(value, filename):
    raw = zlib.decompress(base64.b85decode(_FROZEN_SCHEMAS[filename]))
    if hashlib.sha256(raw).hexdigest() != _SCHEMA_PINS[filename]:
        raise ValueError('frozen_schema_sha_mismatch')
    jsonschema.Draft202012Validator(json.loads(raw)).validate(value)


def write_snapshot(root, data, path):
    raw = canonical_bytes(data)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return Snapshot.read(root, path, hashlib.sha256(raw).hexdigest())

SCOPE = {'scope_level': 'task', 'scope_id': 't1'}


@pytest.fixture
def aliases():
    return AliasSnapshot.read(ROOT)


def entry(**kwargs):
    return dict({'entry_id': 't1', 'entry_type': 'task', 'cast_scope': 'task',
                 'cast': [], 'cast_source': None, 'story_text': ''}, **kwargs)


def complete(**kwargs):
    return entry(cast=['派蒙'], cast_source='metadata', metadata={'出场人物': ['派蒙']},
                 coverage={'scope': SCOPE, 'complete': True, 'field_parse_ok': True}, **kwargs)


def evaluate(tmp_path, aliases, value, who='纳西妲', scope=None, canonical_id=None):
    source = write_snapshot(tmp_path, {'entries': [value]}, 'synthetic/story.json')
    verdict = member_evidence(value, who, scope=scope or SCOPE, source=source,
                              entry_pointer='/entries/0', aliases=aliases, canonical_id=canonical_id)
    schema_check(verdict, 'evidence_contract_v1.schema.json')
    assert set(verdict['coverage']) == {'scope', 'cast_source', 'complete', 'field_parse_ok'}
    for loc in verdict['evidence_location']:
        resolve_pointer(source.data(), loc['json_pointer'])
        if loc['cleaned_span']:
            span = loc['cleaned_span']
            assert story_body(value)[span['start']:span['end']].startswith(('纳西妲：', '小吉祥草王：'))
    return verdict


@pytest.mark.parametrize('case', ['metadata', 'speaker', 'mention', 'inherited', 'exclude', 'conflict', 'missing'])
def test_seven_situations(tmp_path, aliases, case):
    values = {
        'metadata': (entry(cast=['纳西妲'], cast_source='metadata'), 'include', 'metadata'),
        'speaker': (entry(story_text='纳西妲：你好。'), 'include', 'speaker_lines'),
        'mention': (entry(story_text='派蒙：我听说纳西妲去过那里。'), 'unknown', 'mention_only'),
        'inherited': (entry(cast=['纳西妲'], cast_source='metadata+activity_inherited'), 'unknown', 'activity_inherited'),
        'exclude': (complete(), 'exclude', 'metadata'),
        'conflict': (complete(story_text='纳西妲：我在这里。'), 'conflict', 'conflicting'),
        'missing': (entry(), 'unknown', 'missing'),
    }
    value, decision, kind = values[case]
    verdict = evaluate(tmp_path, aliases, value)
    assert (verdict['decision'], verdict['evidence_kind']) == (decision, kind)
    # Each situation also gets a counterexample: change the actual scope.
    negative = evaluate(tmp_path, aliases, value, scope={'scope_level': 'task', 'scope_id': 'another'})
    assert negative['decision'] == 'unknown' and not negative['coverage']['complete']


@pytest.mark.parametrize('body', [
    '[任务概述]\n纳西妲：上章的对白。', '[前情提要]\n纳西妲：过去的事。',
    '出场人物：纳西妲', '派蒙：纳西妲说过这句话。', '大纳西妲：同名片段。',
    '> 纳西妲：引用行。', '“\n纳西妲：被转述的对白。\n”',
    '```\n纳西妲：代码引用。\n```', '纳西·妲：不能去中点猜人。',
])
def test_pollution_and_exact_identity(tmp_path, aliases, body):
    result = evaluate(tmp_path, aliases, entry(story_text=body))
    assert result['decision'] == 'unknown' and result['evidence_kind'] != 'speaker_lines'
    positive = evaluate(tmp_path, aliases, entry(story_text='小吉祥草王：真实对白。'))
    assert positive['decision'] == 'include' and positive['evidence_kind'] == 'speaker_lines'


def test_complete_exclusion(tmp_path, aliases):
    result = evaluate(tmp_path, aliases, complete())
    assert result['decision'] == 'exclude' and result['coverage']['complete'] is True
    assert result['coverage']['field_parse_ok'] is True and '游戏中不存在' in result['reason']
    pointers = {loc['json_pointer'] for loc in result['evidence_location']}
    assert {'/entries/0/metadata/出场人物', '/entries/0/coverage'} <= pointers
    assert evaluate(tmp_path, aliases, complete(), who='派蒙')['decision'] == 'include'


@pytest.mark.parametrize('case', ['flag_missing', 'flag_false', 'notmetadata', 'speaker_source', 'inherited',
                                  'empty', 'raw_field_missing', 'unknown_identity', 'wrong_scope', 'act',
                                  'self_report_only', 'absent', 'placeholder'])
def test_no_unproven_exclusion(tmp_path, aliases, case):
    value, scope = complete(), SCOPE
    if case == 'flag_missing':
        del value['coverage']['field_parse_ok']
    elif case == 'flag_false':
        value['coverage']['field_parse_ok'] = False
    elif case in {'notmetadata', 'speaker_source', 'inherited'}:
        value['cast_source'] = {'notmetadata': 'notmetadata', 'speaker_source': 'speaker_lines',
                                'inherited': 'metadata+activity_inherited'}[case]
    elif case == 'empty':
        value['cast'] = value['metadata']['出场人物'] = []
    elif case == 'raw_field_missing':
        del value['metadata']
    elif case == 'unknown_identity':
        value['cast'] = value['metadata']['出场人物'] = ['未登记人物']
    elif case == 'wrong_scope':
        scope = {'scope_level': 'activity', 'scope_id': 't1'}
    elif case == 'act':
        scope = {'scope_level': 'act', 'scope_id': 't1'}
    elif case == 'self_report_only':
        del value['metadata']
    else:
        value['cast_absent' if case == 'absent' else 'story_placeholder'] = True
    result = evaluate(tmp_path, aliases, value, scope=scope)
    assert result['decision'] == 'unknown' and not result['coverage']['complete']


def test_alias_conflicts_and_missing(tmp_path, aliases):
    assert aliases.identity()['sha256'] == aliases.snapshot.sha256
    assert aliases.identity()['version'] and aliases.identity()['normalizer_version']
    for raw in ['旅行者', '水神', '纳西', '纳西·妲', '未登记人物']:
        result = evaluate(tmp_path, aliases, complete(), who=raw)
        assert result['decision'] == 'unknown' and result['evidence_kind'] == 'missing'
    assert aliases.normalize('小吉祥草王')[0] == '纳西妲'
    assert aliases.normalize('水神')[0] is None
    changed = deepcopy(aliases.snapshot.data())
    changed['variant_to_canonical']['新别名'] = '纳西妲'
    newer = AliasSnapshot(write_snapshot(tmp_path, changed, 'kb_vectors/entity_alias_map.json'))
    assert newer.identity()['sha256'] != aliases.identity()['sha256']
    assert newer.normalize('新别名')[0] == '纳西妲' and aliases.normalize('新别名')[0] is None
    value = complete()
    source = write_snapshot(tmp_path, [value], 'synthetic/story.json')
    with pytest.raises(ValueError, match='entry_source_mismatch'):
        member_evidence(entry(), '纳西妲', scope=SCOPE, source=source, entry_pointer='/0', aliases=aliases)

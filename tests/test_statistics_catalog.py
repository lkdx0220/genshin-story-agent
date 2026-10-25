"""Frozen LS01/04/05/06/08/09 scenarios; local fixtures are not game data."""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib

import jsonschema
import pytest

from app.statistics.catalog import COLLECTIBLES_SHA256, SOURCE_PATH, catalog_request, count_catalog
from app.statistics.contracts import (Snapshot, assemble_result, canonical_bytes, evidence_location,
                                     json_pointer, new_result, resolve_pointer, validate_result)

ROOT = Path(__file__).resolve().parents[1]
# Frozen schema bytes are embedded so the tests travel with the repository.
_FROZEN_SCHEMAS = {
    'count_result_v1.schema.json': (
        'c-rk9$&Ta3^&TMqAP7Fhjx@a_F_O9Dl1qR9$-Sc?s>xET+!RTZMfJ2NGJH&aAcw$5BOl@K$%6bvii@~Zy;ln*s?{?zV9X3zD!zL4'
        '_ND5>Pk%C-%|FZh4c(IY+u3}>d6K=oy80@Mqh<B@O`JYl1u0qctJ~|_>*dYuRXu)D{+cr$(&D$jE5^)rH*aocew;^qmeMQ_`Rs}D'
        'jd&8RL+0~rA^y6fDS4nX5(Tp@;bOAjFxPWZP8HvOMSWgBOwu@^DQ7ejuRauy#6vM3Pl?YxF;~XosC`wv%y=<R35Qp~;T4e0t1<gU'
        'Jq?fJu=i6M<y-OK?(krCn8YWXgz>|Cp}fg>oIZ>983$ha8&ZHt({fm`hQpAR1WHQ3&S?pD3r!r+Kd$@Wd}#lQAgp-_A+G(UwDJzz'
        'J>(tM-5<AKNYfpLK{Kj2ZC+&w^%)8I^LzokeTvgC@G7)G?^E$(v3~i@$^h$@@|5|mpfXOkUWpSBmW&p=TMuxBXw5=FA{hvhZi|iE'
        'T%+kh;t=QqchJjXVFFpv1GMJyb(DwU9D27>AMPQ<)t`TH*yKkluqBJKR7fkBLTWsti&UsW{Q?OB#>FHg{8M*V*CfoSL_^(w_F>h)'
        'S+PCFya=OU)Y^UAUyJ2Bx?rDMu63lgfjOzrG|wIjwA_hN;(W`ZKhWrbZ^Wn@!<RB&EX)h(j)tz%N$421V~89jJ}k31$sUce>V$OG'
        'U{bg6S4~y8x<_@<_TD7<NtzO1V@r*M$$XR6u+SUD29qc3odxZiIRIA+rBsUIi1{Q$c+W~j7?p%hO+X^rdQq0@Pp?zzJ+MHKI${Ni'
        '0&4{WYCy5<-`7<4DutO0WPt+05$i|?VnPJ!g!Nhd7=njIg_0D^R}46#kUdBg-G1O`7$|+EkzpviEs8L%aI{9mmxXN7Zk;Th``cy9'
        'uNDl!H#8c)295MTj3Dn3hI0U0&jc^HEm|JA<XvgS;O(h)rN?<4YX*xKWSsCkGY2oWNk}>Eg(kkCY215IvIptO1+m5~ll11CZ134a'
        '9_KyjGS8zdPm(z0v}f7PX`03<8t<q)<u3TDCNBx(fu_A_d+NHCQN^aPZHa(u=u_T3$(Rve^nDRjVv(0cB*`{$Kc*ALPr|gj7t{4u'
        '8pYfrJHkS8A5wJ1dBng4D;5f5T{N>qak?cT`-TQyDbm3iWwQ?@d!3p_^dIUjVrm;w*{Zr;!4`ZCplSkY%}>le>&?sp;gAS;Rm#w<'
        'UhtjRWYCMLNyba!Qm|PqFNu>pECnV4#?i~H!V=y#4yf%KOL|U{P#Bj5K`VY-$s}2vr#|&|B+RMRQahB3h*+J=gSk5fHw3rT_X;Wx'
        'GX1*$M)2*|IX%4OX-)z0+|GtrY?uMo61avowG@MGwgLBm2sL{LjTjQ}#R6qSxVcA4ARhC=g^6l{!e~OKQ9w1o-rP#r=f0Xy=(I>!'
        'cxy((;N@YdweY2YQ&?_-G%Mo1gl!@@-^3{swh-G<FA$pP<pIm1lxA_by96X1@@p>wh`nr-WdOpCIA<V4YC|u`)<IZ2PGkZ~4^w!M'
        'veU%$G+s^pRWOEjD%BaVED6c87m+O;?<;gyC`G?el{zy3jF#fD($hxpyYVskoz9Ht`G1o228Vw-`>NRt4F=mctDcKz5g^?6Je9_3'
        'lE@v~uR%1g?hd>!2x#q7Y!?);&&;MBY>mf4Szf|4>Ng%)1kH}cQV-swD#!bDzN2Ztd^uUS(f|_1erY9-AuNU`<7=-8Pia`CRZRH@'
        'Fx5~L^)$ZeQD0987jqOLw)wlC{r2sZ`2X(Rw^ysbe0z6&v-<pt&%gL=ZV8khQW6!hjD+AN3M6`l0?KFvQ(28B#j{@wofOZlS;rdN'
        'm_o|+V>;hOj5L)(RFlkh@iof^7Y9@0$EHx_*AZz-+q@nc;57kn%=GnW!L%=qIKoD+aBy8Rg&L%cQ)<&nQKO(U3Z2uI<>)LzUG%Ba'
        'a>C2-A1_N`HHL|<<PuKr06%SOFpZi)S9de6qHGiE)wY};0wuEVtbnYPk10uSkqNg-+MAO1`Xs(al;}h*nmHJx9c(ni-l4k1tPOM-'
        'dv%SV?1ew=CfPyy45-hvGC)r-!d&pfJfP^Z=zIP9JbH}crwDz2C^tW<GPMz3uuEgN(k<mg4m0(4Frnm8i2jg8G{cLiigbX`YH<{z'
        '9M-U)$D$2QnUs%}s9DMGf(R_eoCJplZ_FsvkZL!*o^!IyE$wo%tLAKA+L@Oc8`8#wbBAh}J7)ye#<jC^@C+WAy|ZWbFzi2La{nBi'
        'Ko?hVi1X#*g4v+S)<wJ+SlwO60S=^_8)<MR?UrWgSlYUmc21_dt7&pL2fLj{B(6}|<!DgaAX@L5S~{pUZmJ%i&UoCT&WjY^H>&)U'
        'lFUNSpWbV$`wEY9o2;u`oh^CX)*=}P5}(rI)H=T2fHHjUB_z$L7eBhV1NM%hl_Pb+sHmNDYU!X_JE=B~s@Yl93LZm{)5dXqm7z_u'
        'Lu+<wPan3l;Y$n3*9UctM!42$bq#$q;=+bw@8h^oG>-WZS>l}FODC)VH^h(epr$qwX%#HQ<9XUhia7#d=U;17vQ}#z83qSN!(AY#'
        'i-oowxcKa*z?6f!Fa@ckP}QZ_pA7~OgVDkG!`moaw!|uo=CUuQkQx|R8?rqM^O{UyEkhQ_h8m9OE_`u5Yg0i6E}2*XMO{Pg6X6`Y'
        '_%Zj^<bFDb1FeiW30-nkp<67;!K6FwcP%aLLGd{ISloVyQ}mj6@8p`;DtG0O!E(-H*=4gVb6NwfwNF#h921b;f0CL^o0GmM0vlwe'
        '*l^b?v#6a97DIyN&b9sBVsNyA8z{TR3ZQkhW~283)CrM8ckH?QUo=_#Q<c0Yq#WWndpGNT_wMc0pT&QxpBMS}_Fr=5U9RWfklSB<'
        'dE}?Bm%ky)_3FcyzuZe7b0u3O^;O{qgnV@3BOxPM+sxl3J)q_QZbp_Ovtyhu1yfL0yguH0J27dTW><A*aJ958N!}FcMAJc27^&EF'
        'K-rNC@KH^k{j_~ocDenoS1D}FWr@M%POeBDcp8sN<t^GV#8|B3`18>4^C+5gJ3KXM3mwM+bYW>u<3NwFsE!d~GB2D#8L%kH`FM)J'
        'CsDux!s*A@peQo}oaG;mN>^=D?IpSaAh@IJn+Kt2Ub@*)=C&v+POaheJ@+;&I>8Y%DUAX#V-gE{{~11cIS^Y`W!3J54Z4-jU9N>&'
        'cI43!P_L#W=uIsMd-cp_bzQQjBStN&QOojV?4@qf;$+}Lrh{HhJ8M=7;$?aAcFxzoUWV(wYI10&Z}5UH4hQhK9{1*)?=b|sW&TlP'
        'pkm$=ke`*@Bo}d_^G43lfs54Y7%FrO!2xqMjy7aysJy}JZLlhvl|>PNzK+Hsj;xOz8_w0!=BV_wFjxi5N;z4}vfAaO-DWx)V(LVj'
        'I?;wi9j8vTsS|DLM4L^WXwF%?sS|BTW_;>I!}P+`iPi-BrcShRV4XVAW>Y7cRi=9CL^CGWH7bAVL_^%UsT0jfAx@oWQzzQgi8d_s'
        'F?FI%ooK2`o;uN7)Wy_^Hg%#+ooL7L+0=<Pm=&f@w5bzq>O?y}9yfKOji-sHPPC~L?UUuEsS|DXiFcyCRH1fLl3JI67JpRV2khcG'
        '7UJk#WL*A`W=+uZaM5Z1-caaj&)z+o=yjRU1$OccP2d$a$^|iKgB#L(r0KhWIwN`|<!CWCT$8QTS*!KA#e*ucVveTB5tO#FR~BE|'
        '+E?YqT7!niwJCfPZT$|pkM{7f-mSC%JMHx)*Q#LCty#I{R$@LS)+%?Dz*pDGA`iZR7MKsc+qiqv^!4ytxX@WNLSu1WqQVnc)y2q{'
        'LK@gzd`K<AFv3yqJJ!r8H@95eo_9=udXLY(d+qMrTnH`{qCN{A4xhgiia1<x{&BN&_k!{PjEvn?^K#qP)#amYoxTxvM00s$<h4f9'
        'XsAP4d0&d4!1l8tzzoI5Ktc3LGy6wu{v)@fTLnR|yPHiO1Q0OTYl%(6GLtob63O%W(Fh$1qRO#X&Ptk!a9stsmf6PI;xc6Bw84c='
        '$prSEy&}m4>}WN_-;g3kCp1ejm?;Gd<LxkhR*$q#bz$dO@O+3yI+Gl{L9&}_gK@B|3=QPS%KaB7ZCma}Y5{1@MJh))4hwhFznS!Z'
        '|NQ%Z|Mu7aeE;3QfB47jcJ{9yf1JBS#sxmapZ^EW6_Y*'
    ),
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
SCHEMA_PINS = {
    'count_result_v1.schema.json': '0f9ef21f01fb75f3a6d2d52855c6ea0ac76700597eeba5dabb22dffc043f2279',
    'evidence_contract_v1.schema.json': '8ac6551d35d6be717c4cf2235cc3f554c8501fd9a35e5abae5aba8e93fdf2d90',
}


def schema_check(value, filename='count_result_v1.schema.json'):
    raw = zlib.decompress(base64.b85decode(_FROZEN_SCHEMAS[filename]))
    assert hashlib.sha256(raw).hexdigest() == SCHEMA_PINS[filename]
    jsonschema.Draft202012Validator(json.loads(raw)).validate(value)


def write_snapshot(root, data, path=SOURCE_PATH):
    raw = canonical_bytes(data)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return Snapshot.read(root, path, hashlib.sha256(raw).hexdigest())


def row(identity='x', region='须弥', name='测试特产'):
    return {'采集物ID': identity, '名称': name, '类型': region + '区域特产', '分布地区': '璃月/层岩巨渊'}


def synthetic_count(tmp_path, data, region=None, **kwargs):
    source = write_snapshot(tmp_path, data)
    return count_catalog(catalog_request(region), root=tmp_path, trusted_sha256=source.sha256, **kwargs)


def test_local_specialty():
    result = count_catalog(catalog_request('须弥'))
    schema_check(result)
    assert result['status'] == 'complete' and result['count'] == 9
    assert result['unit'] == '品种' and result['alias_snapshot'] is None
    assert '非游戏全量' in result['answer_text_hint']
    assert result['source_snapshots'][0]['sha256'] == COLLECTIBLES_SHA256
    assert result['source_snapshots'][0]['path'] == SOURCE_PATH
    assert '依据：知识库收录的分类资料。' in result['answer_text_hint']
    assert SOURCE_PATH not in result['answer_text_hint']
    assert COLLECTIBLES_SHA256 not in result['answer_text_hint']
    assert all(m['display_name'] in result['answer_text_hint'] for m in result['members'])


@pytest.mark.parametrize('catalog,metric', [('world_quest', 'item_count'), ('recipe', 'item_count'),
                                          ('specialty', 'series_count')])
def test_unsupported(catalog, metric):
    request = dict(catalog_request(), catalog=catalog, metric=metric)
    result = count_catalog(request)
    schema_check(result)
    assert result['status'] == 'unsupported' and result['count'] is None
    assert result['unsupported_reason'] and not result['members']
    import app.statistics.catalog as module
    assert not hasattr(module, 'count_story_cast') and not hasattr(module, 'count_character_stories')


def conflict_fixture(tmp_path, keep):
    """Only test the generic assembler, never a story counting endpoint."""
    source = write_snapshot(tmp_path, {'cast': ['甲', '乙']}, 'synthetic/story.json')
    loc = evidence_location(source, '/cast')
    scope = {'scope_level': 'task', 'scope_id': 'synthetic-task'}
    verdict = {'evidence_contract_version': 'evidence_contract_v1.1', 'entry_id': 'synthetic-task',
        'canonical_member': '甲', 'scope': scope, 'decision': 'conflict', 'evidence_kind': 'conflicting',
        'evidence_location': [loc], 'reason': '合成冲突',
        'coverage': {'scope': scope, 'cast_source': 'metadata+speaker_lines', 'complete': False, 'field_parse_ok': True}}
    result = new_result({'tool': 'count_story_cast', 'entry_id': 'synthetic-task', 'scope_level': 'task'})
    result['normalized_scope'].update(scope_level='task', scope_id='synthetic-task',
                                     population_basis='仅合成输入，不是活动查询', region_mode='not_applicable')
    result['unit'] = '人'
    from app.statistics.evidence import AliasSnapshot
    result['alias_snapshot'] = AliasSnapshot.read(ROOT).identity()
    result['source_snapshots'] = [source.identity()]
    result['denominator'].update(total_in_source=2, with_field=2, missing_field=0, considered=2,
                                 with_authoritative_data=1, unresolved=1)
    for identity in (['甲', '乙'] if keep else ['甲']):
        result['members'].append({'member_id': identity, 'display_name': identity, 'evidence_ids': [identity]})
        result['evidence'].append({'evidence_id': identity, 'subject_id': identity, 'kind': 'member_verdict',
                                  'locations': [loc], 'member_checks': []})
    result['conflicts'] = [{'subject_id': '甲', 'scope': scope, 'member_checks': [verdict], 'reason': '不可覆盖冲突'}]
    return assemble_result(result, snapshots={source.identity()['source_id']: source})


def test_conflict_with_confirmed(tmp_path):
    result = conflict_fixture(tmp_path, True)
    schema_check(result)
    assert result['status'] == 'partial' and result['count_basis'] == 'lower_bound'
    assert result['count'] == result['denominator']['matched'] == 1
    assert [m['member_id'] for m in result['members']] == ['乙']
    assert result['conflicts'][0]['subject_id'] == '甲'


def test_conflict_only(tmp_path):
    result = conflict_fixture(tmp_path, False)
    schema_check(result)
    assert result['status'] == 'partial' and result['count'] == result['denominator']['matched'] == 0
    assert '已确认下限0' in result['answer_text_hint'] and result['conflicts']
    bad = deepcopy(result)
    bad.update(status='zero', count_basis='exact_in_snapshot')
    with pytest.raises(ValueError):
        validate_result(bad)


def test_frozen_snapshot_distribution():
    expected = {'稻妻': 9, '璃月': 9, '蒙德': 9, '须弥': 9, '枫丹': 8, '纳塔': 8, '挪德卡莱': 6, '至冬': 3}
    counts = {}
    for region, number in expected.items():
        result = count_catalog(catalog_request(region))
        schema_check(result)
        assert result['count'] == number
        d = result['denominator']
        assert d == dict(total_in_source=61, with_field=61, missing_field=0, considered=61,
                         with_authoritative_data=61, unresolved=0, matched=number)
        counts[region] = number
    all_rows = count_catalog(catalog_request())
    assert sum(counts.values()) == all_rows['count'] == len(all_rows['members']) == 61
    assert len({m['member_id'] for m in all_rows['members']}) == 61


def test_duplicates_and_exact_region(tmp_path):
    a, b = row('a', '璃月'), row('b', '层岩巨渊')
    result = synthetic_count(tmp_path, [a, a, b], '璃月')
    schema_check(result)
    assert result['count'] == 1 and result['members'][0]['member_id'] == 'a'
    assert result['denominator']['total_in_source'] == 3 and result['denominator']['considered'] == 2
    conflict = synthetic_count(tmp_path, [a, dict(a, 名称='异值')], '璃月')
    schema_check(conflict)
    assert conflict['status'] == 'ambiguous' and conflict['count'] is None
    assert conflict['ambiguities'][0]['candidates'] == ['/0', '/1'] and not conflict['conflicts']


def test_distinct_statuses(tmp_path):
    zero = synthetic_count(tmp_path, [], empty_complete=True)
    missing = assemble_result(new_result(catalog_request(), gaps=['缺地区']))
    partial = conflict_fixture(tmp_path, False)
    ambiguous = synthetic_count(tmp_path, [row(), row(name='异名')])
    error = count_catalog(catalog_request(), root=tmp_path, trusted_sha256='0' * 64)
    complete = count_catalog(catalog_request())
    unsupported = count_catalog(dict(catalog_request(), catalog='recipe'))
    results = [zero, missing, partial, ambiguous, error, complete, unsupported]
    assert [r['status'] for r in results] == ['zero', 'missing', 'partial', 'ambiguous', 'error', 'complete', 'unsupported']
    for result in results:
        schema_check(result)
        assert validate_result(result) is result
    assert zero['count'] == 0 and partial['count'] == 0
    assert missing['count'] is ambiguous['count'] is error['count'] is None


@pytest.mark.parametrize('broken', ['matched', 'count', 'id', 'keys', 'sum', 'extra', 'render', 'sha', 'pointer'])
def test_reject_broken_result(broken):
    source = Snapshot.read(ROOT, SOURCE_PATH, COLLECTIBLES_SHA256)
    result = deepcopy(count_catalog(catalog_request('璃月')))
    if broken == 'matched':
        result['denominator']['matched'] += 1
    elif broken == 'count':
        result['members'].pop()
    elif broken == 'id':
        result['members'][0]['evidence_ids'] = ['dangling']
    elif broken == 'keys':
        del result['denominator']['missing_field']
    elif broken == 'sum':
        result['denominator']['with_field'] -= 1
    elif broken == 'extra':
        result['invented'] = True
    elif broken == 'render':
        result['answer_text_hint'] = '模型改写数字'
    elif broken == 'sha':
        result['source_snapshots'][0]['sha256'] = '0' * 64
    else:
        result['evidence'][0]['locations'][0]['json_pointer'] = '/00/名称'
    with pytest.raises((ValueError, KeyError)):
        validate_result(result, {source.identity()['source_id']: source})


def test_cross_process_bytes_and_locations(tmp_path):
    result = count_catalog(catalog_request())
    expected = canonical_bytes(result)
    argv = [sys.executable, '-B', '-c',
            'import sys; from app.statistics.catalog import count_catalog,catalog_request; '
            'from app.statistics.contracts import canonical_bytes; '
            'sys.stdout.buffer.write(canonical_bytes(count_catalog(catalog_request())))']
    processes = [subprocess.run(argv, cwd=ROOT, env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                capture_output=True, timeout=30, check=True) for seed in (1, 71)]
    assert all(p.stdout == expected and not p.stderr for p in processes)
    assert expected.endswith(b'\n') and not expected.startswith(b'\xef\xbb\xbf')
    source = Snapshot.read(ROOT, SOURCE_PATH, COLLECTIBLES_SHA256)
    for ev in result['evidence']:
        values = [resolve_pointer(source.data(), loc['json_pointer']) for loc in ev['locations']]
        assert ev['subject_id'] == values[0] and values[2].endswith('区域特产')
    (tmp_path / 'deterministic-result.json').write_bytes(expected)
    (tmp_path / 'cross-process.json').write_bytes(canonical_bytes({
        'sha256': hashlib.sha256(expected).hexdigest(), 'bytes': len(expected), 'processes': 2,
        'all_equal': True, 'members': len(result['members']), 'locations_verified': len(result['evidence']) * 3}))


@pytest.mark.parametrize('pointer', ['/00', '/-1', '/-', '/0/~2', '/items/0'])
def test_snapshot_and_pointer_identity(tmp_path, pointer):
    snap = write_snapshot(tmp_path, [{'~/': 1}])
    assert resolve_pointer(snap.data(), json_pointer(0, '~/')) == 1
    assert resolve_pointer(snap.data(), '') == [{'~/': 1}]
    with pytest.raises((ValueError, KeyError, IndexError)):
        evidence_location(snap, pointer)
    (tmp_path / SOURCE_PATH).write_bytes(b'[]')
    with pytest.raises(ValueError, match='sha_mismatch'):
        Snapshot.read(tmp_path, SOURCE_PATH, snap.sha256)
    with pytest.raises(ValueError, match='source_path'):
        Snapshot.read(tmp_path, '../escape', snap.sha256)


@pytest.mark.parametrize('case', ['missing', 'json', 'sha', 'field', 'root', 'empty', 'duplicate_key', 'permission'])
def test_source_failures(tmp_path, monkeypatch, case):
    path = tmp_path / SOURCE_PATH
    path.parent.mkdir()
    raw = {'json': b'{', 'field': b'[{}]', 'root': b'{}', 'empty': b'[]',
           'duplicate_key': b'[{"a":1,"a":2}]'}.get(case, b'[]')
    if case != 'missing':
        path.write_bytes(raw)
    digest = '0' * 64 if case == 'sha' else hashlib.sha256(raw).hexdigest()
    if case == 'permission':
        original = Path.read_bytes
        def denied(self):
            if self == path:
                raise PermissionError('synthetic_permission_denied')
            return original(self)
        monkeypatch.setattr(Path, 'read_bytes', denied)
    result = count_catalog(catalog_request(), root=tmp_path, trusted_sha256=digest)
    schema_check(result)
    assert result['status'] == 'error' and result['count'] is None and result['gaps']
    assert '统计资料暂不可用' in result['answer_text_hint']
    assert SOURCE_PATH not in result['answer_text_hint'] and str(tmp_path) not in result['answer_text_hint']
    assert all(detail not in result['answer_text_hint'] for detail in result['gaps'])
    assert all(v is None for v in result['denominator'].values())


# SCL expectations are reconstructed from the frozen raw source, never from
# count_catalog, parse_statistics_query or the runtime mapping table.
SCL_REGIONS = ('蒙德', '璃月', '稻妻', '须弥', '枫丹', '纳塔', '挪德卡莱', '至冬')


def scl_expected(region=None):
    raw = (ROOT / SOURCE_PATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == COLLECTIBLES_SHA256
    rows = json.loads(raw)
    answer = {}
    for item in rows:
        if region is None or item['类型'] == region + '区域特产':
            identity = item['采集物ID']
            assert identity not in answer or answer[identity] == item['名称']
            answer[identity] = item['名称']
    return answer


def scl_query(query):
    from app.statistics.routing import parse_statistics_query
    request = parse_statistics_query(query)
    assert request is not None and 'status' not in request
    value = count_catalog(request)
    schema_check(value)
    return value


@pytest.mark.parametrize('query,region', [('须弥有多少特产', '须弥'), ('本库全部地区有多少特产', None),
                                         ('本库全部特产有哪些', None)])
def test_scl_s01_scope_and_explicit_all(query, region):
    value = scl_query(query)
    assert {m['member_id']: m['display_name'] for m in value['members']} == scl_expected(region)


@pytest.mark.parametrize('query', ['枫丹地区一共有多少特产', '须弥总共有几种区域特产', '璃月的特产数量是多少'])
def test_scl_s03_count_phrases(query):
    assert scl_query(query)['count'] == len(scl_expected(query[:2]))


@pytest.mark.parametrize('query', ['须弥有哪些特产', '须弥都有哪些区域特产', '列举须弥的所有区域特产名单'])
def test_scl_s04_list_phrases(query):
    value = scl_query(query)
    assert {m['member_id'] for m in value['members']} == scl_expected('须弥').keys()
    assert all(name in value['answer_text_hint'] for name in scl_expected('须弥').values())


def test_scl_s05_combined_single_selection(monkeypatch):
    import app.statistics.routing as routing
    calls = []
    original = routing.count_catalog
    monkeypatch.setattr(routing, 'count_catalog', lambda request: (calls.append(request), original(request))[1])
    # Exercise the actual statistics gate with its real metadata consumer via
    # workflow tests; parser-level result here proves a single common request.
    request = routing.parse_statistics_query('须弥有多少特产，分别是什么')
    value = routing.count_catalog(request)
    assert len(calls) == 1 and value['count'] == len(value['members'])
    assert {m['member_id'] for m in value['members']} == scl_expected('须弥').keys()


@pytest.mark.parametrize('query,matched', [
    ('  请问 须弥 地区 的 有多少 特产？ ', True), ('帮我列出枫丹境内的全部特产列表！', True),
    ('须 弥有多少特产', False), ('故事里说“须弥有哪些特产”是什么意思', False),
    ('请问请问须弥有多少特产', False), ('须弥有哪些特产以及料理', False)])
def test_scl_s06_safe_wrappers_and_no_match(query, matched):
    from app.statistics.routing import parse_statistics_query
    assert (parse_statistics_query(query) is not None) == matched


def test_scl_s07_region_alias_identity(monkeypatch):
    import app.statistics.routing as routing
    identity = routing.region_table_identity()
    assert identity['alias_table_sha256'] == 'c3970b3557addb9254c8723e85f226b4ab033742fefe4ef89b0a4aaeb7958d97'
    left, right = (scl_query(x + '有哪些特产') for x in ('挪得卡莱', '挪德卡莱'))
    assert left == right and '挪得卡莱' in left['answer_text_hint']
    assert '挪德卡莱' not in left['answer_text_hint']
    assert left['request']['filters']['region'] == '挪德卡莱'
    assert left['alias_snapshot'] is None
    monkeypatch.setattr(routing, 'REGION_MAPPINGS', routing.REGION_MAPPINGS + (('错', '错', '错'),))
    assert routing.region_table_identity() != identity


@pytest.mark.parametrize('place', ['龙脊雪山', '须弥沙漠'])
def test_scl_s08_subregion_clarification(place):
    from app.statistics.routing import parse_statistics_query
    assert parse_statistics_query(place + '有哪些特产')['status'] == 'unsupported'


@pytest.mark.parametrize('query', ['有多少特产', '有哪些区域特产', '这里有哪些特产', '这个地区有多少特产'])
def test_scl_s09_missing_region(query):
    from app.statistics.routing import parse_statistics_query
    assert parse_statistics_query(query) == {'status': 'missing'}


def test_scl_s10_multiple_regions():
    from app.statistics.routing import parse_statistics_query
    assert parse_statistics_query('须弥和璃月有哪些特产')['status'] == 'ambiguous'


def test_scl_s11_unknown_region():
    from app.statistics.routing import parse_statistics_query
    assert parse_statistics_query('火星有哪些特产')['status'] == 'unsupported'


@pytest.mark.parametrize('region', SCL_REGIONS)
def test_scl_s13_shared_member_set(region):
    values = [scl_query(q) for q in (region + '有多少特产', region + '有哪些特产', region + '有多少特产，分别是什么')]
    assert values[0] == values[1] == values[2]
    assert all(len(v['members']) == v['count'] for v in values)
    assert {m['member_id'] for m in values[0]['members']} == scl_expected(region).keys()


@pytest.mark.parametrize('kind', ['same_id', 'same_name', 'conflict'])
def test_scl_s14_duplicate_identity_cases(tmp_path, kind):
    first = row('one', name='同名')
    second = {'same_id': first, 'same_name': row('two', name='同名'),
              'conflict': dict(first, 名称='异值')}[kind]
    value = synthetic_count(tmp_path, [first, second], '须弥')
    assert value['count'] == {'same_id': 1, 'same_name': 2, 'conflict': None}[kind]
    if kind == 'conflict':
        assert value['status'] == 'ambiguous' and not value['members']


def test_scl_s15_stable_bytes_and_complete_list():
    code = "from app.statistics.catalog import count_catalog,catalog_request; from app.statistics.contracts import canonical_bytes; import sys; sys.stdout.buffer.write(canonical_bytes(count_catalog(catalog_request())))"
    argv = [sys.executable, '-B', '-c', code]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    one = subprocess.check_output(argv, cwd=ROOT, env=env)
    two = subprocess.check_output(argv, cwd=ROOT, env=env)
    assert one == two
    value = json.loads(one)
    assert value['count'] == len(scl_expected()) and value['count'] > 8
    assert all(name in value['answer_text_hint'] for name in scl_expected().values())


@pytest.mark.parametrize('case', ['missing', 'permission', 'json', 'sha', 'field'])
def test_scl_s16_source_failure_cases(tmp_path, monkeypatch, case):
    test_source_failures(tmp_path, monkeypatch, case)


def test_scl_s17_zero_vs_partial_zero(tmp_path):
    zero = synthetic_count(tmp_path, [row(region='璃月')], '须弥')
    partial = conflict_fixture(tmp_path, False)
    assert zero['status'] == 'zero' and zero['count'] == 0
    assert partial['status'] == 'partial' and partial['count'] == 0
    assert '已确认下限0' in partial['answer_text_hint'] and '没有' not in partial['answer_text_hint']


def test_scl_s24_success_output_privacy():
    value = scl_query('本库全部特产有哪些')
    text = value['answer_text_hint']
    assert value['source_snapshots'] and value['evidence']
    for marker in ('content_data', '.json', 'sha256', 'catalog:', '/0', COLLECTIBLES_SHA256):
        assert marker not in text
    assert all(m['member_id'] not in text for m in value['members'])


@pytest.mark.parametrize('status', ['missing', 'ambiguous', 'unsupported', 'partial', 'error'])
def test_scl_s25_uncertain_output_privacy(tmp_path, status):
    from app.statistics.contracts import render_result
    value = new_result(catalog_request(), status=status, gaps=['C:/private/secret.json@' + 'a'*64])
    value['ambiguities'] = [{'input':'catalog:internal', 'candidates':['/0','/1'], 'reason':'internal_id'}]
    value['unsupported_reason'] = 'internal_id'
    value['conflicts'] = [{'subject_id':'internal_id','reason':'/internal'}]
    if status == 'partial':
        value['count'] = 0
    text = render_result(value)
    assert all(x not in text for x in ('C:/', 'secret.json', 'a'*64, 'catalog:', '/0', 'internal_id'))
    assert value['gaps'] and value['ambiguities'] and value['conflicts']


def test_scl_s26_independent_snapshot_oracle():
    actual = {m['member_id']: m['display_name'] for m in count_catalog(catalog_request())['members']}
    assert actual == scl_expected()
    assert sum(len(scl_expected(r)) for r in SCL_REGIONS) == len(actual)


@pytest.mark.parametrize('region', SCL_REGIONS)
@pytest.mark.parametrize('ending', ['有多少特产', '有哪些特产', '有多少特产，分别是什么'])
def test_scl_s30_all_regions_and_legacy_entry(region, ending):
    value = scl_query(region + ending)
    assert {m['member_id']:m['display_name'] for m in value['members']} == scl_expected(region)

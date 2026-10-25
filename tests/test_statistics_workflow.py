"""Run the actual workflow with fail-on-call external dependency substitutes.

Real schema, nodes, executor, metadata consumer, statistics gate and Wiki router
remain loaded. Only credential/config, model, vector/data and tool startup are
substituted; this tests graph routing, not provider or deployment behavior.
"""
import ast
import base64
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
import zlib

import jsonschema
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.statistics.catalog import count_catalog
from app.statistics.contracts import Snapshot, assemble_result, canonical_bytes, evidence_location, new_result
from app.statistics.routing import parse_statistics_query, statistics_gate

ROOT = Path(__file__).resolve().parents[1]
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
}
_BASELINE_NODES = (
    'c-ri}X?IlDnJD}{Yvn(jKJK-svV|nDlTNgvcqv%1xxv5%`3Ap2sg$J>RIKT(l5EqG?kWMAM=&5XfFzKZH8E*MYxVn2>^i5aKIJdG'
    '&#?DC`wUf*FiyH}-*hLa&e>;=&wl3NJ>&5EhmG3ix_HxtGNY}v{_t<`$uECln&ydU(}hnH(UyxwcHna6@`lyBW8Yl)`}Sla_H82G'
    '8Y_t=ld)v7q&}K#Eoo_pHkCx1lHbM>#s@}rZ8>vel|T3~b@`XS_~kD|AKk0z&8Nnvv8Lq3cvD5)H}Pb1;%sZQHTL)Ip<HS*yW49d'
    '@uy)ej@wJG(%ZAS)OF~^9$uo3^q#Z3H>|0<&@l7tL3;f;^Z>66>W$}}nY?O5>*^x4%}sUj)_8MMv>_6!yAVqn_U&0~<*L=Ul3pK7'
    'ucvr&Gyp6dryFl<X->2niJ1EHZ^`B+^`kZ3=)7r8{_+>~iRMP5CE9wiA^w#io}Y%F;!W$N7T96=;*)r7YmrgWbg9TV5l_OORc$Q|'
    'F&vf}!&Y9Xy%>!*!Ne1>;zroyC{9{@{fB5W_BlT*GCpf-j5euX701<|XD=mNV~y%(RdaKL_@Ty$wzL$(*3`$<#6rLPg#rKhyy9G>'
    ';?v5LRgvRWm1iodj-NVtw#a#P`b5Ra$eGHjGsi3S$A7Fi8}PxI%4673)fZ>fqhqH|R^hWxj-P=iVQn@I4UL|uz!UKmLRiHAGtzKW'
    'JYjIun&8`b(>Kju$0FaxTQ5cuvDU;T@h*~#HN<LLBMh?IN6v#tW2`RT7Vx|-7Hjc8ZTL_;YmC9+HN}&Sk-xP8u!+9)WhQGc#u}rZ'
    'l?L1i(IFqj*)PvlRel~hedg5Xr>p1;3cRj3dG^nhX9_gH!z%Fl#E0%K;P;;^EB+LLmdC5Uj8s*8>RD1tqWMAswqGtP5^IdNM&#2X'
    'BT^e}s*S<*`zF@Zn$$jQO+;&Ba5Mnwb$~m>=lC@osKIaXm-Y$3a8oN@>ekqIt@4Yn+Tsm$@?nv25$;uU;*#^A6)pe_lWb{jO2&Mh'
    'A(+%g5k<)U1mGZ;HN_Iz2j9lOj>9tIbum~_aa}xmp{Y698n0DLuZ^~}wk2W$wDhII6jUqv5&7y;q$%1M!=*-CPrRQMCu7k>?M3-D'
    '|B8IuoM@=S`~05k381&-A5zoy*mUOQz0AE8>wcFtHu6L2j@>_OPrl4eKL^a1om;TFyR9|&-!(v;>Gk34m7VPFl-0dp_1&=^4)Uwq'
    'R1e|B$Y;kt`K0n>r0UeE6Ooe@pI4qW%8hnYAJ*)|sr>R6b+LLwZd@cuc!)0`ZC$)6*_vpp#k~%Rmvl%?a7ZoD+OOe2!etC~dG`FP'
    'L>%sw+=}Qo(Rf4js|G-Fe>ijelTRxn$4{QEI`hRb#6sLpyxwUQ@{EE1aZkhepgeC+fI`|5O`Z<|Rt<pvH$*RT&JqqoMBIuDiPMXW'
    '=0yBL90}9^+ZIb)I?&2RpOO0J1Pxqd#F}u4bwx%z8A&#`C1Bu2iS)7Cx>!RifLggx-Dtp924LGJ!=W$rMpJXE|JzVwxQ4&S5#vp5'
    'd{N5faG@~Z!8+qBfYT6<5EeKZR?*T54fA0z9MaWitRWfOC+s-Kmlr=WD^YwQ)*3qG+Q>uUu(KdUBIR!UA&-eyQzRBr1jk>C*aBf7'
    'Ssp;y^8E+Cua7sx$`3^u66AUh1#iG1+<-%S^_h&d)ip=p#A4;J!if0Y5Ceo(7X{RZZ$cjM3cJAA3qW$&3Tfus*>C{JD^QPp2bez*'
    'ivqtSFB|S)90uX1Z+E@-ca_D;OjnntYt{Nx^mQa+Em4KG)wgm_#Ep*CMVz~PF`A6H20GBn#O*2g1yIfArg$x!pZnVjz*-`0AsAL|'
    ';!X9<0LuK5CmzNfbM7gD*U9Flm}f`!v9==5K-$(hFJj-tYTFQyBgwYLMu5T)(Osly3h8+}lAHEbb6XSMz_up5kMMm6?poqf^&yxZ'
    'a1F>m9IB~4L><Dn@NwOt8l${i_FJsK$#-`U>ruCe`P}oUK7=pgvE-o|_cf_E+tn&M%M813%o><s;p6YyQ}*;TYkI}%-L&SfmbZJm'
    '#6xI<9y_6>we~!-b<J9u%=GtyY*XH@t+><F_7iS!ECGiXaSo;h`b|U|qao*n=tdV2t5n2{5+JCyCRn@=42j7pZun4jqu@x5Bwi@4'
    'MPl%HQ)|dHjKjY-s!x1q_4UBSYYh8-%6>Hn;*-_=$evin|Gm`<OoU_~uvO;94D6ICwi3p!g;PwxOptHr)SfcYEzdib;3b#Sx(lO7'
    '*MH>jk><A6a&0<cg;<=~Xb45jia?sq5@u=wqG*jr8%*^TcSV%O<Ys0VLDOkTH_uGA)z-plOs9W3xR84tCdw0l#ZAxp+WpHl6U_}V'
    'v&=Agugs#LCIl4Fi2n#`BIaD?Uv5zAdZH+jnFf;61Yg4chMXM?2Yn|{D{~h(X$>MlAS1e-3|xY?$vBkr%hR}1`yJk6lO>|x=RV8T'
    'Ha6GM-0Si3n(ulu<?5Ir4(j|O+}lWPJW<;gZ-v%>i~?Z&b2fV)z?J7b1Y&JB<<*Aoom*aTOA1V{(}lH}$|;;$7i*0r8si|BgRBO0'
    't~FX0ZH?j}Z4IrVMD*K;56$?=;@i{rx_H7-HvkHWAj>8ms>uj^7cd!#MdPpt*cu{7sxMdH3Y#G-?|P#lwf78bY}gvUnOWV+jW6Zy'
    '-TwRb<ooZZx9(?VyE0QR?U4n0X2IJ0-d=xh&wT&>`+wi=%PtStw-z&7-)EOLp|$;J&bm7YLSyF93!rG(l`EMWkC3v)TQ4!M3FN>v'
    ';MA<WIL}gM`puo}t6L_}LwmZP#Z2hfzB_GZcPH#SbJ?|J<A>A@_<tk&u*+I{V!ya)oc@)OULP@%EwNhTk7o{-mL4gge~$iryPqUf'
    'IhJp$-AA7QX&vr`*lT@vVbk($n2QdIeVSQ>dDa+K_Z8Mp!p3D5Z@@F_%~hm@YcEindE&(9rVH{B-bi%zLAh&i0Ceqg_(8GL>C?sG'
    'p(GXnCS~q>`JPePaxvD3tpHH!Vg?Y!mNvLuO)<DjFw1Bi19@8#ezHauI49sM4a9#9#oq3!E>a)R_?u`$9H6-wd#Sw`tNl7@#J-Ex'
    'wi+a8;q+?T5(y;v$g06yn~;b1bjaIOZ88fQupSdQzo2#ca18fU2s6Mv1{xswei~4Ufet%c4=YFtf$|+O{sr|Fz7ezo^vnwN73TRS'
    '=0lA?!$*cSG6M`*c6rMhy#<#QFWT%}?#U1_p?KZ+zS_^`0Cdw^!|C<S%=C=4bk|C4Lz~RZLUy?a?tk_{PiE>CycKK=+_Updf_d>M'
    'P0Ib#!daQ{Pksd@ycB;$&QzWty>aB&Dcp{V<0q^3wHf=@-Mp2E1u`(tFX)iet<&ZsfLrzNmB(Dy*4r3momdi{9IEkLSpPEp^Mc(x'
    'biU~j{DZcN;<~oRmSiY^mpR1x=Lp>NBq|}wj6)60umOkp3LLJDw-VEmJp9d(AT0#9c9bb)0qanrRM7WwfCxy4U&ADzhxTAjUPyp|'
    'QlijT8Hi%le`$9=?5r_^zjcdx`Df_=y3@zSKu1Z^=_YIA@p9t1!eYDkrx+C2MXIGG77y)(P(;5Tb+-J>rGkQG);6@&#iZz6UX^Hz'
    '>6)5vW6`f2H4RX{Br9RvD#^D)WqA~iau5{gl`hUiqQXpq31rR;(ovYEi;<E2Xt#p&m&prwxCta$N%+{i2JNcFlc+pIB|-?kavRQ='
    'YUa^>CNFq<^DQiat%~K{gn5}d$(R563aIMWu2YqrxI}+6M#K@>vOz#07t=I_!IQP~qsu~-zcqnO1{ymVw_APV*||r+ve_foa})Eq'
    '$+7JCD7;mM0IBmww#hWz%CYKJ+E}!Icq|gdK-givAv!IXX0#R7jr=&i6z51=2p{@v1vR`xk4=L>K!OsmLv@j4yfNMoO)zlMu)3`X'
    '4S4Yw%tKk3l2P2Ne~(23%N~IPtZjyus93p(iV<OiBV>Wi>%MCKF7j2hwf16>QFXlPL?uw3Pmco*{nBd`Dn{#Ty`e0@$>xS{Vq}u7'
    'k0tOfXdfW^?y=`(A70P)KUbtwpcGUU4Q+a#rnk1NfjRF}d+~ua@yOr9{an|0rvI_`MdszKeP_)3G}6>kn~dQ6)Pv?kBWwq38tQ!;'
    'X)DC5hURFUL|zdjBAS)2_F@#IXkwjlFCBYy#fjq;XCt3ioG#)&E6+yGo;p)i`HA>2&fO)LJ(SytzanuV(;?I|u;G;Sr-52+(J><?'
    '#PE>|WbYS!BMVfCkI4(5$oQo4j}>2>sEVBWqU!V)RcLGb^YK3&k9>Nj;`C?YA%o|8Mmhb*u;wSTJ4@E`Mt1p*eeHJkL6>-=-BiZ1'
    'xQ(!3h<}Kr0n3PDM$-j}h072>6d7bPC?j)!HJbmc=_&kAO-&eu3i!*p=O{|ym9|!T^qE*AgPHBez>THXQ})s;o|wA8G(tpJGvq_O'
    '%kd(<&atMpMpCeasA-XLL`Q7e=fGUlo0(_N+T)!tcG>wRfGuDa`__O=w^koQ><wfWA6o;D?fyZ298D(Uh+u(tsV@7lv=mzqv*@t}'
    ')x(ToVd<@f^!i|d<z<#qAY$<%0ht{sEj@g+wA5N02gC*9l{NY#y?ZBfbAqR4b-%D*JOV6d&#qg$SD-tPYOU)-@H}^8DZM*qcP&d7'
    'k6-?RB~RJ+WV`p!h#+~NF<nBuIz9_?U~8wCdQZ2tv;jT+@)zySMp6R?C>|>N@T%1}0X^h;hiT+~0aS+#YqvW$x5X>6mq%e!?9nl+'
    'Zw0Y>_gZ@AdtlD-f5(P14_8neC+q@0e@t~T_7Hz>Jm5b7L0I1dwjYL3aDeREBkXjj7o=DC-n14r%pZEL!%>W;H}1gyoA8mDyL>;r'
    '{)AtwUbdb;w$>im>jSXrex9M#Ghy{^W*^?S?~VZ}!RgVIaESDqLF>U~*aef%Bsad4y)tLdT}f}xW|v>`G}G&g_|e8rrt6g&8zy7j'
    'SjE#@9AeqUzP+5@N~JgY2#~X>q1>IjuAbAIJ=}R_Y8?(4`xJ*Tl)3sErk35^5zT0;pl5sPxk-Ed;Ie&pI(Ku|+I(P*j^`%E(Eh!B'
    '+v@HU2QZzoZ><<d4cIa4cxlMK`H~NSA&DHCme;axzK6}wZzIlp`k64NKj^XsX086=%ytSO5%w(CJD`(Xz)&vB6)OTzu5D;6X8(gw'
    'wa%x61Zpnl%M`8&2Z_7Xi{wU%0kOneLZV^pyV@9B!b<wb%}%{1VAPsl&Awc)R#xHuS;IH&!EJkFL0l)gOWEaN4Qn*FpyvZHLBQ9A'
    '-OL#w9macZW{5v3j@H$MK*UFMViL*-SdQ-!et<V{%9Q^W|E)RR6l#D*A?`I?efYz&ny?q2_dNwLWvd1IdMSL)+&OaxEzu@cCITzj'
    '(hTT2j%0{mHpH65Z_$F9Ckc6@<y+&@mKV}H!x^}2x0kK42Y_p`_nz8QF9G0)M|%z%3Sh@`VhI~j!fgQ2d)DGudi^<|2Wx)V>YKN3'
    'Ee5k`(WXnGr186Q1ExvLEB^a|GgW*Qqf-DpA0q+CHEUfm;kDPI`B!jLb%JOSZ$PMw9?)9JRjs;#+}6DRTjOkk-T%}c9kse2<*q#9'
    'm0GEZT&iCnU}BJZpRgMqte%^c1A@#BU16k^ULVN}KFd5E1c0-y?I7MD&180I#oD=vnhe;Yu|9rf-?*NAwPxR0NN>&qLu~hN<a$Sq'
    'isOd;d?K^7%3G<lgv>`9AznIi6v+r`IFj<PTcYtq(sR=IQ!tjWS9Pf40zpIdsZPk(DY{$K&;`5kIycoL&VBi%eR~8D1>l;@Rv%w8'
    'RKvX3%52TpuloSYu!oM`E6q#wM>^B1*3c~h-n5xUb%=i!8EQAfHHeYkOaZ~Q*Vi+1OV-k)oqCwve45_cVYOv?XBSYbbc)cHtwsw3'
    'T=cL+0W#5wB63~W0OmF~{t}%z^T4wl!nc*qHqqf(Z@T#dh8uBMn(bI~Qvl!xUJ7O#mav78Pah!YySY+btJ{pDI#6DWG6)ak*3)3a'
    '-x;MIJSH4BVscQIF&@2MBx77nU22L3oGUa4(Zu=cqWO$R4!hLmXfqz&2Lc)5tp>Vd^|-cLEFlk$E7At`qPBf3_r1MtG0<!UZ}x02'
    '@MugNbJHp0s)@@Lcy5S^FVg?%e*GF5**W-R)2%sk_PLzfdS=TS`+Czbs+G06#yHeYZ95Mcz~aC!)lKIiugLDZ$HlBD4%HtrMK7iS'
    '{FI@064(dbTKTmPj>BQ^Hfv`fzY@T`*Ky!Edn8~7sSD&r02lVz0~nhsT#Ep;^*XydgGT;0PckFRh;8o7AkViogDel<QV*cf#9F_L'
    '5K5rC^<AixLHw}so3OtjEDoC?5szsEDD1x$@Wa=EAB*JOW<U_K^J+E2GBJf39fxRxAj|LDDGyabhEpR<d>Q_mz@p?veli{P@guCY'
    'ku8&<fNy;L7M_O2z0@}}M_W-}>gHI2cm|!xU`m8EoA57*91~Xef<ADGHloh!2#$jX$)?8b@z;$oKkm}Lxe`WCqVZYrd;GQhptKmS'
    'PiX*~Arzwl`{MV8<4%N^6;V4k*$qWuFN^R#hM988pG8K5?gi~IbCJH`1X*ikr5bdHu@S{Bu|z$4HpLQBFyNutAcy1TSIvdIvvl=P'
    '*Cw1Q8nKs9z<7Xc#C*cW`^J&d(o%;l*dHSSpi_%NQmhV_{qG<`1wx=}Nu<w-*R~4#K$#0K<6+4z^OLHI;{bL*Ep_Z*O+QMn4<PS$'
    's~4aUh$jdZLjEU$>mr@AYeq;lFEh@Y?P7JE=gqL0mxgF!AS3pk?)28MJ=72UDexUE9b5BPGh;nK(zAmL`x9$~6`XmKpN${c=SKR_'
    '>^^tur()lOV%~jXUEgKky$h03H1x`T&f1dvQjlLE3F1liez#FA1P$WjY7D=uB9Iujl1MD`coPm^7h>gg@D7~E;liGN;y4^q-=<$v'
    'U@A@Hdj>mbpz>u3eO0XaNufR4-n9B&!`u;R3kMTc208uDPo~$O@F*qx6(aoGBgn<}jia0A#%t~e)jg}1g@_BwT)ZLTe*hPdUK(9t'
    'aCMqK7X1Y!e7m>&_vOY>-Nw(Kqg0Kbqi&9M9rf_{P<u<~kEvmk@L3BYD1V1eS3&df_`=nj=bNhaRn-_u(T0kjp>AvJsnH%h^-cqK'
    'Ol;L;yEgMqJaq3!>77}`7qnrlQxskXs1P3D?cCQ;AQLQP9&}lwPef<kuL(V-NafHIF!mvF*fV3Q8<+~K^Z?5W*9Zby)G16}&TMbu'
    'I2$uU`Ptpaayv?8GYdQpa>^iaEpI?$Ykrj<ax>EcnCl-_CK&|e8?S*$G|7?Tkjpnt0xy84eRULeQkQ^m$5wA<<~QxxZEJfJq#@(4'
    '<~hbXU;dRKblIgNKwT>7BCR1n!&B;JkOsi`EAD4flHNu@G4ZAg$-f$~QufWV7w5%DJJT>2Y)CGZVy!Pxn<e|k23&BlEn`FWA{^d)'
    'Hr1^d6@B)s^#A2A-1y^{zhD~xaMJc_FYLb#a<KnMUg%oF1U%$Ugl~4g02Bmg3Z1a-z4l@vbfh#aZ^~f<E=s{$LMr;~?nHWXiu;5)'
    'q&G*b^(AZIF+%EC53HIe2B%_8y&+AmkZ)<RhmFkUGOJQ-Jm`BM2*VXUcMhraN>>RS5&Q$!m8D_EP3i4Pdv?PfUeb4j#RvvweTBNj'
    'oVl?I)3SQd>5lb_*_A5{xMDUeESK0fyVIM4CD!U)ki1I(JniKha(<KI-jNd!e+)aFd3n$3e`sInR*G~~CcGI>Z*^O}i~2-)imvhO'
    '(J?qUVa3=QG02k0R$OTpxBG4x7cZgzKtu$yp^0PkmauOOJOWtr44U5D%=AA-^TE=J4R?3&nHbc5-EWN!@SM^+J<Jn%&H>;Pj)IL6'
    'a3I$6$C;5qYyFNjHifr?_D3GekTtq2d>F}+98uQJkfTL2vLkCSU*LJt>nqms5F-wnQM(Vxb}DM;a3w&?$Q29surP|#6nZ#0GKm0>'
    'XAgDZ_Ke;(^9o=NsTX0|Y#L`TkFuaBQzG|{O|qitk|q_QJOAi?w)AA4Jww5Nae-;7GA98C0G9;ha~(!t>)(GLC)+g%_#wT~4;TZ)'
    'B{Wg-LupK~r*`e(d+1}TD~AsoXIo<}Mya6>fqtjc{nA?4V~svfuip>~U}}cl0o$>11?Tx}F1xb@tSq~#BGwofK(ki~8@bpVWdh@Z'
    '0Uer+O`%J}5OUwxh~c=b7>Ji>Er^$TpD=NhZnS-*AXx;+24oG>f%Wp$LUp!Ogu(x&cZPG{zsX(MK|ykAfPhd93v?p;@PRdY4Zaqa'
    'l&msvgV^L`ZC|$5*I}u+#S4I`88!e;cpkW$fTp_8H=%@_8A`}+p}6IeLePf|9m>$0y5*8&jS?3@VXjH7ixtYewE{`bzp;jZ0^YJl'
    '*YvgU>fb!HMjxPFPbo#A|6`w3oT)fgRe2^NRJ0&`JO(;vbx&KHH&rKx1t<d+5l3Jx-o`6@dl~OFuY&Cj0IF}U!llNyz?Uvxmj<~$'
    'tA7E|2h0ujG&eB@Qj2l4w3IKuoGaZepe7?~nfNzj#?f2wlX=GMu2?wG0)ESr5qD+nIfD06KSsr@ciS&fQJ}$(UzBfE>ax6|vj%<z'
    'J*gC(e=}t*ZzH5kJi=4y>-ixy4fD=C8nnii0oRr&rUcmJ?9R>)sXGXv(=+zseVnfgc^J9dukEE*thP||cVe?crfg0NfvL1RaRJ(F'
    '#j|i{*cDyGUZQ5X39|aHO5N{%OpWQryB|~6_zy<$Y)safgW}?1)7?W*w}_14vRDt%cfr}iPE+H<82^+ReoTih0Dy)F0U_4&AfWzX'
    'zPxCn0+w`V!kXT+Czko5g5YzwxrJ=<?sG+97j7sLo~8>iREh?89Jzc*DRfl(q%24f<{9s?$|Gfl{}!n$q+cFbu6}vY3&WlsMzcOJ'
    'P1-pDEa}!IwO*xzMfH!{c`M}1&{?3<{$I+q&C0h_m@O<-edJ&1(J~RcX75ZR$Iq5YsmEml9V0|0?#Mu_3lhatSS<xO_0&TQX4t+>'
    'Jocn~d?f5LiYWe}T(YnIfff<_)syVYjUbCifGtF0>5qmT(;S=`x;3~Bbjl<L(C9J}4d2Ybp^CBkfJ*`#k(*k}+<1kKM?F)36A+m('
    '4eXnMx9T~ob#$urp79|}1emp*31QpuXi{+|x-u31*my0xeV)w;#V8Rwv>tZtTHV9B6bfoEVOX?6JMS6)u~q`aYv($A$vZDxfY#TM'
    'e=`r8r~rU7GtB>3GkIl(nH`w0mb$D*(}%4$YlrQ-Pt)rgCZm5gXk>Sup;(3A80aJk8#J_$>lV%qj*$=%umrH;M69I&L(5>$BKkC('
    '#|V62jW}MAAk(~VmDi=lK<zck8_Wp_E&}y;c7F=dmrt>sN5xv?RT--g-8`y#U6J)umG<11TBlp6+?OuP*&;6!!90`c$9$%cLb@iI'
    'd*JJjJ{qhI;ou5j0wkAE!d!shxfEJsFC985g*956TcZv8)nF4fb!)I0-D!?CsO%KfH@h?k^tlG`(iW3yqun=LS@Dtj&PF`|wH_e%'
    'lUS_fY%KP*fTDIE5CnfFO!Mu5078O{sSeP?F*qGb-a!vV^h5*bY)+X@Gd;~*<PciAetR2xzyUcGb;rDsq7GC+GwoM!LxNn@klE+v'
    'gi&1++?;kx1kn27*Ej6~e3iGYc7Pb5iB~b?gNP!aAl?ss=dV5wKo9vo_rL>m5%^sDSjJtrg<B06_5ld{JlB1lY2ow4Ff!3tB)+LQ'
    '3o^`E5#mHzU(|M@WH2(5S-2~-E95ha+!e8~z?x@Pw}4Ft0w+|XH-J}6v3QE#GWFEev8#8H-TMAX=HW1Ydv{O>&Uzz2{irC`*gg=8'
    '$YYfT1kS_i?IEt^uyNuDAUF2+OK%Q>6wInB{0StMG)PLdb?zLwX`*L(2|KfwSlw4LFK1bP&+bmBpwe7w5@<58h`<mSrH79gK#274'
    'GizlP_$QY#Nhv=)3N@)sgF&(rJDIDmGt-0V?QNbjqU)U=p&wJaR*K2AZ!Fu_uOaV)?82sT;;45Z`@v;v_Z49I>`Pb>2<998umCwE'
    '`=FNaKk)n9xf0$0Zcl&nM<wYU*saZT=g4&p)lsmmsD?$hX=W2OvD6Njr-134IEsAA{IGrJvOV&MjBIoQq_)F+3aEF5DS^cK<(JIU'
    '0o|f4?*akEUR;o$i393GCOxw?3Q|f55VG`U53fOd0<zE4EPJF9i@V8hY4-dcRV>>#i5r8*=)UXj!rolAH^;KOJK6Ooyj#;Ld;Bqo'
    'oSBgoWUaq{lItDfw%N5u(3Xuz<R*l66lO!%%bDj4#CERFQ3J6jH%=9OrYGNjKQpkAU405rbK>ax??WTj=EJ4IFl1t>v&)!#0?jyZ'
    'Iq(#RSYIOc8GwKEiE`Qn@<fV^NjN9eO;RKF+ZAoLw%|}vhTFQuI#FrF!vDiS#bcGHt0HjaE8(xm=M`uERCxx|Vueh{eHqw71wHhG'
    'zy$wd7{b38GU%_dr{XVwPCIqS)J=Ef+0%q3<VY-;bO&*1vJv}k>Ol#vSOmgL9FP!cYHR!|mLR`Ycw8Fb%*xn@HO;8z4ZJN%`6aaH'
    'WJm%KM>E@rqlOHL*7bXM5F{mI(lO5I*tcRFJ6ck4;shBl26nQmb37b$Z@)lw5MOr>LINl-?e)jm-6sk$IlHfvJI)w?;0u}t9J9Qv'
    'Bz49&CBl_nhII4DXcgfPgi_9{z>$g>?G!{5nA`o@31;I4WCU%x6yh*Qv{3LX3T;!*@&Q^v+9Ps2a1td!*P0p7J%}fePQCPI$zZ(e'
    '%@bj=Rm1lf?j2q$Qpg0qOr>{*{clKzYW2=q8#nz!fv^H!Zcd@_%1YayCcz(Po;}O==ILy9;Ii*=o)OX;gYbdrPiIm16x=TU4biV+'
    '4ao89(NCTYcR;8-h{p}O*5yGDJ>O~9y<Gd-zn%gi0{-Pm+xxMjCc=(bAA&wyW9^Z^n4ec3`>f*R@w1;(P+;T_r@lD(NyV8j9eZ@b'
    '41HW~k1lpt+o=wKybie19dPkFtd*V)dtjg=yY#pty|dc^1g0asKGFg7umflv{Jq(cz4x>uvo+C?-h$_2*E)cBb^vziutt|Vr1!iD'
    '7rDcJKGy*d(_xR#b^yQ9k-L1q1F(FDweh+G?s`WKKU!Jsu=|JM<0l>IH!$Gviw?jv9ofN!4j`TIE(HTEbYxRQ@a|;?&?sjRz&ag('
    'B|84T-39-hf&Wgye<$HtSI7Be$G?V63aj(($LWfys>(Ab&uaUDaU6{;@rGC^VS;eV`p!`G`L>T@rKN|@xBa%hzP_gYXwk1bKMtc3'
    'HQyFX7Twh$tRcJd{X6UsP9XDe1<vAKb%?!f48Fq-t<k5z4ZX|hz4hDz0N`xZsndTxb><UFttn^>+bJdDG3|Q~tb13~PxLw6?NL9i'
    'fhmyR)z9?$bar9a`MEg=@`uyatqmY<`cG?H|Mphq=8*Gt{B>^prTUq<F=JiZamHP|4L8#HN$jQ5m~*?xpUf>dZx?R>+lN2(^r5lu'
    'P+5Svabz-G%;A!BBD|4Rp=e5sp>+5axcnjtnzS^)g0hhrjdtwBb8QDB){uoqE~NxyF?;Ei@S-DIF)-|z%@?q7)`REj&Sjor5EL-)'
    'ZXO1J38Wm9WkwfZ_joK}ufN_07>Esvu-?qn5^^xiAd$$!Min%i_gok0bVT$Su}{c?5BqtIeTC(V7<SY=2^E@*3q9W?{D|}>&aS!{'
    '6^i`v;s0vs@regg<{SbAa5#RJEm07{6XZ3T57Bv}`N@G4+KW#q90OClgh<)xjUt2LW%Nez1)!)cp(B#tLLVs*cYjQc{FoZ`Iz5WT'
    'g^ps76e?Y?r!fBbO;Du2-_1~TLK6sBow~&b?o5Qw^Tzr=rsvWhkS6V-!t}bj>{m5DYD)>X&s9bKeEj4mr~V9^_93bhwFZAYb?l3?'
    'f#^qxh8kkn1Q{mZU1#9c--1-?yiJYhU7_oa)|F=<H|J^Ue=|Sm`nmqZ`8hvntz2_{_ReN5Z|IEyD>&$9@~Va)d-TzrPc+;HjDz$1'
    '`jECQBTk!v0cYCxo@&_ovD1D>L(JQgPMa$lS{-%ffT7;%?_CFd0;A$!R_$A0WVE>rJ9B$J=Y0D{JJyXsYivY2iv|5B95G3)vXlN4'
    'G|<mcNAT&L9)0(KMbdw=a>V&|XUp2uz-ej7>VBlpR^M<G?A)I#_{=W9^Z*jm`h0|i-F)hsYul27wD?45V1+543cW5zbzcFh<8fXS'
    'u1%xMaGrMmZgyvfUuCX70ANI~LuhlW*EssWKf+Q6oDo!`Ezn&qb%PvUh#CONqBv)ed~^4%W)`PV|IyuNjcnN?{c2RS|BWvJEh@nb'
    'r65_8qlVQp3=k=Fa=lU@-o2aIS`3<nwex*?XByR36jM)b4|jlerPsHlgG`_9=>uJI7&yn~a}@_AOgKAea6S@c-P7qTniNTMsKVUQ'
    'w?Zx*)k$MR9A}+f?n1#_E<@;>R#rinxBBLJll)XuOhg1KyQf8}dF45w@(S?Q;52Toz{*OnAO&wH?K!L+4ibGP9&ctQcGQry4Y6pG'
    'u-_La+r9!evHCAA2C?Gs{}Vm@@1=)-TSGn@MTSpn;EwR%4&Ry%1BJWLN@O+kQIYW>>gzppH%>Wp&`5%@TI55{W1<yGeExwuTLhV+'
    ';xS5ZB^p=9jWX!!1Mx+TkHm3%H^KB09^@NjGjU4$rlRi|48H&yjF7DL2id_@IK&b@LOeUNdGi4yJGQyZ$IM@1SOejo%#BrFeBAI&'
    '=t5nq%ogfc0m-r@+?9Y(tQXF|zOA8w7!-s8x0LV!96T*Gi044%O+!P7xgk?{7fn9`R0#AE?lKS+pd~;;P$`8zWXq_u+`SF=0B8W~'
    's{nNZerLqZ_!<x~h^p)lD;TYO6DY{5@ChA+S>bVlxRsAeYc%BSaQiYf`M1CVHh-JcascCp0zrQpuWP+1wU>GMoqR}}9lc#hMw1*w'
    'o{IiqAcF`XPsTwOP%2IjGDdfo`(1#4Jkk1M_IWPn+i~)Adcvg^_CJ`9_s1UjGaCn=v2;Z%bsl|t5yV{svx#}rYSn#*_g`e1px|jd'
    'og(hU&vs*be;BnVH<nVCH9PU6_~BtbZcncl<68Kr$~{aUe4sJ+9|V3Zjz>bN_`JHIx$I)R#?w~ri>GH7OpE>;9^3=e>?x6mHN?J&'
    'HnoN{NcQm_e#i+@&aVjPR(6<_AUG+M_7bwC_I`xJ5d<S5DJ4p=-CfzGb?JD%Tmr4#VlL`e0sSoAxB4a+;_bm*bdl)ovBzJd$D456'
    '5ss14#)2*;_KTbLP(N!84H-c;guXbV%VL0)YwTUhMj1744!uBBHZXu;MR4KNWu3d5-JLQjILE8zZJQ@dQf_A<Wu{XL!jY22cVSob'
    'j%XM*es3K4RjCfnP8Z^@>N0Fm<4J*}dq1#&m=~t73ZEn%4xMCreK2?D9)>BbKf$^#au=|o3!N?gFP)ywg#yw?fBfkb<q$$|YyMDA'
    ';wi+80HYWhFwrW;u;4d~sdP3X%ah|5;;};OCTLGf?W8B$@iydbAlSTl^h#t7P(|*+x96Lj(7h(F%SoObI{{Ui4xDpGr5Fy$e0Xwz'
    'B6LQf??h%iw>hY|q4`_Kx!0?5#|&k$Ko>{MKFoW<5K?34?%~K;Z{5x=&2#7@#_fzfC^On)odNN3UQ-dSq?nLg66>vy36tj?@a|G9'
    'X$BZRkVF*f5~h>4%cf>CydPVGcoKkJC09>b14Ax%I}dv00LNIXCcsJ0Z*(Z{UIkbymk4s05uOrpFonxlpN=2>vXB&F&|OxFT_U=N'
    '0NHU`CfLU;L46ZSz5-OMq#SO~PJGIk32YuReoK!%Sdm39o-eQ}pM8j<JG#t`mpRHzZL%^GRTCgJG3wQmXy=id!n$rRo^S%HG!G{?'
    '-=XE)#5P4nh;s$CVibPZ%Xg9}(i&@I4PZPG%ol5YVq=QA&d0%u`BSDTY^<$7C5H8+Y4kHr+JN*?kx>_~ZQVN<8Atg#Mq`9bb=sMS'
    'D{SYNf3?P28)BxnJ1PoR&a)&RFX$;;D5oh`SRvS^4B$Yf@DazAa(Ywj5+~ymHv@K0-#O~XbqB@Uz*~rDJn}UlvV!c5!_I3Om>>=Z'
    'U67u5IT<0oP3m_GZV3f=^X=t3zz-yo#3++lB~LW6ROJ@j8y}Jr%mK<C`xRW2nn7MtN57*8nst3h_<2eF3$kCdEOcn$0Mz-1_Jhlr'
    'kzS9&1Jg&>CStKBO#K2ALl=O0k`s771eN?i+jx2^8mOik)8p#3(2mG;q1q03wo&v)<&0dOQ^42=#k@-PLuWm;?h7-}MXneMXX*+x'
    '>*EcWv^1X~smP~42>@3O&lS`I`jvHnkL@ku$-DcQe=gA>l(lo7Em9yVDa>hfp36)v0gXYZC+!*RugB0ONX1SMroGfSeI^ph;)U0U'
    'P`;@C=wYEzx!1i;0dtf22#BZ*D2GHvo-~i`NmG<*$*nf<=(HTlb5H`WaJ&QE<@?k6Dur*@lMxw_10^&RSb}?v7eIS%pbSQKnusVW'
    'm;SUsO>*nx<c3NF)LHTDs}*+iw0587QZwjDxqF-36v_9Ha$Mp=a@NxE<H2Ij_7~H|qwt)6OkLyIdZx!ddHUQ61$TKX4(hJBPMC%('
    '3@QL@@`?*iaoM-X%qdEK<jc)j+}6_amePRo9@gamV)>OYK<9omum*_<&*jcJ)f=$D2faK(^{x0{Sx7@<!PP!|500(m#8D$N_e|!u'
    'm4ulxtp5H(Oxnw`q#i>)_VkDTg`+AF12y1`h9$zIb-i0JQ9})a{STSWlh+|ERgogfK<K($23rZN<?U?mOZ3mpz8<jeTw$#QyWjF)'
    'LE$DE=<CkontUgAl7qlyl#JH4n4X2ud5)9b>jTin3auMgxx|74(u!WLd!A@VX@=OntwTA-bqHrVGLd%po##5(@r}2-13tBHu5^e5'
    '<l-6UyCrY+j_lHN<@wveN77;Sji=Y&gx!+1GiPQ!s2r$oA)=109_;6o;`BMOxpzINg@5}s?>2+&`}w*MMHTpSWdf2ew!DgcI^72Q'
    '+q%4L-8v*~{l@Q&4@(V}NgTi-z8&+oy0(>dzjA(oHDT2CJ!*Q9hBp-pp%8U%r7vtB-`nzb&pbQPF`VvddAmMUe9n*qSFya^HDP?G'
    '-&ty*t)Nq6Uyyg#3`mLML3pUzKn&+vuomeV9Av~;piqpFyF;_gv!z9E7m~yb2b;u1jE}aZ-q!lI;taH(-li9$iMmLljf(#r2%xmx'
    'f6LgSJYhO}?Vx6bXaCn|JyjE+li7>yaiyaYRF9HUuwyrS@go_ik0x6ql=1-J3dj;ZqgwIw>|kzGt_p5>>ixWDoN8%3Oy)`;;_*f_'
    '%d}y21gw=MtM3}8xkzq_;I^>}I<b@|juiVdS!vT1!KZwswNvqIbrD^62gC3OOv3|;<2rlf&MNI5*_}a~xH48UOD3}q38?h#_tr;n'
    '#&|1^c3<ZsO7_-K4dRHXV*_+1s&eub`OXl9kHET3XvKz24(QWYxk+YS(MJkJoE1mpxw9c16WP(fZm~9~y&TDrUh_423kiXaP#)MM'
    '2VfIqVvQ~WOp2t!M9?CunJf-(Pp5z<<AnHn1eZ&V(kg0Qx*?Sopx&97%o4GXV02XN@p4Kau9Iq2{jLaUv)DU#IqUt%Bm3v!JT-3%'
    'NHF!3mO|T35MeUUo*C`V9(-UN=`3mgyy9Hsj}>RDBFC#L&ro@@v-Ae{aSutR7S(zU4V)=Kg93gofzF!`LVXlZ5~QQD!jAw{GdY+e'
    'p~^a0Sb>)6u2WeS&m3f_Y;P5-d{ymTQQm5Wu1cofRW@TCD{KI0PFL1=J61cc<&1FkTW}*>P362$v8v7ysAj++`g_tR4~V@sCJPGs'
    '_eP&`UW`B`4LRgCe+T)FD-Qw*0v!!KjNY<e_oa8|tWnGcX3tQXCz<@g$%;osK<&HJgchD~1Cgy3H`!IJfOrS@wUhjW4&@P~hXexp'
    '?<9R7Dq5364R`0MVXnYl^g<OTwFkvm(+kZP`O10n!fP-+zty+GOXO_`Fq?U~Mqd$6vsz>$Tzk%4)flXd6QO04HkI`<nD>A29&VVI'
    '5z{Jv`lH50JtdB;!4?#3qc<y92=|@cV@>Tz1#8k<BiZFOOo_QTPHtGknI(`DUt*p>DuVL&?I{KXh4tCl&RV=JKD>7~*E_015EgS+'
    '7$~4Nnt-BwNQ-DV>TJHk;$r-b>N4y#`*S)8Sz-^ZJaR*{psWHrQm*gL9;L~hEe@)ViRfmJ4id3i!V_3urQBTyuqDx4A8&}2AHt(P'
    '<mNh5s^j22%Y!%$j*WHG>b!ktPNlrzgg{(8m(=2}>bWRlYODt)%<O*yw-W6IcY622H}4sT;Xj66pI0A3R2p{Agg#NupPU~QL{;E('
    'MxQW~liuucDs8y?Qn}x)`QhyDieAc?gFvu$!)`BQ1WfIt^x+4o2c2)f|Dk1z1F>I0JhexCV{dCWpIBzNc&WF-fdzp=<KA?LV<=;w'
    '(?EmBvBs=cvqgZ+0qU`*%4CsI1z*V$lQoX9(bX4{EA;h5MH66-2$hu&HHMx3UG2mBJY8*hBHxd1c~0R1x=n{vcG^Sl{N(c{!VMyR'
    'KLaG}iyH2mZPR!rpyqGhkaYAAlf<Eq#-!E1ka@60+x##)hyEaZgjnu6NvWks+m*R7gNY{zHQ-S^-sBtuZ2d#0U44_>$wXByQ8l_a'
    'a50A={}4`YKsmn@Oa&cF?#vk##|4-g?%AU8#X-cl8dcec6O~P^*jKKp<VGH6a#Txc@p^D}#$o!e4#Zf(wOnm;b-X5@2MJYeRTE`5'
    'hl|l9xn0pa)0wx)udynt^U&kw;C`w3<?0Qhb#-w(a{%YBzl|m?V7vBCcRS5@RU@ig#*K3>4_oUGByG3`#0NJSUhZLXVmlM*A({Xn'
    'wZ2^@MVS3p(b})e@p)mEL0xEIW035N0f_q3(;@@arsa+Xb$=Z;OI|}<p+nkbDKB(+^Ylk-ez_wv9O5U3!f#_CLooFj$2`_Bx#J4D'
    'lsT{7v<Fu0Mbc5Tk*IewcVmL`oU2}(66jtFpD13Q&o$6f>-asxg9Ipj+X#7uY@qYN`Z^+nl{c6W=Ekzj2b{|GzU0KD0>`gC=evr!'
    'cXlWkerb)3aUOokiag}x10Ti!rGWu{$&Lstolx4x;mp%z%vBBF&3q5I<j2$y7r?|i#Ph>s;UCuR{BCb^ndHJ_@XSNQMBWhAG0M^b'
    'PBLmzOev(uJER|^o{yBEG1l19dMP3=2xTSBPgab5Wh`|ccR{k2H-}X|(&;CgVkB3a{SwDL2xDh34g6_p2TdX*XjLRpTtI~d0sq0h'
    'M0fG&ew{c1{2^dby(zqM<QK_I3PV1F#lDJY=0~5}l>f70(*J5Gbbq>$!r1iPD?wCpOv(O=6z^yg%4|K%Y%MaVDby8!t9?v`!tsHe'
    'g=^!)aAZgg_)O$_r<uW?R=3txA41=zLpAJfR4d#ryx-My9*{woebl@)tF3P)Ab;OxYI1~L3gzddjEdOtu%?o0%*<lHBZKes5d5aM'
    'hB+8luStU{%*A<(U=gVy1^|^SB>r{;?LgGQMg4S%bZH2Pt)R5pW{Z5azTGy%`=T~MaPZHfI=TrMyFZu#Sl=raP>AEfvd_{U>?JMh'
    'H!!XKn>Xq8f!xFxfh~~$MQ7+y<;hPJY0>2UymqO#w0$XAYz=s>hbX5}8Cd=9pd~}=%6&=!e#vI6sm|1F7zn0vu6wj@z<GKEvBO7d'
    'w9e#Jl`Vr1qkI>P7!PJuDRep185Wdt_q2KIF9{G#k2ywdk!A)bYpJ6R)UGB6{HwCk?-|*pH`%2*S?6~WA05V|^DtQ#Ty9b65-P2c'
    'OHJmwu>OS5LrV8(j=e+I@SchECZ+Oc+<WfaInF1Ey;GTV`}v&M4ev;}Tj=D2HGH@?wCrd#pMWbvv!=_ud+mRmJ$2Hs?u@H3umBCI'
    'nqUiZsoBi*jJ0&vtK-;^Uf=rNq!g#T4cXlZdv?tlej%88@=+CG&Rwsq(QBxlrPP^f-9i@&=-hRwHvyCL7C2e@?mguc2Dz!%f=Cdv'
    'Asnhbc_99lY;KA;T}sK%i4!6>Z?{d|o%w-*LTxDnLcyIBsGAB9klmXq3OuoA=W<VmJR!uv^heW+NObKDgwZ^zm90%LwJh_RW8%LE'
    'S3-70@|yl7=pH7{cW{~Y7|3MoLL(DE8Bgo9EvpG?rxGRn<*$GJYv|)LJd*Io=acV?{0O3D*i~iOQ}5MjBi1GQWpBfT2|Z)C&}pdn'
    '{g1=&(=1Yy4qKkp)>c+%q(+r&$W?7xE+^h(1eW5I@x!8>*yCy)U2!d3*&x9!YD{-2YVPNEhH&tz!@NaY%XY7Tq(t;vr~7K|r;NG)'
    '8eXHz(U&}Zp)mqJ#SJXR!cNH$^rcTcf6l<*skk+J*gY4J_l6<~7{&%}pL{*Ox~VzQ7;V6W>)yKzvlMiQZ!z9owAf+vC7S~|mY@vd'
    '+xBtU?STP9USp9TK)WDZ=mAi#aU$Au;n>Bf)ZH_#;(BVHxN4GXRqcqwMmv!r)R?;}W#L6Y?IXNsH>J~|iSF>7ozADYmbds+;K;FI'
    'QOynWOrQabgE%r1m@f^pU-V#kN78|ELqyig8`;%4e~<^e7?cHWrzbs0yhsK8#hTh0kwb_%2Ny0FkH-Ok`wPbt5q0<pb5yj^#E&`8'
    'Xb1HGU)K0K4QMCBh32FM;`wnke!kxcW7jxmk!K?SYWKYGbP5u0xm&mU)d&)PXaxbU1r*U50$!8qtlq?9<BQlHCtoKssB!D176Mgu'
    'KfJ9veThlM)#pWN*PkmHlBK^8E&nO^UL)`%B#4qOL$`u&y45$HoqNR68>YCv*$4PS`OpBv;vw<U{U<*n+lyNX2dysb;a%;qX)INB'
    'Z^gRbg@O`QY_%s}=BA%>vd5Cl*44~buPAs$SV7}w8J$F69MT$7bs3|f8AvBi%CN2v87EI4GcfDG>Rs*$IoX}>?TIB|Y<YBL!Ix@i'
    'tXzVzY_j`rn-dLnz#r~l_13->uBOHu7Hbm~(#=ivh&uIscYu$=G!e8GH=9S!Kh$$wELs;K0<Nc!49s@T_J1lfhf}4XQ`D-%S@IsP'
    'Zt1l`)uJ@yP66dlGSh>)osDwpBhM>}#6f?+s1M8_=R4j7(hYadb(N4Q=M3l+D{L>$qcVX`Pbu)RGU?_G-}doF0RJuhjtws3DUO`Z'
    'm-AlcjnB(3Icg#K2xgJ5E)guUqn9YjqJU6)>+HLO{88rAEa(N1JxQIqMSZ_0Uz<&HJ$K`oS~xG(4VezuBPI2bR{6TNHU3S!l{`<`'
    '<)4B?@Tb2LrnxWV?{Nr7UdY27InlTIG9*MVeHDvD>*~}Mi0IIwxtEYmrp~X-*ANFP%ktk=c`@<gb4~+Umr%V5kZlTHQa=FdvvlB)'
    'EVD8V2e9-^&4?0*TEpy4K}V{8-l^sxztk7zbiy&e@UfTh=ADwuH$(5V#)XGT7{uLk?WjkcN1oBHvjM{D?$m?oxuTU(f$9!Jnp67p'
    'RQz6deaG|vpXsh&urpOFJ>p4`)f#PV;Xd_V<Y@cw=~n@f5oa45cYFD@`%rwNhTP!*GX6k)t^KKUF98F-rGGy~ab4%E!W03FRYTm#'
    'ctkl2knC9}V-GA?*9I^E_xg~>uOFS)NRgy#Cimg*|4(%Fcj=xzvvR4Wh33}fAI3tCR?I?FYDQ)I%mN09KI#I=9(6UsM~?R!(`qUA'
    'J_=TTV!ya)oc@)eI~u~K!=-b%8t&Z747?`u{y^>yRuDB#ANjR`G#>bz!3TEV6E^KbpN_vQ*H<R37tDiQl^A@;b0_v4>}<U0LWI=C'
    '$XcQCe*AGRmC}v$m_dO96`c%5fO;`5J>G;p)J77`Nea$lXQ)8G!=V4baeF$dZEmiuZF?6zVHOICzkC;y`Uar%LcXpXRgib5^7@cd'
    'q3+zhtJdhP8lwPwxQsVSkqm!imp1b_GkA-J@u8z~x@d2iPFf6PNS;aTM4(LOGX!}TAM}wcf&#ulZX{56<XLmJLY#-{o4rB<XLNvC'
    'XPy2^7OvRDy%*;(ID7p^^@|C}0q<(|b$4cFu>_^OtE1_SenhU_3!2yH-nn4Ue=C!~=5y_JrDOe_@nPP)VEP1W&`Jw(C(j5aQ7S|X'
    'Vuv2vE4%FsFe`4~#31?QMRQ-9X`=uB=or=;rhNA(q-iD3I2(#0LU!LZKugvN@I6ylIGX87-UHZYJeE%W3dLiwzrAC1+D|Z+gaKcO'
    'jCR=nH`wJJ2fJuDF!$gV91=Pbk#!eN6Y=KOCYJo|THZ@=6{lw}F65rfA3@Xb)lqBejp)lcL79c%pxFmBto;*n59pGw&ars&u6h`q'
    '0_B7}C51cz8-b~ngX;_BsVlU<+Cm<z>_%SId=LPWbS#)yd+|*fTwZ;ZT7bXrNRQ%?&Te42KkC66cw}Qr0(7+%YAp95>#`JVABJAg'
    '-{`+8x6w6vks%s<bt$5yLks9jleR_l{e?udg}u!}-f$V0Ot+sdactEwhcgK=YM)&@+Cfyqw-%GGBTzK138FL3QITzcH#fxoP5OYH'
    '>Gdb!GAutUhwI!Qb3?uChp+ManYfQD@Poern;~ZiUpR9MR8Tu`jBWEm^-tte@<8;52+@q;NEc~%BHKJTYp?LPNW)P*0?q>)!Xpp}'
    'jSFY8j?#&0*m{i)fTRUQQG++!P=aG_sz<|N2b9F}L4MwdKWRiPfF%F>Uh?+}srp@tCg2QFgI{v;^f9Ti@6V%AR|&VHZuOx^G8mwF'
    '!@NA0gTkfR3Pv{j+Lr@qFMagXH*X5O`sp+GMwN)^1Y6zkKW8t2WZftO4T7asoJaW~;kq0FRDu=4foEuHJyeuGSw99Wgz}5QCP=86'
    'tr^brhc|kC9e8GGa_Dmu-6bw9H-H4(dWI#(b1`^;krib52-*bUbwk^bH7aZzY;npyyqsNn17w;~2q6F>C}yt=*%QlL+B4hx!s>a('
    '@2s(5Xy=%mFsu}-YW43it?b|eCr@Nc3j2X_5oLRE9*ZGUAS5N%lwPeaP+?zTwwvT1D?kGg&dUP56P&H7)TEy~7M({zr_`n@o_UYh'
    'ZcWw^rKhD;i}UO#ZI<12k0L)NF-`H(WA+a3eFAJjF4b=jK2EO>qP1&r0V@jiQ^Az(K1@QV84%fLcs;$njYqOMo4YZgkW93#wHag#'
    'Qqf$}FaSPojV8ZV@BAy$Hz_bRguCy8sUciVHHXdaM*y28nJt)kpRO%b`-3fR^|Cz=CGVlj*`1xt<&6pkRJMk4K{&LdilkdZ!cx-L'
    'i?-sW$@I=J>*~pncw6lbRurLP540hk?QveVM4Oru(Z+b~TWpeNh=RN1ZtH9lYVHkS#Su*ACmEnXYQuuzz~RI8Oh40MicrR*fZgK$'
    'n7TGbTY?ttyyO}f2VbzK`+^I0w+&n{W<I=g57xkS1{uNS=3WW&I{dZYbqgLKVA0|Y<%fQY73&=rT&~toivXa6Zt3;@?86?EqvsX?'
    'TD^-P1v=po!WX&k-vB~MZw-Rjz_x!@pfE$J2hOv(o&5`743H!;TQ99mCoQl{U>2|~@>Lz?;YwnPzWwe^k(-w+cm2{5?C4_`&atDl'
    ')X@j(iA(G&-S*WPQH6bO0Z3xN_W8F4FfkIT!3FMy_XWM$X|vG7r1uF3zjz?)DZNs))O98=#slw=>%H)qJUCSOM=7{NzUEE_zyH%h'
    'x^Tw-M}%}adFOCR<nZM;CZ2E7t&Zp*a~ttuyPM(Yl+9RaLlGNKGBXS3n}kDR&x_3BEllip;&Xkf=bOx;e`26nK1GA3t>lz8yeZkG'
    'IjiqF+-_e0lr{W9FP6jJ_nw_(N~z-Oi8p*#F;$vY2fc(&wYAn5nP<;DI&)M$=)>yv1O0h;&kURdFa`2&?mOdy4+iylRF`4B9q%W~'
    'V~!O*c+jo2y33~)a2Hg|MT>Qj&LFI|t@-IGH1`{%C=$wOp)UWW!xmlfKyb=3Cdg}^Da)reRtYeyz7>&yV|OvVb>DtA2l8Af*wz!K'
    'tYzfhH#U;pokAt1R*d;w#Z?vr(W18n{I@_w{&=Iz_9oy_EYZr5HDgcngsLF?`6GR_uH+aP%yWC$IP-4?`bARsjwmsPb*DtMri^7G'
    '(c2x)WD0n5`_^em3asImTn9F9WYZM;OIk};?4f>D%0QH>+U&u?2NREQ@Z~MT711OGUR<WNvWi6&mab%Xp4r3qU?f;FOw{A%&-c*k'
    '8RlH#>Ge$&XXMDRK4)C?PAjBt7oKPy|6UU8^ZPfupeon{SX?#Fmu36P3|);|CG!T2*T6Q7==4cMyb+r4^uI3@;rRy8L!b#?<&&<9'
    'XsjS7DiFUl#OIyJevA%Y%9PjUu+NM7mQ*S~rK)BsRcP}H_vN@<(05-oG-E!DpT*<i{{*}q0CN9%QT$I<wfsR>IlM=rfqmtLLTczK'
    'uqI@75IuQ-61{t@Bl3I>I?nB2Os?ZMH?n}Ek$OuxqI1F-Kpp+V-1FOMd0c^Yt-8yRlKz5i3ff~w`1ebj<qD;^a-IJt(-!^VEwn}d'
    'yq)`LS|{Ej0e<O?2btAj7hErIvF6pgKj^F_fX@%sSS^XZ5-_~H1%M9w41WZuToY)4DyVnG7XQQ@4w~vd^|$JS1L*n0_7cY6kiqA3'
    'sz9jDGhy<DD=++bS!eRP`&c8$qYB*?=>iF@OabPAKQN}?E%D2N^oBpz85Yno9_Xb1A&N--eDhO;e}ppfpLeC+l~OY}agG3a2J`6@'
    '$}p6XA=eAy#Z67?Ln=sBQ%K{^oVP*wMVv8xcaMEE;oMPEzkM6-pEa|IrOh?n9q<uOvI{QWbbHoXxyn^o+<D|sBBJCH%t3I_T+akc'
    'U}mYR98lt_0=0%=YN-BNykXDvVd@@hY7;T~$dl}wyP80SnUx!(t+f|p$}^+|i_GvFv~?2A5KUa+9kfNU@8X2yk`%BYvw%vwpx(VM'
    '9QnUA-2<9|-ZPXIsD(U#-AYfHFvw~8c}3M{kx$Q5R#qK9`Dx^IMHMi$C-aqi;*w8)sXkwKK6(Cw^M}vBUvj><{Cwzq`263uXU>0I'
    '^TEHGL@PI!fmgN`A7tkqAt${(%F(Fgq}i+ViB6Xte+i{`X03&t4*T*W{O4l4<6;a1$_^0Lt+DZr?8-g({|?|V=^s4nt`2)}8veh-'
    '9)Fx(AE1vXCdFT9Hy-YTzrxk$K|t^N7dXn|n)i8n09;mTBD=hpPl5>Clwvxcklb6Z=S9$2!50^Sb&SO@D8kI2Ugq?XQX{+CWp$%n'
    'TWwy6nx~5S&~K+1cDqYG<=w?#PWoJ$hS80|Jo3R2fNi*5R`0AS{H@`lO}$L7FW9r&_|c;|I;TkyU5|mo(9QVa?Cw}*X*~1n!C`*D'
    'H&V;K&_HO)$#sFy@qV>{j~e*<d>seE@xG}7vCfm<l^aJ(f3E#hJL3b%JvP;Isbu!rVT_AGY$DQJ3Jt}^Yb~f^ny@Q{4T~T+Ks3{X'
    'Fqo4WY<CWs$?n%!{8`}Mo?)PXCSpbk1on;%2+O{TM?~r>R=Sp8D7#?~^;=7Ka}(XD0=qkiX#Le7E-*EeyK`3$W7Q*AF_|tE^OT`%'
    '={I+>uWq@Ay@*vV6Y=ImMCv>^Rt<fZaBK<<Esk)2YlQrkv@y(}I9q=u;lM!x>H-G=h3`9+NGYJT@>5a%DcXD8TiO--XJYi6r!z^S'
    'lkhOcMIU|RQ3S6CVHbJ9xF~1|@X#{@y*!8<(;cwvU#_2A6jFNt;>m74O>gbkm@*E1vY1rLn>0kKHW#fTPg|zRj8uFH<Aw@rLjvxY'
    'Yfhg2KwbhwWR3OloI>s1HIpW$@Q{TSan#v6*T#9LDfKnlqrj*o+G|;k&A<CUZ_v`uZ+SR?6SdB0mic(nqI@U0ul?@S?v{_+{T-<f'
    'PoJnb897r~b>?{G*}Nf8TJV!c`UE8GzFI>%(J=%Z>%%$$L;BF2VGq@(Py=?==ExUobea65Nl+55mVJ!nGjq=}+qXb=#h5^%7P=cf'
    'vd}{tIlSoo9*2E&{PbohN3<UCnGrl!IigY^u<1dw6-Jc5tFQ}08^<6D?>_cs(u>r^lC|6!{sp`P-8gTb+B<L8`YTW53=KfRgg62='
    'EKasG#5wLOTwPj&D)Sl&$)stxOxga;lJ2+lW;^XcCmcdM_m9zTRM=Jw$20Ued^0To1_=q=kEt%R_;1berjRp0*e`*~G3DJX(8N9M'
    'y*z^V@a;8oH-JaQq87|{U|iSEEb0<P0O}%J&$uqEUpd3M^SF(Q56_dlF2DDyX)SL6d+WJqnup{N3K+!NqEGM2HF-Bv%2H(CSzsSq'
    'V9o5V<@{sSW4s|;UHjGwuMzL_#%mB&0wUYTM)q11l1)m^m0m}`=zN17s&04EbE%|RdVZ~T(;B^uTrNDuHvA*CZFa(cY7XUF3@lXP'
    '4xXGcIl`IQgU`@!ML}d|fin>2-Zhkcc^@Qj;Wopo9$B+SZ>gQ-43nfiy$d`qA|I*<A`*Agj_EsT4ZncdQLa=B3t_5`QJmynqf*=2'
    'SGuj9SHUyP?+hI-lzi@;_<y0izb`kdpT!Bx?Fhy_{X}=LeYu3~$1N;B6-6};)5n6IVnH0_yYy3XODR9m&!v#n+iU%n`@nm^nVOKf'
    '{n@-@_PjDb!M^&Bx3d=JW)H;Gz7==-R)WIboNl2H4;qx%=~XZ=sA0aatzl$h19JH8({slf1Ddv`d+u->UkWK$7x@IdLw^$uRJ{Yd'
    'ldEXB{8D^Z(mmon@1y&p{a!|wqd+T#tK@S`@y#nBjyj)UT+macfQ<asSKudjupHQv#kcqS>omxvMcP3MbZ`LaJV;9aZ;F_`aQ@#E'
    'LAx?(xN(w$v)z)FedCR_eZ{-|L26W9LhQSX(KZkV>w*nS1!lnDfsm6Lw^nx4*!qO@dF*cvDgy^Ig48wVK=r|xKk?l`l#%(J#(f&W'
    'xAq)AxEAoB8o;;k|9;!L9s9j%`&QiT`*s{)AM+Iaz}5#U_`za+cHlC30bx99zB(UzT@Qdi?<+lJ+Pf?Caqt=+sN5HJu|>{e<4VFr'
    'i^CG6`h4HAE{JQ%`!5g@_}LznLV}|hbTb_)Lnr2QckTw2&g}o0>y7No2UVxQs?NyX6gAJb6|OWA>=cp@wdOsUU;ZL;@>EsikEgyk'
    '`AOvTnaV#NKUaAcllqDK<CI1!(w>mQ#(4r0A@#F*f;yqa+t&0W_2gu;L3mF+KoD{zlr!zMHEXO-q!{)Mn_XT^Z}h8F(4Mz2lqW3!'
    '-Va|h_Wk3SeJr`L_1e`Y?9Tq|q|nvt=mz9|mhq@sUT2O(oqLp--xOh(n-8%ji5#v3W8`Vmen{PMXZFu7&FfuR+W_Y;F~Z>XvXcXw'
    '%mZlpC+#EqxLG~J*762x8-zg{9SUBe$BwEbMQ*oZtgcCGaSRJP%G}XxY`{>4kr^&YqR$yC&1?>VNc3ZBD0g$n>h8~dzi17w{FuT;'
    'WOpYp9|8%OD!##;-`bTe+EMc8yBnnF(ck-(JxNas$rNy5c6=-2%-kL{0*zt2f|;tt6Kq#Zl`oR0(+dS$8(-FRaz{kg`sE{6c2MaS'
    '>3E=WO7uM{0Ojeg&pdYyU?aViN^kU)@aq!10UQ13F2cGudv?Q`n#Eun>F&s5vhN)NPxm8)F|YR{*6m?n1|YHKXE^tTDT0bu@51Cw'
    '4r|F?+2P7Pr2RuOyW5+2G??vul6?r|X$s&Y*E^(Y00Ho^S7`1DaGS4HeIKd~Ji2TR%u@OSfTLOAN64AnGTjfY^>xk{!G?nD@-V_b'
    '_X^i%Zh-@pcxgz>mq%47I8lvB_ACU#p@vmm!30g-XlrcP`2WU@Tnc?Q#Mh7J>^r+A2NQ{C9%&cJE?-3pO81N0jU}2AzO`Rm%}qat'
    '=fDB8pPIPqkz=P$9;-ZE75U`&=anbV0!fKfRepZ@L`9X3e$nn#Pg#uE?8S#`h{Kg!VaRN0rVN>648nGB7P;P01JfzK*wWf@>NrY-'
    '9B*ui){?UV;|9ZCowH}Yr^ewtGyJ^b%%3XH;QR!;sZXyA`sev!r(%jYX9=441NI590zIZZR&#SN0sG=L@JF-^>~&yDJhQs5WM0nF'
    '+x33H`?TtbN%0q;Ci&O$M&{|DGmL)+a4g;enilT@emT-(jXtNpc2=$L$y$SErp+-L>(!e03wTPn3bc`3Z*r4w=-Hi-%r&?ulRQBr'
    'O!n+Gngy}75nv{cSA7{de)5ybbDB?~q&-b_vF{?a7o%9XA(lvnn5&Jo>Rj#hZ2xmCwy=#ca{QB%)S%}jrm9(kKd`I<Kh^Uu%dj#^'
    '_vob5CntZ@tBB4e%_x{#pFTQZB^9k)1!mMz&UxG!bEo=>L|!GBBK+gexbGR3R)^)cLmta4DURZ;4KZA!XE@$5*Fe&WT;fZ*&z<C1'
    '2fdV}#;?|-*8z$gXV%n0JC>%FS~kYVvCv0LZ8CyIjmq}FtFnW)+&V!3>75TqsX9RrfO52j0#E?=6P1xOm7g9zTXp73fK2gT!WhsC'
    'k@%BNyaX=1)i*9eHn=bdL&QGdSe>zZ&;#rkAHmygm+K~ZR{2l4N&3DraBDAVrSIr&wP0DWfUBh)rE|FwF_7e8)yO^IwJUNiT#@g>'
    'wQvEnz}4cr{MW6;U*62U4s4)(*8FV~C1Bmp#_C|1XA7yjyn9G*_jAh<u?rX&5OB>fm~Lp3uh~IRy!b8{F!u+P2LM4HtuQjr4|H#b'
    'nLtGQIuJ2&{Fg*b)QpqDQ7KBo9qIKEqcPSRMMPW7A5aFlJ@;4EBa2Y!79n_$g(76qNanjMONQfp9lOMys(g=&4thqaJQ;NK?#_i3'
    'V^sLhsedN-lgDW9%m;DU?(F~$zZ57>9@#$%*l?MF*OPOa_dmA2?@}OVFFvp)Kt!9qpX(ZjcO}5d+IPm(Wd!OdA&1H*q7<Kcf(@aS'
    'eQTm5*V~($>cI!O`!m^<Rs08j9pwJ=9R5-FpGSM=_uqrj#CHDUkeV3q=UtZvIDY0u@n}EGit6JHv4}!gjX=*(wh>PCu|zydDRRu('
    '=7xq?ZEO6i2GXCKiCAsCh5xK?Zmx4<&^>Uj9MZcX2SL2S$oD|V)%-_MzF1Qd1KUJE8=)pfO1LmhU)g(|{f;xwSqF6&k(5AB7+i$u'
    'O$P>;vng@KO_wk!FVPX3J#sxaF^^t<tT$QevxcAQI!)4ZW)?vV#whgMl}A{%nug9yt!FUTR$pdoG;kvgYh){XzdN_Eg2~-4Z?LID'
    '70Tlx(1unwji`9C?JML6n=TmaxhlP*MQJh9UR$--2TZK^yu8c`O7YBoanoAwGyUqy3k}U*MH>v4<nGjZamn)qqdYwws3^$I8|Z7&'
    '*Mm_&V^=XN>fp1?(?R>jYf%)9H;$CMsB-8Zwt6Nq+uvJ@r~(9r#U6i%<+MjuWbGeRf#*^;tnJIz`a0%+x;c?vAGC%CuoECh;#;k@'
    'k4Gz^+Z2^D0_&gn^%J^@kIUDUc_l=>3G=f~8^z!1jgVh*nh18zWprd!k(&fN*26-ZF78VrmTYeL22ey3u;MCjHbzDP1Ys^*r<S+N'
    'rU;iKy}p%QUgWUV^tvcsB5o1|a-qsoyhR6DRlyU(@SZ(LuRo`7>3(})C%ZZ)ZVq|GdP;n%OHr2(*T~5#y@g8{61Yz52AZKY+IfQ2'
    'VU7ma@f?#M?l2A?h+rHjW{klh!I{m6_H-)u6gA!$p+B?0AxYLKFlN_e;L#8U&H-(c+MW_t#FPldm6lvYMbK58I9_o!@_EH+tYIlk'
    'QP%up*g1>=MlW*eOs06of|wW)Nds~+XU2J$gjmy}p;e`SII}da^bD%>3fGQ<^{b%?t!o>omsk<4MST_VE8fJLSgqZpnt&~L-fE|('
    'PVQ}bDclcV)}TK4P2sP_Jr4R@yej-XUGbo==|$mh>GlVGOE27JPg7cE_@3p)mjKUGm;<IzE)fJws_qe)4(wpL8xdFAhkx7|2Z%<~'
    'L+_|#DO(=3pUtuTHP<_A%@4ENB-x{t0^L!YhdkPF`U@U;RO4PPdRXhM^4zf#U!XSLWu--j{VvCnI@GKGw7!1=v_<GCTn|&}9mi-Z'
    'NM-M=wV~N|r~9$x<`h~AS%ss&ayQOAd**%ebT&J1*|ER6dq{5#LIYuWRV)0TU!1C{l#$!oDidb)kEx*_QzJE@>c9M$0^BfK(|)w*'
    '$gexY)#9h@8Jhl(nm%u0%O6sA;1Ai5e|d*I`^6vrN9D1qz(Lg^g^Ib8kHgh}-=6sxfS?GF1O}+reEhG@$b63bZ0s}xXbgQ^#z)`5'
    '=N`rfx0gGhyYR>GH_s&eG4%+&<>^UvlU^U_;J@f|)fM;S=|#7F%e)_gu3)vPx_BLyz9dE~FHxjBBQn2iQS7@+4>5WR*hfPCYb979'
    'Y;?`)>BdkMBA65#f{Yo`L#BkI3vlx|O&kzX^f2g~0OAQ`Q>Trrb!-JhM*+KeKuE(8%u|+YG4JB@BEwq>5bzGQ4lbQbuZk}un%i1J'
    'N5X|?<Att&)O<YHkB%0L8P>LXPuNp=L{vR@C$jdPHwU`26`D=Nh`)4x#yEG*$Zwe*60Q<1fI&Y7duuNH`h}lia=S@bU6YtCcd1Y2'
    '!JiZ?*_9oZEfjCIZ#icCHRhXnGpHrkpAw=5>(INeu<r;H5Ol=SaAt`gSCVYg5A=is+tg=_uS*zDV~dR$HD=zr+V09Y0Ba%h*iloK'
    'n|8lN=EJNwaU%G2IGESNpR!`A2O>PIrE6XcGKIKDZiiV18l#D?V+ouPcS{)t)H5%|EHF>Zyt4oP9HJR6G*N};@^RXGVMZ_St#_#Y'
    'E>3VGOcr6fx6)}Zcy#rESoDCWzc-Hjde2<~6a%~AbdI~k&q5|K3d9ZwCWL<V&YI!y9%ouWdU7V1e{M3zlx*svKi|aAVceHnE9n&X'
    '_NiL!{-^fns7mdDY5-SSSVP5c3%{zh=hn&{^i1Bx#4C<d4taWj&~1(N>9j^n%CA#Ba3%VR<1&i}9w=8BVoxW(yW+mA02~eB_GjcQ'
    '(|hW@T5E;Y%b<Q-iLL*o+*`GN->NYTYemm4(IG{-`t<$^-$XQLEo{mkagk=JFY~0Cg@*tYMdGq2UANumuu?2^17(4s7Xf&LC*(#Z'
    'Vx(|czA8!t9;B#)nYl@@c&UN-&r*4ULRFo_WRz`OyC6Y1`B+(pd1<ycSx#X1$Lx6YQ{5NRXBq6$2>e)XI>jY9^gE)ewz;X1x7?bp'
    'A(KvgdslgrT8{F(Tugy7-$jPE9tTc9YI%G}N>B3T&cqy0^NTzN!r_A1nPEWBU6Pd9=|0~y-ZDfeU@499WD>Qa<wmvB4&ctYPXPIi'
    'Ee%K{f^U@Lqy{s>FU2bMg|m6g8n+ucPpR;{L2%b9ob+iWD6mQtRXhQPaQabT-4mI7lKk-o?kNXIK>F-b$mJsJJ69CzMd61cb{t@p'
    ';1pS^2sw`H-liHs5)JnNCb7L(DG;}>oUm`G&`v8}f}<>DCJ%9BgqnPNNR5M2Mg%3qAwVwkLTF%G8<DR-U}vnC(JpRLr_t_NT{Zl-'
    'rqk>UEUH;YA5%b{gqe0|-x=|gkqDdyav3;bMen?=Is<BDifvMNS<B9`k5vS0c-ZU7tj8Y(6*tkm`s>Z$N+()KzJtRK6&Z&Pg*#mx'
    'nBEd7LEgbSW5b^Ho=JELrieO!G8qW>`D1EG?aq%WOr6VJF{Q9pCROTAbhaJp&&)lepc^NRC#7MXY<C?1CZljK$RCr;g}c#|S6tWD'
    '*pduYt27!a9@I&q0gvdfnoj0$ndzmtP(vuLqsiKMyqvtd!>%wYPaXp|sjM;7O6;*|fPVKv{(0p~4qCay9Ym{dP~LByU|An#_f^9Y'
    'Eii<Q!NuFaAqvn&Y~nv83{J7!G8mv-#iBbbb2&#>7Y>yRh?3jS`S~K`T%mED-pB#Uty?0}<7%M;*65IZw~I;z;wuKoz2XLbNKL2L'
    'H(3jlU3z4V43*ejkA6tqv91s4@2p$X_Op8>nZDH$&Tt4#a<bfIdUF%CB>vpLDoGCq3veU=`{8F_&gAZ}e|{e;_^d0VAa2=%Gay_U'
    'XFg(UuF!tKS4%@y_ahNSpyCuLfIvCw%P@jR=-K^n1$BsOPsT<v+bQ&hrg3><Fb#}h8JM!Ruaso33|XU7CF$*XYv~27j<eQy+5$^#'
    '56qY3CZ43XUphPCtWZT7yqw9typLb(e9xSR2wviRNSw@=HjH;l#1K%Ha87K@-BRez1hCQ^NT7<4s5J}YA}tS|qq1#uIs5Q_u6F>L'
    '7z$8uos5b<VEvTwX>&tetjVYn_VM)k_gE5z;t}lW<?QmJj7m6Dd4l=K&yJstoUHs5e*CY>;MB4S6IId@c2Khna}O7{mlho<?Sxkh'
    'X&nG*9d_yw{sTYluGbyb=rByN17}A7hp#C&ntSxqr_LVFKe|$T7o39_03f=9&#{BkWOmrk?q^?(c33ZaDGO~X1?axRS{bl!KI_Q9'
    '{I0;mt21`j<FF~iAzYI?`^6vsc>G-C4`0GGPgI;aUiGCG6wwsB04U%aLYfSCM)uxQ@fRl5=zpAE-!d2&4F*Od*ENpO-cC3~GA{dI'
    'fHW8&4QqYL8hC8*dMFup->Q*$)0Np8aDwY%Z9s}R+Z4ftk=YuxMtTfPnL4s%q&ElCTdz$Oj^J9`u~Yx6@=QRihqkPcSQkY?$fe;A'
    'wK|SM!3C09iPlIBc$wjVb%w-3sTr-6R8h2C>V#Iz;T^bW3c28Qhy4o=iIr0;T0BjO)=$0h7O0R6w|?A}i@T+{A)1J{UMg(;Af5@)'
    'X+A`W*i_<5AyUgNHA)P*5%8y2gpgR^vI8e2mEj%(h*H=76fLdP=M}vC(mug@&VY^-c;xBOd!P60P=SU^m-%gTqRy@Fl}|AIs9#Vr'
    'b@UE#(;m%dQ{C<+J2%DeaPZO?s`8svr1Tq<fZkE#NI-Hx$Q?&oKzWJY37i*w?Kva$?D7JWJ-a`Zn@&;1EQi)G`EbJ2g_^9~C~((_'
    '*OGHAGQ1unqK|^XlK)p9`p?maz7sX3IL@j>n>+MXoNtis@~2dP&9A8pK%T$^GqdYbSTYm5JS-*ARZroF11T>(7B5$p2Cq614?&kt'
    'V&B4@0R~@hBLquvpER{)FmdtKRM!AhA&&npipuzbaccM{YY>BUhj#xk0Fz&Ls4EWt0b0XkTYY`}J0JG;bD`C8JA{&uw@X2Ez3Hu_'
    'YqWFUGHaPam>&PnJvD@W&7eMZr%M#pRDZ6+ZI`nzs07Tf{OkM~^l`q*!v$1ud09Ut!XHTcmKj-5GNrC%113|R8Vd1R4IS3Mu|Vii'
    '!BKslN!RG@t`pjyI!^oEmDJV)7kXk_2@js}T$ybHwQ#zQ!>xh~Igk>DG2H$Bm(j*~Hf>)@IM;p2uZasJF_>#fa(DlEly24P@0x#*'
    '>aDP`F^@`)>h9&fe`Aj<9JFHMUivv5@#K1kF#qQGYb<OuvVw!GKT#39tgzEZ0|da;v@jTgbQ>PUS@zWmtF5Re1y>b}eb<6?2@_$g'
    'y->L`IDZtnw$pH^Xu31U-rCmoO_5q!8yb_s^lsXB7L;ZT3#rIk3fz}IB<!{!@rcrD^`&?3WS6`2@wjUgat3CBT(g5XcBc)D4uQ*~'
    '*7htsVMQY+^FX)U+t2OAEv|Ykbz0giYNNFmV`WA~(<PO3;XQG9nW=Sj0G3u0_QfJS+N4OWLz#K}+s_J2g_-#+pD;R^0mH#fORQ^;'
    'vTxLn<qd1|20NiaCz-{5{N%+=nlyTtN2)5%p(A7EStR#B_k;Cc`eZuvu{-o-b?6i6&?nNtQ;<KjS#<ytWR3NqGVRx$+?nsu&?c+L'
    '-gDgmPJIO{Ia+%3*N01gd-!On!G1QFR&Z$tlan1eiqHbY>c<p-?g)bbCl4Y?b`^nt^TCg)VSD7dwTUj*m~Q;lAaL*Y%|5IOIQ7z6'
    'TN0NDz!dN1+<kO59iFwf=8WG^^4788+&w%Zu}o+%`o!vgY<y&}ZvzJeFzf+ei23Dvo@FL>te%_J(o>k6Ju`+$YM$QCzI>J57(@~e'
    '7yQ|c>=NcZLf^fu;p{HCE#gg>qS?sli^4uPR$&a}DyA{NI}Db)cP+iXWi4-0c*1ZNh8dc-NBS}C5<q|VLN3*h!^nAXEW?kfE_?VM'
    '4EAGcfF}y8%{^WPhUUlAVCcwifBkQzhP5~z7FP^TE4zeg>G^Q1)C6`5V$;es1`EJY>0P+uYaAuP^|g)DN6|x(xeif-yT9c0hp58`'
    '-~t(IZUOch9s;hipKsBH9LhfI0zUV{NK3ROmMA%W=G3QWD$kxheBwh)a5=nY^}SB-OcyuSF?@e|rsDKx1m!3ifM9?HG62AT!zdTF'
    'J)oE=njXFNOeeFu6Y2GNlnw>Ddcujv(wigV2_kF2sn>G7quJ#}`_=?ao2nWRw6mud4ht*9<S>^ovZ)1#iSgl419~Vq@~h$_04}|-'
    'wQvN@i3`!D_`fryIr?whqO|zmL<=@)cs$Qm*0a0QqCf#f1BnwlcJ4#e#o9N!v7GDp5+>G~p0=m^0Me|*F*dexPJVPEog2c&w>G!}'
    'mNk9f8ov+Z#Ok|V0sv~=n<&Z7rLtFn0qoCSexAF0-(Gr!uGcUm@UwR~qf?~fi>gyHAgb!i)0}U}bgdj2IDMU@o!7sEV}q}bYDxhL'
    'zVxEnx=`2^b;w8^L410}M^UHvE$6G!#CC*6_=4nqq;e#<dxE57be?wtjx_0g6Cil`3kdkXDmVTNk18=>_EUUZjOz~l`S_oX3!pyr'
    'Mb+sqs(w-yeH@gm&iaNpCC~Ko$?_U_66<@oUsGEKol|`jXb4tzYd2$wL^B5Uk>6&_sRq^=-g_(jS<?KiniE=fBgIXf_|U-Oxf9F6'
    'M}ZlfrAczsnG}Iy6tpS0tD#Lmn3>I8!BKr+@a+ch@$p#J#!U({*c`Q1-f&hrpv$5d<-`-~{tEXGWS3HmUB;R^B0yBcAz=Gw&rpZ7'
    'aTA-tlsN6)&6k;{vvz-qG|f0?zEV{cXa5x8;INaYj-Rc}ORoPCf_|$p@CFm<^;=U@X`r{=i|O?%R`=8N#vN<8D>Lz?^zd(MKB$JF'
    'a4l1JYdUZ-3#0fCJnVpV!76|n_u(&24BC+!e@X8)Zo*$uRc&@y-RRU#|7;HNuYUXohUSMi=vVh5zq`W!4DdgL9q?ynxflK!#DA>*'
    'Vftrm1pW#Ct0exoU6kbIKh~7%J2Ex2<(Rf}>uRp|ntks<dSmWC)-qQXGqaZg!@&a?e#$#|wi4+8{Bzp3feEwvKh}P{vQ=Z!h)hq>'
    '5IlJmkS1;u1`7_CfY8eP57uw#>9&?O@S1Es<@|{P&gl5HasP0p;@F=mtAYllh@&wTadZs>iGE!wBaW_#>A(j3j-mAT45n!7q3Ya%'
    '(vSgHvGL5Yqw<Nq0Z`7)Axv|$6mJ`YHBS;V7?C&Y@sj~_3{{_R`zTggdiZ?XZ|m#pafTmthIt-5J)R2eR&WqbdL6hrw9%(`$0<`-'
    'mo>UVtS<&8VA@H+42bwRo)pK}<lQphQD9eoNZmef9&)x1_!QWhA5s(ewEaVUD_yU6-haqEoggwndTc&z1~hBuIwl_k&TVN05ysMx'
    '-8ZHu8iGkwSKF_;GF>Z3OQ&`yA>o=m-VGdFO$`l+s}LZE7Lb6o=sn8dvyabLpRa}mov-P5kM<gtaK7fem@=a!r#QBlb#87Guds|C'
    '0X~6BSPOoG8Um8R#G6Z3(p%50{srI$-IwF@%BqUUpDMp(a?SDy3`KTa!^E4pvn1o|C@WoNYf&a$a`kbx>eOjHc+lOI3<h-dCm-sa'
    's>H0X)~ttvCr=-9g8l%2@65QKEcRzMAA(Q-e4z8Nf5ToH@H`Z<Af=A<JhT>WSp%!;VYTPAX942j=xBC##oLj{#vtdR=ciIj?cKDd'
    'H(8<Q{hS3K6$VLJpfdf)q<C6pwr^2VIT3958Qx!Hc=0Uo1U@Q7w(8`u&rY2Y9M!L#2XtU3j(>i<3ZDOpf)&wL_s268pCaq^k$Q5r'
    ';`7rd5Oj~0ma2!JBdS16F+49V{uN6l;{LJ#!iAXi>jdz4_S9+P!{Xn7JaylCa}~V`XV<OWD}e2dBOm?mBS(PUB{|!EF@?cPGI;`r'
    'Sfi$52ea#P_$$EbpHH3n1ZG_MKWRRn9j_8hKHU?X1IZWG@J&p1ur-mpcQvy#j`x}F2M~9W2hLi(X>F&{6i6<_JMVT1B?O9IfLF(c'
    'xH=dJf9!Gt{4on%hzsXlacNy(%B|6P<fB(tv&-xBydRT7+<VIM7?t-T3PKWIdb5XxD882N1u{omijx4a!7Aslb;}zZUE<lVze|RK'
    'y?Ky=t&CbruhZKTfSF+Gm`V4=6hNtR0zgJ-YAO4m3$e-N`zoLpH3`|(xqul_(9<WC5z2xmqmO5h*_oe4tiJ;i(jqxq+@AqboJ~<c'
    '=I+DXl`g^<vNMSyJtxQ~gAy8uPas6WUy2>gtiG_<*E92P1bLPXkeOQ?V_6QwHf!~=<PnDWIcorNsY$FPcJHavvve;d;@_W2Z!I|Q'
    'cBjxHzOrh~&rqp=Dd$!16KieNdXs|xXO;muzV!!85Vc&b2n{H84qwlX?I6LJp+xaZD}X)VG-0}|Wk4r$<0F*$#C#<m!s<E}{#caG'
    '-I--Z1Hjn2J|rsd%nw74xKEluCN}tV#Thh_;XVUwlk3fI=thh8$38oL;uF{FBl_#lzW9Ud<%gx-x10`9oZ(TO_??SXeDVo)c$C1G'
    'SO?@jfe4^OT_3ZbCCp+nOU+jvt}f~(0GhU+z$<j)+ns_xe(1R_G%f=-GYgMIJp5-Br>g)ralYgLa(fW&n0Q@chuL?GrLT%F&P9$@'
    '9QzErZrA;KSR{$I$y_6mZ<=ePUxny#zN~u8mmv})hF_3&{@!2|g*RqoE7v>hj~CO&DUgVZW>Abj<FRD%H_`u}y*Gc3>d5lN|CKw%'
    '`-W9UmH@l^bt{^lpl!OFXg7EOzxISs*At?E8nlf{vgrqkkN`=gT^I?Wg+(9<3}|7MsFF~`{3&+bTU9^xzwpkInKv`<twmt>c;0+_'
    '7)!cK-ds+eJnQE)cg4+2CGkDjz6{eO{tgTEQBFvFB9JHScZZUc_=0K4*B=U<yw1kt1$JQY)Kwd=&_tf>YHyC$V+;jHQZ(3@pm_WW'
    'tf3U|pIY81wMsl?$(<1GY0$C683&jRt;Qh9$xNDvmy@}s<pne83h8iU5ET>oEV|0*ah*TO*@c~sT>91;25@mN$FJum?*bvH7)^}7'
    '8GMLk3aNSiAVL>?mu8ENw=%*T1zFzqw}qZb;2WwP;2gR13wHr7_A-(>bj`sR-b|sLabzuj|7PytaN)sNVR|)}zVD8|*4}@V`}Qh)'
    'M#n#5KFFc7!gxQtBS(~5nS-%>rPPsNS}`pnW4P4ON{Kx~E>yMyB?C8hN4r3bOtid;RPP+4XpoE|WkY!|J0{39Y6R#FH_UjAHm2yQ'
    'i;es6WOD*&I5Lyo{<s)#4Qx{&`Npp1&Z-ubPEURznN36AjhaZT6mSvNBk}QW>+zbdc1jwqYP24t`E8_VHg1hd#>7SRQZ!nm9b1j9'
    'ISb3$*xVe_r9cyB63M2<jx$w8OcCJ@3(R3V<U^Du30hf%;<`4<stg-T#BSPLhJ%59MO?|1F;^5-YHLH-L<n`}{05F%ts@3U#Tv#T'
    'W*0#n^!~1Z+{8}M2>>q#m%c`Fq!1`JZlhX}Qz#D=Hx=lWxz|GA+K^9BVLm#QC=1|ber<`(L!eh#42v}SB`gfFIE(7$OR}xLc>(7z'
    '@`}~P!u@^=N@4yH)p-~cXZF1T?*--u;OX{e3OE}a{PH08d8fBEj!)j5U^^KZHBgRn$aHoUd##LU$l#{$<(BSvz>!nWP==j*3|ct}'
    'R>aH5P9jD!l6LRjl{zvB;X^~nWvO3?&;$}+s^23*%ddXNgvJAt#?m?UJR+a1PUGMy%>dK~emA5dg|RRe(-SwV9l`D>cSdrJV16-G'
    'NFpR*J;Kl6o}!`9bGqW=zSNPg^`o92Xh0kSzC!+QzDk9M_B#9hx5*#Qx$a#pRq_qV;7wMF+f@ujffQiGm}sLcmzRCu3Ty|Rmd1<f'
    'ixSffac&PbH@2KU)976Iy3VP(@OAzE2Dton{k{gPKitlT(2?I3>w~e*_2SP4l1A~0nYwK}9J-;CaU6WCInjC{sY#S21R<q6DNI;@'
    'R}BrJPb)nq_Co)RmzpT8L%|059mqs8ETFQ=W!l2TCgUyTj;BDof`72*+=VA-iA=qQCPnzLsl33JE}<7;ZIDUW_}<ne?6JbFSNKhM'
    'YVjKTM#X1A`(gj5)F;r-^%fOrRyw`uzWmM`!7WgG=vn6X^E<cPvAe;YN(Wl3vn|G(=|4w2kyQXnocqS7!jS*Xf#`=1-C=Gcr42!6'
    'P3IHMXFB3KgA#Yz8#_4e?LT8W6p)9Wq-@I)s0D=yHX(TL*bE)PW;up%i1)DSuzrW+`AttE$9Ts@l-&{~9vH3O0YP(rkR{TyQRg6N'
    '+7tvTzb#v}=d);MLj+iy(ev@fGX|<lGB}5#ypl;{$oKK((21nVOCofY5&hwcl6Y`5rVLcEh-J~PPU+T1%1c~Uo8o<~Me{AALF;3t'
    '3}<z++WbNo?-%S|b8?RiV$_YmR-LsRJeC5?E+1tpQC8UO+d0b1MAfZU*+Nl)B@Zcs-dY}T_P3+PWLZLJ!4qdhPj#e9dZ8p(Ek9#@'
    ';8GZnE15T{t$<+)05w8?rNmV1Mocwm-nLez*nM6o{jOky_N3IS2_cavFiaoX5@)oe@yL9QhYg~Myr4#GwG_0Z*k86<wr0p`E}&Ed'
    'K5sQ=&#2aAb26<Dg$O$qo)gcd+AFdEaAPx%SulP+-~`f~5MTYwI^7t@Y9%H*y?hHA4i&v4X+jDpDAjLGsr*?s@@vN3L0#M7oyOK9'
    'P@@JOi;siKpgWTJp0F89vlKj2#MKSk-uaIW#~beFyQqLXefkwOhgrMs;8Jcm6BK0RDG0lY=A&Dy{W%6Y@}P()97!XuK$T_-*?^Fv'
    'MzU{*LE(aDI2_DDGZE;3*U{VQuHEnk(dva0O3j4aGjkV6)4;<@)-Y1l?!>*lWKz-igy_Ftx2T>Asgr4u(m(3#15A#ruzV8@sO;ZS'
    '1KRFK9d)@EU7@mQi16TV-vCKC;K8T!Fwv3h3_q8(15^zZyGXA@F*${gXKv!34hA(TeTe*4V1>j|1O3$0)NnOKnTzf(k+=Rbo0%au'
    '^tLbU@@x6&jQ47T7ABL&`ii6ex>@h(i`K#hbT)aQ`Ml=Ce~z9?ez=Pm6SN>axrZ?(FkO1(Da%N98s-3%Gi4JC6>)K9@kyRoOE}Oc'
    'k{&YW(VKtbt_z0|Pa_6%&~Mi{q=V<wAN_$M!<RuD@TUyZfUEx_WPk@kMtI<-7-BKDG2D-iBi;i#L2K2NLPBE%iSuacO!*XspjSi+'
    'l}pR^T`P)Y#@aA{tmXC4BWtW3DH|(ipEL?>i4#P;Yb0eiH8)}^m~nuJf)@b?L~|mk7s?~07#)F|%j(3yz!DC&sYT#UwIm!Fz^I)9'
    'RH}EjFty{Rmuq!Vu9mHz98HFC&6!2*j1jSm*bgV)BU}e~rlBio{96p~rEm~nb78ps5?gxmjXQoz6n>_TWBMYpft76L0k;r5P};gW'
    'EsQ9dE@^MF^e&stp(4b;m?}`HWj#Pz^d{PXK6do)?!s)#EtFL?VUSXu=Qj-cfx8;*oJ};NTx3v=_$l~-FLs!elgr#i22E>R%S6CS'
    'jg&#_kG^!We~YlfwKqyu5h08dBi_9YEEhVJ(g*}k<MG9O5Bc)%&*ul9|HlR$2ZJ1M=t~oBsvnK%cP4;M<R5IxxJ2=s@JkJ2=Br-I'
    'Lg9$GsdeiW{37Kz?22^6goTg`{S0L`@4w6@Spmx;x~kHyDeYQxM~DD+)4QP;A{17cOE4NcW0@<$ikKtfm&hzBs8lkYSxb&pF{~DE'
    '%B7xy@To$@O!3vHFDeyLPV$`%5qEX7vcfn1$BDkdG4F=U3RD{kUE*S_?)Vz!Fkq@ksFkp^ZKnid+l3+Ca`oE`PjmhI@_uQ(NK#ug'
    'WHavKI4mN@hHQcg_+-@HC0K6_-itS%5ntC+_{VPsf*>6|(y&&KFs#)h3!Ng-ksK7s#&P8w(oG-&H9;o6iwTLSZs_%wq~d^`w+yDD'
    'M3ohFsoP1<U@A5h1V=drYv-A)4<GoAv#%_JOUBQMhU)Q^y~OUYHK)$8F6?y<e6mk7sx}d`#@o<{Wg3KOYqv%~F`u@9u#F4Eo#Rp9'
    'TS1%PKIb<F>B-;VC&SEr)Dd{X9v1w!z7Fn6{VU>}>cW_$I$%o(yl74tJZ&}3Yf}%SZ{XGpdgZ%DDIcQymc~RYQKvGiP*!j?1LVV='
    'Nt{V)WLi9M8)Y>aK%y*ksMv9A3A-FA-Yvhjq-Q8-+}a2g0ydZ3MzFP9=9=5z$Ntpp+uOwBQ<e``0Ha}nD|%&R4lAD1#-WTiGG$Pz'
    '0{VI?k0v3vO3I2U!GP~)cXE$1?s5iNQ8AFLaq~0U*h~XP*G0oVWSE445{2@jim5@_qU&PNxE1126;K#H8}@AyBGz33RoGBR#erg!'
    'Z$$4zGSZ-Yf<e3()3(frDvGiyHiv8P5JxU)=hnm*-5nGvzA)|!Y#KyGGWUQ+_98dI4C_a6fwBUOI4qSP023|3CMnbr1<BiYj8tKv'
    '{|V}(<htC-B)sIvuwr-#lLD^a6W|bjOfm&HctbZpamo)bxo@6x%Y}tkB<IemScSsvdwx1J30No~Z;i7hhI^RHz!}lc*$lNZmtmwZ'
    '-vb&KqR-u3(pyJ^;?+L4A5WZr`Jc7_vj2cy6irdOoKuUUx%8k+h9FjoiReLCs|(*$W<R;`jgWOwu7#HbQOnO;BqlFQ0)e-GEZ5(`'
    'pUe}A!(@>{hWMzG-7>-O4~LH)K7Qm=)*lX^s6<y#Z7|x2vSaRY26Y>e*kbY#ZJ#x^Hl1(lh$Z7f46E$8!7@>1=rP6tPt2p4k0YN4'
    'MmSQv(0haRH+S`_N|0lf54D5AQFq`T#oOcYP?(&}U3sG=r0H8Qu=y68Y-UVtfc#oU*q`W>;@#%<rO*aDKd85iaX5n*YlyZ7dKeJ^'
    'QtNOQaweWG<Kqx+rQDt^R1dTsu;{MR3vzc?IR2Ug<k961-+gukLzkW|$e?Nw?+7Xav!Yt~BvuHbosH@PJ;xvbB4cTO5L9mNeQ+z|'
    's@eM(b{djj1^kntq+4U>6XXm&pl@6dmVU{I_F6PeM`P;+@|@XT@yhpSS@*nO*8P2tCNV7b7RP)MT%0{M6kp@>8LfBpCa^9!IbjfE'
    'G!=5IRegkyi6Dm>BjO=yPhU;4t;6vC>!n}$k@~t|?u>nc%YYGlv;IKCdz{KdM8$$a1xe5|#Ll%eAFD^FJK~KOEH`xA;5DRdH8r;('
    'g0-YbdPcDsgWpsuex-4q-~IlC!{RD$SF!&wkAF5qEfWfv5M9yt#5{r&cKDMFs9J~-XW~`Ddn|rK=l=KC?>pX|+3e<9c?;v4#H|M)'
    '=%(B^uQ|?McYh$lFa?z&Ur{DpiZ0D&MswF*XE(<zcFVX2dY&&c5ih;wr6-_qj$}p4$JQ+Hx!mm@@xWjY))#n#RRhHcfd|7RZHr_+'
    '4tPZblj(c#2^LAomyiA*xK+6NNRJe!x==m&R~z2&Adg~~!nKS$Cec;6osDQ96`0)0Avo2bQzlhFuavMQsU`%W9_UucqFs2VqfJ!M'
    'G2i&+d;-o?zKv%rzAUH@k8?XOOQUwV_vqc4_O|w_eR?!9@E9atn@>s=y_n6x*mQiy4zgmqK*W(?|MXB;IHMk}6h2+yC1hlH6Mm^k'
    'eHb~s8u*DEBcUdg0xlEch$t2yWk0GsD`o<#75MF2U;@G4dQ=uSSr=N7nxnRSf(olpZ4Hf{tH=K~SRah@u_RvzIp6~4maKWDNbI)<'
    'OVL=FKyNk>(P@ieG4Vu57!*Df1cxZyFuYboywnIVc%;IoqTvK3>ZP$%qF_AvPn*5Jqskc=yLdvLX~E9%#tq!oOAs2NAB5mYh19BW'
    '2)cQ2EaN>|@NVzaa#`<MV2!z%ZBCxcP0g@0tcsB%pOy?>!QkfaKgn(mWw%F#Nm(E;-DVLp#4w0O7Q0SYbwo~GI(4c3ua~+TK0MWZ'
    's++C8)sD7LtfVOMTK!*7ePgED?<JEWzp9%@&vdl4EBs0!*2$Aw60$ZKl&#8ViyEdF)bNr>9Hj9g=F#FqQpxRs<J6^QvYVr#4(*Fa'
    '3~#CqWSSN9H<H<$Pbv0s=H`d}*<}c(!2z^b%|Ft=!?&`VE7(nj!&sYy<1Mscp>-;RRF_udB8yxrSdmLA3I->WZfOE&ajg}2hfwwW'
    '=BzE^->5g=q$nqiUW<vp<bf*7TxCv3(XYt?!Zzk4fw&)1=_FXT1yPvYwp}d<Y!_Ga*)0tc?Y4PMDHJFy$InzL@lcDOkJmXL$@1S~'
    'WKje4I@!YTpZ3J-$qlj;o`wLUi@n%_;M9K7`iIUZ)@md><Lx5iUwg1xZ=<%>G1A=a!@8r9fR*d%@%jWB&OtoYTH{hD385vNi&yP4'
    '+W*KgzKlJJHUtHN(}{D;dE3LzcepDdoBc_Hi{ZSTB1ps=xHq)keCX`2Ij}3PE;Kfgsj;6ri<5%A<;g**NL)}~4eb{{jv^g4Sj%Bj'
    'cuikPg)30H3?B5yhmj6Um#^{YwJ-V2fOV!l)c8r)NhLH%n>JLvzndCtOHR9PEfpuJ3~DqCIr5YZSF)^-UlUq^bs5mLQ05m-(GheR'
    'WUgVo`nfv_Rl$c`z#u+K=oiotGK-b@HkoB~M=-vH!5{-KffajGGx^mOX6CH4FsxSo)jAn%&`QI22>A`sA^0}NeXT!pd*{8uVRSZZ'
    '&7uMWnhY6JxjKkSa{ZV62OPekgC=A*2i5pMiOKhjxczVjVHpI3vV+qiGv>`d_QqGRqDdOg>zmq0{2W#;>Y^Yl1t{3~TK?@@uje5)'
    '-ZO$$ogu`O@lq4+^;<zp8Tw)5I<Q%n`=(F4a(D~;39>^86V<z-UF=eC!y_dTx1EkLL*>wQx^P#BQrUhJ2;>S$&1mb02br}dkQbH3'
    '#d#|F9J1%7PFG5%)<#_)7e{Te<5eE%!4i>?NGRmJ-mq_*B92uIz@joqT1;9v8!N`bI^vc2oytu6PpAU@{1>*8FYB$zWEE3qGWu2N'
    'ivK8jQ;3f)s2a(W=WIv(-^-jk)^Q_EYC4LK#Oa(B{;w-<5I$Ub?m&)L-Ua4hNI`6#M-Jx$@bQV2eqpH&s_Z!-XSxedf(eFr7D*7*'
    'V#qq)$Q5Mqn?ow#6%{-U;KyCW3|%<Bl*ym75rIJ)qZN5FKD$QWcNvb2ptHZiyCLlQ%o)QxpNvy-9hzeFC9r)3Aqv0l32;6x%L;_4'
    '{l|9;h}~Vqz5zsIZ|Y2ZjS%3`-7pH=O{Y1{EX1|Pf-&DNg*;UGCuIuoJNO>>Rh{b_uv2xRQnNDObHZ_m?|~k&#Y{YqL5U(4!am!?'
    '71Nu-sR{9fgoY6y)$JmkF5X(+ML~pJ%tfI`SmJZhkl713EA%jJe8~*l<|}^Y4@=eOyH1-Nu8cfg0$57nljWX1{r>uWZ)J(ACh+s<'
    '-Df^R{sRRnMey9cSn?g1qI}pCr$LZKOUlL?xg}GDtJ}zk=7$3jl_R<In#8OcRB;LWqYg6~29eSH7Yy~pPF(HVLJB=#zNa2wxCb5!'
    'y(&kPVmpv}koh`Wfba{G&qUe5^fLQPyp3DlGY%&b-~2f02$PbWUF4+dd?0|TY`ULQV=$~^<GG3|HlkY>ABstqC|AJQ-O-l{$$<h='
    'd!PkHLco#{On&uhC4gJ1CvN{n{^n!!<qVAG2Cm}6^Xs*oc%FaIi`6p_nErvAe#nvgyA;T^d}_GGbph<`IHOh*C&nd1o);-|#?uv_'
    'F22dDy(@XXO6}<ogO|oKq;RP1VH4IXfG>f2HRzPlq^%$`2@|&}P8GMmD40$$CQnYJaLBVV4Vz5S96=~63^AOEC8;x+I<Kpkb+P>Z'
    'TxprcRncg%G;Du@RcobEu$7D~eQd$`;)kQgqT3*HA^e-DE36M3pa&E$2C9;^D;z1dE8q8vu%N_{EM}(+PWOAn62UZl5)Av{4us(@'
    'goga+O(u=0H6irs{=uOfyAltII-E)f0n!{9iYK_+=n>x-siLw3A?SfxA+|^@jHK+m8-!O1o5>bTi0Z^?;OBc3jh5s>C!#b61rR8W'
    'N$Nba29O<}ml#$XKrV-i7IHaXQDCU}7M<K?`NmF>d@pyAZXS3KaKZP3{4moJif7IQ4qh$EJ-9Pei(zrRyAIPSqo9Wvb&Ls?KJ8t_'
    'mq!B;qb1|YWTK^rRoQr+%Z#%Muj7V;%=FuXB$1&iJ^dP8eH78kQEsv-hlp$u5Nzb-6RLWVXp_>;IvFartAIx<vOgX$b6JzChnHB7'
    'sKAeRihnBF<a-$Y#Wq8`jTG%FvgT+mKHj9-`Pc0qK7PY`xS=^;{xi|QGw(3EN_vJ$oEmOf#je7!sz!?sRXt**D^=oQHzE<5o3ZpJ'
    'GoqHpb~+B#8$@9e#7txSldnNTQss#h@8<$0W=X19y;4?o3v|X57q2p5{uQXdv~T$@vG(b1F00}^h3U=0G^)L#kdpK1C!$;oCYC;Z'
    'E3%ZSAE@V2ClGF!!iLeTHjt)jN@&&-imnXhb)VhIrpIz~tFEkl4Bhf3-(rD8iUfp(HqlwJ9v~Gk!qN)!kMwu}G_g?(uJiri(tcrs'
    '6Aqr#P41wYM(DdlGE8xQ5z<UvMD>}9RjHajID)M-Pl{izh<bv#fIEE{n78&Ozjhnb%z$rBjD{+K8d8$;c_P_a&j4)+wAK&`$)VQ2'
    'H8g~4YJ&QRP6WgXVRp!@KvhvyrzDbf#1q{L?0^<S5EP7zVjE?+ONuK;@w#JnF`<of1+2o=Z6qaBo<)W@rsz<mGla_>oe~Seg<(WY'
    '$e>I+6iJ3M6z){3jD?F_!W%4Scb{s#6qFh9K54Wce9#@?k-`;wus)hYUkZI~G)$emF&2`xMK1l&*(Z>)`$y4x%G*QQc>JgK>u9%o'
    'M8Xq7z*F+8FmcnUMezhJixBe-Pd7GQz}PmUI+-_gAG>&O026k%$JEyh@P)7uS`t6kWCY=czMzCH`OPHFTTPf2FAqj)Kv${xiY<#t'
    'ZWY;$>DFlK9W;ohwwM_R4j0;*nz}kbA8d+O$-x_hWd}%O9U+xPi<QpBYc&!d26Z*#lA3a{$3o1!+^ydnboSBfsN`uM3eyg#wo-T`'
    'f~qviER?oggTr-}vY9?6U-m|4Hzx~ouX%rhjE<Z~vMj4EB9>zkqvBoVR2})uxn!+KcQg|0Q;1L+TEx_--N~7|a11I!sSbbgQut*q'
    'wkf)WP+n$7t4q8^<MUtP2>!BP5%qfm)L!aMvGDNPzEY2AqyUeS!@K0;s;(-|o!q*`=C8ZDq7^G+vA3o-S`G&g1D5P15f`G-ia8c='
    '7~=$}l<#*(ehNE>qSPP3)}g+k;a3?Kph08wAuU(tPARfJ`>P$>IA-{f_BgVH<*Yhvl;|TR#X+?AANdUNA0EAMS7&tzW{=|+(&Qi?'
    'uA|9yHl1IQw*a#i2^(SP5q}QFIV=WBbvJikXA|=zr&8XXdFA9NUe{7!1BFz#Org-YwB(L2dN1eP#V3WS0hAuchM`k%ODnW_ozHr8'
    'MZ~sJB^Crf=`l)mW-}!Ff#63H5(q$QwzL(p9d{S&oT`iU`x>g9i}m{(EZ89q(PSSUoEdFYCK*mYwVo#0W_?|F70ZSOJxj2&Gv3nP'
    '8JCq2;}rJpSAW-HfLZIGd}J5Rb=Pn68Y&Mvkcn;^3bg>WBq`uee#o@=B$A2NWM?Cu<M15Fy|PX-pUCGMlZ2l@_xvK%$$;;e*iI9D'
    'CwZ>Me=s~pqO*xzB~(eCpj#s5P$NY`3bB$n!X$|IsjxVxnB9h##r_{LB=pe0)BK(~;iBPhKo{wyy5i@BG&q5_P({yLB$P1HwqPEq'
    '+M)#NAf_6!^eUj;n;&$jJmbFuFYS!UGWNALNuUOrnsA5iGSHMx3{<=E^xwE@c!HGm(_Lthr`56U`kkqeaLA%=5zk3zSdvfqL}A$r'
    'GV7==wVma*to~W>$?9E-YzR&i#yMCN6;(1~ERl@C&)C_vF5ublwP>g_NelN97evsUzookvNf)>Sqy3kIfW5>#vW}TZH>WtDhNP9G'
    '4c4P2XJ|+X+$$COG$mN8@=*HyhVwe|?l7=4<{uSeTKH*#b!mUvyz}y5lYxQ|A(-40scFtFnh@w!lQ-}cMMv!&ZKvZvjvbPi7#fQN'
    '6dwn*v%>~$V6a3RsKL*l;e;8i7R*T-d_cn~yfV#j31*EVE5qeflCTrup|Kx&K!h0jp=bDq9yrAZshH#KQ;`qgP=}+sDtfB^R0Gv{'
    'yxyQ?!Kefp&Drud7$~q!IZ)wuL4A-d`~m~EtqTuIyq*j|Rj^s7XkBPARw}ERUs&=+u2?+NK<ArLrX{=LbFE(V7)Du1IqBfu^`qA|'
    'H}em#G0X<nfELx5Q|o`<qLSf5L+-|s+qVHdR`pS=BG-j)ze8~T^=DZ5{Kb?kf1J{5NBgoOEWAGDXX1@G@3Ing?3=cZ<}+w)^LlRQ'
    'H`WBk;k-a4pfBIyxyIm%g(a3LbD0?~fd!92PqCIb^*S{32e-5Bxsi0HEwOqYgUlpkQ)e{;M+>B;5E;R@w1v@2$#3DW^4v+{F@s-X'
    '=4c-qs&n*CFd4AuH>eH#NU?I*#x&8bN}45Rfz1}-8oD1zv`tS|(zfBGEqA=xP#gAF%wpE{Kw_XIG&uSu4&A{c0{)oriNd$AEw_mQ'
    'QGT*P3(j7r9)D{vd7LqDn>%TvChiU*9tio>N)9sFQ#FPFb`X<5B%@?|TWd0ILjqRBxL}uI*p^+^eoiZY?jY>vVj6aTVZMiVe-UoR'
    'TUqfb(8^HFEW@fos?}?C>a9TB{HygKV@1H`g(Xr?ZhMaw+&jZr&~!I@(AK*00JIu1j@`+vOh$5dm-1`<Zu$l$g@6dOJ%x3QXg(q#'
    'fRr7HbBWf*W_F&Ul7oxFJGLts@8Ih`s+)GZBmNEOZy*)w&lvl|Y&^J8xM;Z{w35j`gjJzBfMGbHtf^alHNeVbfQo9rR4-6(lSD_r'
    '+P^*MP=wbXFy80L$-|VDb@W83HugAp&TMZ3Q656pPf!W0crZg}vhyKmhweLHG&i=k#ygzC^3DAADhAuF+$7BPF}8U!$x7_nK@(7%'
    'cZM=?qc9$8ZUm8?#w<3MuZ9%ow6cW!kH%xQi4_8EQPyWP$K~=$G*^yQn{-zZ&(;U8EDF@)GUf%T3xG~;1z_otEM=mQs2iGT6g1EI'
    'eNab-Amy;*AgodhbV!-cY}=Uu39Sk4^p?O<(KLmUfGRRC$FfR<%&Tv`&}K>~mN8lPrCzHz0iv%r_z=CNLrd;;kdv?LX5+I6_2i{C'
    'IA{nn{%#@C$|ZC4yY*MZ8{V~Xk}+qq>Hhq~2l^?3Ck64!T0Ts49g!<$8tw6xmOFM<#4pK~#4$l$;G)M9rS%aY%EBw?hA_ElQZ1h='
    'bkdAO^s&6i<T7`ybB&G;M<n;*7_o9gcIzHcWK&lXMa`Ytxv3dA5^zA64)RjEm&agyg{zPFU3PoRv1(MEsCKGsLNZgRX@#*aK%Hfj'
    'Flf@Grx3RZbN;#jOAm1nu)V6zxum;TloKfI1$yFagp43MPr9Vuid`~}g6`1o3NMRqz_XvyEN4b;w?^}FLe54mCAw=6yN5~-k`>bL'
    'jstq4lHZ2qIV;Uqy0_VAJSrKc6c^DGC(OP?FAdJ6dd9toRrA40tJKg9loLX)c%tdT&h$M?DGP{MgR-xoPPT|X`PS-MG!g0OY8B2i'
    'O$Om>QES>G!aM>GV{)3z2ds8t{s`??I6e{I2%8V>X-QC~T~e>uBM%2`Oc{RV!W<MotF=vW(El~6e<~8c7;oxA8jQ7ri~#M)m$hdU'
    'ox*K{W{roKD5zLp(wsx{7FBZc3;S5*n9!+ozOC&7_qECw-DUI^#VS=hNYRPbbNGC<f0l&+zys?9dO^Tp5N21F^2@!5I0bv)#^Y>y'
    'jww~oA(1(Bn31t_3x)0B9mL0*evJVKLqmGVDA!Ja_OhEp90-9*&vUq&VOziMQ4Wh`F;6OS&{bx0wq1tC`~*FCMjV4Y>D9c27GBfO'
    'cjI*qwW=}Bc*BCIElypf5`$*_i`vDAnhkA_ZQ)uFm?CXUE)zB>795ByG@TDfxwTU(@yNH~g$5fCp6LY+c8o^RL<~64g7O2Obp~$D'
    '&rT?D7iR^TQzdK++#((;aOO3dC1GU?7_WE#MQ)<Mlw`uf0tgi7c1*2v_q^AG?)VUXZ}YK;@sTQr46_(1Ctv*ckP&=x{<%m9x@;f>'
    '0}E5hpU>(N;sO5O9_Jwa&tb_`;KgU(Ea#q$8m1HYGO=FqJih5Y&p7Q;e;pX`?rnHOH{GE}Bq<=~k~jE<V)|FJo5Q*Eva`STW5-**'
    'k{cUv*RSW-uQH@4ipR2bOiUx{1sUY{S0C4Y`qlp0uf96qu+v<|tL+g;#RBFy1_P}%jV#!80$E~&WQAeOHXQyIrsGMhWllkK$JzF?'
    'M?mQ#c$0*bt%Z{WHFUz=fZl!el>>r1Y#_MkjSOTn51^0P?HdBsi8AxD=`=j!y-s<(Yb3-o>i8I&aq<I?^E>MjDQZp(3$w|k72$XA'
    'ozFl2vx7E-t1mD<imlm`_zNN&3S%{uDbb7x=)q+0Y2pDirr7brhdzt_{>#rlkDWYr?DG@o*D2!VqpPRssP`{&gniTbMv&!z_L1OV'
    'n$l0QnQc^Hh^at#Bks~0@77v=y|)&AWHUY4Hz|Iq*1Nsrj`c@az2LwMJdO#KMfwXU#A5uOJ9H1hQs$omA#(3YC#4}A`An#$#}6~;'
    'SW{a|d!jjB)e+$jUFPp!#te1*L7%;xzdGX0tX*c5l*_P;K<$??(infFCbFC9%b5B&IEwe+9UP$cRZ7$0;)d{5R&BN0pe7?$t$FRp'
    'QBJIZJD(VLes}EHpGq|LFJB%$aninTdPqCsa*0H4O^AGjx05yhmINM!ft=*aLec)=wfx9ccjTr!F$U`XCkR3+%7Bjya+#3A%qYdG'
    '%z1aNxtTO?Qz|!tPFMX1J=ffJy0O^_ADfb$iVVG^Q?z80Xh_k{>`ueNh!ypmHej1oZONMU#?JH1{XRW*^d#EyK09(8$l0G9&>%p;'
    'h@hO))P};*!AMu<*^hpO-tDAwHY6wm-^DWx->$*UQgt>ODEL&|uaB>D$?E8?v8Xy45$2qlU<sZ>1fwMjm`h}~5@s`Kw>X*>zN=Y='
    'OyVvw1X>~`QbP09)CDO(#xFJ@yxL*<L1AS{yrvgh)7%{LbYNdygTu7UMP8XiWnFqo;HLOeAZa-9@eHnlhNCqxj)jP+qjC=dUgMD`'
    'Ld0g-D2t~NReN;jwhKM8?)Y<bfD$%f?(T|!5Zru@F1(@p?&v+=B1<E-W1`v{6CHJ;GF+kq!=lio@pZf-89#`@5TY|hnFF<Idb;^a'
    '2tQy(UYsE)V1Jk=&iFfi_zy=;oIL)o4jg`HjQVL=gU;8&c!R<BDrF8KjauBn@W7el)?4PCRXWIFSG(vPc2AjI^J9^ib-?U~#*_dc'
    'itC1z1mqK)X=!5AO-<()F!4YnZxZYRkQ8Ry{kZg{ozY?Br61vyQdh9a5aLo9m7<zL?8LF-Cl7xXB(AX9fBr)t{&5{bU_a~EosW5T'
    'W@(mG`7(+#MWKnJNJiYH*YJ-_$%F3VV|NvVuNx}h043bs*STx271BRep4l0|QhOxeD(0opD-v-<@H$P6tz5v1i_rb~&=+(h(fe3G'
    '27VR|6QXx33==Z;1ie^YatB(@4elnEo^AZ5<aR65G<Vr3vx``;6JzEvl2XTro4(;LOuFln_5ng`$)|@(A?j*P{JRzQCHg}=Cn5Hv'
    '9WV&DI0O%V5vC(-$r>kKs>f%z8aFhn7Z4I)rudL_Q5lG)7Ju1Sp1+vFa($IH5_aR_Z`ERD5>#Wyj3y|y0~JF;=A$B>Xu)Jifp1?5'
    'hIon48?uD>iG`rE4UVz`gEizy%_9|VSR{>UI-c(05&9lS1?;ew#weJkOi1itr+r^t#LF%uIQ_`tur{YkdjWO_&N~?%V&@YSTon<r'
    'XSDvK19c5nxmrct<V1%S{SPal(TZ8dr-XRQ0CMtG6Ngu>RFU1{TOHaN;zK}tbW+-0do(<R%~)3Q?+2H)OemYs(}g($oqTv2{e63$'
    'Z5sFs2kBEZ8%Bt6B_%41jFKj;s<K0H5d;o{FU<98_GmWRxv73M1I%EGBpW@@_BXzkzq*ZOk~X<U=|FDfE!hlAOGdtD#If{hVFGY|'
    '_d`!_P&-$_BKXK>@)2ECmp2c8-!lpSco2FlKZ4BB(511!yZQo#jUZ0&Uo$GjH$*sA7!6Jo6<?(cf(1NrrXRVBXlmfCfB`KFOE9Iu'
    '&^Ly&D_nWNn5oj<L#lu8EQaI_jAI6dR*Tx-Qquijh5@`|u-5s;ujB6#g`#5-`EWzA&c+L{tz;Mq^QOAu!PnS{@aSnj45D<vooX%^'
    ';#~xKKlB6G5iUI+(w=5%l!HaH8ErNh_NbeOFt&1Ym|`ANoN3Arqew{7iIQ4SjzJ%IuR0t}IZiVG)v&p*iWb;5C^NPJOnsQ`Dr8zh'
    '1VSHm&OD1t3B<*00Q_cH2uCQ0680)u7T|f0!)(n8!K6_K&3M7U%aAeCc$Mm5jn*(Zg;caCGs;RvXpW9}b<xq8rpxyqo~c3qq~kLf'
    'l#S9*B2u+v@FyQ$D#TMAXUt>koJ;i@XZrvN_JeAtQ)6n>kp|nH5E=w(qyG4_$RYvxMy)u{cN59H`JVjF_3Y-NWm30fGv+x;a#Lsp'
    'a#F%&vy$Rib*Z37Q)Jr{lpJRZJy)<o^~@s>GuW?lvC}~ih@CknOqAo09js<AC(O#wHziRtx5F{&F1>ND-~Ya68BOaYq*G?IW*+|g'
    '%9uq(#~@5wMY5>2Ur_d}q!shcvmI@q61BJ2kXtbRb>a*r)YqVI=p3A`E$yJ}K$l~j;VWY#B?wDPBLPp;xA?Uu<x1F@ebvq%@o^)y'
    'j}5(6+f^;mhCl)sf2yp+E_W;E3ToaMd%}K1Y21%`q|MBynz@1Fone`kp}BP-0;-mAiMtbx9KKTmgEhu7oPVQLM<2Aa@OGP&8J1h;'
    'CuUXz#l^Xp=8|aXo7HsEggXL)`I#~+%ks)qoR+<-tj%DyCNGod3!1Rh9a^if8+<J=u3|}@T-FPv$6`dOrFoiEOs|%)K5|qEpTn#b'
    '`XMt&7-4ov+gUj)GA2k4MaVDqNs@MBx+PQYCXnKYkXXs^W6@+-6!5zVjYZ;drJJ#MG_ED17-7V^fvB=5Vv#x|jC=>3ruqX6^xmW*'
    '<D$C^MGnZ}zILTsh)Ty8`IE>hU^zk%K`6=C+16^uRKlu}x)IIGHy4={<Bbdo1gC**Z)zPBdPaOgf*=QSwc~Doi^ZkTWim<_?AJlZ'
    'R-tU{cLn*pR#lHJNil|8s`~wNBo8YHP0p*D(rPE*y(qh&7)w%mCH+*6B72-;3NiX`dsUAn<oInW#>pCLN^t{NHRrcNjly44G2R~7'
    '=Hr-s^STf5Y6uOY)B<7-icR5W0t#nn>`s%FUn$20^;pZ2(fJ__26mFxSalKguUMRjc77NDh=O2$Q=DG8V|(ANXS(JlyA}v+(NDcf'
    'X|nNe<l#3$6+?p=&G<#3IS`4WK&&y9JJ$+5_ac@xErNxasi+(V?~r<ffkG5*m|tCRQ{Vo)BNs!{DeP!6cNNPtacm<idnGd}w8jvp'
    'Tm$PAO`_3oyPvg1t{TKaUmW~IIP&vlGbdDZf%4r{7%EmWPogMCjIkHwrwobozg;30QXPHJtIF~uXg^#^{G`E;7ZR;#K#lPFMoil%'
    '<|_wzj;M0Ogb~?I9OWhaS~q4_;2iwWGt7$}%&=%W-%+)HU$l6V#eb7T04<wWRc9o!_>uE@i|%%Gm}q_rD<YEp8rjT)+`V3REA6IU'
    'Qvv`+5NH9WT5fNdB;b%}OqFw4xP2%0U0RF5ob_`*9fC_V!ZC$EMGMO0(XeF(dH?ea7peFGOR<f?KYuDlUm|lG-VtR(*ProlDFPq_'
    'A;8WHEtO9B|5y&NtWl*3M*HpR1(|KFJNi>+1}nE!MMl!yCas1@biOEEUCyGTi`CKH=5^ZHWn!1f1T#Uy+x_1BW9^(BvCdzVHR`L7'
    'IYebG^%(qN<ZN|UkW6MWr5`JyjLkd1#xK!n2ERf7B4oLM-#faCcG+&I5+)s<VOC5awM9{b)3N}{>^sQ7ZK&q==in-NWv`w`BJyH2'
    '_pzSXUI`Y%KL)UXfaXpf{^}%0$H9))v@$EJyn<Zk|MGwaKM5FskqU>+mMIlcDx1GpT3yH}Okf5|smQNiMY!j|Q33M(u2>EK06>O!'
    'z$CzJEm5)$mW5|GW>TgN`-y_6O2&1Z`F0(FJ6=3@vB5f?ev~}YcSjzm)+GAMQx!#)qQ(JruyRI+tnW1O_&Wn>pAG)Hh*!cO{IwxB'
    '=;y1hsq;yTi?5hygD_ehyd?Y7UG4D6DsP|#roPFt-%@mB{k{gdaH4k&QFvX)rH$zPoZAwQM!P|i6u(O-Tfa0>=s`_7Oo@}Zl0&kj'
    'T2x>6i+%foDc*8@nR8OEcM}%@jx=cQ9iI+LG~weKn>)(ZaU&XIgBe2AT>KjTpqNQ`PW)E=Zeq)SyEJu`#fTE-%vvp{#@ccRNuS<y'
    'Z+>G<%VNy0zRj;L5Df1g=Of3PSuOO;VukHh^bdK1Z}V%t-stN0J-1nu$!13Luh!kvfO~toc0bS>hG(zNX17OWeZvtiHG#Qnn96ob'
    'WcGdJuz?B-Y*WT=t>`Kvzp+_b^gF+HEt}cctK=#8w15c_jw_+#=H^~vD(=udINFqGjUPjty3zdVcXVPo7!LUB|IZOs498#wUZVmz'
    '{y^k9ek8IT5!()rMwyW~!czfX%&)D%Bb>PjSMoapI6u^XZ{>lTn!!Y6mAXvTnV`?^_()-3v@kOPia3V#r`A1?>bA#(3Lg97(9vVZ'
    '5B>Sbr$Q4$*~`x969gRLIlIi?yv);dnazop^%=a(g%QLb(us%Dd6|Fk!<XT`?Dh?O+I`k@Iifyud#Uht#vRM#uMJ*CqvG5>cVf)D'
    'GfyvUKCbzY$0$Cd{^VBGpZN{A<vknldNP-@o6qyTlb11WW$-(1;eof1g3n=cp+D+Bf;xKmINH7Hd4xiXA}duxL^n(~qZ05pP19*_'
    'Y;EgkY)Lc)a0qq{R9psYmQPN^B9?z)jTr;YSVdy_k;8}u8&YS0o|EYT@Dq(`n7CIJn3ibq8mVQ`WQ!DN({vV^ZX$8q<giDx+dCM{'
    ';J*VKI5+m4_hJgOc5h>_f;aXkKRO|{Z2m!?yEx!Jn*zm!>to{H+nEF1g$>=@atJJ_itZ3)ydwTLjSvq%i6Vs6iDID??8DT>2{syo'
    '6F0{v-0>mKnS(JLI^Xuur$9K#MAHR_b8VpO`G*fctcA^;U+>k+T4R(iyq{W!Z7mpDe(hy`?KY4#HGEUOncW(4``&o>`@ENPweILb'
    'e*HdNsx92wan~nvnU~(pHO0>uFfTX!H1}j!;(hcAs~gu)l}2~?4n+=QDzNaG9Xa~h;je&+octqD<zJ5c>4*RfKlbIxFTOlkBT7Bi'
    '3N=bb9^g+ns~C`r(m(AMt+ZSXF*o+b@gv8M!(XV^2@>*MeSYbLNNal&{3%bjh>m&+*AK|+K=X;S@G@NcSYG=k-q=o0eIl=*u^ayW'
    'h5Wk(R1E&!FXbgsJnd|}5eN(AlK(0181Sob<5%*=xyCbu3GVz_wX5o&IQ!K|d{<**YrN@9TPL=@UyY3G*g5<CA<076a8Zp3lS=Rc'
    '4hatI*oiL=ANteb;~@#$RsUS~*UP6`qo<M|p8D{g;lEY&r@B6g@7wp$sjgq2J$v>)HX1G+sNTP?8=t6$GrO*V^0Z}(Cr%#w0_!Vd'
    'cKfA>H{FNcvz|WOPTtG6`EO?;-66D#(da$1s1D9NF5G=Vg^2x<y#yujT{iQ&HurQgzq(%Q^*n|PweAR}{kRX-;T4YBV;;{$DtmPF'
    '*pU;5W!Yy+x)+lIQbT@6A&Nz~ZS^Po?ru-{cc*6Zk9yRf-t>~YGwuILK0Wm({0_WG{aFCz^``cr$&9z4@Iuhh;@zG1zdQ6ScjLAH'
    '@W>N){E6Qboc*h7{)d)c!0`QU+`H~B-}E1zdz^dz7XOSIhrJIh2ea|<KJ(sJCyyWElpnac|G+-@U)alZJbZfS3wY?4^oX{m98+en'
    '7A=;CeFV&7P!^+AX5N@zTjF<tKcOw2xT!+_#Jq{Yw?e>@Hi4nS^z&@`dTkCk>-AZ8^@+Q4)qAxexZ8_4{&HbzhZ_Sq$^}l$(xF!K'
    'j2~md`SR$Wjvo8VQAI!}4;}x*;ggKmt??bjJ3^UE`1UtAip2<wy~_Jko*b%P8dkc<EUM^!HGRrE%1#@e#9Hx(NL&P&_$H1T)@S`0'
    '0yN0wg2~3vD}ttz;2Y!=@pmQHM{^fn<`*XO3y;KRqtfAAG(3+!6S$Jjruz%Go_j0zQ9fOM1OIAssn^-`^IEs}wR?5kKY3ov<sPnc'
    '%LHYjiaAm}HxLRX(X6?tMQE9w%*7T>kmVl^A3b~=s8r;EVjY-{w*M1v#Xv=#UtI^|4w@eD8%yxdy!3Da{+XAy?!rIQ8&9J&E_rLC'
    '_N82E1OAz9Q39!XX?zm?8JF<%e)!0IiYC;&)IaBLri@G3Oh2r#^;BQs>6Y0%<^nwic49wE;9!q)lN<I8ek0KI`gLd|aOurUD{>*w'
    'T*#;06~r1aFD*U{Jk^)VP2RP>IBqXkzNgo_y<<L=o(2jwFHHsJ%Da8tTfc5i-?xRC@7z^$%}@_~+vi@tWj?!_az~$-mmWNE`(By7'
    'TV1C!QlFx&oB6lacgG&ti<?iqaTlN1AsoSpTz21nYf{C|0*Kk>rR^QN6ZwZ%vRl(;TN6+1OQ3WIh+}0kw{*u|y27+Yz|Wp#H;2qw'
    'oPUhSFD3y$0Iks?mI3YL=1X(;3v)f@6plQ}Jzq61J-p?vnNVHcR9`kdY%hnqV?8zfx-e}M@m)}~%}c$n3M*I47q9Jj^K%j3SP~5~'
    'D@H^Jkx&9O<<p)!I-d1IUo6VCsMexFivlg`vnbD^I*Z~gYO^TKqB4uZEb6i-%c3f416!LH|I}y6yFR4f&CL(P3~1umw{Y0`f8OYI'
    'NACIWPR-;pSG6JBE=>6%6;`@lUGN{CA0>N>_UWNTG1UamRJeTyMxlym=h83Sg~`I(85k)jT7~ac-M(!;4}qLObS*5r%H8V~qSKRM'
    'cRron9Lr^Hz;Vd=nV{E-XlasXjKlBfv7>+d^3R8k7AGkFpg(OOK##wM?)X<;y(~-)>sNA_Z*x0a`jx_qQEzk8c<YV%0F6)0fxCXo'
    'TN!a5yv7i?mv`Jn5YVrSf!<n!>v$ebKhO6Lx#R0BPOyL>!qi0R&=E!)!=i+kl5h((>Y-ci_&re?bM-37FNh<Rc>p2`mQ|fwHKynA'
    'SD$|V<!6UOW+$TeEK<$A4txj2u7S1O^}D%=7ot?`NE%*%h0Kl3<v~{-UC<lib>);~UNaPJs6n^ySvLK|y_?Q0^eFiZzbeaoj&No!'
    '>N6k*lWi+Em?D!V6}cW+R5{V^o7e8dHFxwLzamT!6QhOuw|UZ7vJ)SZwcEY<p2Cw`lyj|35l_vDFMt30BVUCmhcLSOzhXU<OZ%%o'
    '+1CxfffD#X7>G>jWPPu44@Z^Y?_d;~1Qol20EYoPiP3>WpMQSrFNZ%12-{l3x~Acml)MA-xlrswW$<t@y@+HUrtvF*eeP|3{TZ&('
    '#tU!oI~XEM>)DJbO;;<N?~G18fmD)B&w;dD=(&dzh;{2mRt4y9EmsOgbqFZw&aiBeX$3Y3{VnWZN5A+~bCwS2LNut=peJGq#oQfP'
    '8a?*;vEzqfQ-EssS$XAV_mda8D50}v^hDse?K5qNXgNx@$D0yo6HPG!aiMCN(r`z`S+zV9@*FEPKvlw-R10505<B7Pf;Y8}Mn$S5'
    '<m^SJt!pP%o=L?unM8S6>>KQ%uGUUjnn~3cArtMs5EvV7n*NL@1Nlt+SXne15%x_F5uS{rf1-%SR5tB^Mzz-%7JtF8tm0DvE3jE%'
    'S6ezi`y<h+rxMTtiVcI2z~fi)Jy+QYDpr$$T&~^C-Cg3^jl7zA0di)N6Qio!H@Zr=cr(3?>8*6#8-D7JZ|J2SG0SB63IfSIdj{ek'
    'UMI^=Zg2z-fbH+x{&g<+$cPH+ie4vz>s1B$f<k-cI+^UcQY9;8W%OApEIuSl^p56M2B9@HY7bpuEjSo~C?j9$0~FezJs=CvhtQf>'
    '%hSMeFT}}NfAY|YKgE7`{LrU=I($-#$_Z`(6&xU5*62y(qj4CpxLtFuqphpGYQM}->1^w4#1wOFQ^=4(JKjcA+2csVGJAlK6EOb9'
    '20P;KndGd&EGUQtcq+({FwwL<-gqId90=l1<p8RfS16W%0V@hBO*R*dukigYKQ-TgN&A?3#V^UM$s+a^|H1I?@5XgR0Z(CG1C@;>'
    '0my>h10S^_#Iq2L`7z-u2}>!|BcbpnlT|?{NDV~?3MZEZWT|IWhXF4ZtuRH!yV(dxj-v)gc$)>>;s|3c2E{6j==4#EOAkxTydLft'
    '@*L2M1%wJiMX5~g0uk3rkxg=miew5?9zF(p;IkGs1gC2HE{dA7u<97JP^H!1vnEV1gBHb41X)M8+FwZ4EE00n0j#s`GYI(OSJB<n'
    'Wme(L0r$slTETef3zEgr6kISGH9Z)WchFA&4ze^m!&mGxzx;0u^Z4byQ-NxF@$G6P<>sz4qO^dSW0iaQ)Y<QND;Sp_bbpc}gr(>6'
    'Z{ILXQvUuEfeLl&t-CcY9)`y@p5xgrAffcsKiN?Yv(4ZVDrPdAg_x0y`n|C7pz>p~qSDv#4h{!2-I*d*hVMp<Oz;Xyn2SOgT&{@Q'
    'U@#%GzyO@jAyvrvJ-H$k(+JlkI4BC6;@9GVvf#2tl8UOdYML8GPQw(r$~Qyn*)|5qQx&fL;-pkVL?FmZ`1ooka-p^Dn^yAuf+oU@'
    'Nc@FDDE`_V4SuCwyw`w-|9&eK(O+#p?BmQC#pe76jBP=@%-<B%T0^u!v@=I+kON|aC|6AG`CAN@l4VSl49gCAmQqtUb-xbPL6v^D'
    ')e%*xOnG!ffy>E9Vu^itcJI+b?!^%2=<#>_{5wz|;hSuw7@Y!L37;qTj8I%Rmt{H6!Jq(YWO@`iB-D)AMLVooyF$|BM84)kOQQ4O'
    'fqh{cAW+3?*L$H<?cP#O!HPeyt8%`ESolu|c|r-}1b-3z3YFe}r0r%EA^Guko_tpBJ^K*qh&RW-rl2T%UP;wVIT%CSayro(XHhF^'
    '?x>G(N8zzC(2wCisYQ2n7B?7YI8?F!lk1VIZBy;&8%j3{ME|}ur)5RpKJryA{w_O6^AAB4o#Cp1>=u<Fd<Y)IO~UbpI0d6GvYT)O'
    'UZrqHY~dqERISvKa#8W*Y+5bu35PZJwlMgz7K@{=KcmLyWFsPV_s%eTX(>obcn@mX0A9;R^IEolQ<&74ZgqxNbo+EI8;jvNKA}U6'
    '(ulK2QGHrZf;Lc`c0@wDMTdB=X<SK#xrxoOdDHjVBCOVoqLfl%;sPcetl9Y%CIWMq-T4QF-f6O|tt`7Uk3=dXq9ZQ%`cj7`oJf*o'
    'Nd#G5?<7pOWT&a@_I!3TZCmVE_NM480+v)ZIbq7fN^rnT$ERURkb(BiyXmPQ>y+m)?-br~4ZBw+DW9fv>|JPjh}>$hfl+=cr|8-_'
    'Yf6&k&iSHjNsm{#<iB#Q7V&$$Z?ob4f^x0;m4?e2*aEK$i(v%@Pm>24%dRgW9v9r`L1$kOj;She+~JX;8Gn7duQ2&C%9bfW{niPb'
    '$AOWF3tHc*Z5ts6;Kwhv!xtp5Xd+%j^UzXYbcri|F_G-V{0)rUhmW3(ldX|wDTq~qC&njINeLL}$WG|n;w*Wrcb1<O@sfSL`PEG)'
    '#HNnHYh=4uws|(j!#BLC2Z{lhR=?DNeFuK|(Y{|hpMK((Kv%{<jLRu*5A=E-GQ(!h2E0AHH4D$XsdfGoM&_V{>1J*U*whs=Ilj(j'
    'ZUSF(Z}bueOEIrx-?(6Cy|dn654Xk^OK8wtz3Qf>8Ip%>suH{<C188{UgTb6bRm(?HI`*-MPQNereS#Lb+q$tJTLSN+BLDvKqu$>'
    '!6ncypDv(aO1?gCaFka8J!2z%A*@x9mIl}n#~ji|*5oV;MKT^IDUy&Eb>c(hMY^cbpicj-@x;0Fr`tL>Es6C$k(A03E%&1eKcI<M'
    'EZXkhw~ze;I(BA5SmS#UIgxv6w0^p==|Xd&^+MA80JWMd(kjX8qGhr}$ghOpZ;D%?lD4C@V?E&S%U#)5%*G6fJh0VvYjQD;Siif#'
    'DuZJnP!W`tX)sJj(f1gMm=;NXAuW*y8o{iuWZY+cjAsqv3fGIvJp^HALKK1{xM-b!plky|+15Jqru^!<Ec11xVzYqk8Jmyn&PQ!8'
    'QA|6!0-hfEdVnK`WF>GzzcG(1(<~Ye$!i)*c_JDdwvJ{qeazljn9FWXB5noE7@A4d@VJtjo_S|}q3TiyL3UR=M?P~d1?H$b8q!V('
    '8f|Ejo{@8QHqqP((<UuhVQ4Q!@Mw&ZtfK5*prh3#-s9p%+DlwlyX=<)_)N%jm3mh!zZS?J`k0;k#~ZTPw1h$HL@VA0Vy}lI0F{_^'
    'r7SDrFY=tPL>GBL*Q|>?A|_2tU$LjeG}Q$XIn@&&>|rtidS*f%u^=sjyB7`Via8Q6p5@rDD~UDzj;&}d2q{y(tqGTyf8E%gjK`8#'
    'g6ec5{3i0;+d9tR-eO>}Fg&|*aX^^0zO_`#C97(`?^!M%5>zS%hzjj)&5Agmv2RhD860&!-se;p=mYP#>2ZwjxqT1bR6ze+%hxxc'
    'iy6_yA&?kaX>x+%kUae4bZNdbsg|1OX=&q@_(+ksqnc#b=_<s276B8oe3U^a)C77NWv9!AE~qEzP{Uu0qMu?u<W|SwSQvHk5uLDD'
    '2t;-5bQeYol_e-|WKbkROszAdA;Qe{WH4o7YQ1p(T5fgPOFeeSZ^9bpGAR+pB5OGD1Nok5cYWI(ec|@bi@?B*7a%lSu_|0%MiuEp'
    'H#T7xCQTQ+wYc?lp~)h;E4hT3g1&@#k+6}`L6T^#!Yy6Q8W9EQwP<#UT_(Wq|6czazjcb_ub`?5Gf*0k6CRK&&K=SL;I!`@R39{G'
    '0$#V+48lBg#G6rgl9VB|gm6_>_Z@_&6e^qvH6fL7NSYpOZI!hwqQ#`Gi{r**&WlyY+fOHvW=pnW0@n(&U(Wg_M&B2Q`B}G@@&k{<'
    'avOmjCk39z{39dm&C=p1i5abTQXkX|u`<z-zpX5P98QPn#=MDk=@bDiQ5y1m^I~~>WUVr$sti-_&NVMJ;m;){nd$~vX%#TnNEi(T'
    'BIC-SN^)WofnGx0{dUACI6e6c_&-cJriAz2a2K!TCi=tFT%n8|bS{NKT<Fx-?OYCjNF&7V5Qpn~x{F1*s`vD%Npd!9k<__z9w&JM'
    'YBn@X{Z<g|{-eNW>Vuq1)X-|NW=h6QNqOCZ7M5IAbGHzLl?gumb-V*T>Dnak${k`bOxad=#hH);(l*aQFGL$!yOs~<ptGNB3!2$c'
    'k8AW_At@D~XTzU)QwXOG{=uZrq;)1s4y-GaMHbMvjcN;J*kFxD_Hrw*?0#b*CznVJ58W@SzN&BXz^z!s+MpAL9BsErMlw+Er?-%*'
    'sTG2NUz#qsB$|StRjRvK=U6(@M;A3>sSe&_stCCu%*%m09AqJNK4+he78jZ?PA^@w=b!XZ=hwfsn+TUz{MV!Q3l}Z9SGyfGkzo9p'
    'Ew7VAbs?^}q#5-~^%!OjG{Q;)x3r1&vehI1_Nu#u4n$==r6>dfU>NCh`}@4X8}I-euKCq<t^7&;?F~4%L2SsSmk~1a*%efK&?MF4'
    'jxSMA0A^}@{;|M1VJ{a}HWN{5SF@R?A{~I>K~>?Y+{z>zgY3KGuc0!!i_N2CS6V%ovuFb$Y`|@%7*cMd-L~{rtv;m5aUJnwTl3d('
    'AKD|9Y-($d2O;`=NU9J-3zW}BuDWwC1Ab_Rk*OA_QPqxq%vU>lFdzIS*P$@ij}B>+^$9ITgYEmrVf2MV#_STMPsV2M2216?8N}Wm'
    'j4Z$+Y-*RSU&nZ%+Y8deGMC-%7m&S~8BDvL+Tr}ETKFxyi4Jj*YCk%Kf^vGzOHT-=%A30^y}~Axa%<|uu`isDYkuW$BmB7TxDloq'
    '6RHmD$Ew0RXjI2XE__3Fn_sa@^&7I*Gb}%x6yLx`EjILMCDW?F^WG6xPZRDAh6#6kxL3t%6jjP9o!V(11eP;YK00)XuSgI28Kb*J'
    '^h1C2Vg0vNMQb|30LN?~J*&My8`b59p>at@@Dp3MQI&eC`VqboEK}wUZz0eR@mBw&5|~>O_)4P4yAw|e3u!k!?)F{vrq+qT2looa'
    'S7)}4x~TEo&?;sA+63^CjC^0JqlT_g9S6-s4qM7@4re#h0>pq-Ju~<RR6PMOrl8X(>RTM06Hi235WyfxRHWiq_$s8%2fVk|$OOJ;'
    'ak`krhPMYr4%l0yNFlAWAWSo460Z{+=m=Y=#j$idRp>-9W6rvx=oWVf?vUiuZY;#jxJvrBTH>9JT$T{F2mTQ|n}|1`Nd^o$s0G07'
    'QZ>y&fb;{DBrGZdw2fHu>smCmZm-s2q4dlO_S0K^CDsJB3E<a4oj4o0bfFvFPG8s8?f>PzhHh3PE>t^TGf-G(qbvd)EoyN$`fSF4'
    '1h!kT@@B9oa#?~KLnp}Kj2m&9r9Sv$ky9v1eT4seXELI<S=Tcs&}L2*&tGX{oS0GKFlA=6L=SX}yEgC&!eK2A8|rcnXb`h#5ab9s'
    'h65wh8-^j0?^!7nq)m)eq8@}gq2-8RZ-{lg^!dlJzZ^dFC#B6D`a^eQS9BHXp5j2D-<B{rhs=xk5dH`=jmtM1*lJKyQ&TStuR8-M'
    'lx;lsQjfixD-Ds#=vh+oB|ADxc$(jXx`kMv6(bZSG}M{S-e`5EC*vrufOIWY9}R{4U5<GHI~WoVj~eIbI(8R(fL2~cw<NGo3<pa0'
    'b0I02+289+0gJQl;zNpf{nop?<be8#Ua#ewnE2VZ<c>eEOOF=8^wpss`NKZ2Z+*C#R2Kxh!!Ucxys2g@@U|w96luerUTA{-NQ@9H'
    '=URgW{?7mf4mncKMuIbqCt^^)=ynhVK@;xalg49e{`gVj2;&zbVc!?6*fS^^(Wc&dJ6Zv%aYeAjAu4YGyV!q5!!NFY4vV+r!y8vf'
    'l*OC!Pm*G-1<3RLv6M^K!SRg;KSC)53&xmq3>)&7kNuF63ww=xOjUCugGE;v)0d9~jHB<LgkN;-1V>dHRs4*%eq9xTbMWxdL%;j{'
    '@Mma_*$-;yKOMxM+Bvny@wO&0E&a(%A#*Fgx`+j`rdM<ZikSPVp@aKaFa<t|wt=S$xyid;Y68wGj9a++(CKWu5O2i}BNon#H_{6%'
    'G@DsOd)B~XcjFo6Pz(;cLyz389(Q8RIMRb5<!Cv>bNU^OYHC8bkzJS5`B5}pG@;?=?V40Sss1ZmWdES!xFVEt4V<_HPC4}cpr~0!'
    'JJVlnyJ%S%6!WtoCG$?TR#YZWKBj8p8M~R0tWULC#qMl{8@98y$%UFOD_ET}4$FTayt)v^=O1&X3#ywMz`ECa-(AGm>;SrYe(j}q'
    'YfNFRvpJ5cG1i7ujaKc8f?k^<w3^V#fd}L!pXE1Rkd=cX$Jxa%D|^lf1avL~_Uww=mtt$b!)D?b5QoF?G+d91;#cucG)zw4<CH{p'
    ')-#TC?&kIE)>dJ12A%XP)4B9LP)8JaI^nfUp^IVrDMNN(T*3CU{O#A-H^aFn_YmPypD|+L-o!kDC2!vL?t&mP%$YkBuOPaezyG9g'
    'E5pn=VD(ZZU}C5LCh0z4vj&!)&5Zd~u4}Zak?!~?%BuRlK^asKB_S=V69M*cd!qH6mg&4}WO2XJHYbG`py65#ZN7~V<NifUCGtx('
    'lzfc!x-(jdY&_S4&rr@xSOl~-@_KlpL2F5r@rhVPM8(s(I-{1U?JN)0S~Iy+XA&K8^17SV#r+SlNkSb+{Cw~v;_HD_d+m!ff+97{'
    'QS_m&iLsgE;6jic=VBB#pw(^B->*h@tWJU6$e>AOOnLdWC9KLvMR+J3|B5%Y?)Gfybf-~SNBrw}M>2jU)GzZs^!Dz=rImC?%EG{X'
    'eVj!oYFB03^<KfWVz+R1>xu^_2MWEz^U>mumnh#Jl27H=Yd6(-y*@#SVn=oFy{aYZ6UZ%?JZJRHLJaYXCy`2H6~fcR?~UiV%s6JP'
    'mpQ52_1pP16suLSTa1XE>xXk*r{p3qBtH!c{ZUAoYb0|*o;C-O@Y2fBACuYxG%P!VP($iku$1#V5VsFe;?s6%pc0>gd|`sbzC<}n'
    'Dk6L^?GQ)24^m!`c0}X<6{!d7_4>n({Io>EF$3>T3u2y2QiFKL2-Rq6`x>{0Mh>z<qfnC0HYS?mTy-<FQg{e_P2tvSZ}pY49}S5d'
    'X91c(eslwM2@+*X5!Bj};zVY|z0@45O4bEG)fy>Z7~EGLKJbG&KB#s+_#oPCG!Q5gF1x08+4^KaGa{XXc8I(7=7*k<OWKrn|Ijn4'
    'q-%tk9~sOoJmt`0ko;M~C?WY9eO*}1uP)~BOZ{&81}Y$g)sUONi~Jqbkd<2?qN`L|mgI}7BCM#HlPzisQm+D&{XzXDRbo`%IMsgD'
    '?n{Es;Q?b7BHi%6h7U|rtK6+VvZh`o-1vfCNmYDGeM$e0p;2lD!tH%fqC|M{(%{|yHR{Ic6IYS7RTOVEW+?JAD(zOUesB1>Rv!0f'
    'uM<YIKWMc>+fuSKJS0xCQ;V4>w&8r=|1I!^RB<+eVQ+<=D~Poc9`>f~5sC!}QJD0I0nuwM;znp>au{Jsng6t}f@)$A|Al9uNg^U}'
    'Oh1M+o@4a8M66x~ZH2|h>Fr}6)~@8IQdkbVJb6vCZjh{$2@fBx3X)Y3z1=6S0}h8^P=W|nIsx@or=jedVR*os@5%36=X_}6Tq8V1'
    'RD{Rw>Zi9=s=i9UUG0=^&3m2lde^+vO`5!@GLxebjT5@Dcm*9{I^pMgB>p*U2u<{dP2jC8BCuCxiHE1ZrOU94P!kx5ktV|Ps-W`x'
    '<)xS*QVttifo+kqO_j_22)K&#5&gfVD#~Z-6Afsm=jJVG&-!MfXkr$6>-A@g8vZd!P%rWx|2l>=*VPd(IVQdBB11yq{-8dp0D8(U'
    'i}#tS76zXxX`nK{E4Fb(Iw4m-P9$T{+t}H*uGTYD3ow8+YD}6^;EUwT?QsZFV3A2*Y27;AV@dJhC1C4?>6fCQGs$Kca5{9Ct-Ba4'
    'LMCb64I9t%+fUtz>-?Z9<Ei4r+2U(VMQ~t_20K?}2v_CT!z4aqPBhF6tr>$~l|ONcoI3i+qkpvA`BV8Bq@6>Umh+R2pt7@*`z}tl'
    'kZ#(st9*LsvlDiHfK1zFA3y#nQlhcIjw>m`90<_}b90;7tu0ik2y2{#8LXPcRUGu~LApm_U^Krr#fL@(f=!X)<6-u*kW4+q&W$A#'
    'Es5sFj+jp;T8(X<k<PaESnI+4c5!dnQYm?$sjUNT-qb<=jrx5Jsx9q_gBICgIc#-TFI`?n0>KprA`7t4k}Jzbe4TS@L4T@A39YzG'
    'vzyVWvL8XqOh|))_JnQblD^05{3+`W-PzWwMPB-u*&{-Ie5DN&y0*Y}34^%MGNF4Eg>iy+YkC=N45=slS@;w>QK1(P*RdUcuAmLu'
    'NKx6=em~>GC7)Khb@Kxuke}&lX-`)9tBeueNg()GW3nlcI7o2b(OsJ({4a(WBeai|Ge8h0{(ss0;7`;$7$0zU{(sZMu1pVFuos<r'
    'axN<l^D_ML<>MzPU?gP+DD;miM&hPs)R_-_3V2pfQ#5g73m`2o_i|`KHZ$z@Eip3!9nE{yn|m~z%RJ0wR<h~ETxJGE;-JE&pvnW@'
    'hLDst>)`XFl}g7g=&B(l#1(fYT8dh^*epZo3{`%LDf;r=Jr0XtWsFFiR$APYWGk5Ot5I<?AqJCov6$IoJzySk`e8<+EfF0<5R8Ug'
    'O1VcOwuO)Qzi$nOPKV_-s%X?DBy0};dq+D@K<zdfD2F>5x*k7`s^G&CSdMQMyOm3?F~~?h{Un>&X7W|MhsBX{fm``!y$B&n3B~SQ'
    '8vdJq_`tg}VTfgz)rLfJ%^kTZN+jL7=dNFi2$i~jlwV@qyMKlOJiW2U`L}meR28OrOuhvwi9o;g9!C%ZIkSXt%<H|i-pBy_$gK?W'
    'Be}aP`Lz`UhhBdxvq!15#Zh-6<&H05`V>70oy_lSLx-_bZ-?FGD-rkkce$})ck{M8F~`j#m%O!>`}V3gpF-o@#B_wwSKMcJvgt9a'
    'XQD8ggQd}O4>Q0`3DRbiCk;&wuhwB}xSN|Sh%xI$bH&aKd<JnzuZ?GSZo8`~RfG_qPt6p1r@gIluA6q`GnJEa<n!<n>fHIuJU5rI'
    'GIFLT`zDo5r|}QWW7r<`FSD~MTL1U0yZ;O$bZ^bM*YCSiL+*7fj`#Pi-hYB&K!;8x8@Sq@IvLv=JCbn$4=jS8l3ch4_bMC`s<77Q'
    'iDYN}$*%V1cs+SQ;Xe%xW|*4xp2K0xf=cSEn24&K{6orUpBRlc;L41BTbNnQ4?Je-GR{DpPtjs~W1=Ie5hSI?5n$|=yZQ#s%0f?%'
    'wD`<%m1m<bxyfe^XQszlt&nxKFlZUOac|4r8qaQ~o&O#AD&oMTJOBE>as75yy~#Hi=R^;9)2{_4(Zq<if)-SFU|ZG-GfrCSa9#M6'
    'S(q-(iB>*6^bSkVv-ajhCq7^TytFXAh>ZD;4<c3de~mQkjdIVcYW7AysHS#<tgof48B3OU!Vh$v24bw|0yqt1g{jsG<b;5-f%4wg'
    'nrNZ|T;&=#5Dl*!#T~!{!*9(m3iVG1{mX$YOqg_;4aL)0?LTaV0no!bc67WmrV$%6^*ZMdpcb7^v>rP1bt2i;aRM(^v>CQ;fdG-+'
    '-a*H;L3vI#{g9cZB4?e*kr<~Ra+)a9wVPVkx00DzoFE)FO&r#m;%J+C--g0O893GIs~TaNI5!=CLt^BXaWQxycmZhg8%!5YDE|m_'
    'h^SgdKt`=;ZzsKzp2P5WF116k=kL1=aL9Q#SC}F2xyHc&m^vMHk|KZS!jEbEZ*FB0*2xLsT@Hik>44sRo)T1;dpC_Ii*}qM@|t#v'
    '^3jledprN?rt<~pMjRDQxsRA4xiYPTU9)T&`RXg<ysXvFgj#hb)LLgk1aZOE-xjVu;+_O~mB>x3DuLo*qkL$N#b~Me0WuMna6jxr'
    'Px3Fl28T|T4RUG)j8uJaOZ%nkw5#N!4BgIApS_5}VGTxi@k6CLvd3}9GKB%u?!*RXxXk$;OwyF#Z-xvKft8PlU-8s{6GTSE`*n2('
    'NAo>>N|MH2)F5b5m5GY<5&F?C*6(YOoa{opy|c12CTxnV!3soc#bhtQhs;`hbVZdoz>k;riQrRZIkK328cxaC7l=UVWp8NFyEjhN'
    'u!Z!Nn|lenl{1F9*zxy0^Lz{THIROGGn3uE@qN$j!qshbCuzHpn^hZQkHfMgAq%c}x9;Y8pX8SAxZjTC#@0b?6n~~r*5(}vt)i$)'
    'jb+pQ){B%DFaj6#*EydcfBy-8Tmwi#VH3JIGH7uq9wC|$CYC{*#4^uY<8Z}&^IAY7?exL;Q3@w5RT~e>QY@9`R&M5|xTIRIJHF;F'
    '&VoeC=0=uikvF}$tMWDYvsCK@J4*FuhE&^>GVP!Bfj!PAzLq|`2UCl4PnQwSVqnDGc<xR-0r4QebBnE{-ux5bR;*#bP6L@Aq%W9j'
    'ZxR#GPW<9mzxV~H8`IhC8F^9cnx^wztrw)+f?^X29<XMx{|Li;D8F$RHXwWmRv&Z^xYBT$7x6NX#AV)`mtp9UY95vrrb&$x@E(0d'
    'qf)+ekblaGfzA&-JuLJ5&;wr|hqZ_G{=Vnd_dPQooI(|<T1e$6uftl@K^G%IVT~FsyT|qjh;rNuJ9jXcZEducX~tc6;`a4<tHUr~'
    'sB$T~mc&clnpaee=<ullq06lf6UODB(X9>!wV~r#DJpUvVY)BFd|!sqT!#6*3{#HtdYLEvGS9j-+j#RbsyKLjZT6s6L%(6+^2_=`'
    'hN@7s=0jK=nwIjsM@*v6)#Uo=EHPG$muc)r`xBqERC1Om*gD7AVea@$eh14zYhf4y`9p5vL0?C=?qP}go-5D_F4Mq0;3BoaE-(pc'
    'eybMF4mbU<mg~6VdNCOE=v3~?n{0a6UA-exr!XxkGn~te643K-ZGQDR{HsO#<h`xhTxQ%I>#yb6_3mvTSET+shgy^0#5+hD5n~~q'
    '!1f|mmoRth+H01Mg!~gb@vlcu{_*gMBPU{iK6Ly~hmW7|Jwg%ghbrGKyEUw?aNqnt5q9;Z`qLX9hspF`Pp5NO_5`bn_^%LwSQq(8'
    '9;)@Eb&+Sqe{mC}x3tz_F-zL>WA+u`u9##lp2Uy_tp(9NDVGJW1Nj?G>EfGucxMJaQM4)ZuHK;DE2~ZW(<E00dlH-to?HD%7CzM#'
    '>rb&3t39y#_F9-1-SzS8);<3jVq^ZFS4X^=HH}o3b=vTjuXw{xb-IGX*q$i3sIEkLBa1p|>{Prph7rUhN1z1p@})a=*L^t5TNF=i'
    'N@rA+VzA7i+`wY5%w7X6z?*t0HVr3xZ{Nsn&oe@y*rMVXhx7F9FdV@lTTif&5miu;XADN|UG1y&KRddBJ^)zY7ek;d@;LI>HnW?2'
    'PREWP`b+H3hff~*?9j<Wa3=rDmxoWB4B4pMu0FZBjJ9)LUGr|v_;XF4w$>7QVlBCTg(uSglZyTL4U*92-=!(^+lO@k)#&z;{{R&Y'
    '3SLp`DiqA*`L?d+GqL2~T023XO#C0vG-;wOph4fvb-Jys+14O+RjxR{A5r{XEIJ4;JL@)i5LMX8At*yxJu0PFahC(Ka<B+99)2y2'
    '9T(zMl!x1;yc2aT$h$*kk6Vj%G=4+iSZ5kL8!MbYo~=@Qg{UI9V^|OW8O>9vT@<xs0(+R!K+i|KZlDLgot_gueHt-JUO+&~)ahe}'
    'ji3!E{R9KuUcYb`?+OEjMw-G@4l*7lKf}}8t0=frtN^e={bQ&{V68H;<gb3mZE3mL$~4NWk<E<h<m(M@!9~f=$YRb+iroS8k5soY'
    'WOngpzIW2Q`Xc{oL*-xK5&32rD|ca*{771)Thubd6%FLGBgYSadNOwC=!w5zwbWzBK0o0{9Yj#U$9i&#AQc~ACmdq#H#T*~JFM$p'
    'w<Vh5jGl;2XY*!9{9N#&tY2qb^b6b>mq9L$cO)8{?anZ0W8!pkJn+S)M7w?QY+KtIyWzIhq!>)MWj^D4VKonuzbP*-6_2oiGHJhJ'
    'lu<<DBs!NZI3*?{HlCpsYWzA71!5>EffYngEgD4;i!h<}z2?2GI9_S@HU+R@?mf{*;r1PC6JgcQ)J?9}iG@(Nr-TQFLZo$=QSp7!'
    'Gqan|`NZZvdN<#==}Dn4E1Fk5ch~#XYa9WM(Ql0ynZg(^LF*Y%J^`jEg&DaVaWnmZR#Jcx0ZIuSe8BUGTWiZ72xuT!qmSB*GBqp*'
    '&JoFU{5y+F3OO>Dxr^!m2M81qH*~DlO3%)6__}2MtN0{|miGVap#)?VY|()9<z*{*(hRINZGJ|3r9X`mJ*I+|!!a4Mn=7Eu;+jbm'
    '1|Vte>mq8O$(Tcp70_d`xQqX8hz-^ZltOM0$O{HeLsd(ivEC*QtdG<nV@MQ`LQ${=1F)e)(17+pfRIW;IFtr(@MGqIL-46inCA!%'
    '7+{m)V5d!vY5_tms<WL7ei-Fz^?8fRz}2v$SM4%m$CTNv3j0<wMh6?w?P)5aw53M=4n0+*Ta6{&WGD?4iNHMHmS|WDct^q$g?bvr'
    '%~D{lCcKg08T3R2`Ar1xSqvtz1EXM!$f%^AB_n-XJ|Sy<EtNEs$7H1x{OL}Nae*#(b6Jbca|}l1pfoly#-RO<!Ih*tORxJ>7wm$)'
    'ltnNgf>q?pbEhJBCP|T=6D?hXX?I8yTE!;J`p}9}H5bXu@jF|#b{ti9Ra1gx1oUN>5MMEZ>w1Tk$Ni6{^UdWSdPdx>XN+Nq8Po*v'
    'uk8OaU^&Itu|gPKCp4vJDgxQv+$;=?Tj-XZhSr32c^sIo8jM;^U|mlq(bC@5(diuiUuf|E^l<FN$>WEQ{^8^w5oER6zfAC}s3S>!'
    'psF&UV#R2!p>1?Kmx7IS`{jw^?eKB>ff@LrP9yzKO-3lQ?_l};{v{YMMQ78J`DkZEw1Jm5rh5G8v7^5~a{SM+FODDk^A{&|Tj=I+'
    ';r4wptc|G@b+IfdT)N!g0LTKc6#2CcHa$?u2h`j6me8k1`rL^pK?M-YE6?|!Znd*OLh)S{A0EN;gB;9U`!BzSZcGfZ!R$h46D!_t'
    'j$%c7eu_nGN~UuA``pzx6pik;r+*j2_bJX1R-+pq5+q1nhMT8aFC|+Ou;DtBxQJ1s-dXF}L`Ms62Zeu^R33g)Cm~FLZ-In;m74PX'
    'aVUH;_C3NWlG_4P9qA~{zTI(G=kpJGVOH^oOf9ng&6|AV_RoN->&-v*2C*QuuBd3}+zL-!R^mvZ4<kLnyUW?@@b|IknO_lss$SDK'
    'S^ePTHn#auX6r|7?Ff@7Dt;k$^86#`_~FkF|F1(xNPjqV@-WsxIN-oQP$i#Ah4}9)+^lA^)#000->9$`tgigVIS?xOX!V~uJK9>$'
    'mEYblNtNzq*(FWr*Fq6gxH^S?>k%(VQ3UQ>hT|GXJ3bTA1A&pLOe!dd4T=L-E;rj#6g{G6b>jtZ1mxFS&)o5+sF!YT!ar2g7C>I>'
    '<DF`VWcnOd$bu^6Z_%Q&E4MGsn6VdQ@{S$nDke^DY_6WgkkKsiRMPSKx{vpXu!0r|G0qod@>e?$bUTHT7vZlp@N!FIXO-;dLHUcw'
    '#!x*xD1V8DZ5&kbDP6bed}C`O*%JFVERhszZf>bbw0_-oAzoGg$F7#fRy!kt`xkA9h818_<Hy#EOo1ty+S|ctOgg%{!NAiJZd%n2'
    'RMn~j+y*$6#%TuYoJ*KIQx%Wa#5iz1)~#!3TINhdm5qy?gOx`>p9lq_V@itP+c*+2T)Z0Z=xFPpWTPgL(-Fas$MEBk?r1UM>xloG'
    'O#c&&!K9$T{7s_sd{qQkHP4p3*#*nkQ57{FI(jTp5}w)~Oz||J;25n3v;kw+*e@<dKspJUy@MG#jo-i<;@$eX1HnWg+ILN`f3azd'
    '_MzQiKjlyDnYM3Q&vi7OiHCKey)l`L6wmw-{7$}9l;@#8dS*12euxIH)V;zC@itH_Zw_QLqj+4=bk};*OYY7z2SNk8^Uu|L1MWb-'
    'I$bf8QP_+{?854I-t>Jmg%OIeH!?`!Aa`<^741kKWv_F76-(ZV!0Mapg`RufJSJ<XL($+Pdc<svuNIz-sD>#xWaN_1Fh1GWfa!Xw'
    'I4Vm%oCv`ijrlvYQ0SSZQe`AK>!9#u_9lxSD1&S~=b2?7aa!wchfWkBy%>>_lspjwZgUQJNwP|3@F9K|5j#VC0HZoqQ|{;!Z+ICb'
    'P?dd5wHZc1k$|a`0^<lTK<>MAe)}oT`>Ij$S;PwJoL%ooMI25D<=B+5f$MWGGP%rMZ+s%Rm~L>Wj_i<#Oro$#oY098>^==EVv3Ql'
    'YtWjG$zb|C8H!b+wbNFmSbY`{93bhX)469Ewka!Z8K?l}#F|o=Or#X5ekWtVg%}6KNwetd<%4f{E@8?s+_N>_;dpD~FfzSSj7+<='
    'mK%PWdonC_d!r3+eKWr^&z56nKaz@G@7voN_r?Z!<#I3Aat|?L>Z`8~U@<uF)iCcV=T{DjFca70`-4G&Qi+gM$4trrHK#iga3D%)'
    '63CJB6Xn_q0zvC8t*TWYdiQW3x(e=NXjD7;DI8Um!vg)$U$T{Uqn8QHHY}&qC%amz_J>(u>;1Os8p@d=v@|G<LX+pBMM;@<m&hF|'
    'P0YSU$T@Vx(H%3Ajs;sK!t^<-qF^lI+&h3gnm{(((HGukUt#Vw7LD(n_g3y(MgrL@bD@7~u-EdY>i4n?g`$Fl*s0av_a3x~$I7G+'
    'ykV55E#B#J_%Isfzq6@I_DP2cMsofp(F(E!Qgx?AbR1}mXH7K{U_%*pYHU{1{8+`N49u7!dgC*x+ObYrJ}!Y%0)<BPc>D3kx%pxF'
    'QPQ@ySF>1x^fqc+WBu;<bJWM5E+B!^7x1u4PXZIozUkxo1}t-6pQj%Jm&}c=dxKA~);G1|GglbP^fI>zXA)Kp-x=UG(RIjz%L?>!'
    '%<UiL;8|>etOx%%4twTewcLGLk+#aVU&TB%xEoAfdgh`CVsA{wK$ndryPBHdUAUzMU6f;^?~EWEBAze@36n|!8=w4?K}aB+jfjv<'
    'hL^q6<A`*V;O9bidT{I~JG$Z}_xzr4vPaT=%Dj1&nRj+`5vZEtbZK8qzvdW@+{&Q+T_Wr63Ozm9o!j2fqH-{5$`+n#e!upcwZuxT'
    'P{j)*X0M3!90}Mr%B+?tLRtf^RzowYQ6<iui+4zaNOLDDO<K~hWPt?Z>bD^N)Xr(Tf?F=E^3;n*m_*HtfaF&=G~x6FVGOt-h}W7A'
    '26>-<c42tmtqdHloW0LU)iU7WikZ2ux<V$wVm1L?qGVyP>XP<uHxN{mWC6_kW)m`Jit9V#e8NU`p^>6#*Amm2EcK5HzXFXRyE%!;'
    '^`K(hUedJ;3}Pbo=+~|>KlK3`z$uN2;lKDz5k{fey|Jz3D`<5cUqexc9z)pJ>qEJ_OA(G<@lEX@_e{Qax2GcT#ScQzt#mcI)FfL3'
    '&6B`evg(p-;is8u=KxCZ!f9}OK<y=~OSt<r#{w&|pi>D)0Zdrm3pP;OGap==|D3f*7Bzc`>1Mn|>SWa=xsQL+NuQWklg{$9LIqNj'
    '7VO*4gt&hm!nt`<b0_N$G@dz=KzmGMbL_%55`(Hc_?+qL*RKex_+DD*P$^g*5QO&<YdqU2&WN%pr$$L(5rW2CdtxlV0qgV(>tgM9'
    'I3FDY-OeHe3d~+>Cl<Cppxr@O9C-&T4l}_NdNTR9C^%|CW~xDyb<K~OpO98$iM-R&cE&=5HSgge`}qM48`ggHAl%;{3Bk?uzZvnP'
    'RMhZ<m2e(qeIAzk?TGvKac*pl$5**iM~i{kc%Dr^L`T?sPcD<<y~0z6IhAnu%3wf07?G9)wyMLiYx*o<RWSdfW~5&vB(f!<H=zxB'
    'w_Zs&6?I#nNGTW^62Y|rm!y#$NFL;oK{lF(+DhH=2e1I9#WbbB2R&dJ<VjQlVM!mX9HpkdR%D?FRRgMZ;j@&gsbVVw!i+Flj!y@~'
    'Y<K&adjrGm3d=W9YL+@5g6VIjxvDa^k-s{UeY5OG+-Z{4!AeYta!!DLs?@xYn^tO0R^Ld7H3SRJiY_YARV=ppGinH{(F$o--t-w!'
    'Bv7a7EO6@6jZGJJ5m+2#loVOc0q!6ZProMcj(ImdMNmv*Mg%<V0^HBz6Yls;3|wKD8)=l6VFe@J@RmDpU8SP^DC)KF!G@^5(O{ry'
    ')F`2N$bkuz2xbzvDB1Wo0)IV^)wdc?!)fDmo`=iS_y<YBl^RbcniHLWs|hY!y%Y#A9-qaE2IPY<Gw$1Igdmw;=b(9Z%Aj*(yI%w&'
    '%s=M$(8w~C@+Yc9aNcg>OUu9&i#R^*Sk@!?3{6ju<ud}Z%t$@jUwUC$=eN#*eW7)zK~Kji8XScO_U#i{$05P09bK(WjRJX8g7_Wq'
    'z0XAh1J#Qag$81|+7}@2832=9X^``tEm;n!U&)wUB2=nQC_7TcB5&>`YFOJh6mGYd!b}U0U$Lkrh@w4x9Am)oq5i_}adN9YShthX'
    'I5_i4-+>a`v*mQ6m1c$nV{+{q&i!`Y677r_(xDvdl@2<a0nR*(J`a7|dN>ppl^0I9S^WGZ*)57QLM`XTBjO=T<Sk00LvZF1vksMB'
    'q@~b2&Sl0yTS6N-W*klQQzrJHV0*En$4<t6f9%VnpUL3Y6GxAH@x|ejdZJuWxm%`el(-eTs{UOH3orFRu)*nj-is;T+FI_LpNcdw'
    'iuu9MK&QEpUrp;!g&-ig18I^Co_miL+&jZ=>Yh6=Uds}QmZco~{K$!u6bBsp<KaV}9X^gUAqvzI<-roe48}e^1dkm*cH$&VToBa?'
    'KIYD+IWaK5fVm#0&g?yv{1C2iHCaS1LP?ZAcjV;ZKZmsPS9k;8tHGc3^?yCp(6IMZ1N>1{|5s?Ts)5_^tC_(e6+7MB1_v0P3tZow'
    'EO%VUhZ%5@W6L_4yk`SmPln-m;9I_^jHy6L7zCQ0f$Nxa?%qbfI0tv}JbPQ?g@re8405xD+juG(9>g?fsUm!k^%Wo7UvX)cGoi)Y'
    '+$yG@aE}>HhI$Oju-y!Q2xVs?9aeYqYojpT_6PSmx=Jjf+ImKO*&Y4;^N-z~K6i5&4keIwo$q@l;U9nGz`g^&{AmBakM>Jfq&N7)'
    'e;I{=yDK23qo^iK=Ba$oG+cLn`HLiiH_&uuGo!h!G}DT*sDC7?nSS5@tt=fn>)l%@JRb3GUv(deSD}9&e(0@@s}yOt3>P?&^XfK7'
    '(__{eTMGt8RO9Kz!d$=K=Gr*A=Lx_CHqwV53J}SA1BLsyDritQ=LNTv1dW!UIgwBm#Dwy!0v7wsofzS*6_G}1dfmG_A)!hKx$Zqb'
    'C6d79vn4)qKHf;F9PFtl7rp^WR==;obnUmZ7l2<N5DtVa7Q0|14eH$fX#IY2lbSbF$4Lyp-z2^WhQdLU*s|VadIt{)*(COZ?<t5F'
    'STz5h-bZ2u9Gi&aqhsU}e@6xwceG)y9qIxmLz9R_{Iq}Y^Ye9PX$F$4TIILtpll`M3q7nr)cis9MkxGPy`o7VEs123tGCv-`YrKS'
    '@D)Wn%*_0_<Pb|*CoCU#?xNNSt(O-mj8JF6H3$KZFqLxT;a%(GEFoG;{s^Y|kj&OuvWHmvgPSQD=q6YyomdTy&PAO|;@$eXPa3+B'
    'q4hoEh#GM{+RZErPBpYn%QRM`Jd&6rL1l?)WG~7+k&rr>!NXM~*lCAr;r=x^2@rHe0)2^26O#}n%>>vP$wUSyNPcYy)6S`v(1y=J'
    '`Jfc1^&52l<Q~01;IaAjkT$ED&_>gCmOziAB2(9$yXUPeM!e1E-rXChNyvm;J;N6fC4kp4TXx6S3b*eGp6<Xr!uf>E&o5>VQUHiM'
    '<n+K+Km1T25Ae>RT<Ignj)oy!D&{0mUKpN<FfZu`^Q_CpZ@HNa{>JaYi6HLi^2KF-hW{`H0>Z9g7-%BXs#1C@2mrjcc*cQTMuG8)'
    '=VM^l<RkqGdV=~#K)P{zX9X_U$eRKohjNCryE2j8e4bxhlhy9tDe>auk&~YvhUxqR?9$`^T4`E@%A|)?&~oQZlr?P^n_tm)GbYJ5'
    'ScQ`>R$&uVJoq4NKVoLoD90n}hJ`b6U?wezRuIh+O)=(((!5Y=HLU%H(tqHP6DK^;0`C@!P+2l?aBDb<VmYP|wva&1wlT9qNLii+'
    'ItoGYIVV!XBPE>tEZjW2QKGGIDk1ZA4ex(RC^|R5P~uqp-?gKF-Q{qt3SqirQvAV)&NdLgtFhx;yz?OSs#KWr#ila+o0(#R!*&c6'
    'ihQxDNHB6x=&{5G%@NNskCuKRU=u?e^;WZ`dg{~8-6hmxX%47frB6jD4pT-s(oQ5K%CSQpuX|aOX7ppwR|P#!K2sW_0)9}KPzbb;'
    'L40LHQB_Bf6j4*UAjly$)>))rCt{x-JND_PUzX;}7)thPgC*I^dfW03vN<8UMmZNQs~6}Qcwjy)-=;rICJfVp@C-3QpIlJJ-}(q&'
    '+mh&%KKBE}3Q&_P!kJ0kXN8nfu4moFQS_lvi4b-H5H1gCtdd1@tde5UusR~IN`O3;<IJM;0C|u)<c1?i1~DpxlYqheO{YARl)|1V'
    '7XM#7xowZoriwHtVHP?uVhfdPYxLC)s9jin0OaVcscM9_^oF0h;~RV-Dvx`d*uK}S@mNhz#al`4{9PDbD=OGk7kyc>3P)|458+BR'
    '_e@y+=t?wdJ$A90J`s&lE#V3dFSJS_EeUyB0Q0NP$?|7-i209dhuI#7BX^l{v)fn=5KF)i+9#gjKrHEHMDE<4$2bRi0AtM3552K('
    '-SoJcHCA*-2KkNb)@-3?5MwLnUcwc$*pUaGCsckL!9#O5mh)3@^J8PMt-kMBhE+k|KB1Q*bh$&JX=`L;5WRuMFr@R<My_`)KXipm'
    'Yo(w>TU+X!s(&Y$zN)narj~$3;WvrL*>t`w8E<9tzGWVkGa3#aD(^NiXd(mdVO!<F`uI6%T%~m}nkS<#w}rkE<__h;8~KHzaz|5J'
    'TT@e4MS=%&SxxZpg^JwjbarzQrBH(br`+)o@8%oekA;N{3MXb(U}<6F|2FsSPi-A}zW>S@d8<H)f^a;O%4L_ydNQ8b)OzAvJF_#l'
    'Fjpmmu-Skh5;*Y^wuEg!ys=}9F<^sjY+{Fp6F+#Gs{5xTI+FNl{)PK}boc4*bB@4HeCuYn#z^YZryt+`_V@Yu1~BN?lQyIRT7l2T'
    'fUA0?8;}C<x2TolXZ4$mIp^?*W77uAt>V9qHoi5%Bx%Sc9SPbpPbN9{hVlq+=-ZS>4l8e%BhkqU<&9*o^Hcsir{|n7nZKc+A?Wv)'
    '2pX&=f;54&&6rKa=@*P#Qc2kO?DUB<l(iQsZHRe9s+rk+18friDb6p+Ppf<M3ICZJVk0@U{YE51dw=EE#b40+8@$kdSj1nE@4{dC'
    'b+#^}LIcD2^!Q&+kXb*%>=h#+%GYPyS1<9e((<<Za2x+Zy#Jjg{Hu89NnvA?e%<=fUA#e04F2d2d~YXvaX67XKVG_ar?7>VsEGUE'
    'yV7@C?hmun6Z$Gf+|?)a%hSv558t^jm*HDONHW<~XC{@Ugv`SzO8N$AJ9k@`XAyyK5G|UeS@eyt!B{nx+$v9rUk`+japH1eWBf2I'
    'ENI)`0uMXtg$hisx)V$L4{z>YL1TvV{(pbq6gD0gpANYz(<C3iz!-pMkIFCaB1BmnC?gIu5eHg{13?MP%Je-pAtE~S-mDUJ@W#Hm'
    'E^W+SIL78(MDrOQRt-TK#nV8d6?xwX(~6}5WgDOj9!#^M%U+!9+^CqsAQmi9zk(>BU+JRug9P9uzsK3`-==D7-#gp=KU!K^{?E(C'
    'p7&!P^nMhr{-_S-`HeUMc!KcwM1kb=4e(5G+Exh5uUG5AmzqGkVBNsV?1_&zTBsV-rS#w82Xx-5dk107IeJ2TA}Iq}9pV)sBeKYe'
    'vE7t>njslQK>O(o0qwaYwd#u$)#_)5TyjbPw@fvhovco;?~=2AulDX%9&tVY^QL;YJkGzuRcEmVQ~RbkZ{dt2goHP`41L43^>4X4'
    'cpdgsSN&@hTy<iBI0u$CnN*6Zs@^xbFs)f0^3Fs=x790Xj$IkTxK52*24APjQBhe@|Bn-=kN^2ZMNM7x^bP-28i%`+oW$N=#kCuS'
    'S0mrJ4{p2b*Te_G=U|uv&-r3hulxA;=O_Mr>hve#esp)gE6<Mboapv?w(Sltd!GwC3+1(EY=@&8bL9qNR;$m2jhm&ZQT_9FX=^X='
    '&Qw?DqZ`GcTizphq0PQ3Y>gDQU(hRVPnM@AwBHG+#QR*_*x1i)6sP<5r?)G<sIc=43qx%^riRe*mF42}g!mk-pgUYlWs>I)cNynV'
    'bI?6gimKWF4yppj>~Tv`V`%e*o1Z?Mw&16d*Sl()l3j{1YRX$6L0~k_Ti}m!LBsf?{Jk2+`i8tv?Z7YOg$MMiZ!Q&rO_FW0iUE(S'
    '-c*qHieE7EG5FHm^6DZ+#twZKD#&v%L`TOL*+g6G7b!2xNs_QcL#v2{tQV@0rF5`@fQ(D|DL0RC59H8CG<z(CnMd@{wN3aEYugC;'
    '9K*Oqv+bl%qiQpRI}p}aHOKN<DT+m+&nQuRP<vRsUx5Z9QjoYqBz^7s@Nggl$&Cx!iulT`$Q=`(y^zg-`hY@+1&uaCMNHe;wK}7p'
    ')xL+t8)ZN;g=5!K-wsCjkqeM_!rA~1ujE3)dd6gbzPz{V4lk4zzo&@hnc~Fc{=ju^2*Yb`?QW;2tT8?q#j$%%b1Kz|cN@_ze~q>5'
    'ayb<yCV(!<m0BFxDc|qM*z4_ij8Pc|Eoa;@X5f_W&X#6h!2qpMySv|VR5wGU`jaMknYDm%0$Q_{IU3Yfnw31lICCl6m1>tzE`gzo'
    '_0|4c0~5W9S0{pZg|a}rSRpJ8`w~kHpdV9UwAqOu94+>+uefqg?POSVSv)XwEUw3YsfrNt<c$WsPh2k||HK<GZ($&+wAD@2ms}E5'
    'H(skerV%(+dF8Skv~TcDf+?@gs3Ir2kvr#W9X({<NWX{e8QW^fGE76%Z92G)y?N6~5FsQBR9i*Q3?7s~jEh8bYw~=$0|f|Kdt0ks'
    '3L}t)?|SuaD-;%op+T0)*9GTHY6{o^phEszO_3dQheoHzd#4y$%&9`+XYf$0)BaIawAaz0hu~&S_1cNHfrVOdUMdJh2GLi(t)Nx('
    '^`?OQ?6A|oyqZ_jXgK0xpB!Hq4TOEgq3Du9*!!7s<49(XJ3lDlnMRqL>U@+#<1NA9fN*^KS`jx?;?(Isvt|oI+{gch>I37Hsvi<('
    '{`#jsee(AcdWdUrYQ~+tr-DrPA769tKheK&$0iCGVWpx_`}JSp@xu0l{Tc6@()QKT_MrFp)PVlD+yA}s@zD+U+AZUofm!421GC=W'
    '+V}b+;_a;0{N=Obr$0FZlCf6yBI52`b|*(kdl=nv^K6S-+XVH9G>Eku<yTi&T^SSY7=eb`y<COIafcAZf3UPX0_t15@alQ_b`FJo'
    'LRaL@uu#>VNp@5+lq38qP~hg5%7c%^-3cvkZlh#)?}>=-85=Av>|oeG^-$V=2GXCz_@8)8p#I|`>u~gnJNvA#x5{iE@zk&9;q@FK'
    'j-g3t;+y3^L>&QgaUq({+L#Vlz>zzjoj&#Pi8E&se>-*hkBO5f{{G3wr#{u_qjZ0{ICfXtk00E;R}P1l!J~#3d1&u7p1kAEcx779'
    '%Jt~J-grMZ?=>DA*`Iyk)dA_B1od10XuRm@V)5>`-p94RQs0W#)wA!67hRt!-@j~^3u(<JP_<5GTC*K#5Bkn4cvx9HRx+`^^~!A&'
    'RL#QcS!ddZFH7O4ZM(eY7wL|#(ut1Dd0{za1vRCE#|&Nvv9P?mtH<F}g;G4U<WL`FYAZgx?Cw2B0(!PA0^X<%(_is^j?6SVNC(<8'
    'faT-GldbtrSX6{aM<o~Nd-4fZ#iI^DbSZ4yKzH<`_0snb-O0Rs`FLq3hn{-kNO8r9;^Ga&hvLkC;)?H-R)*LuA+6i9?iDP<rj^p_'
    'zfyX(fYm3;SBHe)^a%L$T5$oPJ-BxiQEA3;HA<AM=kM@G?i^3fy(c_SAy<zs7RLi%KhC#xoCC$zX31Vmu+6K5JfndP==VWjH_|`~'
    '8(bZzkju?L5rqoW3&|mYn^PT;KbzHK703uk%_I(qR&{|O8kz?m9}rIbfHWPyEFh;Ka(eM`&dy}g2!Rw+0vYJ>3E!hkZIy99=$-hx'
    'xTuuz5_>%K$VGfX4G5MDYUaOWX%x~?-V-Tay<OPaD(p-PyP3Q6BVJ;?0k5Q;#oTD!J^p%dk$g-U46e~p$D0p-fXo&kJkTf%CMCYn'
    ')ZzAyi>X95g=E!~%ChK-#<Q%RcR^nWh;gi08elggDTqM_IWnjGVz@Ll3Ktf}qD(BgE0Zd*f(#R!xPo|T+hbh6P(6W2DO8R*z=Eci'
    'spVn$4x1N}-r1UJN?C;_t3Ude`UWoP_>Y~tzxn8Fx-n|jy~M(Ss1tQ+8B^SmR;Cc&O}}smrual7imGVKY<F816>i}?EUH}?2(Txb'
    'QcquZ85_j)sD*+;+mmhT=uFwyoRJ&@r#REv(v`6HE#{#3oM=m)OSOd=KqnrvIKsQZ`CJ?z>#k48Y@<<84YwaQYDO6W?7h+l;oBu1'
    't|$s28j=x_!s~g#S|Sw32OXd#E@CXT*16^vQ`4hlc<Tx@GUK~BvfhT3I9lT{6=MM#CQ&A52KJW6P!V;!xca<2xl48zTnw>b>%04d'
    '6Z^SIcV(KF3L2;Yb@j6MYY>1Cb3<>gyZ7!ejyD&Fghjv-pevK`2L~;{fN%#yFmpg}$7)15DRB%zEuu8*^4^{DGeUBKD_-s-mmdWw'
    '6eel+77XiAX~m#pDp%nhVUk-B3hByjadr>XIk>1XV=(uqyts^|*4Cf$_rheZnhgo)((>98N#<e8wp4o9Cw6M;8SDk_*VHqLahMI`'
    'K>7kmQVv;2GQ0>XAHT~aPI2X!fGG;*O^)F%?asO1_VM4vg$;M_3R4}{vz28&{jd1#ueI}03wc?G(JkyQV8CbJZB~hO#e)FiwQgD>'
    'tZ(rBix~0VQR=l?+Gz3~C+gKe=eey~J4Tl0L+@|KCHg=GFFkvNYL3Qx@C_b+upf=^bXb<7cSO+3NTm8MC>@NC4^+N#AEiE3?@^XR'
    '6EOOZ?(Yu{i>^pR)Ym5zf?8OQ*c#+nj>ueLostIAifIE<TV5~2zT~r~#eBQZ0}^%MZ7Az-qth>fvo_@uK20W;^(bHBx@h3DGD-Qk'
    'k|4qh5DVt`42&$MUiWKte8eeijUq4w<q~47i7u_o4Jp<_^$BL+h$+7?PgJkXrHc#RE`{A?ueIQT;uDU^Tyw2|YxO;vskl<AVbEeJ'
    '8Ai0x#Fu1eXPa`jkld0u-<`}f2UVnuIl5O%_rYX?0ewy%jemU9K?{zyGPqaft?v%q(3NA>_W64e*lU_>Y$--_awU>&nN+g*QUdF0'
    '6F*9HCet06WP58<b%ioZq6<%XJcMuWpl;v;B12KMWg^?%-ku~Ig$9xn)WUCbCS;d%cl$ZYqGT6ca2FR^sn)vGHUrsWtF+6I&Y6HE'
    'G3yfe+-U)0jOk<Yl>?Ht5gD8BGnts}iCife6ZJSfF52r!hMKpg4e$#3PUb=e7BX%0sg(4(0EMvPQ8ww7&#@v|d3<|=YO{G<E=)}b'
    'Sa6)6IL9%+j2vP&*GbRVXukn2yhfl%g)mL6X!+T>Tza(aK6vClxUJh+IY2MKX3GXx;B|`6dfhXqJ%SKPmJIDs=nS#GA`UDieWHxT'
    'o>~HYV*4N(qUJ`*kNSSf&HG-ai0iA43_GOh5480K8fCF!k!YY38h!WwfY7Xk=XJ%!A&Pt3gl4@x@@q&PWW-eR2FGa_GKNjQT3nY?'
    '+S<dsuAM0ai649nojEG0mt%Yi8{_4*tHtLd?#!}-fiT@k;DB+Ma0ZxGys5Q|7+1EY^AZ9Nmj|E0^`mqJlzn!l+=-V|S7D10PJpTz'
    '6G^ys=azN9F2N5RanN^69xV*=eQ*`%4s#*lvZ9aw*qLWKB@4-Dss9d@SselO0pCIML1?kj{__2PJWDU1f~Y{=gZY2#<p1sH&$;2^'
    '^VPz}&GPE{&$$si44`#^LUwD7i_@2;`(Yw9l94@bU3~tSTe}0--Tr=e^Tqy*m=C`Z*1shQ<N#M=8rXhZP>3w$amk^`snOy%rq(z|'
    'B{D?f<4-<6PJvKZusL!34;+c?FL{or6?4l5x(a5J4eQ^q-d72M1-_vIu6~f0P?Q!8?Zxw&Y>Zj&!f&+UalSXlC+hD4X|lZ57vRaj'
    '3ag3aIeA@nbfu~dgARQa>d+uEQ&1K<YJ8kW9#<pA;szXeu(RcEtd!SQ+<RAm?wp?nt~<!{`mexu<>efzc|T4uk1VZDOS;nJqvo$+'
    'e|VIfqC|uAWOHorFHYqkpCr{lQvEbwZgOpm9(~U_`n#x8M}NOdk_q<@ACA>FR&c(EK5g$yB|<X_siRT^GFc`82R!B3K?96s<J(-u'
    'p)|9L*k=oarOQ}>gzW}+G*+hLSTlBQEMA;ir{H#s5#+T*Zn(CIM|Sr*qVSI1)K^zY)iv4fbJdwh!`Z(j8s8=PE#@WB!rv`z$@AHI'
    'X!qAo;gb2|38Ov%^6HjM@_ajRzpOPCWfs)DOQS=jxxUiW5|UGX5STT(MyXaZ5Y<n)hnVIz1PaG<@%Q97)xaAQE}7xWFg2Xu<W*#G'
    '`2jco1dSqVoBGrwn3-mkZK(Oj&Nv3c_kIK2zH3a;WaSN=1_Fi$XeD!_?EUMEQX1NC8+D{<6y00X>`yT<hMKy*2AMq5o@{IVFT|D('
    'J#lIW2|jb1Hy@2|#Ms&P$462ChRTJ-OpnnaYked?6t1%TcJb;29z3|3<PEfc`97>MW;_x=8ua1zL&xL$i(Bs2#Qv3CG@@yxBHY#8'
    ';`}RR8X?rpL3d*V?<JtgJAK7l-(n|Q6B6G*<VtV~L}};der|zmXY1&9-n=WkXZZ((jjagg_0V+`!RhVZb0<g3Gq=mDuXrZz{q@qL'
    'b+HPxSa8Ac(v;@zqGyhOA`TgZx-V#!P#*nM%sL4E)hH7oQ%zq`RH6l5kIx~EsOOD<*W`I^v+-RP@n8#oLWT9*Au=ZA;7oLR=*}L3'
    '5CbhL43x?Jp_1Y~8liWidez#}nrh}tC2QU)g8s7=nSy|NR3=bO6k&M-6OjeZV_p}TFvXJOC@X}NM$*g-0Pl@tTN|(dAnfXf{|%(u'
    '*z?=ihqb*?H2F)nM$~xnUGveZ2s{{(k43729}+=`RFn)Ooxez3%JPlU9JO0Ed`YzPCA;$JOReZY=v@uhv{kZPJS-emYPmxb#m#*W'
    'AV}GT#T3KGYqPp9Z5AhMq8@R6h9jv-bU){=v82G3XHkyo{)XTORE~;wp8}(<fXE8sw-9+rGDnZ!?f{$R1^NK71+37daR!dEX2CaC'
    '*j~bFoeM9~AiS`FvM;?JRAHUA;tB;#NZJBAeFkI}ETS;l#{#(}WS%+kG5ve;_#aN3G=KuNiF;BUNBdm6rOS|ln=u}d*^*Cz^(Fe!'
    '*0d-Y`e;8+OB&dx)keih*)u|9ve0x*1mdgysP0J5(bxyQ@b@=o-+}+jz7zcj_WYx2w3$b~5l*+T#X`9&6M<8C?IVQqd#9&1_CEIY'
    'Pmz!jo%#IKXCa_~+E{2P-@q_T&<UwqC9)~1p_gDBGaCUgfL*y0AV-b*X*rHx(Hg=BydhVH6k#KBo$tcJOX~7{i~(C5)!A25M_U^X'
    'ijcH4CX9HoW(s~L`4!2dEFuf^u(N3+y#wckOO*U7u<m*2MvJsUeB7V1uMN;&&m=Iali8DcPB1L&z3vcChi?91N2x7i_=~hs;MAeP'
    '0^vtJ1E@9G)^WZ&mFP@f!pIqVAVi8sU~UOWC<$Z?wIR&?ToCMz9lY-96%=)LCV-~_RWN_j4aO0~vco;MRxaC_N`8SCL?Y&hA<>1D'
    'PSv3BN)Y<h^spaGK|C9E{-xgeP^D#xUL?_wjQc^bpkOt%DxhP4DMg?D;pC~0|NVdd_0;Dl5`X;U_{md${_BaL<J<82v)M*Cv+qb8'
    '&)a9yjc}Mh?2Y2z$Qs^3bkKh?&NIQZ(beSb&7qHUapgW+17WLv@4WL4Z`V8T=o`iwh+xk1)AD;IQVU48v3qbYU_-hoKqdvDxwJjy'
    '?&SO&Lji}eK+MHb1f&F&J1h<5sb$!M(0+uQF`%6gujLp8)*an>#o^&VI%INFb{%*M1aVAh?_W}i)&Be)ae>?!(K^qwtqsvE)~~?3'
    'Fv|n)WFh4<#u?^AQ`mHI{5p&i?VkBb7XAHF`CH*|bO`j~lD`+lDMARc0!w)f2B8VY7Cj2PM5ve?bJ|Iq>P{=nqhm1yVr}p2vNcBv'
    '{(<W;gsuZ9XZ1NqIcgKx04_Pus6s6*voK{^4q5k8CPSr5j!j|liM36P9NrjqC)V(3U~RY7c+cZXGsPmUu=}^KDzZc&?CIswv$gW-'
    'BEl!EU-M?~=-pD>#sdn!&KDPUEGlz{m&>oNDq3S*!lE_b3OZu;s;(==9bh8V^eV<iXh63TPko4ORbli?8!R-+aOnaCodfxm>4D~s'
    '-<p)~5vz6l=xB-CMt`4o#RT7kHEnznBfodNC;<6K<eh{Qa1Dv)5q0JRGRz>5G+STjhq^;b8Rl&i`6>61qVa&Y_c@X1VS4dX3b6~@'
    '?jy&GMfQBB^N@q}sZXL;pAuzt=<S$fNOCv^PtrD4tg`nF-RRz7qdfTr-H4S+!(2fFRm6Or?4BS{RVx=UyI?$5x-Zkwi!O<o$$#*%'
    'JDq54uE&UGJ7J}sxSDMaV<!rF;DbyAWMJkR!KJN*{TnkPnUTKU9#gCXa~4Gv>n&l)CL=yvhDL_ua&cyjujiA;@Z`T5aUa}N{+IIJ'
    'HRpYJ9hzW)vINZFXumtw=MFCL_o%kg7<g1veBt}zoxc4ie0Se)P9AgA_|Z2B{C910e`*idv=|2KeV`W$)8{O!%&4$VpvQzuZaaln'
    '1bzw(`@tC8+rsnMcU!)HoTuz(Sp<}z=Vbl{t`v$Fh#34P-DQ20r~2S90^&3VSCn7cMPBrXh_4Z@>`<@pNIItdo$xbbp%G|zbF~o*'
    '&%`X>9@C!moo}BzGC23toR@YQWnP;--UFr@Qq4rmC6uZ3nN(9pra3|N>m^I@zNwD1rOqdtF0pk<mh6cruWG%v8p;6=k9T%w>(%-M'
    'ASrm$sjsvv_4XGEGh76_P|oCGVqrvVdM1UF)`c#)b=AyeVil<*=Dpe5G4)l2J<&6ay)q{r-S96MtWsT(GkP>vd6+yI4Q*V1vwxh%'
    '&n9?vSZXn6`1;Kx{v)fYPgFZZ<c0gPh$&ES4)dVcr$Jf;ay??-<+YXK&6(G(*dq$^(2&fDc~wcYCf=w6Z9uPoytS*zbFPi*5~Nis'
    '0bycAtq74nRestBk}Ku{&fmjY5#-9jm<dY5b0?PcMLFW=eFHz&`wo0smsFLCi8)QfprYMIFVD_YdHw~aKs{N&(cM~8V}VW>K7~Wt'
    'RBbc7Ex(7G%vKEg*4?FDfyK6Zh4U6MhaWCH-%XA8I7P}7wg$w#IK5C_|CVnmy`Y1P39;lZ&0b@MvM@%w_{XbE({5J+RHd7&O|{D!'
    '#Y|{`sK?WTj)(7R3Aj*vbfzDo8!z@}2P!x}l-`CJ0rxjRIkI$?xJ46+6qwR1Cy6QZYxeyBpN?-T1h8ap%$HH=UGmMd(SE0QJBKUE'
    'hgraS|B{9Xz<zdWq`~}Z_lLHlL*Ke%>+a?Y5YvFO>M|_Gq{(8f7%;NUsLev;)FnG>*Em{94{aA<Sz70fjv38!Tgb;fmi*s~n0RQJ'
    'HmS;PCvNCoRLAQaBdoqhYX;}e_e?|G?;3mUD0i4tBGuli&m!WTBlIxpAWc11VgqkSq)uFgmJky0h5${x4_L>mF}y7g7oKSNx}omF'
    '+C~qr*lt4|RqBb9Dd@@8wcd3CSJm!G&`}b4P$Z?b>5Wz3L2pUe(5||ThZfc9iWW0-O@-pGMYUlHYqz<Zw7dwPUjC+!!I1$of0Mhy'
    'i-Hm^FcoHa7IXGDHdN)L(sx^h4G)m9bmJ+f3n21th`xtu@H89H3FU1z#$oSjcy9_m13TXE%Ry!Y-m0Pg<`rm<tP|qP{}CZ$!iKC$'
    '4{?z-kwP{XAgf^v$IvT6`4on}ppJ3JhQ`Xdel+nd48lb&?kTMvBUZqr27Vi)?if?n$2dwy1R%><HvZS@_j*g@JqJ_wzV!0;zBG?('
    'L*3EZ#^}Ml^dPbKMht70|Ik<H%a(;m3L=P5VFF-GAC9F&Ig4`2t;j~Sxl=Pta@m_b&`}-&A5>m@DF|3p!@VtX-6*+G9=B1IdWZut'
    'hhLhz3zDK&24?&&$Pegdr(`lIO$K5KVE_-cLfh|+O%<=i)$BnW#7s6B<fkVyvO8T=2qsuXa>PGG{<1tGk|qv99cm0tqQaB<YXR|i'
    'JTz**!qfe#O;DM)m>^|d`8p@Iu<SG>yP*6iWB68XN|=6WfZLb@u~;?Qz$Ewe;$@>caFP-Td}4sp<Gb&j2&Rv8<7FMeI=JUE$!0jG'
    'C<I=L^o9fVwWTPMK0vrN=BSCd-gAc$Rx9`Nmh3k4Qq5}P8^jyhZgA28RvT|EEbj!at+Z%)jSfdII^ptVtO8isqTmGr{yJ16#3XDO'
    'B)HWE5K({~rP5gxr%)#IIlBCf%fphl6L$h7Z*7%H=B{{x-m*9ybk-sjWo{h(8%x{X)L%=^rZp83wzhh~a<i|3n@lCFd^31=!aL@*'
    'Am!u3AMuWJ*;M8t%z?1R%xH<gRNs*M$n2-u7?e;D{<SID2_4IP8lI|N{hO2V>YP8P(%B2G>Eq28fn8+IsD#Krwl<MdIp!QsU(!6w'
    'r#idd!=P6*zqI}rm$@cbRz#^mRXpYxAi2aIP9Aeo$xPcNG23j->)HHL>EyXKR8=}bT~B6U;#8`fW&j#aS68wLR}7IZV$MYv7h?1!'
    'bQ}o5uS7?NW1*=nmBcjFfWCQ+{9M6!V|7Ab)`T=oRa3vw827KL#iPfrHGMw8&YODSccYXq*dSG99&c?G&tn!C|7%*#u-WMXdP&2='
    'r(peIa4;yYD}Dhd;{5d!r%B+%)i{6Z$b6N|H0wR7y7R5=nEwquwq5#X2U&{Kk%rB}u9Deos+|{K#u|&_jptHr9beU$eof44L^qOH'
    '5g^X4WW;h{63<G6%v4_tzW&0OLcXxxdtpjAud3A#csA%;Btz*SrVP|fM_UTSG4E-gi`dvJ>20;f6V(Ckvvx?E#-xMi8AAR|$#h4$'
    '6*ezn9)$Br*prNc+2l3|1xeo<<a)&U4Cpuw8^y`5tsuv=Cof?}L$dQiCJ9n;clsQqvv@sGsoxss-w~V;dXzm$n&uo};@ZjX=2pJ?'
    'i4!<DaH{M=R00g^Tz4064x$v;$W-e^2^e(5ftPf{Bg%fn_Ie;g;9*!FprZv|o`l|+CtVUVxKyJD(C`U`xntOMwO|Ea(PBKr^${+n'
    's^-^F#Q^(VRgJRLJUnJ$>VzC;{jD*_py7J?hqAWNzSlDWM<o(XlH^c+50fA*ozfH{8hrK<=aY0(TX!?8idZeR2Utf(^a4M&VAU}^'
    'sNQaOcDAKB!$I3ZiUowH{|0mhdjyw5DpTWp+5uY*Bt|vt;nAihWCsL$3Jcgo-S7)Ly1P2NyTlI24HE_sEv;K#STL7{#wcCQ*g~JQ'
    ')kMb6z<6LS@Lgg9ytV4==&X(;=!68cgMKiOKy)N{BNutFu7YaX1CjL~le35gy}Ul)J1NoAF4SvpYKRE3qEC@V31^KG6lzvpOW5oG'
    'Of|t;M<_!Bdolvl8bmsTw6#S<VYG<Y8c#>_Jqr(uP=uF1xD&VB(VLA(Pz@5w_b#gZq#o+uLu1O`S!s+!b@t~C9_!m>GTogA4qER@'
    'j2a&7$VBXoNvO{G4>c$GeZ4nd;xK{d*ZP5<t)hw{)e%AMI8jmmH^Q0#vi!hl$|nvSj84jJ#}p!cgo@Mg*FtvYyG3?<h_^}%0WXjj'
    'W1#d)DCG8&m)t?O53x!WXT-y(=YL|3$&3;pQey$vYWiALg-cOJCD#aGfU<OqGvzWUgJeOrG6u}WtnDjWujHreb6OKISoqT(Sv`HU'
    'haX<t!HZeus3VPA2^}aZc|pP>(t01g$|bPm7gN?j6;wz)JnUXm@t{dbNi}$0qQAhQMEz=ubxnm!^`yU;_ZA0W8Fv=3(n|8bFe%^S'
    'qF=WeL7a@z*ZE7TL^xR-dW2!@!%Ob9;h%HY-0KD`I+vFwhxlCzjzY+H?)m53WiE8VHI)myQ~aRr3p2L>2-5rvRLLNlSe-s%HjXsf'
    'WT!HOg-mWf?X{Cn+UEc8+wV0r9ATazm#WP8Qd&=IHX-O!yg<}-Og1Od9lARLRUG+IkX`aCy@C0oX)=Ws;=4(+f?h~w6UMNi5C2f5'
    '^rgSdG;IDyvaLIH0&fF<s7jA52Gu$KtIR<dk=<2wJ-Sb)N)OP)$cFFY&GjM%wA@1~L8dP^qRtppuat&9hB<)z&+;y!?~(|mL@Xsn'
    '3LT`zIfeR=S|LX+hwP73FIZtXi*Os@lQBodOvc!X6qlIYTzE9qo=l^9d?D4=iORFCPF2`|n0M)c&fn630;|wmB}b4=<+dDZW20}R'
    'j|jLS^|K0DnZz=Kz#e|Ow%~Am<KnF~t8$V6Nn2YgY&|G#&j^H+$HN4kCo?1R3&rsvkc58D4P%(&&=~&j<oM4yN*SE^!Ck*m%;)#F'
    'XG=qy<U*R2W8nXOy0++)=K9!0={|TQ-a_mJ&5cY@v(MbUC(7SvY7r*%k@=f0vRg4J##0`Uo&E%{b=Zb<2UkkanrlDk?M10!g3hy^'
    'Fn(h8V<<~lzyO26QxvP>eOYbxslzWFLEz)U_DE@aEM8vy0scQ;%snbD?8Hmk6Ylr`!XU2<m9}@>$-Fx;Q{G$W%oI*Y5jgiEIYt$}'
    'FYj(+wLwNU?|Xr;IcsYeuCGO*d0<xA3mx5U&57)#G#t-t>whUhJ{$<hn6D4uF2pXaPomLba5*^vp~Gq=dXAtui$gZz<rn=Jw0m=q'
    'yTJS%dKxKe2Z33fFTYBqkJWtmUVHLuV{Vm#91TW#h{?4b3z^u-V?0hZDQz@?GyAOey<?Pwv{l&76*lv6^aIW=#EWxVZvF*+f};&|'
    'FiyWcybRZ~0yc*ULmRJz4DYxqsjOAuMx2&EI9Q=Z4e5jTTH2D?3yu^Buqy^nbk}yugU>LiZTUxc^|p6P+XMpM7pY4U`^Y+jD4%M-'
    'R!7vl7}0;LfZ4Z{4-1ndynlMJ_ztE@-_dO8JDRYmDZLTFdCLj!g#~PMjKvGsnxOEX25tte8pZO~_Za87mqOrsyiwJ7MJ{?{aObtA'
    'zkmy>-rwSjuacSbS>i2KK&M$??;z5rT3|)HusjCv`&3mF`T#;ouocqmMSZPB0jY}RLsT$(<Y6)Y7>JGRtt7@eaWXa}n90KCp-#E='
    '2xx*e%i@cCv2V?&n<Yw(NXCsOXBeo|(KQZf2^pRr%X$beXY;Frl-*Eomp08t$C?3G=wfMDcW=Q#+2{wtbMR7af}H@h3QakT#m}Yh'
    ';C9s342~25N>TBK=j&1iNmWNN!+?8Jg+o0($Xp$%2^sn&e`%X<jd77}M!MQGGfZXq&RkzpUejc?5PCtVZ#X!QsP0w&k|&VEfu>L('
    '+#QTL(zFUhMw$L@KZijyaEk3(i*e_(qaWxIs7HL#wL7=WLTPb$%H4emk6<O&{v{aP{@~#LEUfV?-fKG(?oQ5~Tr1{p3L$p=X=(d0'
    'rbym+<*x2x-GJ}5c<B0t5I~035DrIY8fB={qpw5-+WVC@Obkv$<km-=cbwnV8f=Wx6E!^&LxX&Zpvn2HenOORI;oLh>w@MbHCrFT'
    'M;c@^u-cjuSe+@Xg$434sl5mPIVc%#Zz;`LA~s*csx*{yE65G5P65fxz&iW4ekiWoBUV`0d5pL^12=%t05M~?>In7c3)|z)@lQk`'
    '8dYOYpm!+il=p7odE&}LjAS~y0A~pS-d1lHHivAcoS%h}I-kRrqBp$CMG*Mn6EaJAWhfHoF423K<@EG=rmprZ!RP40j%nZ0L$b4='
    'Myb(9#qr2eybb*p@g^jaeBo<tmKGNyj9jW<S+N<B5DmPmNeJ4_K_G@O*e)NZ)JryKtIz-~8?_$sXDQ_iqLsrD33~NTt@eX3{cFgq'
    'DsNMXl4#(wA&Z*uETisnQKkK4y{_@XuG|7uMqvqs7m;Dydx5vBtueW=sdRTc-$Y^Z?AzUAdl}zl5L)w@s8yia9*;)lK^deRJB3}!'
    'PgvyI7_^N3ib|Ig{SdXmMH$xy<0|u$?(khWl$aAJ04Rl4ft(&Lmz@c;fDp2lETk!fZH6;&S0)P^Kd@qD!Fy)q#-cXZn)NV(X-ug9'
    'SxJ6|@Dr9g<SVC=O_19FZ9hbY5s{!kcT_|}DV3CCP=p*~aK#lriNeMn^*60R(Fg??_H!)o9o7T`T)mZ#m~XccVe+QNbMu9rTfn4s'
    'daFUsfY&vs1`nPJZOJ2QU`y@<<1P#Z5Wc9NqoNX-7(Heqb7F+gL1R$FmdwZWxANmLe-RzeNThtJmaR&wWJJd|Anw^Xoj6A>yQuZj'
    'P^EotMwbubXIq9SB4^(jgDVgujPl+!RLm8pA<>-nqrA4n-cAZmzFeGIw`T}rLd8gnm(*{R&P?iJLSAdqAEGnAhPu|qpb|(X<B{Ej'
    'azn3ypC!uQDqm{m9eDU><YlMdUF7FdG*?&I69cP<t_@{9mp$RC3LhGx;Ue62v~o}SX0$}Adr&R)a=$Kew%0{Q?G*2Xkzb7VMkAFw'
    'J_ic#7r{*eeiAidFegT>L2r*5TyMn3;Ouh}uwqA?)5)*2xQufh&8?|y96Ib~&wetKA_+5tx`I~E)H!hxVe<X`QCJYAB52laDMGY&'
    'XS+B)>g&!9Hioxj2r`F*_`8#HRuc|l;@S-mBvl27GBU&lmXgbZ)Hx01dr!pOk$=qUw#|tTx-t&=CJ(OnaJ)WP9P(pg9e&df8m!0N'
    '5}p-Ww5g_PkmzN52!hU0$ecLrIeotCf&lE76$4&>u=>ghfd<vh>K^XD*Ik*8Mnd`}Aa|@Ys6B|L4CyssW{oI(i=vbZsPW}T`QYAU'
    'WjkbYt*Ni12TK=mJaD0g+Qx>u4{95OyTW}md#k|hf>x*Dy`zmNuCzp)Z=4>huii+oVxewxGGS7+K0?G#Xn6(n?K!vu^)C^|5;XP<'
    'G)1m~u_@88yTGqHFexhqk}6xWZdn?BQOxyup(xg~+AG>qZCw~Y?1g+p&>($tkUNTKmfS$u>G$={`xU!uiLi()mEL@L?@noZr6)|f'
    '>&2{NdD1~}(nnzH3%@r2rURyeHm1qB(zAQ)&W;2Yw+ec0dLd%3tck^ej&*HbaD;A(66+$f4Q4U5USA8M3zr(fHRS?+Wb##Fz2~6R'
    'kv>4PC~S?o{a4)F4mshU7Pjw~7W&+$o@I>94e+b5MWf|w<$*hZHh4yh;xrEp3ti1<;&J(59~<Ec+lV*JCmE$GEeHS&4eL+2L>F5e'
    'C}(J;ya;Xj*`Ld+fa&K`&kCS@5JIq3GhVl%Rfm!hg@yVwYY@&={(?=%QLhMf5VO~RGrXk%wVZg_Ve8e#1QUy|y=-fkij@0_5#tby'
    'ZS^@D&>Yj~BO0+Gvb6QkIr=+CA=IE#_WMP{B_ujITJ!#YAFcUNG!*~>FZ$8sezX<{c3}VdUCq<Vbuo%JHZf0Obcg{2N?UtzcoH@Z'
    '%d5Jxuj1vG%kZV}wZWJdU*I4f+(v+#%@@GSMUdL$cyVN>e82w}yUS!st*4J9q;WjqImKnG&>Gq?%i0*ztctN9)1YZZw?`3N&eZ@_'
    'G#&_d?a{tBI^{?A5sXjPs=#!<Tuy8*kP35*h;47=6JNT!QhvFH*hX7xZvF|VqUEh8h3#EFeWh8fXJ*jUpWb(r!w(+YpMAkc+IiRE'
    'FA>4}?|;XF59@vL;^-j!gLYu<98(~FTrO?T0OKg$T5#8|f?BIA(!x|v-@E-kg50Os;~C)_ZRm5$9)ELq0)@>HcZe*PlMmhe48w^>'
    ')Zl<6dR(f!wpQ3N%4oe7SI}L5<fbRkK(A_0Cna6bFES=tHu0qspaWCy!;U`}X!Z?Ut&*Bz+AFLoepX_hmkLWwGy#ybAN>uMV8g$u'
    'c-sgZYLOvJ@5cDuh(8C@%z)Af36r`EI${dKN_+aNe1%{F&Gfdpp27nCKlcPnG!cWBVo(wD(vV0&zt2M-aM(lH3kV+3)S7APZpEA='
    'l$pZXN~Z%wa^ZOR1<kD);6tIqdJPwZ?p+8-m#ysKF=l&^LoSGcfHc7ZIv+VDM+EQ?t|clWx<FRoSih|S6(3~v<uM2>qPQ+zAVUZ-'
    'GGKcGQ~8N~peIB_u3`6x2soKr#*miXIm}O&RbH^>_$C6pmHTcKul!hA-gX~u$M<JnmX_|sOEbHrk;&4^kSe)?PT0N0V&9r#Ac)xx'
    '%C~;Y7zt1btATM{`R^J(Fq-{>iuJ&)#&XX*g^@$vjB=hW0&KwVd;)=b|67E(6R>g<Yo)bCs_Pm6KLE10PejF&Z?RWj(N9gMpz@%m'
    'z-%|S<>VgYi{9W7k-S;Fb_>K(e5C51rKu=LNsFpG!FIKf0lx|a?kf8|^@sY;SDm!5i)ApXcnR18P$kMYJ-m$R_=`{6ff)jC7*=G*'
    '$EV7_*i~@%){32aH4P6|VE`qU^(GCW3iOC7){s8c6`?AR$15>`jYT@P8Ef`lfi4hvC3dAnUMj^RD9lg=1X;!DsQ#7)aB&-J>as+j'
    'T4Lc}n%+~yRpv@|@%>xR%acp-;?#^gdk^gd`N`6g5qIJTO?m(k6=AM&w=r3l-c%fa<mO*+cU+fRL>u?N1euj<*1~lNx>0fZo&a{x'
    'M$C>EcW@Whol&jS+x~y4pZHcQCR*h2uD^OS5<ByigLXPE3fuHOcUSYF$R?m@e$Nm|K~!;@UDa`Z3t}4$&tjxu{w8Rb@CWQE$&-EV'
    '_yCYbaek?|HHpAlRLI&cT1@W~xEw@kOdCva&<T?qg3%DsVPR!1+&p^^Uu4i=AL=yMZiy&Z1mM^!{;&<y1;TJ(1!vaQ(78v@ZiuZS'
    'OE!v=JvLPu`5wESSmORc4}hKp*j~j_-U!h#M}XZEld4;+&_=b%IO0yOEJmcUe2y5*RI3Ru>Wv&Ci>=MO8xJWk_p)6!TAgs}0f)Ue'
    'Uz)oc9#^Po6e3MP)W@KTYVNgNu8S_B=88ijs>+?JqpeqLq%g*#h!!g&bg-OJ-|f=u2B;NSwsCK;xcva<wQ?UpVeefnt;`lj_HcOz'
    '2Vlw^k!*z~Vr_G(->8ch3hJB+zN#5>m=R2>wDn_oY0rK5gS+``{}rk`JVAxr2lK*8kWX+$K^6h>cNg!J_hwZU61(8<7h!>pZ!N^J'
    'J=Js}3BpV}7w5&hpry5~i!hIjUn~}&gd95o_-mB+yL?Y8(I;kCW;Ga<`J*pvx-;3D$r{43sc~6vwREln`I^n!Y#Ki-Mw6e;SuI9z'
    'L;`CvsT~f$qjeRq?kD~*Xda2ngF?d9iSpA$WEfQA6j|fmHQ<@>ba8eM#CcWa9CW@ug_MM<Ca#pHWy<2x)=EYN#F9?~@!6x`Ma<;z'
    '6j1FzVSgl91<iycu3(@i*%)CxOIr_x1%TFy(c|IZ?B^y+TYK=oT;Kq!tdO;dAvc+M)3H)VmQ4h#5ftFw7#KrQXn5GOjfuFu@sZNb'
    'cW4fK|G$qp?${M9ZT9doy!jU!ru;;9{Ld#o{XB8zf1UaK#9y$&>t8<mJaO_^;`pa${&wQDw}PJC!_vMCA-LXahEW4rw4Xb>g;EPu'
    'd1J^lm1~81lO`>pt9)S-mJkv6U*z{^9spr#amkLmw_BQ8(nbWA4%PAO!?F;Iql_5E?iq5YV{riBy&>S!^@edYEY#7oHH1t$C_dPV'
    'daP3BpsEmT8SB~%H$eoLAN-~VZ)O2HdqDnEC5+yn9u8@TcpYm(Z!iO`khE2C(3MRJ$`D35FBMOU)=rZ&D0os2DMdXUQSe5HkKEP)'
    'Qh`nB*@^Tqhp~&Au80`E1hC4H#w?u}88s?ok;`xVA(>5ySPcyBYcOH|ButYzov6b8u==b4HbNd>@x74m)$i4z>Xj~ik?><>X6h4X'
    'aZ#<37KyB<IQdf@)F_3k*QA?Dc!vh&A!xsR&)r(H23cCo!4WTQe_LMN!pNxYegu%4xLnR5!Z61OV==b?5W_8y-z1XY*>sONm4>?C'
    ')z&upjE~V^;gyz8PzF_2ld=1GUGsLYkD4j1L%sml!v(iiYmr}i;1B6^z6?-`Z?Z&}knQ1MLlaC|r)u4>4r`0xRU5hD{32e`+P>gf'
    'UVnwMF`TBU8LmGB%|P&Vv|wT*vP!-b{j#fz(3Md$dTgk)yeG=mu3jmREERUg3R~lDe!9E}BY6U^!=JdS-TG6vZw^iW$!xY2*R9Jb'
    '?}CyvOcXJO4yPw;aH$vP_qg)9cbDygSRum8>HOmpT;-Z?1QIcv6Q|CotV0#r$r%N3r!oA6%yc=*qh`H70xggNAB=f&&YZh76#<z-'
    '7xI8Tasdg<aNb_RtkVq6ffp9;M=VY_{bE0Nc^@<iqC1|C5;sCN_}{wQlWCjNv>+rTCuhSVmG{<pFPTbJHa5Gj6+EQGia-)kE}kA!'
    '{O*l-OeQ$G9<!fRAU?zF)G3Ue*a4Ri+!L691i#7%L><63;^i+%*pFTV-wV#|^FAzVMf;5os@$a0bfG)_g;N|~-p}=+kPVmAlQk^6'
    'MlLgugG)EB6?S$w!U$wT_Mq+0-{HP=!w$!5NWY9i`T!BIJwa#}@HGz0_@M<Y!3Y<Hc~n~HpqYRD%V5ML&*8bEhG=4hX%Ln*Jgn*v'
    'Wcy$qMg7L8lv+Rt2|*rUWDT}DRQQy9LhiEYKL{%`AV^dgxdWc}L@&`s(h2q@vk#2O7i)9mb1G}h`;`Y2)Zn!EJuraB=p!WlCWKNj'
    'lF|4s>f0k)$hy#bmWfre3ThlY$}o7e3ea8Q&=>S??;C-e@|kNi&avg&RwvU3YHFQCEq;j~5J!A9?~b9(&{*1fj!a{eT#!EmGu-|b'
    'uRTs~nOK5riQ#S8E~YM-Rg6m!@1VA#^d0eb|G;(JY%h2p?u)TuVO!h%mgO9EWoW=O+8kT6UNp6_<8{78(V<>hw>KV$)?|D&^f;)x'
    'HH~nhZ7D+AvdU?C<FQB=QE6yBZBUwkKFy#>ip8=7M>c-z)4*b3AJ*2|5>T@>TmSD2Zd;R0B{NMIus%H12<!XD&fVX9L>0rve$Gvh'
    '>ucz2^lYT+r`-HcxtYd)io~q0b+Pev=ZGb_tTuH)KK1~1WgQd!>H_)2r0AZ&y#cbR0%n=+_$po18*DdfiR@49xmyqGdjeLzSIBHZ'
    'Bps>fezuNhM11xZAwr?bmpW5*PAlAcnN$Ocd*{;_zSu|yZEeY&xP^xOp*?hKajXm3HmTeJ;=-fE_;e95<)xj2O%s~3X`dmLm0(u7'
    '@*O4f#?~XY4goRkgXTXNW7<qJlJ5%2H41>6mMC1>o*cxA&G)r{2>Z0SLYw{IYH4x+<mCMu=$b>etLH4U#EUyXzDYK`NNj!u_OBAj'
    'opNs4UEgK+N(asFtFmP2!lrWj>v~mjWXB!6#w<CYy6_>(l$p~@oa=6FYfea57Ktqp=9-TN9Z(4%Ayoigh(>b(s16e4Z_%&Co14JW'
    '<+WwWM+b4~1Arsn09V?>tP$`a2=wbO2PRpGSgrbVpz~Gh=Sa6bd{+D3`#!6rvPhzWM&2Y=y~4sd;k5hELQqE)>}C{0N2mk#NI^Fh'
    'EKqC%E^LeoOEbA|OLu2Wv#)%SjG}xXV==^=Q>jjTbbCzb3qxbbb6~qQ<xan#{f13ZRgY>(GT(1&1rDLCf|B$khYb~ua9t5KnJmD&'
    '13Y~-O*mpQro5r<x3!JG$p}{MpO7vzX(xi=t|hWqR-^{{7*}}oiowOys*=N3NnMsZ*d4mDKl4EOB3b1}7X{TARt?xA4|5fTV{c#O'
    '2Bqz1TqwYF^?R|T3yb@YN7>R{cr{;sep5gI4L&YELcAb!D6efgr;o-zKI(9_1r9_)M<I=zRbk7^Zyus^DNw#riEL9xXDV^NJDCYM'
    'Ls3JE6cH6|YPq#%gIx1=y-huq&>Rb4f*v>&Wvnm@Y_hy5>f>rPs6<psU}@l2)2e6)f*@$6Uflk?zfg=OH|y0o!%MZ~PabnX9uc*D'
    'jS@)|y&?J@;~sPv2W_nntZAy3g4BJVJ|Cui9D=Z5Vj(fzF%tzc+yo!!^E`y%#qv=25DhGA%r5{u@DSo9KBnEmk0>c%+a(qHyY!2z'
    'G#LNaVULRI$$QEt<5j|9s0bHv(9&8?pRj!ocKP@zFVL|=>en0YngxZA>6cuL8jpwfDS@IzA~>we<YZK0T|O2l!)Wh#*9oddsV$=J'
    'APPTX2d!K$BV-<N>O}!1iX&G6Cdk!TuV4JWwDqt!Paga4zjuDRcF!rUEIG%{Nw<AH_TmmN7w5JpyoliF=9VMuXm)-YxZ$`%kBV3N'
    'Bm54EeMfUNTCz9B5Q;~He{+mRIfoL<{`>+b>0>RpXZMH~9&tQx^xz6!Oj}Q|a@^hkrozxWIGzN4C3$*=g&ttb5%*ONt~z(&a{2je'
    'd2JeQuF}q}2=hS9*LiRi7-{Jl@>Q4xO-YWHSvrhU0&Mod_~44P1n|P{9Kmw-VPzLaz?pu*;fdw7XC`o)bX}i5b?S52{?C$K7pfB&'
    'ERsqjqBXE-9c>p=)zKP+D@=D~8$g(eI}ye+$wu&R_K@l287Q@ln@~@LWlP7Ka7y7{51jNQg61aEge0EnrUL8^sJW?T=iDU$o05(5'
    'EB@I@o}+*`<PAxO4A$M94zY_Gz2X;DqSWR^Qh6uN3<rX<!qmG<SWqTf(+oFpGu*-5T`li@7{MP2N4q+5HglHH0x`)@|5kp-6BZ8t'
    '#OV_!kAMEjsZSFhpZfI7=ckW<^6BSvNbRMNtSOUfQ^i_g=1GtlYr4Mf!fyXBOhgGa'
)
_SCHEMA_PINS = {'count_result_v1.schema.json':
    '0f9ef21f01fb75f3a6d2d52855c6ea0ac76700597eeba5dabb22dffc043f2279'}


def schema_check(value, filename='count_result_v1.schema.json'):
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


# This setup helper is copied byte-for-byte from the original catalog test.
# It avoids a test-module import without changing any test expectation.
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


def finite_fixture(folder, entity):
    slug = entity.split(':')[1]
    page = {'entity_id': entity, 'canonical_name': slug, 'data_mode': 'production',
            'page_status': 'draft', 'conflicts': [], 'element': None,
            'field_assertions': [{'path': '/element', 'status': 'unknown', 'evidence': [], 'conflict_ids': []}]}
    registry = {'versions': [{'version_id': 'version:synthetic',
        'label': {'value': 'synthetic', 'status': 'candidate'},
        'numeric': {'value': None, 'status': 'unknown'}, 'order': {'value': None, 'status': 'unknown'}}]}
    resources = {}
    values = {'page': ('wiki/characters/' + slug + '.md', b'---\n' + canonical_bytes(page) + b'---\n'),
              'registry': ('wiki/__schema__/version-registry.json', canonical_bytes(registry)),
              'span_index': ('raw/wanderer/span_index.json', b'[]')}
    for kind, (relative, raw) in values.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        resources[kind] = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
    identity = {'format_version': 1, 'kind': 's4b_lab_read_identity', 'scope': 'pinned_local_read_snapshot',
                'entity_id': entity, 'page_key': 'raw/' + slug + '/response_candidate.md|production|' + entity,
                'snapshot_id': 'synthetic-statistics-routing-only-v1', 'authority': {'kind': 'synthetic'},
                'resources': resources}
    raw = canonical_bytes(identity)
    (folder / 'identity.json').write_bytes(raw)
    return {'root': str(folder), 'identity': 'identity.json', 'trusted_sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture(params=['off', 'finite'])
def runtime(request, tmp_path, monkeypatch):
    mode = request.param
    counters = {'model_calls': 0, 'tool_calls': 0, 'network_calls': 0, 'mode': mode}

    def model_call(*_args, **_kwargs):
        counters['model_calls'] += 1
        raise AssertionError('model_boundary_called')

    def tool_call(*_args, **_kwargs):
        counters['tool_calls'] += 1
        raise AssertionError('tool_boundary_called')

    def network_call(*_args, **_kwargs):
        counters['network_calls'] += 1
        raise AssertionError('network_boundary_called')

    monkeypatch.setattr(socket.socket, 'connect', network_call)
    monkeypatch.setattr(socket, 'create_connection', network_call)
    import httpx
    import requests
    monkeypatch.setattr(httpx.Client, 'send', network_call)
    monkeypatch.setattr(httpx.AsyncClient, 'send', network_call)
    monkeypatch.setattr(requests.sessions.Session, 'send', network_call)
    import dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', tool_call)
    monkeypatch.setenv('LLM_WIKI_MODE', mode)
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    config = {'version': 1, 'entities': {}}
    for entity in ('char:wanderer', 'char:nahida'):
        folder = tmp_path / entity.replace(':', '-')
        folder.mkdir()
        config['entities'][entity] = finite_fixture(folder, entity)
    raw = canonical_bytes(config)
    config_path = tmp_path / 'finite.json'
    config_path.write_bytes(raw)
    monkeypatch.setenv('LLM_WIKI_CONFIG', str(config_path))
    monkeypatch.setenv('LLM_WIKI_CONFIG_SHA256', hashlib.sha256(raw).hexdigest())

    def module(name, **values):
        obj = ModuleType(name)
        obj.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, obj)
        return obj

    # Never import the real dotenv/config or instantiate a real provider.
    module('app.config', MAX_AGENT_ITERATIONS=10, MAX_PLAN_RETRIES=2, MAX_FAST_ITERATIONS=2,
           RECENT_TURNS=3, CONTENT_DIR=str(ROOT / 'content_data'), PROMPTS_DIR=str(ROOT / 'prompts'))
    spy = SimpleNamespace(invoke=model_call, stream=model_call, ainvoke=model_call,
                          astream=model_call, bind_tools=lambda *_a, **_kw: spy,
                          model_name='offline-spy', model_kwargs={}, max_tokens=1)
    llm_names = ('llm', 'plan_llm', 'plan_llm_l2', 'assess_llm', 'answer_llm_medium',
                 'answer_llm_deep', 'answer_llm_l3', 'mechanism_judge_llm', 'coref_llm')
    module('app.llm', **dict.fromkeys(llm_names, spy), llm_invoke_with_retry=model_call,
           _select_answer_llm=lambda *_a, **_kw: spy)
    tools = module('app.tools', tools=[], tools_by_name={}, MELTDOWN_TRIGGER_TOOLS=set())
    tools.__path__ = []
    module('app.tools.search', search_world=tool_call)
    module('app.tools.query', find_similar_quest_names=tool_call)
    module('app.retrieval', _sanitize_query=lambda q: q, _is_compound_hit=tool_call,
           _judge_alias_sandbox_batch=model_call, TITLE_REGISTRY={}, sanitize_prompt_text=lambda q: q)
    data_names = ('角色知识库', '地区知识库', '任务知识库', '武器知识库', '圣遗物知识库', '素材知识库', '_npcs_data')
    module('app.data', **dict.fromkeys(data_names, {}), _normalize_for_match=lambda q: q,
           _load_content_json=tool_call)
    module('intent_router', route_intent=model_call, get_tools_for_intent=tool_call,
           get_pseudo_legendary_note=lambda q: '')
    module('wiki_entry_graph', WikiEntryGraph=tool_call, DEFAULT_OUTPUT=ROOT / 'kb_vectors/wiki_entry_graph.json')

    names = ('app.workflow', 'app.schema', 'app.agent', 'app.agent.nodes', 'app.agent.executor', 'app.agent.coref')
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    workflow = importlib.import_module('app.workflow')
    nodes = importlib.import_module('app.agent.nodes')
    assert Path(nodes.__file__) == ROOT / 'app/agent/nodes.py'
    assert Path(workflow.__file__) == ROOT / 'app/workflow.py'
    from app.wiki_route import router_from_environment, WikiRouter
    router = router_from_environment()
    assert (router is None) if mode == 'off' else isinstance(router, WikiRouter)
    if mode == 'finite':
        for name in ('流浪者', '纳西妲'):
            probe = router.node({'user_query': name + '的元素是什么'})
            assert probe['wiki_route']['freshness_status'] == 'fresh'
            assert probe['wiki_route']['answer_status'] == 'answered'
            assert '未确认元素' in probe['final_response']
    counters['finite_config_sha256'] = hashlib.sha256(raw).hexdigest() if mode == 'finite' else None
    yield SimpleNamespace(workflow=workflow, nodes=nodes, counters=counters, mode=mode)
    for name in names:
        sys.modules.pop(name, None)


def run_statistics(runtime, query, expected):
    history = [{'user': '旧题', 'assistant': '旧答'}]
    initial = {'user_query': query, 'messages': [HumanMessage(content='旧工具不能决定本题', id='history-fixed')],
               'conversation_history': history, 'count_result': {'status': 'complete', 'count': 999},
               'final_response': '旧答案999'}
    before = deepcopy(initial)
    statistics_gate(initial)
    assert initial == before
    graph = runtime.workflow.create_agent_workflow()
    final = graph.invoke(initial)
    schema_check(final['count_result'])
    assert final['execution_mode'] == 'STATISTICS' and final['count_result']['status'] == expected
    assert final['final_response'] == final['count_result']['answer_text_hint']
    assert final['conversation_history'] == history and initial == before
    assert runtime.counters['model_calls'] == runtime.counters['tool_calls'] == runtime.counters['network_calls'] == 0
    return final


@pytest.mark.parametrize('query,count', [('须弥有多少特产', 9), ('璃月一共有多少种特产？', 9), ('本库有多少特产', 61)])
def test_actual_workflow_zero_models(runtime, query, count, tmp_path):
    result = run_statistics(runtime, query, 'complete')
    assert result['count_result']['count'] == count
    query_id = hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]
    target = tmp_path / ('workflow-' + runtime.mode + '-' + query_id + '.json')
    target.write_bytes(canonical_bytes({'runtime': runtime.counters, 'query': query, 'result': result['count_result'],
                                       'harness': 'real_workflow_with_external_startup_substitutes'}))


@pytest.mark.parametrize('case', ['missing', 'ambiguous', 'error', 'partial'])
def test_actual_workflow_uncertain_zero_models(runtime, monkeypatch, tmp_path, case):
    query = {'missing': '有多少特产', 'ambiguous': '须弥和璃月有多少特产'}.get(case, '须弥有多少特产')
    if case == 'error':
        monkeypatch.setattr('app.statistics.routing.count_catalog',
            lambda request: count_catalog(request, root=tmp_path, trusted_sha256='0' * 64))
    elif case == 'partial':
        # Inject only the approved generic assembler fixture, not a story query route.
        result = conflict_fixture(tmp_path, False)
        monkeypatch.setattr('app.statistics.routing.count_catalog', lambda request: result)
    run_statistics(runtime, query, case)


@pytest.mark.parametrize('query', ['须弥有多少特产能做料理', '须弥特产的用途是什么', '须弥特产采集路线',
                                  '须弥有多少特产除了劫波莲', '某活动有多少角色参与',
                                  '蒙德任务里有哪些角色出场', '枫丹的料理怎么制作',
                                  '蒙德有哪些怪物掉落物可以做料理'])
def test_modifiers_fall_through(runtime, monkeypatch, query):
    assert parse_statistics_query(query) is None
    before = {'user_query': query, 'count_result': {'count': 99}, 'final_response': '旧答案'}
    assert statistics_gate(before) == {'count_result': None}
    visited = []
    def rewrite(state):
        visited.append('rewrite_query')
        return {'rewritten_query': state['user_query']}
    monkeypatch.setattr(runtime.workflow, 'rewrite_query', rewrite)
    monkeypatch.setattr(runtime.workflow, 'assess_query', lambda state: {'execution_mode': 'L1'})
    monkeypatch.setattr(runtime.workflow, 'route_after_assess', lambda state: 'fast_agent')
    monkeypatch.setattr(runtime.workflow, 'fast_agent', lambda state: {'final_response': 'LEGACY'})
    monkeypatch.setattr(runtime.workflow, 'route_after_fast', lambda state: 'end')
    final = runtime.workflow.create_agent_workflow().invoke(before)
    assert visited == ['rewrite_query'] and final['final_response'] == 'LEGACY' and final['count_result'] is None
    if runtime.mode == 'finite':
        assert final['wiki_route']['route'] == 'legacy'


def test_legacy_consumers(runtime, tmp_path):
    for query in ['海灯节有哪些角色', '纳西妲和流浪者共同出场的任务', '纳西妲是否出场', '活动剧情覆盖率']:
        assert parse_statistics_query(query) is None
    nodes = runtime.nodes
    name = sorted(nodes.DIRECT_ANSWER_TOOLS)[0]
    ai = AIMessage(content='', tool_calls=[{'id': 't', 'name': name, 'args': {}}])
    tool = ToolMessage(content='原始元数据', tool_call_id='t')
    assert nodes._get_direct_metadata_answer([ai, tool], '请给我原始数据') == '原始元数据'
    assert nodes._get_direct_metadata_answer([ai, tool], '纳西妲是谁') is None
    assert nodes._get_direct_metadata_answer([tool, ToolMessage(content='检索', name='hybrid_search', tool_call_id='s')],
                                             '请给我原始数据') is None
    # Compare the four actual prefetch implementations to their pinned originals.
    baseline = tmp_path / 'baseline-nodes.py'
    baseline.write_bytes(zlib.decompress(base64.b85decode(_BASELINE_NODES)))
    plan = {'copy_sources': [{'relative': 'app/agent/nodes.py', 'source': str(baseline),
        'sha256': '6a2ad5b37ad663789f98dc99bed50ec2996e37489bfac033a6e15d0083d1fb05'}]}
    original = next(r for r in plan['copy_sources'] if r['relative'] == 'app/agent/nodes.py')
    raw = Path(original['source']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == original['sha256']
    names = {'_deterministic_roster_answer', '_deterministic_cooccur_answer',
             '_deterministic_presence_answer', '_deterministic_coverage_answer'}
    def selected(text):
        return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef) and n.name in names}
    assert len(selected(raw.decode('utf-8'))) == 4
    assert selected(raw.decode('utf-8')) == selected((ROOT / 'app/agent/nodes.py').read_text(encoding='utf-8'))


@pytest.mark.parametrize('query,category', [
    ('璃月有多少世界任务', '任务'), ('请问蒙德有哪些任务？', '任务'),
    ('列出须弥的所有委托任务', '任务'), ('龙脊雪山的任务有哪些', '任务'),
    ('枫丹有哪些料理', '料理'), ('稻妻一共有多少道料理', '料理'),
    ('统计一下纳塔的食谱数量', '料理'), ('挪得卡莱有哪些菜谱', '料理'),
    ('蒙德有哪些怪物', '怪物'), ('至冬的敌人名单', '怪物'),
    ('渊下宫有多少种魔物', '怪物'), ('什么地区有哪些任务', '任务'),
    ('在璃月有哪些任务', '任务'), ('璃月的任务一共有多少个', '任务'),
    ('蒙德有几种怪物', '怪物'), ('有哪些料理在枫丹', '料理'),
    ('纳塔有哪些活动任务', '任务'),
])
def test_regional_exclusions_stop_before_models(runtime, query, category):
    # RE-REGION-STATS-BOUNDARY intentionally supersedes the old task fallthrough.
    initial = {'user_query': query, 'conversation_history': [{'user': '旧题', 'assistant': '999'}],
               'count_result': {'status': 'complete', 'count': 999}, 'final_response': '999'}
    before = deepcopy(initial)
    final = runtime.workflow.create_agent_workflow().invoke(initial)
    result = final['count_result']
    assert result['policy'] == 'regional_statistics_exclusion'
    assert result['status'] == 'unsupported' and result['category'] == category
    assert result['count'] is None and result['members'] == []
    assert final['final_response'].startswith('暂不支持按地区统计或列举' + category)
    assert final['conversation_history'] == before['conversation_history'] and initial == before
    assert runtime.counters['model_calls'] == runtime.counters['tool_calls'] == runtime.counters['network_calls'] == 0


def test_offline_boundary(runtime):
    assert runtime.mode in {'off', 'finite'}
    source = (ROOT / 'app/workflow.py').read_text(encoding='utf-8')
    assert 'statistics_gate' in source
    result = run_statistics(runtime, '须弥有多少特产', 'complete')
    assert result['count_result']['render_policy'] == 'direct_verbatim'
    # Explicit claims are bounded to this graph and fixtures, not deployment acceptance.
    assert runtime.counters['model_calls'] == 0


@pytest.mark.parametrize('query', ['某活动有多少角色参与', '璃月有多少NPC', '须弥有哪些食材', '璃月有哪些书籍'])
def test_scl_s02_out_of_scope_categories(runtime, query):
    assert parse_statistics_query(query) is None
    assert statistics_gate({'user_query':query}) == {'count_result':None}


def test_scl_s12_no_history_region_inheritance(runtime):
    state = {'user_query':'这里有哪些特产', 'conversation_history':[{'user':'须弥', 'assistant':'须弥'}]}
    final = runtime.workflow.create_agent_workflow().invoke(state)
    assert final['count_result']['status'] == 'missing'
    assert final['count_result']['count'] is None
    assert runtime.counters['model_calls'] == 0


@pytest.mark.parametrize('query', ['须弥有哪些特产', '挪得卡莱的特产数量是多少', '枫丹有多少特产，分别是什么'])
def test_scl_s18_real_workflow_zero_calls(runtime, query, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='app.statistics.routing')
    state = {'user_query':query, 'run_id':'scl-zero', 'conversation_history':[]}
    final = runtime.workflow.create_agent_workflow().invoke(state)
    assert final['count_result']['status'] == 'complete'
    assert final['count_result']['count'] == len(final['count_result']['members'])
    assert runtime.counters['model_calls'] == runtime.counters['tool_calls'] == runtime.counters['network_calls'] == 0
    events = [r.statistics_route for r in caplog.records if hasattr(r, 'statistics_route')]
    assert len(events) == 1 and events[0]['route'] == 'direct'
    assert events[0]['request_id'] == 'scl-zero' and events[0]['mode'] == runtime.mode
    assert events[0]['observed_source_sha256'] == [events[0]['source_sha256']]
    assert not any(key in final for key in ('statistics_route', 'alias_table_sha256'))


def test_scl_s19_injected_state_and_no_match(runtime):
    run_statistics(runtime, '须弥有哪些特产', 'complete')
    initial = {'user_query':'你好', 'final_response':'999', 'count_result':{'count':999}}
    assert statistics_gate(initial) == {'count_result':None}
    assert initial['count_result']['count'] == 999


def test_scl_gate_single_selection(runtime, monkeypatch):
    import app.statistics.routing as routing
    calls = []
    original = routing.count_catalog

    def counted(request):
        calls.append(deepcopy(request))
        return original(request)

    monkeypatch.setattr(routing, 'count_catalog', counted)
    final = runtime.workflow.create_agent_workflow().invoke({'user_query':'须弥有多少特产，分别是什么'})
    result = final['count_result']
    assert len(calls) == 1
    assert result['count'] == len(result['members'])
    assert final['final_response'] == result['answer_text_hint']
    assert runtime.counters['model_calls'] == runtime.counters['tool_calls'] == runtime.counters['network_calls'] == 0


def _scl_bytes(payload):
    """Full boundary payload, including all message fields and their order."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


class SclReplay:
    """Nth-call broker: compare before giving a response, fail closed."""

    def __init__(self, expected=None):
        self.expected = expected
        self.calls = []
        self.errors = []
        self.delivered = 0

    def invoke(self, messages, **kwargs):
        payload = [m.model_dump(mode='json') for m in messages]
        return self.deliver('model', {'messages':payload, 'parameters':kwargs,
                                     'model':'offline-recording', 'tools':[]})

    def deliver(self, kind, request):
        index = len(self.calls)
        raw = _scl_bytes(request)
        response = {'content': 'L1' if index == 0 else '离线受控回答。', 'id':f'scl-response-{index}'}
        actual = {'index':index + 1, 'kind':kind, 'request':request, 'raw_hex':raw.hex(),
                  'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(), 'response':response}
        self.calls.append(actual)
        if self.expected is not None:
            if index >= len(self.expected):
                self.errors.append('extra_call')
                raise AssertionError('mismatch:extra_call')
            before = self.expected[index]
            if before['kind'] != kind or bytes.fromhex(before['raw_hex']) != raw:
                self.errors.append('input_or_order')
                raise AssertionError('mismatch:input_or_order')
            # The recorded response is released only after the full check above.
            response = before['response']
            actual['response'] = response
        self.delivered += 1
        return AIMessage(**response)

    def verdict(self, route):
        if self.errors:
            return 'mismatch'
        if route == 'direct':
            return 'direct_not_consumed' if not self.calls else 'mismatch'
        if self.expected is not None and len(self.calls) != len(self.expected):
            return 'mismatch'
        return 'pass'


def _scl_replay_worker(root, mode, query, output, expected_path):
    """Use each tree's original workflow/fixture; replace external boundaries only."""
    import logging
    from pytest import MonkeyPatch
    root = Path(root).resolve()
    assert Path(sys.modules['app.statistics.routing'].__file__).is_relative_to(root)
    spec = importlib.util.spec_from_file_location('scl_tree_fixture', root / 'tests/test_statistics_workflow.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = json.loads(Path(expected_path).read_text(encoding='utf-8'))['calls'] if expected_path != '-' else None
    broker = SclReplay(expected)
    events = []

    class Audit(logging.Handler):
        def emit(self, record):
            if hasattr(record, 'statistics_route'):
                events.append(record.statistics_route)

    logger = logging.getLogger('app.statistics.routing')
    logger.setLevel(logging.INFO)
    logger.addHandler(Audit())
    patch = MonkeyPatch()
    scratch = Path(output).parent / (Path(output).stem + '-runtime')
    scratch.mkdir()
    generator = module.runtime.__wrapped__(SimpleNamespace(param=mode), scratch, patch)
    rt = next(generator)
    spy = sys.modules['app.llm'].assess_llm
    def model_call(messages, **kwargs):
        rt.counters['model_calls'] += 1
        return broker.invoke(messages, **kwargs)

    spy.invoke = model_call
    spy.stream = model_call

    def retry(messages, **_kwargs):
        return model_call(messages)

    patch.setattr(rt.nodes, 'llm_invoke_with_retry', retry)
    initial = {'user_query':query, 'run_id':'scl-replay-fixed', 'messages':[],
               'conversation_history':[{'user':'旧问题', 'assistant':'旧回答'}],
               'conversation_summary':'', 'fast_iteration':0}
    before = deepcopy(initial)
    final = rt.workflow.create_agent_workflow().invoke(initial)
    assert initial == before
    route = 'direct' if final.get('count_result') is not None else 'fallback'
    raw = final['final_response'].encode('utf-8')
    record = {'root':str(root), 'mode':mode, 'initial_state':initial, 'query':query, 'route':route,
              'calls':broker.calls, 'verdict':broker.verdict(route), 'audit':events,
              'counters':rt.counters, 'errors':broker.errors, 'count_result':final.get('count_result'),
              'final_hex':raw.hex(), 'final_bytes':len(raw), 'final_sha256':hashlib.sha256(raw).hexdigest(),
              'boundary':'Actual full messages model_dump JSON at substituted external invocation; not SDK wire bytes.'}
    Path(output).write_bytes(canonical_bytes(record))
    try:
        next(generator)
    except StopIteration:
        pass
    patch.undo()


def _scl_run_pair(tmp_path, mode, query):
    import os
    import subprocess
    baseline = os.environ.get('SCL_BASELINE_ROOT')
    assert baseline, 'SCL_BASELINE_ROOT must select the frozen actual workspace, never HEAD'
    results = []
    old = tmp_path / 'before.json'
    for root, name, expected in [(Path(baseline), 'before', '-'), (ROOT, 'after', str(old))]:
        output = tmp_path / (name + '.json')
        argv = [sys.executable, '-B', str(Path(__file__).resolve()), '--scl-worker', str(root), mode, query, str(output), expected]
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1',
                   LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
        proc = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
        (tmp_path / (name + '.stdout')).write_bytes(stdout)
        (tmp_path / (name + '.stderr')).write_bytes(stderr)
        (tmp_path / (name + '.command.json')).write_bytes(canonical_bytes({'argv':argv, 'cwd':str(root), 'pid':proc.pid, 'exit_code':proc.returncode}))
        assert proc.returncode == 0, stdout.decode('utf-8', errors='replace') + stderr.decode('utf-8', errors='replace')
        results.append(json.loads(output.read_text(encoding='utf-8')))
    return results


@pytest.mark.parametrize('mode', ['off', 'finite'])
@pytest.mark.parametrize('query', ['须弥有多少特产能做料理', '须弥有哪些目前可获得的特产'])
def test_scl_s20_modifier_verbatim_fallback(tmp_path, mode, query):
    before, after = _scl_run_pair(tmp_path, mode, query)
    assert before['route'] == after['route'] == 'fallback'
    assert before['calls'] and len(before['calls']) == len(after['calls'])
    for old, new in zip(before['calls'], after['calls'], strict=True):
        assert old['request']['messages'] == new['request']['messages']
        assert old['raw_hex'] == new['raw_hex'] and old['kind'] == new['kind']
    assert after['verdict'] == 'pass'
    assert before['final_hex'] == after['final_hex']
    assert after['audit'][0]['route'] == 'fallback' and len(after['audit']) == 1
    assert after['initial_state']['user_query'] == query


@pytest.mark.parametrize('mode', ['off', 'finite'])
@pytest.mark.parametrize('query', ['须弥有多少特产除了劫波莲', '须弥和璃月哪个特产更多'])
def test_scl_s21_exclusion_comparison_fallback(tmp_path, mode, query):
    test_scl_s20_modifier_verbatim_fallback(tmp_path, mode, query)


def test_scl_s22_legacy_consumers(runtime, tmp_path):
    test_legacy_consumers(runtime, tmp_path)
    name = sorted(runtime.nodes.DIRECT_ANSWER_TOOLS)[0]
    messages = [AIMessage(content='', tool_calls=[{'id':'legacy', 'name':name, 'args':{}}]),
                ToolMessage(content='原始元数据', tool_call_id='legacy')]
    final = runtime.workflow.create_agent_workflow().invoke({'user_query':'请给我原始数据', 'messages':messages})
    assert final['final_response'] == '原始元数据' and final.get('count_result') is None
    assert runtime.counters['network_calls'] == 0


@pytest.mark.parametrize('query,category', [('璃月有多少世界任务','任务'),('枫丹有哪些料理','料理'),('至冬有哪些怪物','怪物')])
def test_scl_s23_regional_exclusions(runtime, query, category):
    test_regional_exclusions_stop_before_models(runtime, query, category)


def test_scl_s28_mode_and_config_preservation(runtime):
    assert runtime.mode in {'off','finite'}
    assert runtime.counters['network_calls'] == 0
    assert (ROOT / 'app/config.py').read_bytes() == (Path(__import__('os').environ['SCL_BASELINE_ROOT']) / 'app/config.py').read_bytes()


def test_scl_s29_spec_stage_evidence_boundary():
    import app.statistics.catalog as catalog
    assert not hasattr(catalog, 'list_catalog')
    assert new_result({'tool':'count_catalog'})['contract_version'] == 'count_result_v1.1'


@pytest.mark.parametrize('mode', ['off','finite'])
def test_scl_replay_direct_not_consumed(tmp_path, mode):
    before, after = _scl_run_pair(tmp_path, mode, '须弥有哪些特产')
    assert before['calls'] and before['route'] == 'fallback'
    assert after['route'] == 'direct' and after['verdict'] == 'direct_not_consumed'
    assert after['calls'] == []
    assert after['counters']['model_calls'] == after['counters']['tool_calls'] == after['counters']['network_calls'] == 0
    # Independent raw-source oracle; never use the candidate selector as oracle.
    rows = json.loads((ROOT / 'content_data/collectibles.json').read_text(encoding='utf-8'))
    expected = {r['采集物ID'] for r in rows if r['类型'] == '须弥区域特产'}
    assert {m['member_id'] for m in after['count_result']['members']} == expected


@pytest.mark.parametrize('mutation', ['messages','missing','extra','reordered','residual'])
def test_scl_replay_rejects_mutation(mutation, tmp_path):
    request = {'messages':[{'role':'system','content':'原系统\n'}, {'role':'tool','content':'完整工具结果', 'tool_call_id':'t', 'extra':{'order':[2,1]}}],
               'parameters':{'temperature':0}, 'model':'fixed', 'tools':[{'name':'lookup'}]}
    recording = SclReplay()
    recording.deliver('model', request)
    recording.deliver('tool', {'name':'lookup','args':{'query':'须弥'}})
    expected = deepcopy(recording.calls)
    replay = SclReplay(expected)
    changed = deepcopy(request)
    if mutation == 'messages':
        changed['messages'][0]['content'] += ' '
    if mutation == 'reordered':
        expected.reverse()
    try:
        if mutation != 'missing':
            replay.deliver('model', changed)
        if mutation not in {'missing','residual'}:
            replay.deliver('tool', {'name':'lookup','args':{'query':'须弥'}})
        if mutation == 'extra':
            replay.deliver('model', request)
    except AssertionError:
        pass
    assert replay.verdict('fallback') == 'mismatch'
    if mutation in {'messages','reordered'}:
        assert replay.delivered == 0
    (tmp_path / 'mutation.json').write_bytes(canonical_bytes({'mutation':mutation, 'expected':expected,
        'actual':replay.calls, 'errors':replay.errors, 'responses_delivered':replay.delivered,
        'verdict':replay.verdict('fallback'), 'not_accepted_as_output_only':True}))


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--scl-worker':
    _scl_replay_worker(*sys.argv[2:])

# -*- coding: utf-8 -*-
"""从米游社 channel_25 构建角色语音档案与归一化评价关系表（仅汉语，落盘 content_data/character_voices.json、voice_relations.json）。"""
import json, re, html, hashlib, importlib.util, os, sys
from collections import Counter, defaultdict
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
CH = os.path.join(ROOT, "content_data", "wiki_raw", "channel_25.json")
ROLES = os.path.join(ROOT, "genshin_knowledge_base", "roles.py")
OUT_REL = os.path.join(ROOT, "_voice_relations_preview.json")
OUT_PROFILE = os.path.join(ROOT, "_voice_profiles_preview.json")
OUT_REVIEW = os.path.join(ROOT, "_voice_review_preview.json")
OUT_STATS = os.path.join(ROOT, "_voice_stats_preview.json")


def load_known():
    spec = importlib.util.spec_from_file_location("roles_kb", ROLES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = {r["角色名称"] for r in mod.角色知识库}
    alias_map = {}
    try:
        from character_aliases import ALIAS_MAP as _AM
        alias_map.update(_AM)
    except Exception as e:
        print("alias load warn", e, file=sys.stderr)
    return names, alias_map


def clean_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def resolve_name(raw, names, alias_map):
    if not raw:
        return None
    s = raw.strip().strip("「」『』《》\"' ")
    s = re.sub(r"(大人|陛下|殿下|阁下|自己)$", "", s).strip()
    s = s.strip("「」『』《》\"' ").strip()
    if not s:
        return None
    if s in names:
        return s
    if s in alias_map:
        return alias_map[s]
    s2 = re.sub(r"\s+", "", s)
    if s2 in names:
        return s2
    if s2 in alias_map:
        return alias_map[s2]
    return None


def parse_about(name):
    if not name:
        return None
    m = re.match(r"^关于(.+?)(?:[·…]|\.{2,}|$)", name)
    if m:
        return m.group(1)
    m = re.match(r"^对(.+?)的评价", name)
    if m:
        return m.group(1)
    return None


TOPIC_ABOUT_KEYS = ["神之眼", "月之轮", "女皇", "尘世七执政", "星之楔", "月之意志", "三月女神", "深渊力量", "风之神", "岩之神", "雷之神", "草之神", "火之神", "水神", "狐耳女人", "我们", "自己", "母亲", "父亲", "父母", "亲人"]


def is_topic_about(raw):
    if not raw:
        return False
    s = raw.strip().strip("「」『』《》")
    return any(k in s for k in TOPIC_ABOUT_KEYS)


def split_multi_target(raw):
    parts = re.split(r"[和与、,，]", raw)
    return [p.strip() for p in parts if p.strip()]


def main():
    names, alias_map = load_known()
    data = json.load(open(CH, encoding="utf-8"))
    items = data["items"]
    relations = []
    profiles = defaultdict(list)
    situations = defaultdict(list)
    review = {"unresolved_about": [], "missing_chinese": [], "parse_anomaly": [], "unresolved_speaker": []}
    stats = Counter()
    line_name_counter = Counter()
    char_pages = has153 = has2861 = has_cn = 0

    for it in items:
        title = it.get("title") or ""
        page = it.get("page") or {}
        mods = page.get("modules") or []
        ids = {str(m.get("id")) for m in mods}
        if not ids:
            continue
        char_pages += 1
        if "153" in ids:
            has153 += 1
        if "2861" in ids:
            has2861 += 1
        # ---------- 153 ----------
        for m in mods:
            if str(m.get("id")) != "153":
                continue
            for comp in m.get("components") or []:
                if comp.get("component_id") != "role_voice":
                    continue
                raw = comp.get("data")
                if isinstance(raw, str):
                    try:
                        d = json.loads(raw)
                    except Exception as e:
                        review["parse_anomaly"].append({"character": title, "module": "153", "error": str(e)})
                        continue
                else:
                    d = raw or {}
                tabs = d.get("list") or []
                cn = [t for t in tabs if (t.get("tab_name") or "").strip() == "汉语"]
                if not cn:
                    review["missing_chinese"].append({"character": title, "tabs": [t.get("tab_name") for t in tabs]})
                    continue
                has_cn += 1
                for tab in cn:
                    for row in tab.get("table") or []:
                        lname = clean_text(row.get("name"))
                        content = clean_text(row.get("content"))
                        if not lname or not content:
                            continue
                        line_name_counter[lname] += 1
                        about_raw = parse_about(lname)
                        rec = {"character": title, "line_name": lname, "content": content,
                               "audio_url": row.get("audio_url") or "", "source": "m153"}
                        if about_raw:
                            parts = split_multi_target(about_raw) if re.search(r"[和与、,，]", about_raw) else [about_raw]
                            for part in parts:
                                about = resolve_name(part, names, alias_map)
                                if about:
                                    r = dict(rec)
                                    r["target_raw"] = part
                                    r["target"] = about
                                    r["target_type"] = "playable" if about in names else "nonplayable"
                                    relations.append(r)
                                    stats["m153_relation"] += 1
                                elif is_topic_about(part) or is_topic_about(about_raw):
                                    situations[title].append(dict(rec, topic=part))
                                    stats["topic_situation"] += 1
                                else:
                                    review["unresolved_about"].append(dict(rec, about_raw=part, target=None))
                                    stats["m153_unresolved_about"] += 1
                        elif any(k in lname for k in ["喜欢的食物", "讨厌的食物", "生日", "爱好"]):
                            profiles[title].append(rec)
                            stats["profile"] += 1
                        else:
                            situations[title].append(rec)
                            stats["situation"] += 1
        # ---------- 2861 ----------
        for m in mods:
            if str(m.get("id")) != "2861":
                continue
            for comp in m.get("components") or []:
                if comp.get("component_id") != "collapse_panel":
                    continue
                raw = comp.get("data")
                if isinstance(raw, str):
                    try:
                        d = json.loads(raw)
                    except Exception as e:
                        review["parse_anomaly"].append({"character": title, "module": "2861", "error": str(e)})
                        continue
                else:
                    d = raw or {}
                rt = d.get("rich_text", "") if isinstance(d, dict) else ""
                soup = BeautifulSoup(rt, "html.parser")
                for row in soup.find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 2:
                        continue
                    speaker_raw = cells[0].get_text(" ", strip=True)
                    if speaker_raw in {"角色", "角色名", ""}:
                        continue
                    # 合并重复的链接文本，如“旅行者·火 旅行者·火”
                    _parts = [p for p in re.split(r"\s+", speaker_raw) if p]
                    if len(_parts) >= 2 and all(p == _parts[0] for p in _parts):
                        speaker_raw = _parts[0]
                    speaker = resolve_name(speaker_raw, names, alias_map)
                    if not speaker:
                        m_tr = re.match(r"^旅行者[·/](风|岩|雷|草|水|火|冰)$", speaker_raw)
                        if m_tr:
                            speaker = f"旅行者/{m_tr.group(1)}"
                    if not speaker:
                        speaker = re.sub(r"【.*?】", "", speaker_raw).strip() or speaker_raw
                    content_text = clean_text(cells[1].get_text("\n", strip=True))
                    if not content_text:
                        continue
                    marks = list(re.finditer(r"【关于(.+?)】", content_text))
                    target = title
                    if marks:
                        for i, mark in enumerate(marks):
                            inner = mark.group(1).strip()
                            body = content_text[mark.end(): marks[i + 1].start() if i + 1 < len(marks) else len(content_text)].strip()
                            if not body:
                                continue
                            rec = {"character": speaker, "speaker": speaker,
                                   "speaker_type": "playable" if speaker in names else "npc",
                                   "target": target, "target_type": "playable" if target in names else "nonplayable",
                                   "line_name": inner, "content": body,
                                   "audio_url": "", "source": "m2861"}
                            relations.append(rec)
                            if speaker in names:
                                stats["m2861_relation"] += 1
                            else:
                                review["unresolved_speaker"].append(dict(rec, speaker_raw=speaker_raw))
                                stats["m2861_npc_speaker"] += 1
                    else:
                        rec = {"character": speaker, "speaker": speaker,
                               "speaker_type": "playable" if speaker in names else "npc",
                               "target": target, "target_type": "playable" if target in names else "nonplayable",
                               "line_name": f"关于{target}", "content": content_text,
                               "audio_url": "", "source": "m2861"}
                        relations.append(rec)
                        if speaker in names:
                            stats["m2861_relation"] += 1
                        else:
                            review["unresolved_speaker"].append(dict(rec, speaker_raw=speaker_raw))
                            stats["m2861_npc_speaker"] += 1

    dedup = {}
    for rec in relations:
        speaker = rec.get("speaker") or rec.get("character")
        target = rec.get("target")
        content = rec.get("content", "")
        h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        key = (speaker, target, h)
        if key not in dedup:
            dedup[key] = {
                "speaker": speaker,
                "speaker_type": rec.get("speaker_type") or ("playable" if speaker in names else "npc"),
                "target": target,
                "target_type": rec.get("target_type") or ("playable" if target in names else "nonplayable"),
                "line_name": rec.get("line_name", ""),
                "content": content,
                "audio_url": rec.get("audio_url", ""),
                "sources": [rec.get("source")],
                "content_hash": h,
            }
        else:
            if rec.get("source") not in dedup[key]["sources"]:
                dedup[key]["sources"].append(rec.get("source"))
            if len(rec.get("line_name", "")) > len(dedup[key]["line_name"]):
                dedup[key]["line_name"] = rec.get("line_name", "")
            if not dedup[key].get("audio_url") and rec.get("audio_url"):
                dedup[key]["audio_url"] = rec.get("audio_url")
    rel_list = sorted(dedup.values(), key=lambda r: (r["target"], r["speaker"], r["line_name"]))
    json.dump({"count": len(rel_list), "edges": rel_list}, open(OUT_REL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(dict(profiles), open(OUT_PROFILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(review, open(OUT_REVIEW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 正式落盘：角色语音档案 + 归一关系表
    content_dir = os.path.join(ROOT, "content_data")
    characters = {}
    for name in sorted(set(list(profiles.keys()) + list(situations.keys()))):
        item = {}
        if profiles.get(name):
            item["profile"] = profiles[name]
        if situations.get(name):
            item["situation"] = situations[name]
        item["outgoing_relation_count"] = sum(1 for r in rel_list if r["speaker"] == name)
        item["incoming_relation_count"] = sum(1 for r in rel_list if r["target"] == name)
        characters[name] = item
    voice_data = {"version": "1.0", "source": "mihoyo_channel_25", "language": "汉语", "characters": characters}
    relation_data = {"version": "1.0", "source": "mihoyo_channel_25", "language": "汉语", "count": len(rel_list), "edges": rel_list}
    json.dump(voice_data, open(os.path.join(content_dir, "character_voices.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(relation_data, open(os.path.join(content_dir, "voice_relations.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    stats_out = {"char_pages": char_pages, "has_153": has153, "has_2861": has2861,
                 "has_chinese_tab": has_cn, "counts": dict(stats), "relations_dedup": len(rel_list),
                 "unique_speakers": len({r["speaker"] for r in rel_list}),
                 "unique_targets": len({r["target"] for r in rel_list}),
                 "top_line_names": line_name_counter.most_common(40),
                 "review_counts": {k: len(v) for k, v in review.items()}}
    json.dump(stats_out, open(OUT_STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(stats_out, ensure_ascii=False, indent=1))
    sample = [r for r in rel_list if r["speaker"] == "爱可菲" and r["target"] == "芙宁娜"][:3]
    print("SAMPLE_AIQIFEI_FURINA", json.dumps(sample, ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    main()

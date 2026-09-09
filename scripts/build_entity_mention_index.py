# -*- coding: utf-8 -*-
"""构建实体提及索引：谁在哪些词条剧情文本里被提到。

输入：kb_vectors/wiki_entry_graph.json（Schema v3）
输出：kb_vectors/wiki_entity_mention_index.json

原理：Aho-Corasick 多模式匹配，纯 Python 实现，不新增外部依赖。
用途：全景题旁路中，从任务/地图文本的剧情文本抽取人名/组织/圣遗物等实体，
      再按实体反向加载对应词条档案，逼近 WorkBuddy 的“抽人名→全文检索”效果。

用法：
    python scripts/build_entity_mention_index.py
    python scripts/build_entity_mention_index.py --graph <path> --out <path>
"""
import argparse
import json
import time
from collections import defaultdict, deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
DEFAULT_OUT = BASE_DIR / "kb_vectors" / "wiki_entity_mention_index.json"

# 参与实体索引的叙事/世界观类型
ENTITY_TYPES = {
    "character", "npc", "organization", "weapon", "artifact",
    "book", "monster", "region_feature", "character_anecdote",
    "task", "activity",
}
MIN_NAME_LEN = 2


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clean_names(title, aliases):
    """标题 + 别名 + 去装饰后的名字；过滤过短/空名字。"""
    names = {str(title or "").strip()}
    for a in aliases or []:
        names.add(str(a or "").strip())
    out = set()
    for n in names:
        if not n:
            continue
        for cand in {n, n.strip("「」『』《》【】\"' ")}:
            if len(cand) >= MIN_NAME_LEN:
                out.add(cand)
    return out


def build_trie(name_to_entities):
    """构建 Aho-Corasick trie + failure links。"""
    trie = [{}]
    fail = [0]
    out = [[]]
    for name, entity_ids in name_to_entities.items():
        node = 0
        for ch in name:
            nxt = trie[node].get(ch)
            if nxt is None:
                nxt = len(trie)
                trie[node][ch] = nxt
                trie.append({})
                fail.append(0)
                out.append([])
            node = nxt
        out[node].extend(entity_ids)

    queue = deque()
    for ch, child in trie[0].items():
        fail[child] = 0
        queue.append(child)
    while queue:
        node = queue.popleft()
        for ch, child in trie[node].items():
            f = fail[node]
            while f and ch not in trie[f]:
                f = fail[f]
            fail[child] = trie[f].get(ch, 0)
            out[child].extend(out[fail[child]])
            queue.append(child)
    return trie, fail, out


def scan(text, trie, fail, out, entity_id, mentions, inverted):
    """扫描一段文本，记录命中的实体。"""
    state = 0
    for i, ch in enumerate(text):
        while state and ch not in trie[state]:
            state = fail[state]
        state = trie[state].get(ch, 0)
        for eid in out[state]:
            key = (entity_id, eid)
            if key not in mentions:
                mentions[key] = [0, i]
            mentions[key][0] += 1
            inverted[entity_id].add(eid)


def main():
    parser = argparse.ArgumentParser(description="构建实体提及索引")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--min-count", type=int, default=1,
                        help="只保留至少出现 N 次的提及（默认 1）")
    args = parser.parse_args()

    graph_path = Path(args.graph)
    log(f"加载图: {graph_path}")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    entries = graph.get("entries") or []
    log(f"节点数: {len(entries)}")

    # 1) 建实体名 -> 实体 ID 映射
    name_to_entities = defaultdict(set)
    entity_meta = {}
    for e in entries:
        etype = e.get("entry_type")
        if etype not in ENTITY_TYPES:
            continue
        eid = str(e.get("entry_id"))
        entity_meta[eid] = {
            "name": e.get("title", ""),
            "type": etype,
        }
        for name in clean_names(e.get("title"), e.get("aliases")):
            name_to_entities[name].add(eid)

    log(f"实体数: {len(entity_meta)}，模式数: {len(name_to_entities)}")
    trie, fail, out = build_trie(name_to_entities)

    # 2) 扫描所有节点全文
    mentions = {}          # (source_entry_id, entity_id) -> [count, first_offset]
    inverted = defaultdict(set)  # source_entry_id -> {entity_id}
    total = len(entries)
    for idx, e in enumerate(entries, 1):
        eid = str(e.get("entry_id"))
        text = f"{e.get('title', '')}\n{e.get('story_text', '')}"
        if text:
            scan(text, trie, fail, out, eid, mentions, inverted)
        if idx % 2000 == 0:
            log(f"扫描进度 {idx}/{total}")

    # 3) 组织输出
    entities_out = {}
    for eid, meta in entity_meta.items():
        rows = []
        for (source_id, target_id), (count, first_offset) in mentions.items():
            if target_id != eid:
                continue
            if count < args.min_count:
                continue
            rows.append({
                "entry_id": source_id,
                "count": count,
                "first_offset": first_offset,
            })
        if rows:
            rows.sort(key=lambda x: (-x["count"], x["entry_id"]))
            entities_out[eid] = {
                "name": meta["name"],
                "type": meta["type"],
                "mentions": rows,
            }

    inverted_out = {
        source_id: sorted(entity_ids)
        for source_id, entity_ids in inverted.items()
        if entity_ids
    }

    result = {
        "version": 2,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "graph_path": str(graph_path),
        "stats": {
            "entries_scanned": total,
            "entries_with_story_text": sum(
                1 for e in entries if (e.get("story_text") or "").strip()
            ),
            "entities": len(entity_meta),
            "entities_with_mentions": len(entities_out),
            "source_entries_with_mentions": len(inverted_out),
            "mention_pairs": len(mentions),
        },
        "entities": entities_out,
        "inverted": inverted_out,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入: {out_path}")
    log(f"stats: {result['stats']}")


if __name__ == "__main__":
    main()

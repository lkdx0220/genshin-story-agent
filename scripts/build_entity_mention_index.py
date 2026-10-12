# -*- coding: utf-8 -*-
"""构建实体提及索引：谁在哪些词条剧情文本里被提到。

输入：$KB_GRAPH_DIR/wiki_entry_graph.json（默认 kb_vectors，Schema v4）
输出：$KB_GRAPH_DIR/wiki_entity_mention_index.json（与输入同目录，运行时按同目录读取）

2026-10-12 缺口补全（任务 1/3）：
- 实体名来源不再只限「图里已有 type=character/npc/... 的条目」；
- 额外加载 `kb_vectors/entity_alias_map.json` 的 canonical + variants，
  为每个角色规范名建立 `alias:<canonical>` 实体记录，并把该角色所有写法
  （含瓦蕾莎/瓦雷莎、若娜瓦等）的提及都统计进去；
- 图内已有实体记录保持不变，保证既有下游行为不回退；
- 输出 stats 含实体总数、提及总数、alias 实体数、相对上次索引的增量，
  以及瓦蕾莎/瓦雷莎/若娜瓦等抽样名字的覆盖数。

原理：Aho-Corasick 多模式匹配，纯 Python 实现，不新增外部依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, Set

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# 与 wiki_entry_graph.py 共用同一份目录常量，避免两处硬编码 kb_vectors 漂移
from wiki_entry_graph import DEFAULT_OUTPUT as DEFAULT_GRAPH  # noqa: E402

DEFAULT_OUT = DEFAULT_GRAPH.parent / "wiki_entity_mention_index.json"
DEFAULT_ALIAS = DEFAULT_GRAPH.parent / "entity_alias_map.json"

# 参与实体索引的叙事/世界观类型（图内既有实体）
ENTITY_TYPES = {
    "character",
    "npc",
    "organization",
    "weapon",
    "artifact",
    "book",
    "monster",
    "region_feature",
    "character_anecdote",
    "task",
    "activity",
}
MIN_NAME_LEN = 2


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clean_names(title, aliases) -> set:
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


def _load_previous(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _name_coverage(entities_out: dict, names: Iterable[str]) -> Dict[str, int]:
    result = {name: 0 for name in names}
    for item in entities_out.values():
        item_name = str(item.get("name") or "")
        for name in result:
            if name in item_name:
                result[name] += 1
    return result


def _load_alias_payload(alias_path: Path, enabled: bool) -> dict:
    if not enabled:
        return {"canonical_to_variants": {}, "variant_to_canonical": {}, "records": []}
    if alias_path.exists():
        try:
            return json.loads(alias_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 未生成映射表时，从 character_aliases 即时构建（不落盘）
    try:
        from build_entity_alias_map import build_payload  # type: ignore

        return build_payload()
    except Exception as error:
        log(f"别名映射不可用，退回纯图实体: {error}")
        return {"canonical_to_variants": {}, "variant_to_canonical": {}, "records": []}


def main():
    parser = argparse.ArgumentParser(description="构建实体提及索引")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS), help="别名映射表；不存在时按 character_aliases 即时构建")
    parser.add_argument("--min-count", type=int, default=1, help="只保留至少出现 N 次的提及（默认 1）")
    parser.add_argument("--no-character-aliases", action="store_true", help="不加载角色别名 canonical 实体")
    args = parser.parse_args()

    graph_path = Path(args.graph)
    log(f"加载图: {graph_path}")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    entries = graph.get("entries") or []
    log(f"节点数: {len(entries)}")

    # 1) 建实体名 -> 实体 ID 映射（图内实体）
    name_to_entities: Dict[str, Set[str]] = defaultdict(set)
    entity_meta: Dict[str, dict] = {}
    graph_entity_count = 0
    for e in entries:
        etype = e.get("entry_type")
        if etype not in ENTITY_TYPES:
            continue
        eid = str(e.get("entry_id"))
        entity_meta[eid] = {
            "name": e.get("title", ""),
            "type": etype,
        }
        graph_entity_count += 1
        for name in clean_names(e.get("title"), e.get("aliases")):
            name_to_entities[name].add(eid)

    # 2) 角色别名 canonical 实体：补齐图里没有/名字写错的新角色与变体
    alias_payload = _load_alias_payload(Path(args.alias_map), not args.no_character_aliases)
    canonical_to_variants = alias_payload.get("canonical_to_variants") or {}
    alias_entity_count = 0
    for canonical, variants in canonical_to_variants.items():
        canonical = str(canonical or "").strip()
        if not canonical:
            continue
        aid = f"alias:{canonical}"
        variants = [str(v).strip() for v in (variants or []) if str(v).strip()]
        entity_meta[aid] = {
            "name": canonical,
            "type": "character",
            "canonical": canonical,
            "variants": variants,
            "source": "entity_alias_map",
        }
        alias_entity_count += 1
        for name in [canonical] + variants:
            if len(name) >= MIN_NAME_LEN:
                name_to_entities[name].add(aid)

    log(
        f"实体数: {len(entity_meta)}（图内 {graph_entity_count} + 别名 {alias_entity_count}），"
        f"模式数: {len(name_to_entities)}"
    )
    trie, fail, out = build_trie(name_to_entities)

    # 3) 扫描所有节点全文
    mentions = {}  # (source_entry_id, entity_id) -> [count, first_offset]
    inverted = defaultdict(set)  # source_entry_id -> {entity_id}
    total = len(entries)
    for idx, e in enumerate(entries, 1):
        eid = str(e.get("entry_id"))
        text = f"{e.get('title', '')}\n{e.get('story_text', '')}"
        if text:
            scan(text, trie, fail, out, eid, mentions, inverted)
        if idx % 2000 == 0:
            log(f"扫描进度 {idx}/{total}")

    # 4) 组织输出
    entities_out = {}
    for eid, meta in entity_meta.items():
        rows = []
        for (source_id, target_id), (count, first_offset) in mentions.items():
            if target_id != eid:
                continue
            if count < args.min_count:
                continue
            rows.append(
                {
                    "entry_id": source_id,
                    "count": count,
                    "first_offset": first_offset,
                }
            )
        if rows:
            rows.sort(key=lambda x: (-x["count"], x["entry_id"]))
            item = {
                "name": meta["name"],
                "type": meta["type"],
                "mentions": rows,
            }
            for extra_key in ("canonical", "variants", "source"):
                if meta.get(extra_key):
                    item[extra_key] = meta[extra_key]
            entities_out[eid] = item

    inverted_out = {source_id: sorted(entity_ids) for source_id, entity_ids in inverted.items() if entity_ids}

    previous = _load_previous(Path(args.out))
    previous_stats = previous.get("stats") or {}
    sample_names = ["瓦蕾莎", "瓦雷莎", "若娜瓦", "丝柯克", "玛薇卡"]
    result = {
        "version": 3,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "graph_path": str(graph_path),
        "alias_map_path": str(args.alias_map),
        "stats": {
            "entries_scanned": total,
            "entries_with_story_text": sum(1 for e in entries if (e.get("story_text") or "").strip()),
            "entities": len(entity_meta),
            "graph_entities": graph_entity_count,
            "alias_entities": alias_entity_count,
            "entities_with_mentions": len(entities_out),
            "alias_entities_with_mentions": sum(1 for eid in entities_out if eid.startswith("alias:")),
            "source_entries_with_mentions": len(inverted_out),
            "mention_pairs": len(mentions),
            "previous_entities_with_mentions": previous_stats.get("entities_with_mentions"),
            "previous_mention_pairs": previous_stats.get("mention_pairs"),
            "entities_with_mentions_delta": len(entities_out) - int(previous_stats.get("entities_with_mentions") or 0),
            "mention_pairs_delta": len(mentions) - int(previous_stats.get("mention_pairs") or 0),
            "sample_name_coverage": _name_coverage(entities_out, sample_names),
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

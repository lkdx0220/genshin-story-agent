#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
知识库来源隔离预处理 v1

目的：
  1. 把本地知识库按来源严格分成：B站独占 / 共有 / 待复核；
  2. 为“共有”的记录生成替换准备清单（official_match、待替换动作）；
  3. 不修改 content_data 下任何原文件，只写 content_data/source_scope/。

输入：
  - content_data/lore.json / concepts.json / npcs_processed.json / books.json /
    materials.json / collectibles.json / foods.json / monsters.json / recipes.json /
    quests_*.json
  - genshin_knowledge_base/roles.py / weapons.py / artifacts.py
  - 可选：content_data/crawler_corpus/*.jsonl（官方正文适配层；没有则只能做标题级匹配）

官方目录来源：
  米游社观测枢公开只读列表接口，用 curl.exe 拉取 18 个官方频道；
  结果缓存到 content_data/source_scope/official_catalog.json。

输出：
  content_data/source_scope/
  ├─ official_catalog.json            官方频道目录缓存
  ├─ classification.jsonl             每条本地记录的 scope 分类
  ├─ bwiki_only.jsonl                 B站独占记录（隔离清单）
  ├─ shared_for_replacement.jsonl     共有记录（替换准备清单）
  ├─ review.jsonl                     需要人工复核的记录
  └─ _summary.json                    汇总统计
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_DIR))

from merge_crawler_corpus import _act_core, _normalize, _normalize_for_match  # noqa: E402

CONTENT_DIR = PROJECT_DIR / "content_data"
DEFAULT_OUT_DIR = CONTENT_DIR / "source_scope"
CRAWLER_CORPUS_DIR = CONTENT_DIR / "crawler_corpus"
OFFICIAL_MAP_TEXT_RAW = CONTENT_DIR / "mihoyo_map_text_raw_full.json"
OFFICIAL_MAP_TEXT_SUPPLEMENT = CONTENT_DIR / "source_scope" / "official_map_text_supplement.json"
MAP_TEXT_MIN_MATCH_LEN = 6
MAP_TEXT_COVERAGE_THRESHOLD = 0.85

CHANNELS = {
    43: "task",
    251: "map_text",
    20: "npc",
    25: "character",
    5: "weapon",
    218: "artifact",
    6: "enemy",
    21: "food",
    13: "item",
    68: "book",
    255: "organization",
    54: "domain",
    261: "character_anecdote",
    49: "animal",
    211: "outfit",
    278: "gray_eye",
    249: "phantom_theater",
    55: "commission",
}

LIST_URL = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"


# ====== 通用工具 ======

def _base_name(text: str) -> str:
    value = _normalize(text)
    for sep in ("【", "（", "(", "〔", "["):
        index = value.find(sep)
        if index > 0:
            value = value[:index]
    return value


def _split_desc_parts(desc: str) -> list:
    if not desc:
        return []
    parts = re.split(r"[；;、|]", desc)
    return [part.strip() for part in parts if part.strip()]


def _parse_filters(ext_str: str) -> list:
    filters = []
    if not ext_str:
        return filters
    try:
        ext = json.loads(ext_str)
    except (TypeError, json.JSONDecodeError):
        return filters
    if not isinstance(ext, dict):
        return filters
    for key, value in ext.items():
        if not str(key).startswith("c_"):
            continue
        if not isinstance(value, dict):
            continue
        raw = (value.get("filter") or {}).get("text", "[]")
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                items = []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        filters.extend(str(item) for item in items)
    return filters


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, data):
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _write_jsonl(path: Path, records: list):
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def _curl_json(url: str, retries: int = 3) -> dict:
    curl = "curl.exe" if os.name == "nt" else "curl"
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                [curl, "-sS", "--max-time", "40", "-H", "x-rpc-wiki_app: genshin", url],
                capture_output=True,
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(attempt * 1.5)
            continue
        if proc.returncode != 0:
            last_error = proc.stderr.decode("utf-8", errors="replace")[:300]
            time.sleep(attempt * 1.5)
            continue
        text = proc.stdout.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = f"JSON 解析失败: {exc}; head={text[:200]}"
            time.sleep(attempt * 1.5)
    raise RuntimeError(f"curl 拉取失败: {url}; {last_error}")


def fetch_official_catalog() -> dict:
    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channels": {},
    }
    for channel_id, module in CHANNELS.items():
        url = f"{LIST_URL}?channel_id={channel_id}&app_sn=ys_obc&lang=zh-cn"
        data = _curl_json(url)
        if data.get("retcode") != 0:
            raise RuntimeError(f"官方频道 {channel_id} 返回错误: {data.get('message')}")
        items = []
        for group in (data.get("data") or {}).get("list", []):
            for raw in group.get("list", []) or []:
                title = raw.get("title") or ""
                if not title:
                    continue
                desc = raw.get("desc") or ""
                items.append({
                    "content_id": str(raw.get("content_id") or ""),
                    "title": title,
                    "desc": desc,
                    "desc_parts": _split_desc_parts(desc),
                    "filters": _parse_filters(raw.get("ext") or "{}"),
                })
        catalog["channels"][module] = {
            "channel_id": channel_id,
            "count": len(items),
            "items": items,
        }
        print(f"[catalog] {module:20s} channel={channel_id:4d} items={len(items)}")
        time.sleep(0.6)
    return catalog


def load_official_catalog(path: Path, refresh: bool) -> dict:
    if not refresh and path.exists():
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    catalog = fetch_official_catalog()
    _write_json(path, catalog)
    return catalog


def build_official_index(catalog: dict) -> dict:
    index = {
        "by_module": defaultdict(dict),
        "by_module_base": defaultdict(dict),
        "all": defaultdict(list),
        "task_titles": set(),
        "task_subtasks": set(),
        "task_items": [],
        "map_text_items": [],
        "map_text_keys": set(),
        "map_text_locations": set(),
        "npc_bases": set(),
    }
    for module, payload in catalog.get("channels", {}).items():
        for item in payload.get("items", []):
            title = item.get("title") or ""
            norm_title = _normalize(title)
            if not norm_title:
                continue
            index["by_module"][module].setdefault(norm_title, item)
            index["by_module_base"][module].setdefault(_base_name(title), item)
            index["all"][norm_title].append((module, item))

            if module == "task":
                index["task_items"].append(item)
                index["task_titles"].add(norm_title)
                for part in item.get("desc_parts") or []:
                    index["task_subtasks"].add(_normalize(part))
            if module == "map_text":
                index["map_text_items"].append(item)
                region = ""
                for value in item.get("filters") or []:
                    if value.startswith("地区/"):
                        region = value.split("/", 1)[-1]
                        break
                matched = re.search(r"【(.+?)】", title)
                location = matched.group(1).strip() if matched else title
                index["map_text_keys"].add((_normalize(region), _normalize(location)))
                index["map_text_locations"].add(_normalize(location))
            if module == "npc":
                index["npc_bases"].add(_base_name(title))
    return index


def load_crawler_corpus(corpus_dir: Path) -> dict:
    """读取官方正文适配层；没有时返回空索引，只做标题级替换准备。"""
    docs = defaultdict(list)
    if not corpus_dir.exists():
        return docs
    for path in sorted(corpus_dir.glob("*.jsonl")):
        module = path.stem
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                docs[module].append(record)
    return docs


def _find_official_text(crawler_docs: dict, module: str, title: str, content_id: str = ""):
    norm_title = _normalize(title)
    for record in crawler_docs.get(module, []):
        if content_id and str(record.get("entry_id") or "") == str(content_id):
            return record
        if _normalize(record.get("title") or record.get("page_title") or "") == norm_title:
            return record
    return None


# ====== 本地记录加载 ======

def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_local_records() -> list:
    records = []

    lore = _load_json(CONTENT_DIR / "lore.json", [])
    if isinstance(lore, list):
        for index, item in enumerate(lore):
            records.append({
                "record_id": f"lore:{index}",
                "module": "lore",
                "title": str(item.get("title") or ""),
                "source": str(item.get("source") or ""),
                "text": str(item.get("text") or ""),
                "source_file": "content_data/lore.json",
                "source_index": index,
                "metadata": {},
            })

    concepts = _load_json(CONTENT_DIR / "concepts.json", [])
    if isinstance(concepts, list):
        for index, item in enumerate(concepts):
            records.append({
                "record_id": f"concept:{index}",
                "module": "concepts",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("正文") or ""),
                "source_file": "content_data/concepts.json",
                "source_index": index,
                "metadata": {"类型": item.get("类型") or ""},
            })

    npcs = _load_json(CONTENT_DIR / "npcs_processed.json", {})
    if isinstance(npcs, dict):
        for index, (name, item) in enumerate(npcs.items()):
            if not isinstance(item, dict):
                continue
            records.append({
                "record_id": f"npc:{name}",
                "module": "npcs",
                "title": str(item.get("name") or name),
                "source": "bilibili_wiki",
                "text": str(item.get("doc_for_embed") or item.get("dialogue") or ""),
                "source_file": "content_data/npcs_processed.json",
                "source_index": index,
                "metadata": item,
            })

    books = _load_json(CONTENT_DIR / "books.json", [])
    if isinstance(books, list):
        for index, item in enumerate(books):
            records.append({
                "record_id": f"book:{index}",
                "module": "books",
                "title": str(item.get("title") or ""),
                "source": str(item.get("source") or "bilibili_wiki"),
                "text": str(item.get("text") or ""),
                "source_file": "content_data/books.json",
                "source_index": index,
                "metadata": item.get("metadata") or {},
            })

    materials = _load_json(CONTENT_DIR / "materials.json", [])
    if isinstance(materials, list):
        for index, item in enumerate(materials):
            records.append({
                "record_id": f"material:{item.get('材料ID') or index}",
                "module": "materials",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "content_data/materials.json",
                "source_index": index,
                "metadata": item,
            })

    collectibles = _load_json(CONTENT_DIR / "collectibles.json", [])
    if isinstance(collectibles, list):
        for index, item in enumerate(collectibles):
            records.append({
                "record_id": f"collectible:{item.get('采集物ID') or index}",
                "module": "collectibles",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "content_data/collectibles.json",
                "source_index": index,
                "metadata": item,
            })

    foods = _load_json(CONTENT_DIR / "foods.json", [])
    if isinstance(foods, list):
        for index, item in enumerate(foods):
            records.append({
                "record_id": f"food:{index}",
                "module": "foods",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("介绍") or ""),
                "source_file": "content_data/foods.json",
                "source_index": index,
                "metadata": item,
            })

    monsters = _load_json(CONTENT_DIR / "monsters.json", [])
    if isinstance(monsters, list):
        for index, item in enumerate(monsters):
            records.append({
                "record_id": f"monster:{item.get('怪物ID') or index}",
                "module": "monsters",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": "",
                "source_file": "content_data/monsters.json",
                "source_index": index,
                "metadata": item,
            })

    recipes = _load_json(CONTENT_DIR / "recipes.json", [])
    if isinstance(recipes, list):
        for index, item in enumerate(recipes):
            records.append({
                "record_id": f"recipe:{item.get('食谱ID') or index}",
                "module": "recipes",
                "title": str(item.get("名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "content_data/recipes.json",
                "source_index": index,
                "metadata": item,
            })

    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if path.name == "quests_processed.json":
            continue
        quests = _load_json(path, [])
        if not isinstance(quests, list):
            continue
        for index, item in enumerate(quests):
            metadata = item.get("metadata") or {}
            records.append({
                "record_id": f"quest:{path.name}:{index}",
                "module": "quests",
                "title": str(item.get("title") or metadata.get("任务名称") or ""),
                "source": str(item.get("source") or "bilibili_wiki"),
                "text": str(item.get("text") or ""),
                "source_file": f"content_data/{path.name}",
                "source_index": index,
                "metadata": metadata,
                "quest_category": str(item.get("category") or ""),
            })

    # 结构化知识库（Python 模块）
    try:
        from genshin_knowledge_base import artifacts as kb_artifacts
        from genshin_knowledge_base import roles as kb_roles
        from genshin_knowledge_base import weapons as kb_weapons
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 结构化知识库导入失败: {exc}")
        kb_roles = kb_weapons = kb_artifacts = None

    if kb_roles is not None:
        for index, item in enumerate(kb_roles.角色知识库):
            records.append({
                "record_id": f"role:{item.get('角色ID') or index}",
                "module": "roles",
                "title": str(item.get("角色名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "genshin_knowledge_base/roles.py",
                "source_index": index,
                "metadata": item,
            })
    if kb_weapons is not None:
        for index, item in enumerate(kb_weapons.武器知识库):
            records.append({
                "record_id": f"weapon:{item.get('武器ID') or index}",
                "module": "weapons",
                "title": str(item.get("武器名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "genshin_knowledge_base/weapons.py",
                "source_index": index,
                "metadata": item,
            })
    if kb_artifacts is not None:
        for index, item in enumerate(kb_artifacts.圣遗物知识库):
            records.append({
                "record_id": f"artifact:{item.get('圣遗物ID') or index}",
                "module": "artifacts",
                "title": str(item.get("圣遗物名称") or ""),
                "source": "bilibili_wiki",
                "text": str(item.get("简介") or ""),
                "source_file": "genshin_knowledge_base/artifacts.py",
                "source_index": index,
                "metadata": item,
            })

    return records


# ====== 分类逻辑 ======

def _scope_bwiki(record: dict, reason: str) -> dict:
    return {
        "scope": "bwiki_only",
        "official_module": "",
        "official_title": "",
        "official_content_id": "",
        "match_type": "",
        "replacement_action": "keep_bwiki",
        "replacement_ready": False,
        "official_text_available": False,
        "official_doc_id": "",
        "notes": [reason],
    }


def _scope_shared(record: dict, module: str, item: dict, match_type: str,
                  crawler_docs: dict, notes=None) -> dict:
    official_text = _find_official_text(
        crawler_docs, module, item.get("title") or "", item.get("content_id") or ""
    )
    return {
        "scope": "shared_exact" if match_type == "exact" else "shared_base",
        "official_module": module,
        "official_title": item.get("title") or "",
        "official_content_id": item.get("content_id") or "",
        "match_type": match_type,
        "replacement_action": "replace_with_official" if official_text else "pending_official_corpus",
        "replacement_ready": bool(official_text),
        "official_text_available": bool(official_text),
        "official_doc_id": (official_text or {}).get("doc_id", ""),
        "notes": notes or [],
    }


def _scope_shared_cross(record: dict, module: str, item: dict, crawler_docs: dict, notes=None) -> dict:
    result = _scope_shared(record, module, item, "cross_module", crawler_docs, notes)
    result["scope"] = "shared_cross_module"
    result["match_type"] = "cross_module"
    return result


def _scope_shared_content(record: dict, match: dict, crawler_docs: dict) -> dict:
    """地图文本正文级匹配：按块判断是否可安全替换。"""
    page = match["page"]
    match_type = match["match_type"]
    content_id = page.get("content_id")
    result = _scope_shared(
        record,
        "map_text",
        {"title": page.get("title") or "", "content_id": content_id or ""},
        "exact",
        crawler_docs,
        ["地图文本正文级匹配"],
    )
    result["scope"] = "shared_exact"
    result["match_type"] = match_type
    result["official_text_available"] = True
    result["match_source"] = page.get("match_source") or "mihoyo_map_text_raw_full"

    if match_type in ("block_exact", "block_loose"):
        result["official_doc_id"] = f"map_text:{content_id}:block:{match['block_index']}"
        result["replacement_action"] = "replace_with_official"
        result["replacement_ready"] = True
    elif match_type == "multi_blocks":
        block_indexes = ",".join(str(index) for index in match["block_indexes"])
        result["official_doc_id"] = f"map_text:{content_id}:blocks:{block_indexes}"
        result["replacement_action"] = "replace_with_official"
        result["replacement_ready"] = True
    else:
        # 官方文本只能证明同源，但切不出唯一替换块；保留本地正文，仅标记来源。
        block_index = match.get("block_index")
        block_suffix = f":block:{block_index}" if block_index is not None else ""
        result["official_doc_id"] = f"map_text:{content_id}{block_suffix}"
        result["replacement_action"] = "keep_local_mark_source"
        result["replacement_ready"] = False
    return result


def _scope_review(record: dict, reason: str, official_module="", official_title="") -> dict:
    return {
        "scope": "review",
        "official_module": official_module,
        "official_title": official_title,
        "official_content_id": "",
        "match_type": "",
        "replacement_action": "review",
        "replacement_ready": False,
        "official_text_available": False,
        "official_doc_id": "",
        "notes": [reason],
    }


def _match_same(index: dict, module: str, title: str):
    norm_title = _normalize(title)
    item = index["by_module"].get(module, {}).get(norm_title)
    if item:
        return item, "exact"
    base = _base_name(title)
    item = index["by_module_base"].get(module, {}).get(base)
    if item:
        return item, "base"
    return None, ""


def _match_all(index: dict, title: str):
    return index["all"].get(_normalize(title), [])


def _map_text_region(item: dict) -> str:
    for value in item.get("filters") or []:
        if value.startswith("地区/"):
            return value.split("/", 1)[-1]
    return ""


def _map_text_filters(item: dict) -> list:
    """兼容 catalog item 的 filters 列表和 mihoyo raw item 的 filters_text 字典。"""
    raw = item.get("filters")
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, dict):
        text = raw.get("filters_text") or ""
        if not text:
            return []
        try:
            values = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [str(value) for value in values] if isinstance(values, list) else []
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
            return [str(value) for value in values] if isinstance(values, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _map_text_region_from_raw(item: dict) -> str:
    for value in _map_text_filters(item):
        if value.startswith("地区/"):
            return value.split("/", 1)[-1]
    return ""


def _map_text_norm(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _map_text_norm_loose(text: str) -> str:
    """去掉括号内容和标点后的正文，用于处理官方“少女（少年）”这类插入语差异。"""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[（(][^（）()]*[）)]", "", text)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _html_to_text(value) -> str:
    """把旧版组件里的 HTML 片段转成纯文本，保留段落换行。"""
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_page_blocks(page: dict) -> list:
    """把官方页面拆成最小正文块：每段对话/每条基础信息/每段折叠文本各一块。"""
    blocks = []
    for module in page.get("modules") or []:
        for comp in module.get("components") or []:
            component_id = comp.get("component_id")
            try:
                data = json.loads(comp.get("data") or "{}")
            except json.JSONDecodeError:
                continue

            if component_id == "interactive_dialogue":
                trees = data.get("list")
                if not isinstance(trees, list):
                    trees = [data] if data.get("contents") else []
                for tree in trees:
                    for node in (tree.get("contents") or {}).values():
                        value = _html_to_text(node.get("dialogue"))
                        if value:
                            blocks.append(value)
            elif component_id == "material_base_info":
                # 旧版地图文本（如 7491 商家的海报）正文放在 attr 的“描述”里
                for attr in data.get("attr") or []:
                    if not isinstance(attr, dict):
                        continue
                    value = _html_to_text(attr.get("value"))
                    if value:
                        blocks.append(value)
            elif component_id == "collapse_panel":
                # 少量旧页面的补充说明放在折叠面板 rich_text 里
                value = _html_to_text(data.get("rich_text"))
                if value:
                    blocks.append(value)
    return blocks


def _extract_page_interactive_text(page: dict) -> str:
    return "\n".join(_extract_page_blocks(page))


def _load_map_text_items(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data.get("items") or []


def load_official_map_text_contents() -> list:
    """加载官方地图文本正文：当前频道全量 + 历史/补充页面。"""
    items = []
    seen = set()
    for path in (OFFICIAL_MAP_TEXT_RAW, OFFICIAL_MAP_TEXT_SUPPLEMENT):
        for item in _load_map_text_items(path):
            page = item.get("page") or {}
            content_id = str(item.get("content_id") or page.get("id") or "")
            if not content_id or content_id in seen:
                continue
            seen.add(content_id)
            items.append((item, path.name))

    pages = []
    for item, source_name in items:
        page = item.get("page") or {}
        content_id = str(item.get("content_id") or page.get("id") or "")
        title = item.get("title") or page.get("name") or ""
        raw_blocks = _extract_page_blocks(page)
        if not content_id or not raw_blocks:
            continue
        blocks = []
        for block_index, block_text in enumerate(raw_blocks):
            blocks.append({
                "index": block_index,
                "text": block_text,
                "norm": _map_text_norm(block_text),
                "norm_loose": _map_text_norm_loose(block_text),
            })
        text = "\n".join(block["text"] for block in blocks)
        pages.append({
            "content_id": content_id,
            "title": title,
            "region": _map_text_region_from_raw(item),
            "text": text,
            "norm": _map_text_norm(text),
            "norm_loose": _map_text_norm_loose(text),
            "blocks": blocks,
            "match_source": source_name,
        })
    return pages


def _strip_bwiki_nav(text: str) -> str:
    """去掉 B站 wiki 抓取时带上的“首页 > 北陆图书馆 > …”导航行。"""
    lines = (text or "").splitlines()
    while lines:
        first = lines[0].strip()
        if first and "首页" in first and "北陆图书馆" in first and ">" in first:
            lines = lines[1:]
            continue
        break
    return "\n".join(lines).strip()


def _find_map_text_content_match(local_text: str, pages: list):
    """按正文级匹配官方地图文本。

    返回匹配信息 dict：
      {"page": page, "match_type": ...,
       "block_index": int|None, "block_indexes": [int]}
    match_type 取值：
      block_exact / block_loose / block_contains / multi_blocks /
      page_exact / page_loose / page_fragment
    多个候选命中时返回 {"match_type": "ambiguous"}，避免误合并。
    """
    local_text = _strip_bwiki_nav(local_text)
    local_norm = _map_text_norm(local_text)
    local_loose = _map_text_norm_loose(local_text)
    if len(local_norm) < MAP_TEXT_MIN_MATCH_LEN:
        return None

    local_segments = [
        _map_text_norm(line)
        for line in local_text.splitlines()
    ]
    local_segments = [segment for segment in local_segments if len(segment) >= MAP_TEXT_MIN_MATCH_LEN]

    fragments = []
    if len(local_norm) >= 16:
        mid = max(0, len(local_norm) // 2 - 4)
        fragments = [local_norm[:8], local_norm[mid:mid + 8], local_norm[-8:]]
        fragments = [fragment for fragment in fragments if len(fragment) == 8]

    block_exact = []
    block_loose = []
    block_contains = []
    multi_matches = []
    page_exact = []
    page_loose = []
    fragment_hits = []

    for page in pages:
        blocks = page.get("blocks") or []

        for block in blocks:
            if local_norm == block["norm"]:
                block_exact.append((page, block["index"]))
                break
            if local_loose and local_loose == block["norm_loose"]:
                block_loose.append((page, block["index"]))
                break
        else:
            contains = []
            for block in blocks:
                if local_norm and local_norm in block["norm"]:
                    contains.append((block["index"], len(block["norm"])))
                elif local_loose and local_loose in block["norm_loose"]:
                    contains.append((block["index"], len(block["norm_loose"])))
            if len(contains) == 1:
                block_contains.append((page, contains[0][0], contains[0][1]))
            elif len(local_segments) >= 2:
                mapping = []
                ok = True
                for segment in local_segments:
                    hits = [block["index"] for block in blocks if segment in block["norm"]]
                    if len(hits) != 1:
                        ok = False
                        break
                    mapping.append(hits[0])
                if ok and mapping:
                    ordered = []
                    for index in mapping:
                        if index not in ordered:
                            ordered.append(index)
                    if len(ordered) == 1:
                        block_contains.append((
                            page,
                            ordered[0],
                            len(blocks[ordered[0]]["norm"]),
                        ))
                    else:
                        multi_matches.append((page, tuple(ordered)))

        if local_norm and local_norm in page["norm"]:
            page_exact.append(page)
        elif local_loose and local_loose in page["norm_loose"]:
            page_loose.append(page)

        if fragments and any(fragment in page["norm"] for fragment in fragments):
            fragment_hits.append(page)

    if len(block_exact) == 1:
        page, block_index = block_exact[0]
        return {"page": page, "match_type": "block_exact",
                "block_index": block_index, "block_indexes": [block_index]}
    if len(block_exact) > 1:
        return {"match_type": "ambiguous"}

    if len(block_loose) == 1:
        page, block_index = block_loose[0]
        return {"page": page, "match_type": "block_loose",
                "block_index": block_index, "block_indexes": [block_index]}
    if len(block_loose) > 1:
        return {"match_type": "ambiguous"}

    if block_contains:
        # 同一段本地文本可能被多个块包含，取最短块；长度并列则视为歧义。
        best = min(block_contains, key=lambda item: item[2])
        same_best = [item for item in block_contains if item[2] == best[2]]
        if len(same_best) == 1:
            page, block_index, _ = best
            return {"page": page, "match_type": "block_contains",
                    "block_index": block_index, "block_indexes": [block_index]}
        return {"match_type": "ambiguous"}

    if multi_matches:
        if len(multi_matches) == 1:
            page, block_indexes = multi_matches[0]
            return {"page": page, "match_type": "multi_blocks",
                    "block_index": None, "block_indexes": list(block_indexes)}
        return {"match_type": "ambiguous"}

    if len(page_exact) == 1:
        return {"page": page_exact[0], "match_type": "page_exact",
                "block_index": None, "block_indexes": []}
    if len(page_exact) > 1:
        return {"match_type": "ambiguous"}

    if len(page_loose) == 1:
        return {"page": page_loose[0], "match_type": "page_loose",
                "block_index": None, "block_indexes": []}
    if len(page_loose) > 1:
        return {"match_type": "ambiguous"}

    # 片段命中后，用“本地正文被官方页面覆盖的比例”确认；官方页面更长不影响该比例。
    scored = []
    for page in fragment_hits:
        matcher = SequenceMatcher(None, local_norm, page["norm"])
        covered = sum(block.size for block in matcher.get_matching_blocks())
        coverage = covered / len(local_norm)
        if coverage >= MAP_TEXT_COVERAGE_THRESHOLD:
            scored.append((coverage, page))
    if len(scored) == 1:
        return {"page": scored[0][1], "match_type": "page_fragment",
                "block_index": None, "block_indexes": []}
    if len(scored) > 1:
        return {"match_type": "ambiguous"}
    return None


def _find_map_text_match(index: dict, region: str, location: str):
    """官方 map_text 页面标题有【地点】也有“离岛港口告示板”这种前缀式，需要两种都覆盖。"""
    norm_region = _normalize(region)
    norm_location = _normalize(location)
    if not norm_location:
        return None, ""
    for item in index.get("map_text_items", []):
        item_region = _normalize(_map_text_region(item))
        if norm_region and item_region and item_region != norm_region:
            continue
        title = item.get("title") or ""
        norm_title = _normalize(title)
        matched = re.search(r"【(.+?)】", title)
        bracket_location = _normalize(matched.group(1)) if matched else ""
        if bracket_location and bracket_location == norm_location:
            return item, "exact"
        if norm_title.startswith(norm_location) and len(norm_location) >= 2:
            return item, "base"
        if norm_location in norm_title and len(norm_location) >= 2:
            return item, "base"
    return None, ""


def _match_like(record: dict, index: dict, crawler_docs: dict, preferred: str, allow_cross: bool = True) -> dict:
    item, match_type = _match_same(index, preferred, record["title"])
    if item:
        return _scope_shared(record, preferred, item, match_type, crawler_docs)
    if allow_cross:
        matches = _match_all(index, record["title"])
        if matches:
            match_module, match_item = matches[0]
            return _scope_shared_cross(record, match_module, match_item, crawler_docs, ["跨模块同名，需确认内容是否同源"])
    return _scope_bwiki(record, f"官方 {preferred} 频道无同名条目")


def classify_record(record: dict, index: dict, crawler_docs: dict) -> dict:
    module = record["module"]
    title = record["title"]
    source = record.get("source", "")

    if module == "lore":
        if title.startswith("地图文本"):
            return classify_map_text(record, index, crawler_docs)
        if source == "任务剧情":
            return _scope_shared_cross(record, "task", {"title": title, "content_id": ""}, crawler_docs, ["任务剧情摘录：内容与官方任务对话同源"])
        if source == "提瓦特编年史":
            return _scope_bwiki(record, "官方无提瓦特编年史对应模块")
        if source == "书籍":
            item, match_type = _match_same(index, "book", title)
            if item:
                return _scope_shared(record, "book", item, match_type, crawler_docs)
            matches = _match_all(index, title)
            if matches:
                match_module, match_item = matches[0]
                return _scope_review(
                    record,
                    f"官方 book 频道无同名，仅在 {match_module} 发现同名，需确认是否同一本书",
                    match_module,
                    match_item.get("title", ""),
                )
            return _scope_review(record, "官方 book 频道无同名，需确认版本/是否已下架")
        # 北陆图书馆 / 概念 / 其他非地图文本
        return classify_worldview(record, index, crawler_docs)

    if module == "concepts":
        concept_type = (record.get("metadata") or {}).get("类型") or ""
        preferred = None
        if "组织" in concept_type:
            preferred = "organization"
        elif "副本" in concept_type:
            preferred = "domain"
        if preferred:
            item, match_type = _match_same(index, preferred, title)
            if item:
                return _scope_shared(record, preferred, item, match_type, crawler_docs)
        matches = _match_all(index, title)
        if not matches:
            return _scope_bwiki(record, "官方 18 个频道无同名条目")
        module_names = {item[0] for item in matches}
        if module_names & {"organization", "domain", "book", "task", "character", "enemy", "map_text"}:
            match_module, item = matches[0]
            return _scope_shared_cross(record, match_module, item, crawler_docs, ["跨模块同名，需确认内容是否同源"])
        return _scope_review(record, "仅在 item/npc 等弱相关频道发现同名，需人工确认", matches[0][0], matches[0][1].get("title", ""))

    if module == "npcs":
        norm_title = _normalize(title)
        base = _base_name(title)
        if norm_title in index["by_module"].get("npc", {}):
            return _scope_shared(record, "npc", index["by_module"]["npc"][norm_title], "exact", crawler_docs)
        if base in index["npc_bases"]:
            item = index["by_module_base"].get("npc", {}).get(base)
            if item:
                return _scope_shared(record, "npc", item, "base", crawler_docs)
        return _scope_bwiki(record, "官方 NPC 频道无同名/base 名")

    if module in ("books", "weapons", "artifacts", "roles", "foods", "monsters", "materials", "collectibles", "recipes"):
        preferred_map = {
            "books": "book",
            "weapons": "weapon",
            "artifacts": "artifact",
            "roles": "character",
            "foods": "food",
            "monsters": "enemy",
            "materials": "item",
            "collectibles": "item",
            "recipes": "food",
        }
        preferred = preferred_map[module]
        item, match_type = _match_same(index, preferred, title)
        if item:
            return _scope_shared(record, preferred, item, match_type, crawler_docs)
        if module == "recipes":
            item, match_type = _match_same(index, "item", title)
            if item:
                return _scope_shared(
                    record,
                    "item",
                    item,
                    match_type,
                    crawler_docs,
                    ["按官方 item 频道同名匹配，需确认 item 页是否包含完整食谱"],
                )
        matches = _match_all(index, title)
        if matches:
            match_module, match_item = matches[0]
            return _scope_review(
                record,
                f"官方 {preferred} 频道无同名，仅在 {match_module} 发现同名，需确认是否同源",
                match_module,
                match_item.get("title", ""),
            )
        return _scope_bwiki(record, f"官方 {preferred} 频道无同名条目")

    if module == "quests":
        return classify_quest(record, index, crawler_docs)

    return _scope_bwiki(record, "未分类模块，默认按 B站独占处理")


def classify_map_text(record: dict, index: dict, crawler_docs: dict) -> dict:
    # 1) 先做正文级匹配，解决官方页面名与 B站地点名不一致的问题（如 荆夫港公告板 vs 风息山）。
    match = _find_map_text_content_match(
        record.get("text") or "", index.get("map_text_contents") or []
    )
    if match:
        if match.get("match_type") == "ambiguous":
            return _scope_review(record, "地图文本正文匹配到多个官方页面/块，需人工确认")
        return _scope_shared_content(record, match, crawler_docs)

    # 2) 正文没命中的，再退回标题/地点级匹配。
    title = record["title"]
    parts = [part.strip() for part in title.split("/")]
    region = parts[1] if len(parts) >= 3 else ""
    location = parts[-1] if len(parts) >= 2 else title
    item, match_type = _find_map_text_match(index, region, location)
    if item:
        page = index.get("map_text_contents_by_id", {}).get(item.get("content_id") or "")
        if page:
            return _scope_shared_content(
                record,
                {"page": page, "match_type": "title_location_with_content",
                 "block_index": None, "block_indexes": []},
                crawler_docs,
            )
        return _scope_shared(record, "map_text", item, match_type, crawler_docs)
    return _scope_bwiki(record, "官方 map_text 正文和目录均无对应条目")


def classify_worldview(record: dict, index: dict, crawler_docs: dict) -> dict:
    title = record["title"]
    matches = _match_all(index, title)
    if not matches:
        return _scope_bwiki(record, "官方 18 个频道无同名条目")
    module_names = {item[0] for item in matches}
    # 北陆图书馆世界观页如果只命中 artifact/enemy/character，大概率是本地 source 标签误标，进复核。
    if module_names <= {"artifact", "enemy", "character", "item", "npc"}:
        match_module, item = matches[0]
        return _scope_review(
            record,
            "仅命中 artifact/enemy/character/item/npc，疑似 source 标签错误或非同源内容",
            match_module,
            item.get("title", ""),
        )
    match_module, item = matches[0]
    return _scope_shared_cross(record, match_module, item, crawler_docs, ["跨模块同名，需确认内容是否同源"])


def classify_quest(record: dict, index: dict, crawler_docs: dict) -> dict:
    title = record["title"]
    norm_title = _normalize(title)
    if norm_title in index["task_subtasks"] or norm_title in index["task_titles"]:
        for item in index["task_items"]:
            if _normalize(item.get("title") or "") == norm_title or norm_title in {
                _normalize(part) for part in item.get("desc_parts") or []
            }:
                return _scope_shared(record, "task", item, "exact", crawler_docs)
    metadata = record.get("metadata") or {}
    chapter = _normalize(metadata.get("chapter_name") or "")
    act = _act_core(metadata.get("act_name") or "")
    if chapter and act:
        for item in index["task_items"]:
            haystack = _normalize((item.get("title") or "") + "|" + (item.get("desc") or ""))
            if chapter in haystack and act in haystack:
                return _scope_shared(record, "task", item, "base", crawler_docs, ["按章节+幕名匹配"])
    category = record.get("quest_category") or ""
    if "委托" in category:
        item, match_type = _match_same(index, "commission", title)
        if item:
            return _scope_shared(record, "commission", item, match_type, crawler_docs)
        return _scope_review(record, "官方 commission 频道无同名委托，需确认是否旧委托/已下架")
    if "活动" in category or "彩蛋" in category or "隐藏" in category:
        return _scope_bwiki(record, "活动/彩蛋/隐藏任务，官方 task 目录无同名条目")
    return _scope_review(record, "官方 task 目录无同名子任务/幕名，需确认是否在官方容器页内")


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库来源隔离预处理 v1")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录，默认 content_data/source_scope")
    parser.add_argument("--refresh-catalog", action="store_true", help="重新拉取官方 18 频道目录")
    parser.add_argument("--dry-run", action="store_true", help="只计算和报告，不写文件")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    catalog_path = out_dir / "official_catalog.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[1/4] 准备官方频道目录...")
    catalog = load_official_catalog(catalog_path, args.refresh_catalog)
    index = build_official_index(catalog)
    map_text_contents = load_official_map_text_contents()
    index["map_text_contents"] = map_text_contents
    index["map_text_contents_by_id"] = {
        page["content_id"]: page for page in map_text_contents
    }
    print(f"  官方地图文本正文: {len(map_text_contents)} 页")
    crawler_docs = load_crawler_corpus(CRAWLER_CORPUS_DIR)

    print("[2/4] 加载本地知识库记录...")
    records = load_local_records()
    print(f"  本地记录: {len(records)}")

    print("[3/4] 分类...")
    classified = []
    for record in records:
        result = classify_record(record, index, crawler_docs)
        row = {
            "record_id": record["record_id"],
            "module": record["module"],
            "title": record["title"],
            "source": record.get("source", ""),
            "source_file": record["source_file"],
            "source_index": record["source_index"],
            "text_length": len(record.get("text") or ""),
            "text_preview": (record.get("text") or "")[:120],
            **result,
        }
        classified.append(row)

    scope_counts = Counter(row["scope"] for row in classified)
    module_counts = defaultdict(Counter)
    for row in classified:
        module_counts[row["module"]][row["scope"]] += 1

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "official_catalog": {
            module: payload.get("count", 0)
            for module, payload in catalog.get("channels", {}).items()
        },
        "local_total": len(classified),
        "scope_counts": dict(scope_counts),
        "by_module": {module: dict(counts) for module, counts in sorted(module_counts.items())},
        "crawler_corpus_available": bool(crawler_docs),
        "review_total": scope_counts.get("review", 0),
        "bwiki_only_total": scope_counts.get("bwiki_only", 0),
        "shared_total": sum(
            count for scope, count in scope_counts.items() if scope.startswith("shared")
        ),
    }

    print("[4/4] 写入...")
    bwiki_only = [row for row in classified if row["scope"] == "bwiki_only"]
    shared = [row for row in classified if row["scope"].startswith("shared")]
    review = [row for row in classified if row["scope"] == "review"]

    print(f"  scope 统计: {dict(scope_counts)}")
    print(f"  B站独占: {len(bwiki_only)}；共有: {len(shared)}；复核: {len(review)}")

    if not args.dry_run:
        _write_json(catalog_path, catalog)
        _write_jsonl(out_dir / "classification.jsonl", classified)
        _write_jsonl(out_dir / "bwiki_only.jsonl", bwiki_only)
        _write_jsonl(out_dir / "shared_for_replacement.jsonl", shared)
        _write_jsonl(out_dir / "review.jsonl", review)
        _write_json(out_dir / "_summary.json", summary)
        print(f"输出目录: {out_dir}")
    else:
        print("dry-run：未写入任何文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())

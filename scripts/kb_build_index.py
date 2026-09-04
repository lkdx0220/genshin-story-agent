#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
知识库向量索引构建脚本

遍历所有知识库数据 → 切片 → 嵌入 → 写入 ChromaDB

用法：
  python kb_build_index.py          # 增量构建（跳过已有集合）
  python kb_build_index.py --force  # 强制全量重建
"""

import os
import sys
import json
import re
import time
from typing import Dict, List

from kb_vector_store import KBVectorStore, COLLECTIONS, VECTOR_DIR

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- 从主文件导入映射表 ---
try:
    from genshin_story_agent import ACT_TO_QUESTS
except ImportError:
    print("[警告] 无法导入 ACT_TO_QUESTS，parent 映射将为空")
    ACT_TO_QUESTS = {}

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(PROJECT_DIR, "content_data")


# ====== 活动剧情子任务 → 所属活动名（硬编码，来源：铁律6） ======
ACTIVITY_PARENTS: Dict[str, str] = {
    "风莺梳春，开天呈祥": "彩鹞栉春风",
    "描露摹晖，诉愿云海": "彩鹞栉春风",
    "故鸢吹归，再宿堂楼": "彩鹞栉春风",
    "人来人往": "彩鹞栉春风",
    "璃月港佳节兴，八奇现瘴疠隐": "春羲画桃符",
    "往生堂三日无主，玉京台遣将调兵": "春羲画桃符",
    "奇门术息灾平昏寿，护摩法净世定幽冥": "春羲画桃符",
    "终回：八奇炼桃都": "春羲画桃符",
    "在云间": "奔霄颂玉轮",
    "在岩间": "奔霄颂玉轮",
    "在人间": "奔霄颂玉轮",
    "白马闲游记": "奔霄颂玉轮",
    "蝶去蝶来": "盛典与慧业",
    "沙起沙落": "盛典与慧业",
    "人聚人散": "盛典与慧业",
    "落幕时分": "盛典与慧业",
    "水妖的猜想": "特尔克西的奇幻历险",
    "王子的国度": "特尔克西的奇幻历险",
    "奇迹的冠冕": "特尔克西的奇幻历险",
    "划破宁静的枪响": "蔷薇与铳枪",
    "景框内外的虚实": "蔷薇与铳枪",
    "雾中隐现的孤岛": "蔷薇与铳枪",
    "何处盛放的蔷薇": "蔷薇与铳枪",
    "两个铳枪手的凯旋": "蔷薇与铳枪",
    "野林猪与小魔女": "幻友绮旅",
    "若你遗忘梦的入口": "幻友绮旅",
    "永不失效的魔法": "幻友绮旅",
}

# 向量切片参数
VEC_CHUNK_SIZE = 1500       # 向量切片最大字数
VEC_CHUNK_OVERLAP = 150     # 切片重叠字数


def chunk_natural(text):
    """按对话场景自然切分，上限 VEC_CHUNK_SIZE 字，重叠 VEC_CHUNK_OVERLAP 字。

    切片边界优先级（从高到低）：
    1. --- / *** 分隔符 → 强边界
    2. 空行 + 下一行【标题】→ 场景切换
    3. 空行 + 角色切换（说话者不同）→ 对话轮次边界
    4. 达到 1500 字时：往前 200 字内找最近空行或角色切换点切；
       找不到则把当前角色长发言整体推到下一片
    5. 每片尾部带 VEC_CHUNK_OVERLAP 字重叠
    """
    if not text or len(text.strip()) == 0:
        return []

    if len(text) <= VEC_CHUNK_SIZE:
        return [text.strip()]

    # 角色发言检测：「角色名：」或「角色名:」
    speaker_pattern = re.compile(r'^(.+?)[：:]\s*')

    def _get_speaker(line):
        m = speaker_pattern.match(line.strip())
        return m.group(1).strip() if m else None

    lines = text.split('\n')

    # 步骤1：拆分为自然段落（segment）
    segments = []
    current_lines = []

    for line in lines:
        stripped = line.strip()

        # 强边界：--- / ***
        if bool(re.match(r'^[-*]{3,}$', stripped)):
            if current_lines:
                segments.append('\n'.join(current_lines).strip())
                current_lines = []
            continue

        # 空行：段落分隔
        if not stripped:
            if current_lines:
                segments.append('\n'.join(current_lines).strip())
                current_lines = []
            continue

        # 标题标记：独立段落（原则2）
        if stripped.startswith('【') and stripped.endswith('】'):
            if current_lines:
                segments.append('\n'.join(current_lines).strip())
                current_lines = []
            segments.append(stripped)
            continue

        # 角色切换检测（原则3）：与前一行说话者不同，且紧跟前一行无空行
        if current_lines:
            prev_speaker = _get_speaker(current_lines[-1])
            curr_speaker = _get_speaker(line)
            if prev_speaker is not None and curr_speaker is not None and prev_speaker != curr_speaker:
                segments.append('\n'.join(current_lines).strip())
                current_lines = []

        current_lines.append(line)

    if current_lines:
        segments.append('\n'.join(current_lines).strip())

    # 步骤2：合并段落为切片
    chunks = []
    buffer_lines = []  # 改为按行追踪，方便回溯切分
    buffer_len = 0

    def _lines_len(lines_list):
        return sum(len(l) + 1 for l in lines_list) - 1 if lines_list else 0  # +1 for \n, -1 for last line

    def _find_backtrack_split(buffer_lines, max_lookback=200):
        """在 buffer_lines 末尾 max_lookback 字内找最近的自然切分点。
        返回 split_idx（从此行之后推到下一片），或 None。"""
        # 从后往前扫描，累加字数直到超过 max_lookback
        acc = 0
        for i in range(len(buffer_lines) - 1, 0, -1):
            acc += len(buffer_lines[i]) + 1  # +1 for \n
            if acc > max_lookback:
                return None  # 回溯区域找不到

            prev_line = buffer_lines[i - 1].strip()
            curr_line = buffer_lines[i].strip()

            # 空行边界
            if not prev_line:
                return i

            # 角色切换边界
            prev_sp = _get_speaker(prev_line)
            curr_sp = _get_speaker(curr_line)
            if prev_sp is not None and curr_sp is not None and prev_sp != curr_sp:
                return i

        return None

    for seg_text in segments:
        seg_lines = seg_text.split('\n')
        seg_len = len(seg_text)

        if buffer_lines and buffer_len + seg_len + 1 > VEC_CHUNK_SIZE:
            # 超限：尝试回溯找自然切分点（原则4）
            split_idx = _find_backtrack_split(buffer_lines)

            if split_idx is not None and split_idx > 0:
                # 找到自然切分点：buffer 前半保留，后半 + 新段落到下一片
                keep_lines = buffer_lines[:split_idx]
                push_lines = buffer_lines[split_idx:]
                chunks.append('\n'.join(keep_lines).strip())
                buffer_lines = push_lines + seg_lines
                buffer_len = _lines_len(buffer_lines)
            else:
                # 找不到：当前段落到下一片（原则4 兜底）
                chunks.append('\n'.join(buffer_lines).strip())
                buffer_lines = seg_lines
                buffer_len = seg_len
        else:
            if buffer_lines:
                buffer_lines.append('')
            buffer_lines.extend(seg_lines)
            buffer_len = buffer_len + seg_len + (1 if buffer_lines != seg_lines else 0)

    if buffer_lines:
        chunks.append('\n'.join(buffer_lines).strip())

    # 单片段无需重叠
    if len(chunks) <= 1:
        return chunks

    # 添加重叠（原则5）：每片尾部带前一片末尾 VEC_CHUNK_OVERLAP 字
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        overlap_chars = min(VEC_CHUNK_OVERLAP, len(prev))
        overlap = prev[-overlap_chars:]
        result.append(overlap + '\n' + chunks[i])

    return result


def build_parent_map() -> Dict[str, str]:
    """构建 子任务标题 → 所属幕/活动名 的映射"""
    pm = {}
    for act, sub_quests in ACT_TO_QUESTS.items():
        for sq in sub_quests:
            if sq not in pm:
                pm[sq] = act
    # 活动剧情映射（优先级低于 ACT_TO_QUESTS）
    for sq, parent in ACTIVITY_PARENTS.items():
        if sq not in pm:
            pm[sq] = parent
    return pm


# ====== 数据加载 ======

def _load_json(filename: str) -> List[dict]:
    path = os.path.join(CONTENT_DIR, filename)
    if not os.path.exists(path):
        print(f"  [警告] 文件不存在: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _type_label(raw_type: str, source_file: str) -> str:
    """统一类型标签"""
    if not raw_type:
        # 从文件名推断
        for kw, label in [("魔神", "魔神任务"), ("传说", "传说任务"),
                          ("活动", "活动剧情"), ("世界", "世界任务"),
                          ("部族", "部族纪闻")]:
            if kw in source_file:
                return label
        return "未知"
    # 标准化
    t = raw_type.strip()
    if "活动" in t:
        return "活动剧情"
    if "魔神" in t:
        return "魔神任务"
    if "传说" in t:
        return "传说任务"
    if "世界" in t:
        return "世界任务"
    if "部族" in t:
        return "部族纪闻"
    return t


# ====== 建库主流程 ======

def index_quests(store: KBVectorStore, parent_map: Dict[str, str]):
    """索引所有任务到 kb_quests_vec（仅向量语义轨；不再构建已废弃的 BM25 向量集合）。"""
    print("[建库] 索引任务...")

    # 只保留向量语义轨道：每个块都带任务标题/层级前缀，便于独立检索时知道任务归属
    ids_vec, docs_vec, metas_vec = [], [], []
    total_vec = 0

    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue

        quests = _load_json(filename)
        for q in quests:
            title = q.get("title", "")
            if not title:
                continue
            category = q.get("category", "")
            source_file = filename
            entry_type = _type_label(category, source_file)
            metadata_raw = q.get("metadata", {})
            version = metadata_raw.get("所属版本", "") or metadata_raw.get("版本", "") or ""
            chapter_name = metadata_raw.get("chapter_name", "")
            act_name = metadata_raw.get("act_name", "")

            parent = parent_map.get(title, "")

            # 构建嵌入用的层级前缀（每个向量块都携带，避免首块之外丢失上下文）
            hierarchy_prefix = ""
            if chapter_name and chapter_name != "开场动画":
                hierarchy_prefix = f"【{chapter_name}"
                if act_name:
                    hierarchy_prefix += f" {act_name}"
                hierarchy_prefix += "】\n"
            elif chapter_name == "开场动画":
                hierarchy_prefix = "【开场动画】\n"
            quest_prefix = hierarchy_prefix + f"任务：{title}\n"

            def _base_meta(chunk_idx, total, preview, source_tag):
                return {
                    "source_file": source_file,
                    "title": title,
                    "parent": parent,
                    "entry_type": entry_type,
                    "version": version,
                    "chapter_name": chapter_name,
                    "act_name": act_name,
                    "chunk_index": chunk_idx,
                    "total_chunks": total,
                    "text_preview": preview,
                    "source": source_tag,
                }

            text = q.get("text", "")
            if not text or len(text.strip()) < 50:
                continue

            # 向量轨：>1500 字按自然场景切分，<=1500 字单块
            if len(text) > VEC_CHUNK_SIZE:
                vec_chunks = chunk_natural(text)
            else:
                vec_chunks = [text]

            for i, chunk in enumerate(vec_chunks):
                if not chunk.strip():
                    continue
                chunk_id = f"quest:{title}:chunk:{i}"
                ids_vec.append(chunk_id)
                docs_vec.append(quest_prefix + chunk)
                metas_vec.append(_base_meta(i, len(vec_chunks), chunk[:200], "quest_vec_chunk"))

            if len(ids_vec) >= 100:
                store.add("kb_quests_vec", ids_vec, docs_vec, metas_vec)
                total_vec += len(ids_vec)
                print(f"  [Vec]  已写入 {total_vec} 条...")
                ids_vec, docs_vec, metas_vec = [], [], []
                time.sleep(3)  # 写入间隔，避免触发嵌入 API 限流

    if ids_vec:
        store.add("kb_quests_vec", ids_vec, docs_vec, metas_vec)
        total_vec += len(ids_vec)

    print(f"  任务索引完成: Vector={total_vec} 条")


def index_lore(store: KBVectorStore):
    """索引世界观设定（lore.json）"""
    print("[建库] 索引世界观设定...")
    lore = _load_json("lore.json")
    if not lore:
        print("  未找到 lore.json")
        return

    ids, docs, metas = [], [], []
    total_vectors = 0
    for idx, entry in enumerate(lore):
        title = entry.get("title", f"lore_{idx}")
        text = entry.get("text", "")
        if not text.strip():
            continue
        source = entry.get("source", "")
        chunks = chunk_natural(text)
        total = len(chunks)
        total_vectors += total
        for ci, chunk in enumerate(chunks):
            lore_id = f"lore:{title}:entry:{idx}:chunk:{ci}"
            doc = f"【{title}】({source})\n{chunk}"
            ids.append(lore_id)
            docs.append(doc)
            metas.append({
                "source_file": "lore.json",
                "title": title,
                "entry_type": "世界观设定",
                "parent": "",
                "version": "",
                "chunk_index": ci,
                "total_chunks": total,
                "text_preview": chunk[:200],
                "source": "lore",
            })
            if len(ids) >= 100:
                store.add("kb_lore", ids, docs, metas)
                ids, docs, metas = [], [], []
                time.sleep(3)
    if ids:
        store.add("kb_lore", ids, docs, metas)
    print(f"  世界观设定索引完成: {len(lore)} 条 -> {total_vectors} 个向量块")
def _clean_html_text(text: str) -> str:
    """清洗书籍元数据卷内容中的简单 HTML/实体标记，统一为纯文本。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(
        r"</?(?:p|div|li|ul|ol|h[1-6]|blockquote|span|b|strong|i|em|u|a)[^>]*>",
        "\n", text, flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def index_books(store: KBVectorStore):
    """索引书籍（books.json）

    按 metadata 中的卷内容分卷，每卷作为独立单元：
    - 短卷 1 个向量块；
    - 长卷卷内再按 chunk_natural 自然切片。
    """
    print("[建库] 索引书籍...")
    books = _load_json("books.json")
    if not books:
        print("  未找到 books.json")
        return

    ids, docs, metas = [], [], []
    total_vectors = 0
    for idx, book in enumerate(books):
        title = book.get("title", "")
        if not title:
            continue
        metadata = book.get("metadata", {})
        # 收集卷内容（metadata 中以 卷N内容 形式存在）
        vol_items = []
        for key, value in metadata.items():
            m = re.match(r"^卷(\d+)内容$", key)
            if m:
                vol_items.append((int(m.group(1)), key, value))
        if not vol_items:
            continue
        vol_items.sort(key=lambda x: x[0])

        total_vols = metadata.get("卷数", "")
        genre = metadata.get("体裁", "")
        version = metadata.get("实装版本", "")
        author = metadata.get("作者", "游戏内未提及")

        for vol_no, _key, raw_text in vol_items:
            vol_text = _clean_html_text(raw_text)
            if not vol_text:
                continue
            vol_name = metadata.get(f"卷{vol_no}名", "")

            chunks = chunk_natural(vol_text)
            total = len(chunks)
            total_vectors += total
            for ci, chunk in enumerate(chunks):
                book_id = f"book:{title}:vol:{vol_no}:chunk:{ci}"
                # 向量文档：书名 + 卷名 + 元数据 + 卷内切片正文
                doc_header = f"【{title}】"
                if vol_name:
                    doc_header += f"《{vol_name}》"
                doc_header += f"\n体裁: {genre}, 卷数: {total_vols}, 版本: {version}, 作者: {author}\n"
                doc = doc_header + chunk
                ids.append(book_id)
                docs.append(doc)
                metas.append({
                    "source_file": "books.json",
                    "title": title,
                    "entry_type": "书籍",
                    "parent": "",
                    "version": version,
                    "volume": vol_no,
                    "volume_name": vol_name,
                    "chunk_index": ci,
                    "total_chunks": total,
                    "text_preview": chunk[:200],
                    "source": "book",
                    "author": author,
                    "genre": genre,
                    "total_vols": total_vols,
                })
                if len(ids) >= 50:
                    store.add("kb_books", ids, docs, metas)
                    ids, docs, metas = [], [], []
                    time.sleep(3)
    if ids:
        store.add("kb_books", ids, docs, metas)
    print(f"  书籍索引完成: {len(books)} 条 -> {total_vectors} 个向量块")


def index_characters(store: KBVectorStore):
    """索引角色（genshin_knowledge_base 角色知识库）"""
    print("[建库] 索引角色...")
    try:
        from genshin_knowledge_base import 角色知识库
    except ImportError:
        print("  无法导入角色知识库")
        return

    ids, docs, metas = [], [], []
    for role in 角色知识库:
        name = role.get("角色名称", "")
        if not name:
            continue
        title_tag = role.get("称号", "")
        element = role.get("神之眼", "")
        weapon = role.get("武器类型", "")
        region = role.get("所属", "")
        rarity = role.get("稀有度", 0)
        description = role.get("简介", "")
        identities = role.get("身份", [])
        if isinstance(identities, list):
            identities_str = "、".join(identities)
        else:
            identities_str = str(identities)

        # 拼接角色故事
        stories = role.get("角色故事", {})
        stories_text = ""
        if stories:
            for sk, sv in stories.items():
                stories_text += f"\n[{sk}]\n{sv[:500]}"
        stories_text = stories_text[:2000]

        # 文档内容
        doc = (
            f"【{name}】{title_tag}  {element}元素  {weapon}  {region}\n"
            f"身份: {identities_str}\n"
            f"简介: {description}\n"
            f"角色故事:{stories_text}"
        )

        char_id = f"character:{name}"
        ids.append(char_id)
        docs.append(doc)
        metas.append({
            "source_file": "角色知识库",
            "title": name,
            "entry_type": "角色",
            "parent": "",
            "version": role.get("实装版本", ""),
            "chunk_index": 0,
            "total_chunks": 1,
            "text_preview": description[:200] if description else name,
            "source": "character",
            "element": element,
            "weapon": weapon,
            "region": region,
            "rarity": rarity,
        })

    store.add("kb_characters", ids, docs, metas)
    print(f"  角色索引完成: {len(角色知识库)} 条")


def index_regions(store: KBVectorStore):
    """索引地区（genshin_knowledge_base 地区知识库）"""
    print("[建库] 索引地区...")
    try:
        from genshin_knowledge_base import 地区知识库
    except ImportError:
        print("  无法导入地区知识库")
        return

    ids, docs, metas = [], [], []
    for region in 地区知识库:
        name = region.get("地区名称", "")
        if not name:
            continue
        element = region.get("元素", "")
        god = region.get("神明", "")
        ideal = region.get("理念", "")
        description = region.get("简介", "")

        doc = f"【{name}】元素: {element}, 神明: {god}, 理念: {ideal}\n简介: {description[:4000]}"

        region_id = f"region:{name}"
        ids.append(region_id)
        docs.append(doc)
        metas.append({
            "source_file": "地区知识库",
            "title": name,
            "entry_type": "地区",
            "parent": "",
            "version": "",
            "chunk_index": 0,
            "total_chunks": 1,
            "text_preview": description[:200] if description else name,
            "source": "region",
            "element": element,
            "god": god,
            "ideal": ideal,
        })

    store.add("kb_regions", ids, docs, metas)
    print(f"  地区索引完成: {len(地区知识库)} 条")


def build(force: bool = False):
    """主入口"""
    store = KBVectorStore()

    if force:
        print("[建库] 强制重建模式")
        # 清理旧集合
        for col in list(COLLECTIONS):
            store.delete_collection(col)
        print("[建库] 旧集合已清理，开始全量重建...")
        _index_all(store)
    else:
        stats = store.get_stats()
        existing = sum(stats.values())
        if existing > 0:
            print(f"[建库] 向量库已有 {existing} 条记录，跳过构建")
            print(f"  各集合: {stats}")
            print(f"  如需重建，运行: python kb_build_index.py --force")
            return
        print("[建库] 向量库为空，开始构建...")
        _index_all(store)


def _index_all(store: KBVectorStore):
    """全量索引入口"""
    parent_map = build_parent_map()
    print(f"[建库] parent 映射: {len(parent_map)} 条")

    index_quests(store, parent_map)
    index_lore(store)
    index_books(store)
    index_characters(store)
    index_regions(store)

    stats = store.get_stats()
    total = sum(stats.values())
    print(f"\n[建库] 全部完成! 总计 {total} 条向量")
    print(f"  各集合: {stats}")
    print(f"  存储路径: {VECTOR_DIR}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    build(force)

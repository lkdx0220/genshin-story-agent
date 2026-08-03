#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
NPC 完整数据构建 - 合并 SMW 数据 + wiki 详情，构建字符串DB + 向量DB

流程：
1. 读取 SMW 基础数据 + wiki 抓取详情
2. 按规则合并字段，构建完整 NPC 数据（npcs_processed.json）
3. 删除旧向量集合，步进式嵌入到 kb_characters

嵌入参数：
- BATCH_SIZE = 4（防限流）
- MAX_BATCHES_PER_RUN = 50（每次运行 200 条）
- 重试 3 次，等待 10s/20s/30s
- 请求间隔 0.5s
"""
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(PROJECT_DIR, 'content_data')
VECTOR_DIR = os.path.join(PROJECT_DIR, 'kb_vectors')

# 输入文件
SMW_FILE = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')
WIKI_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')

# 输出文件
PROCESSED_FILE = os.path.join(CONTENT_DIR, 'npcs_processed.json')

# checkpoint 文件（向量嵌入进度）
CHECKPOINT_FILE = os.path.join(VECTOR_DIR, 'npc_full_step_cp.json')

os.makedirs(VECTOR_DIR, exist_ok=True)

# 把项目根目录加入 sys.path，以便 import kb_vector_store
sys.path.insert(0, PROJECT_DIR)
from kb_vector_store import KBVectorStore

# 嵌入参数
BATCH = 4                     # 每批最多 4 条
MAX_BATCHES_PER_RUN = 50      # 每次运行最多处理 50 批（200 条）
MAX_RETRIES = 3               # 嵌入 API 最大重试次数
RETRY_WAITS = [10, 20, 30]    # 重试等待时间（秒）
REQUEST_INTERVAL = 0.5        # 请求间隔（秒）
COLLECTION = 'kb_characters'  # 嵌入集合名


def log(msg):
    """带时间戳的日志输出"""
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    print(line, flush=True)


def safe_write(filepath, data):
    """原子写入：先写 .tmp，再 rename"""
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def first_value(comma_str):
    """取逗号分隔字符串的第一个值（去除空白），如果为空返回 ''"""
    if not comma_str:
        return ''
    parts = [p.strip() for p in comma_str.split(',') if p.strip()]
    return parts[0] if parts else ''


# 种族关键词白名单（按长度降序，避免短词误匹配长词）
RACE_KEYWORDS = [
    '叮铃哐啷蛋卷工坊',  # 不是种族，而是工坊名，放在前面作为组织排除
    '深海龙蜥', '纯水精灵', '水形幻灵', '元素生命', '魔神分身',
    '玩具企鹅', '长颈角犀',
    '美露莘', '兰那罗', '膨膨兽', '匿叶龙', '嵴锋龙', '鳍游龙', '食梦貘',
    '蕈兽', '妖狸', '花灵', '龙蜥', '玳龟', '妖怪', '鬼族', '兽人', '精灵', '机器人',
]

# 属于"无意义值"的组织/种族，拆分后应清除
_NOISE_VALUES = {'无', '未知', '魇', '狗', '斯汪'}


def split_org_race(value):
    """从合并的 org_race 字符串中拆分出组织(org)和种族(race)。

    SMW 的「所属组织」字段常混入种族信息，如：
    - "海沫村、美露莘" → org="海沫村", race="美露莘"
    - "鬼族" → org="", race="鬼族"
    - "千岩军" → org="千岩军", race=""
    - "美露莘、逐影庭" → org="逐影庭", race="美露莘"
    """
    if not value or not value.strip():
        return '', ''

    value = value.strip()
    # 处理 wiki 模板语法残留（如 "|相关系统=无" → 解析失败导致整个模板片段漏入）
    if re.search(r'\|[\u4e00-\u9fff\w]+\s*=', value):
        return '', ''
    # 处理 "无"、"未知" 等无效值
    if value in _NOISE_VALUES:
        return '', ''

    # 按分隔符拆成片段
    parts = [p.strip() for p in value.replace('，', ',').replace('、', ',').split(',') if p.strip()]

    org_parts = []
    race_parts = []

    for p in parts:
        # 清除噪声值
        if p in _NOISE_VALUES:
            continue
        # 去掉括号注释（如 "魇（妖怪）" → 检查 "妖怪" 是否为种族）
        clean = p.split('（')[0].split('(')[0].strip()
        is_race = False
        for kw in RACE_KEYWORDS:
            if kw in p or kw == clean:
                race_parts.append(kw if kw in p else clean)
                is_race = True
                break
        if not is_race:
            org_parts.append(p)

    org = '、'.join(org_parts) if org_parts else ''
    race = '、'.join(race_parts) if race_parts else ''

    # 排除误判：叮铃哐啷蛋卷工坊 是组织不是种族
    if race == '叮铃哐啷蛋卷工坊':
        org = race
        race = ''

    return org, race


def merge_npc(name, smw_info, wiki_info):
    """合并 SMW 数据和 wiki 详情为单个 NPC 条目。

    规则：
    - SMW 为基础，填充：name, sex, occupation, gift, system, version, appear_time
    - wiki 字段优先覆盖：origin, region, org_race, dialogue, related_quests
    - origin 回退：wiki origin -> SMW 所在国家第一个值 -> ''
    - region 回退：wiki region -> SMW 所在国家第一个值 -> ''
    - org_race 回退：wiki org_race -> SMW 所属组织（忽略种族字段）
    - 如果 wiki 有值但 origin 为空，则 origin = region
    """
    # SMW 基础字段
    entry = {
        'name': name,
        'sex': (smw_info.get('性别', '') or '').strip(),
        'occupation': (smw_info.get('职业', '') or '').strip(),
        'gift': (smw_info.get('对话赠礼', '') or '').strip(),
        'system': (smw_info.get('相关系统', '') or '').strip(),
        'version': (smw_info.get('实装版本', '') or '').strip(),
        'appear_time': (smw_info.get('出现时间', '') or '').strip(),
    }

    smw_region_raw = (smw_info.get('所在国家', '') or '').strip()
    smw_region_first = first_value(smw_region_raw)

    if wiki_info is not None:
        # wiki 字段优先
        wiki_origin = (wiki_info.get('origin', '') or '').strip()
        wiki_region = (wiki_info.get('region', '') or '').strip()
        wiki_org_race = (wiki_info.get('org_race', '') or '').strip()

        entry['dialogue'] = wiki_info.get('dialogue', '') or ''
        entry['related_quests'] = wiki_info.get('related_quests', []) or []

        # origin：wiki -> SMW 所在国家第一个值
        if wiki_origin:
            entry['origin'] = wiki_origin
        else:
            entry['origin'] = smw_region_first

        # region：wiki -> SMW 所在国家第一个值
        if wiki_region:
            entry['region'] = wiki_region
        else:
            entry['region'] = smw_region_first

        # org_race：wiki -> SMW 所属组织
        if wiki_org_race:
            entry['org_race'] = wiki_org_race
        else:
            entry['org_race'] = (smw_info.get('所属组织', '') or '').strip()
    else:
        # 没有 wiki 数据，全部从 SMW 取
        entry['dialogue'] = ''
        entry['related_quests'] = []
        entry['origin'] = smw_region_first
        entry['region'] = smw_region_first
        entry['org_race'] = (smw_info.get('所属组织', '') or '').strip()

    # 兜底：如果 origin 为空，使用 region
    if not entry.get('origin'):
        entry['origin'] = entry.get('region', '')

    # 拆分 org_race 为独立的 org 和 race 字段
    org, race = split_org_race(entry['org_race'])
    entry['org'] = org
    entry['race'] = race

    return entry


def build_doc_for_embed(npc):
    """构建用于嵌入的文档文本"""
    parts = []

    name = npc.get('name', '')
    occupation = npc.get('occupation', '')

    # 标题行
    title = f'【{name}】'
    if occupation:
        title += f' {occupation}'
    parts.append(title)

    # 基本信息
    info_lines = []
    sex = npc.get('sex', '')
    org = npc.get('org', '')
    race = npc.get('race', '')
    origin = npc.get('origin', '')
    region = npc.get('region', '')
    version = npc.get('version', '')
    system = npc.get('system', '')
    appear_time = npc.get('appear_time', '')

    if sex:
        info_lines.append(f'性别：{sex}')
    if org:
        info_lines.append(f'所属组织：{org}')
    if race:
        info_lines.append(f'种族：{race}')
    if origin:
        info_lines.append(f'出身：{origin}')
    if region:
        info_lines.append(f'地区：{region}')
    if version:
        info_lines.append(f'版本：{version}')
    if system:
        info_lines.append(f'系统：{system}')
    if appear_time:
        info_lines.append(f'出现时间：{appear_time}')

    if info_lines:
        parts.append('\n'.join(info_lines))

    # 对话摘要（前300字）
    dialogue = npc.get('dialogue', '')
    if dialogue:
        summary = dialogue[:300]
        parts.append(f'对话摘要：{summary}')

    # 相关剧情
    quests = npc.get('related_quests', [])
    if quests:
        quest_str = '、'.join(quests)
        parts.append(f'相关剧情：{quest_str}')

    return '\n'.join(parts)


# ====== 阶段1：构建字符串 DB ======

log('=== 阶段1: 构建 NPC 字符串 DB ===')

# 读取 SMW 数据
if not os.path.exists(SMW_FILE):
    log(f'错误: 找不到 {SMW_FILE}')
    sys.exit(1)

with open(SMW_FILE, 'r', encoding='utf-8') as f:
    smw_data = json.load(f)
log(f'SMW 数据: {len(smw_data)} 条')

# 读取 wiki 详情
wiki_data = {}
if os.path.exists(WIKI_FILE):
    with open(WIKI_FILE, 'r', encoding='utf-8') as f:
        wiki_data = json.load(f)
    log(f'wiki 详情: {len(wiki_data)} 条')
else:
    log(f'警告: 找不到 {WIKI_FILE}，将仅使用 SMW 数据')

# 合并数据
processed = {}
for name, smw_info in smw_data.items():
    wiki_info = wiki_data.get(name)
    entry = merge_npc(name, smw_info, wiki_info)
    doc = build_doc_for_embed(entry)
    entry['doc_for_embed'] = doc
    processed[name] = entry

log(f'合并完成: {len(processed)} 条 NPC')

# 原子写入：先删除旧文件（如果存在），再写入
if os.path.exists(PROCESSED_FILE):
    try:
        os.remove(PROCESSED_FILE)
    except Exception as e:
        log(f'删除旧文件失败: {e}')

safe_write(PROCESSED_FILE, processed)
log(f'字符串DB 已保存: {PROCESSED_FILE} ({len(processed)} 条)')


# ====== 阶段2：向量嵌入（步进式） ======

log('=== 阶段2: 向量嵌入（步进式） ===')

store = KBVectorStore()

# 读取 checkpoint
cp = {}
if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
        cp = json.load(f)
    log(f'checkpoint: idx={cp.get("idx", 0)}')
    # checkpoint idx=0 说明是全新的重建，仍需清理旧集合
    if cp.get('idx', 0) == 0:
        log(f'checkpoint idx=0（新一轮重建），删除旧集合 {COLLECTION}...')
        store.delete_collection(COLLECTION)
        log(f'旧集合已删除')
else:
    # 首次运行需要先清理旧集合
    log(f'首次运行，删除旧集合 {COLLECTION}...')
    store.delete_collection(COLLECTION)
    log(f'旧集合已删除')

start_idx = cp.get('idx', 0)
npc_list = list(processed.items())
total = len(npc_list)

if start_idx >= total:
    log(f'所有 NPC 已嵌入完毕！({total} 条)')
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
    log(f'Stats: {store.get_stats()}')
    sys.exit(0)

log(f'从 idx={start_idx} 开始（共 {total} 条）')

ids_batch = []
docs_batch = []
metas_batch = []
batches_done = 0
total_done = 0

for i in range(start_idx, total):
    name, entry = npc_list[i]
    doc = entry.get('doc_for_embed', '')

    if not doc or len(doc.strip()) < 20:
        log(f'  [{i+1}/{total}] {name} 跳过(文档太短: {len(doc) if doc else 0})')
        continue

    ids_batch.append(f'npc:{name}')
    docs_batch.append(doc)
    metas_batch.append({
        'name': name,
        'title': name,
        'region': entry.get('region', ''),
        'occupation': entry.get('occupation', ''),
    })

    if len(ids_batch) >= BATCH:
        batch_start = i - len(ids_batch) + 1
        batch_end = i
        log(f'  嵌入 batch: {len(ids_batch)} 条 [{batch_start+1}...{batch_end+1}]')

        success = False
        for retry in range(MAX_RETRIES):
            try:
                store.add(COLLECTION, ids_batch, docs_batch, metas_batch)
                log(f'  嵌入成功')
                success = True
                break
            except Exception as e:
                wait = RETRY_WAITS[retry]
                log(f'  嵌入失败 (尝试 {retry+1}/{MAX_RETRIES}): {e}，等待 {wait}s')
                time.sleep(wait)

        if not success:
            log(f'  致命错误: {MAX_RETRIES} 次重试均失败')
            safe_write(CHECKPOINT_FILE, {'idx': batch_start})
            sys.exit(1)

        total_done += len(ids_batch)
        ids_batch, docs_batch, metas_batch = [], [], []
        batches_done += 1

        # 更新 checkpoint
        safe_write(CHECKPOINT_FILE, {'idx': i + 1})
        log(f'  checkpoint: idx={i+1} [{batches_done}/{MAX_BATCHES_PER_RUN}批]')

        # 请求间隔
        if batches_done < MAX_BATCHES_PER_RUN or i + 1 < total:
            time.sleep(REQUEST_INTERVAL)

        if batches_done >= MAX_BATCHES_PER_RUN:
            log(f'=== 本次完成: {batches_done} 批 ({batches_done * BATCH}条), 累计 {total_done} ===')
            sys.exit(0)

# 处理最后一批
if ids_batch:
    log(f'  最后 batch: {len(ids_batch)} 条')

    success = False
    for retry in range(MAX_RETRIES):
        try:
            store.add(COLLECTION, ids_batch, docs_batch, metas_batch)
            total_done += len(ids_batch)
            log(f'  嵌入成功')
            success = True
            break
        except Exception as e:
            wait = RETRY_WAITS[retry]
            log(f'  嵌入失败 (尝试 {retry+1}/{MAX_RETRIES}): {e}，等待 {wait}s')
            time.sleep(wait)

    if not success:
        log(f'  致命错误: 最后一批 {MAX_RETRIES} 次重试均失败')
        sys.exit(1)

# 完成
if os.path.exists(CHECKPOINT_FILE):
    os.remove(CHECKPOINT_FILE)

log(f'=== 全部完成 ===')
log(f'总计嵌入: {total_done}')
log(f'Stats: {store.get_stats()}')

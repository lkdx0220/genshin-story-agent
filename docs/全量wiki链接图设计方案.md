# 全量 Wiki 链接图设计方案

> 状态：设计稿（待批准后实施）
> 目标：把当前至冬四线试点图（34 节点）扩成覆盖全知识库的 wiki 词条链接图。

---

## 1. 目标与范围

### 1.1 最终图

- 节点：观测枢全部词条（任务/角色/NPC/武器/圣遗物/地图文本/书籍/怪物/材料/食物/动物/组织/活动/道具等）。
- 边：词条正文内显式 `data-entry-id` 链接 + 反向引用（backlinks）。
- 用途：
  1. `wiki_graph_search` 定位词条；
  2. `wiki_graph_expand` 顺出边/反边做多跳；
  3. `wiki_graph_get` 读取定向片段；
  4. 代码守卫保证多任务/地图文本等复杂题不依赖 Planner 自觉。

### 1.2 当前已有资产

| 资产 | 现状 |
|---|---|
| `wiki_entry_graph.py` | 已跑通：解析、建图、搜索、expand、backlinks、脏链接清洗 |
| `app/tools/wiki_graph.py` | 已跑通：3 个工具 |
| `app/agent/nodes.py` 守卫 | 已跑通：多任务+地图文本确定性补全 |
| `mihoyo_map_text_raw_full.json` | 706 条地图文本详情已全量抓完 |
| `mihoyo_tasks_raw.json` | 只有 28 条（7.0 版本任务） |
| 试点图 | 34 entries / 48 links |

### 1.3 范围分级

- **Tier 1（必须）**：任务、地图文本、NPC&商店、角色、角色逸闻、武器、圣遗物、书籍、敌人、组织、背包、活动、「灰眸」、地区供奉&聚所、秘境、冒险家协会、空月之歌。
- **Tier 2（随后）**：食物、动物、教程、成就。
- **Tier 3（可选/低价值）**：头像、名片、装扮、洞天、深境螺旋、幻想真境剧诗、幽境危战、酒馆挑战等纯玩法/COS 频道。

---

## 2. 类型映射（channel_id -> graph type）

已用观测枢频道树接口实测，列表接口 `common/blackboard/ys_obc/v1/home/content/list?channel_id=N` 一次返回全量卡片。

| channel_id | 频道名 | 卡片数 | graph type | 建议阶段 |
|---|---|---|---|---|
| 43 | 任务 | 1056 | task | Tier 1 |
| 251 | 地图文本 | 706 | map_text | Tier 1（已完成抓取） |
| 20 | NPC&商店 | 2314 | npc | Tier 1 |
| 25 | 角色 | 134 | character | Tier 1 |
| 261 | 角色逸闻 | 63 | character_anecdote | Tier 1 |
| 5 | 武器 | 246 | weapon | Tier 1 |
| 218 | 圣遗物 | 63 | artifact | Tier 1 |
| 68 | 书籍 | 93 | book | Tier 1 |
| 6 | 敌人 | 415 | monster | Tier 1 |
| 255 | 组织 | 37 | organization | Tier 1 |
| 13 | 背包 | 2036 | item | Tier 1 |
| 105 | 活动 | 267 | activity | Tier 1 |
| 278 | 「灰眸」 | 19 | gadget | Tier 1 |
| 276 | 地区供奉&聚所 | 21 | region_feature | Tier 1 |
| 54 | 秘境 | 76 | domain | Tier 1 |
| 55 | 冒险家协会 | 292 | adventure_guild | Tier 1 |
| 257 | 空月之歌 | 32 | story_chapter | Tier 1 |
| 21 | 食物 | 321 | food | Tier 2 |
| 49 | 动物 | 227 | animal | Tier 2 |
| 227 | 教程 | 734 | tutorial | Tier 2 |
| 252 | 成就 | 1573 | achievement | Tier 2 |
| 244 | 头像 | 33 | avatar | Tier 3 |
| 109 | 名片 | 283 | namecard | Tier 3 |
| 211 | 装扮 | 53 | outfit | Tier 3 |
| 130 | 洞天 | 2330 | housing | Tier 3 |
| 65 | 深境螺旋 | 14 | abyss | Tier 3 |
| 249 | 幻想真境剧诗 | 27 | theater | Tier 3 |
| 275 | 幽境危战 | 12 | challenge | Tier 3 |
| 260 | 酒馆挑战 | 60 | tavern_challenge | Tier 3 |

Tier 1 合计约 **7300 条**；Tier 1+2 约 **9150 条**；全部频道约 **13000 条**。

### 2.1 类型推断规则

- 优先：raw 文件 manifest 的 channel_id 直接决定 `entry_type`；
- 其次：`filters_text`（如 `任务类型/`、`武器类型/`、`套装效果/`）；
- 最后：`page.modules` 模块名兜底（`任务过程` -> task、`地图说明` -> map_text）。
- 单条补抓词条（无 channel manifest）：保留现在的 `_infer_type` 兜底。

### 2.2 地区推断

- 任务：`任务区域/xxx`；
- 地图文本/角色/NPC/组织/食物等：`地区/xxx`；
- 兜底：标题 `【xxx】` 后缀（如 `蓝藻【奥古洛夫镇】`）。

### 2.3 别名

- 列表卡片 `alias_name`；
- `page.alias_name`；
- 现有 `character_aliases` 只用于主 Agent 别名消歧，不进图，避免双重体系。

---

## 3. 抓取策略

### 3.1 已确认的接口

- 频道树/列表：
  `https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list?channel_id=N&app_sn=ys_obc&lang=zh-cn`
- 单条详情（含 modules 全文）：
  `https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page?entry_page_id=<id>&app_sn=ys_obc&lang=zh-cn`
- 批量元数据（**无 modules，只用于校验 ID/标题，不用于正文抓取**）：
  `POST /hoyowiki/genshin/wapi/entry_pages`，body `{"entry_page_ids":["..."]}`

### 3.2 抓取工具

- 复用 `wiki_data_tools/_fetch_mihoyo_tasks.py` / `_fetch_mihoyo_map_text.py` 的 curl 包装（不用 requests，遵守 AI 使用说明）；
- 新写一个通用脚本 `wiki_data_tools/_fetch_mihoyo_channel.py`：
  - 参数 `--channel 25`、`--tier 1`、`--delay 0.6`、`--resume`；
  - 先拉列表，过滤 Tier/频道，再逐个抓详情；
  - 支持断点续传：已成功 `page` 的 content_id 跳过，`fetch_error` 重试；
  - 输出 `content_data/wiki_raw/channel_<id>.json`（结构同现有 raw：`source/channel_id/fetched_at/total/success/items`）。

### 3.3 频率与时间估算

- 详情接口单条约 10-50KB；
- 9.1k 条 × 0.6s 间隔 ≈ **1.5 小时**；若限流升高改 1.0s ≈ 2.5 小时；
- 使用 `--resume` 分段跑（每频道一个任务），失败不重来。

### 3.4 抓取顺序

1. 43 任务（补 1056-28 条）
2. 20 NPC（2314，最大块）
3. 25/261 角色+逸闻
4. 5/218/68 武器/圣遗物/书籍
5. 6/255/13 敌人/组织/背包
6. 105/278/276/54/55/257
7. Tier 2/3 按需

---

## 4. 图构建架构

### 4.1 构建脚本扩展（`wiki_entry_graph.py`）

- 新增 `CHANNEL_TYPE_MAP`；
- `build_graph()` 读取 `content_data/wiki_raw/channel_*.json` + 现有两个 raw；
- 新增 `--scope pilot|full`：
  - `pilot` 保持现有至冬试点；
  - `full` 不按地区过滤，全部入图；
- 输出：
  - 全量图 `kb_vectors/wiki_entry_graph.json`；
  - 试点图改名 `kb_vectors/wiki_entry_graph_pilot.json`，避免覆盖。

### 4.2 存储与内存策略

- 先构建单 JSON 全量图并测体积/加载时间；
- 若 JSON > 200MB 或加载 > 10s，切分：
  - `kb_vectors/wiki_graph_meta.json`：节点元数据 + 链接（小）；
  - `kb_vectors/wiki_graph_texts/` 按 channel 拆文本（懒加载）；
  - `WikiEntryGraph` 增加 lazy text store，`wiki_graph_get/search` 按需读。
- 预计 9k 节点平均全文 2-8KB，单 JSON 约 80-250MB，具体构建后实测。

### 4.3 链接清洗（全量级）

- 保留 pilot 已验证规则：同 ID 多名字 -> 整条丢弃；
- 全量新增：
  1. 去掉 self link；
  2. 全图统计 `target_id -> target_name`，多名字的 ID 统一标记 `dirty`；
  3. 目标不在任何 channel 列表的 ID 进 `missing_targets`；
  4. 用批量 `entry_pages` 元数据接口校验 top missing 的实际标题，修正/剔除脏链接；
  5. 保留 `context` 截断（防止上下文过大）。

### 4.4 全量验证

- 统计：每个 type 的 entries、links、missing；
- 抽样：
  - 蒙德/璃月/须弥任务；
  - 角色 -> 武器/圣遗物/书籍跨类型；
  - 地图文本 -> 任务反边；
- Agent 回归：
  - 灰眸四线复跑；
  - Golden Test 抽跑 F1/F2/H2/R1/R2/X7；
  - 全量 25 题最后跑一次。

---

## 5. 里程碑

| 里程碑 | 交付 |
|---|---|
| M0 设计确认 | 本文档 |
| M1 通用频道抓取脚本 | `_fetch_mihoyo_channel.py` 可 `--resume` |
| M2 Tier 1 抓完 | `content_data/wiki_raw/channel_*.json` 约 7300 条 |
| M3 全量构建 | `wiki_entry_graph.json` + 统计 |
| M4 工具/守卫适配 | `wiki_graph_*` 支持全类型、搜索性能达标 |
| M5 验证 | 灰眸四线 + 抽测 + Golden 回归 |
| M6 增量机制 | 新版本只抓新增/更新 ID |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 抓取限流/567 | curl 指纹、0.6s 间隔、断点续传、按频道分批 |
| 数据量过大 | 先单 JSON 实测，超阈值切 meta+texts 分层存储 |
| 脏链接比 pilot 更多 | 全图 dirty 统计 + 批量元数据校验 |
| 工具搜索变慢 | title/alias 索引 + 全文检索只扫 type 相关或分段 |
| 影响主知识库 | 图构建独立文件，不碰主向量/BM25 索引 |
| 影响 Agent 行为 | 新图默认仍只在 D/搜索场景暴露；回归测试兜底 |

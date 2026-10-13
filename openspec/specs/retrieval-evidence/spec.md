# 现状规格：检索与证据层（retrieval-evidence）

> 本文件描述系统**当前真实行为**（2026-10-12），不是目标。所有需求用 SHALL/MUST 陈述；已知缺口在文末单独列出。

## Purpose

> **实现锚点约定**：每条 Requirement MUST 写明实现锚点（`文件::符号`），供 `cq spec-rebuild` 做符号级对账；锚点必须指向真实存在的符号。

检索与证据层负责从知识库（词条图、任务库、实体提及索引、向量库）中检索剧情事实，并以分级证据输出；关系类结论（出场名单、共同任务）以确定性方式交付。

### Requirement: 名单类问题确定性直出

实现锚点：`app/agent/nodes.py::_deterministic_roster_answer`、`plan_agent`；`app/entity_retrieval.py::task_cast`；`app/tools/entity.py::entity_query`

系统 SHALL 在规划阶段（调用任何 LLM 之前）对"某任务有哪些角色出场"类问法做确定性预取：从问题中匹配任务标题（任务标题注册表最长匹配），直接调用 `entity_query(relation="cast_of_task")` 查权威出场人物表，渲染后作为 `final_response` 直接返回，并追加来源标记「以上名单由知识库结构化数据直接给出」。

#### Scenario: 命中名单问法

- **WHEN** 用户问「《人前人后》里有哪些角色出场？」
- **THEN** 回答由代码渲染，包含权威 cast 的全部 8 个名字（含菲尔戈黛特），并带来源标记与覆盖度声明，且不调用规划/回答 LLM。

#### Scenario: 未命中名单问法

- **WHEN** 用户问题不匹配名单问法正则（有哪些角色/都有谁/谁出场/谁登场/哪些人出场/出场人物/登场人物）
- **THEN** 走正常规划与回答流程。

#### Scenario: 任务无权威 cast

- **WHEN** 匹配的任务在词条图中没有 `cast_source=metadata` 的权威出场人物表
- **THEN** 确定性预取返回空，走正常流程；不得凭空生成名单。

### Requirement: 关系查询的证据分级

实现锚点：`app/entity_retrieval.py::grade_body`、`entity_docs`、`format_entity_docs`

`entity_query` SHALL 对每条命中标注证据等级：强（出场人物表或净化正文台词）、中（有动作）、弱（仅文字提及）。只有强证据可以进入【结论区】；中/弱只能进入【线索区】并标注「不可当作出场结论」。

#### Scenario: 弱证据隔离

- **WHEN** 某任务中角色仅有文字提及（如被转述）
- **THEN** 该命中出现在线索区，不出现在结论区。

### Requirement: 来源分级

实现锚点：`app/entity_retrieval.py::_graph_cast_is_authoritative`、`_is_authoritative_source`、`graph_cast_map`、`metadata_cast_map`

关系查询 SHALL 区分 `cast_source`：`metadata`（页面信息框字段，权威）、`speaker_lines`（正文台词推定，非权威）、`activity_inherited`（系列活动继承，非权威）。只有 `metadata` 来源可以独立支撑否定性结论。

#### Scenario: 推定来源不作否定

- **WHEN** 某条目的 cast 来自 `speaker_lines` 且未包含某角色
- **THEN** 系统不得据此输出"该角色未参与此任务"的确定性结论。

### Requirement: 净化正文优先

实现锚点：`app/entity_retrieval.py::story_body`、`_META_LINE_RE`

一切台词计数与证据摘录 SHALL 取自 `story_body()` 处理后的正文（剔除任务概述、前情提要、词条元数据）。正文净化后提及数为 0 且不在权威 cast 中的条目 SHALL 被丢弃。

#### Scenario: 前情提要不计为证据

- **WHEN** 角色名只出现在条目的任务概述/前情提要区域
- **THEN** 该条目不产出台词/提及命中。

### Requirement: 否定与不确定的覆盖度声明

实现锚点：`app/entity_retrieval.py::format_entity_docs`、`format_presence`、`format_coverage`

任何"未找到/未收录"结论 MUST 附带覆盖度声明：比对的任务条目总数、其中带权威出场人物表的条数；并 MUST 声明"未收录不等于游戏内不存在"。

#### Scenario: 否定附带覆盖度

- **WHEN** 查询结果为 0 条命中
- **THEN** 回答包含覆盖度声明且不含无依据的存在性推断。

### Requirement: 交集（共同出场）确定性直出

实现锚点：`app/agent/nodes.py::_deterministic_cooccur_answer`、`_extract_entity_names`；`app/entity_retrieval.py::entity_intersect`

系统 SHALL 在规划阶段（调用任何 LLM 之前）对交集类问法做确定性预取：命中问法（一起出现 / 共同出场 / 共同剧情 / 都在场 / 一起出场 / 同场出现 / 同场）后，从问题中提取前两个实体名（实体注册表最长匹配 + 包含去重），代码直接调用 `entity_query(relation="co_occur", target=另一人)` 计算交集并渲染，作为 `final_response` 返回，并追加来源标记「以上交集由知识库结构化数据直接给出」。交集 MUST 只包含双方都有强证据（出场人物表或正文台词）的任务。

#### Scenario: 命中交集问法

- **WHEN** 用户问「胡桃和行秋一起出现过吗？」
- **THEN** 回答由代码渲染（线上实测 3s、工具调用为 0），列出共同出场条目（含《人前人后》），不含仅被转述提及的「往生堂三日无主 第二回」，并带来源标记与覆盖度声明。

#### Scenario: 两人无共同任务

- **WHEN** 两人交集为空
- **THEN** 输出「本次未检出命中」并附覆盖度声明；不得编造共同剧情。

#### Scenario: 名单类问法不受影响

- **WHEN** 用户问「某任务里有哪些角色出场？」
- **THEN** 仍走名单预取，不被交集预取拦截。

### Requirement: 地区/时段存在性（presence）查询

实现锚点：`app/entity_retrieval.py::presence_hits`、`format_presence`、`_presence_entries`

系统 SHALL 提供"角色在某地区/时段是否存在文本证据"的查询，与"是否参与某任务剧情"严格分离：候选范围覆盖全部条目类型（task / activity / housing / item / map_text / lore / book），直接在全量条目文本上匹配（不依赖提及索引），每条标注证据形态，支持地区与版本过滤，并附覆盖度声明与"未收录不等于不存在"的提示。地区/版本严格过滤无命中时，SHALL 回退为全局证据并明确标注「未按地区确认」。

#### Scenario: 彩蛋/留影证明到过该地区

- **WHEN** 用户问「瓦蕾莎在奔霄颂玉轮这次海灯节中去过璃月吗？」
- **THEN** 输出存在性证据区，列出 `[activity] 奔霄颂玉轮` 等条目及其片段（含「纪念留影·瓦蕾莎」与「璃月有好多好吃的啊」），并声明存在性证据不等于剧情参与。

#### Scenario: 无任何证据

- **WHEN** 目标角色在所有条目类型中都没有目标地区的证据
- **THEN** 输出「未检出 + 覆盖度 + 无法判定」，MUST NOT 断言其"没去过"。

### Requirement: presence 接入聊天链路

实现锚点：`app/agent/nodes.py::_deterministic_presence_answer`

系统 SHALL 让存在性查询从聊天入口可直触：`entity_query` 支持 `relation="presence"`（`target` 传地区名）；`plan_agent` 在调用任何 LLM 之前做存在性预取（问法：去过/到过/来过/到访/来访/有没有去/是否去/是否到/是否来/去没去/到没到），提取实体名与地区名后代码直出。实体名提取 SHALL 按出现位置优先，并在注册表只有短名时用问句首部汉字补全（实测：注册表只有「瓦蕾」，问句里是「瓦蕾莎」）。预取顺序 SHALL 为「名单 → 交集 → 存在性」。

#### Scenario: 线上端到端

- **WHEN** 线上问「瓦蕾莎在奔霄颂玉轮这次海灯节中去过璃月吗？」
- **THEN** 7.9s 内返回代码渲染的存在性证据（工具调用为 0），且不含"没去过/没有前往/未到过"等越界否定。

#### Scenario: 既有预取与普通问答不受影响

- **WHEN** 用户问名单类、交集类问题，或不含存在性问法的常规问题
- **THEN** 分别走各自路径（名单 / 交集 / 正常规划与回答）。

### Requirement: 覆盖度查询与双口径展示

实现锚点：`app/entity_retrieval.py::format_coverage`、`_coverage_data`；`app/agent/nodes.py::_deterministic_coverage_answer`

系统 SHALL 提供分类覆盖度的确定性查询（数据源 `kb_vectors/cast_coverage.json`）：分类同时存在 `raw` 与 `narrative_only` 时 MUST 并列展示并标注"原始口径上限受页面性质限制"；只有 `cast_source=metadata` 计入权威覆盖分子；未知分类 MUST 如实列出可用分类、MUST NOT 编造数字；输出 MUST 附数据生成时间。

#### Scenario: 彩蛋剧情双口径

- **WHEN** 用户问「彩蛋剧情的 cast 覆盖度是多少？」
- **THEN** 线上由代码直出（实测 1.5s、工具调用为 0），同时给出 `11/24 = 45.8%` 与 `11/14 = 78.6%`，并说明后者是前者上限受限后的口径；不得只报一个。

#### Scenario: 单口径/多口径分类

- **WHEN** 用户问「活动剧情的 cast 覆盖度是多少？」
- **THEN** 输出 `648/698 = 92.8%`，其 `narrative_only` 口径 `316/319 = 99.1%` 一并展示。

#### Scenario: 未知分类

- **WHEN** 用户询问不存在的分类
- **THEN** 列出可用分类与数据生成时间，不给出任何数字。

## 缺口闭环记录

### 已闭环（2026-10-12，数据侧交付）

任务书（已本地归档，不入库）三项已由知识库维护方交付（提交 `c0e9c749`、`b45d3e8e`），
下游独立复查通过：

| 缺口 | 交付结果 | 下游复查 |
|---|---|---|
| 实体提及索引只覆盖已注册角色 | 索引 v3：实体 6183、提及 pair 86387 → 112256；新角色 0 → 1 | ✅ `瓦蕾莎` 0→1、`若娜瓦` 0→1（用提及索引直接复查） |
| 活动/彩蛋 cast 覆盖不足 | 活动 71.8% → **92.8%**（metadata 373 / speaker_lines 234 / inherited 41）；彩蛋按"有剧情/语音"口径 11/14 = **78.6%** | ✅ 校验脚本 exit 0；活动 ≥90% 达标 |
| 名称变体未归一 | `kb_vectors/entity_alias_map.json`（canonical 163 组 / 变体 722 / 映射 700） | ✅ `normalize_entity("瓦雷莎") → 瓦蕾莎`；`瓦蕾莎 ← 瓦雷莎、牛角妹…` |

**无空键审计**：图内 task/activity 无 cast 条目共 473 条，fallback 补 `cast_absent` 377 + 已有标注 96，
`no_marker = 0` ✅ —— 不再有空键被下游误读为"角色不在场"。

### 覆盖度声明口径（下游必须遵守）

- **彩蛋剧情必须写双口径**：原始 24 条 = 45.8%；有剧情/语音 14 条 = 78.6%（其余为音乐/截图/读物/攻略页）；
- `cast_source` 语义：`metadata` 权威 / `speaker_lines` 非权威 / `activity_inherited` 非权威 /
  `cast_absent`、`protagonist_only` 明确无权威 cast；
- 名称查询先经 `entity_alias_map.variant_to_canonical` 归一，再走索引。

### 遗留项（已闭环 2026-10-12）

数据侧提交 `aefb678f` 后，下游独立复查确认三项遗留全部关闭：

1. **`cast_source` 复合值**：全量分布已归一为单一取值（`metadata` 1567 / `speaker_lines` 602 /
   `activity_inherited` 15 / 空 12493），**复合值 0 条** ✓；重建脚本已加取值白名单与告警路径。
   下游的防御性兼容判定（见下）保留，用于今后再出现未知形态时不误降级。
2. **彩蛋剧情覆盖度双口径**：已写入 `kb_vectors/cast_coverage.json`（`categories` 下 `raw` 与
   `narrative_only` 两个口径）✓；无剧情内容页面清单可复核。
3. **冲突别名结论**：10 个冲突别名已在 `entity_alias_map.json` 的 `conflict_notes` 逐个给出结论
   （`action` / `candidates` / `reason`），如「哥哥 → 不归一：可指男主角空、也可泛指旅行」✓。
   下游对未归一的别名按普通字符串匹配处理（可能多命中，属可接受行为）。

### Requirement（归档自 change 005）：来源分级的复合值判定

实现锚点：`app/entity_retrieval.py::_is_authoritative_source`

`cast_source` 的权威判定 SHALL 支持复合值：按 `+` 拆成成分后做**精确匹配**，含 `metadata` 成分即视为权威；
`speaker_lines` / `activity_inherited` / `cast_absent` / `protagonist_only` 仍为非权威。
判定 MUST NOT 放宽为子串匹配。

#### Scenario: 复合值不降级

- **WHEN** 某条目 `cast_source` 为复合值且含 `metadata` 成分
- **THEN** 其出场人物表按权威处理（证据等级「强」），不得静默降级为「弱」。

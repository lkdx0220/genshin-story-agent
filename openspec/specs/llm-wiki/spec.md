# 现状规格：LLM Wiki / canonical 实体页（llm-wiki）

> 本文件描述系统**当前真实行为**（2026-10-13），不是目标。所有需求用 SHALL/MUST 陈述；已知缺口在文末单独列出。
>
> 当前状态：Wave0 工程基线已完成；`wiki/characters/wanderer.md` 已由 6 个真实 BWiki 来源生成并通过 L1–L5；Wave1 最小运行时接入（`canonical_query`）已落地。标注 **[已知缺口]** 的条目表示当前未实现或未达到通用性。

## Purpose

> **实现锚点约定**：每条 Requirement MUST 写明实现锚点（`文件::符号`），供 `cq spec-rebuild` 做符号级对账；锚点必须指向真实存在的符号。

LLM Wiki 负责把游戏原文/资料编译为带来源 span 的 canonical 实体页，并让运行时能够以可核验证据回答实体相关问题：

```text
raw / manifest
→ 来源登记与段落 span
→ canonical 候选页（JSON front matter + 确定性正文）
→ schema_check + L1–L5 lint
→ runtime canonical 检索（带 citation）
```

知识库是“编译一次、保持更新”的结构化知识层；LLM 不得凭预训练记忆补造原文、URL、角色、版本、组织或否定事实。

---

### Requirement: raw 来源登记与快照

实现锚点：`ingest.py::load_bundle`

系统 SHALL 以 manifest 为唯一来源入口，每个 source 对应一个文件和独立 URL，并固定文件 SHA-256：

- `data_mode` SHALL ∈ {`fixture`, `production`}；
- 每个 source SHALL 提供 `source_id`、`file`、`sha256`、`provider`、`title`、`url`、`paragraphs`；
- `source_id` SHALL 匹配 `source:[a-z0-9][a-z0-9_-]*`；
- `url` MAY 为 `null`；非空时 SHALL 是 http/https URL 且不带 fragment；
- `provider` SHALL ∈ {`bwiki`, `miyoushe`, `local_normalized`, `user_fixture`}；
- production SHALL NOT 使用 `user_fixture` 或 `example.invalid` URL；
- 文件字节 SHA-256 SHALL 与 manifest 一致；不一致时拒绝摄入。

#### Scenario: 多来源独立绑定

- **WHEN** 摄入 6 个 BWiki 任务页
- **THEN** manifest 含 6 个 source，每页独立 file/url/revid/sha256，段落 URL = `source.url + "#pNNN"`。

#### Scenario: 缺 URL 的本地来源

- **WHEN** source 的 `url=null`
- **THEN** 允许进入来源层；页面渲染时 SHALL 报 `evidence_gap`，不得伪造 URL。

---

### Requirement: 段落切分与来源 span

实现锚点：`ingest.py::load_bundle`、`wiki/__schema__/source-span-format.md`

系统 SHALL 以空行分隔 raw 段落，并按文件内顺序从 `p001` 连续编号：

- 每个段落生成唯一 `span_id`，由 `source_id`、文件 sha256、anchor 确定性计算；
- 每个 span SHALL 记录 text range、quote、quote_sha256、source_id、artifact sha256；
- `paragraphs` 元数据数量 SHALL 与文件实际段落数一致；
- 段落 URL SHALL 全局唯一。

#### Scenario: 段落元数据缺失

- **WHEN** 文件有 `p003` 但 manifest 的 `paragraphs` 缺少 `p003`
- **THEN** `load_bundle` 拒绝并报 `paragraph metadata missing`。

#### Scenario: 多来源编号重置

- **WHEN** 6 个来源分别落盘
- **THEN** 每个文件内从 `p001` 重新编号；跨文件不共享全局编号。

---

### Requirement: canonical 实体页结构

实现锚点：`wiki/__schema__/entity-template.md`、`wiki/__schema__/entity.schema.json`、`ingest.py::schema_check`

canonical 页 SHALL 使用 Markdown + JSON 编码的 YAML 1.2 front matter，并包含定义的全部顶层字段：

```text
schema_version, entity_id, entity_type, data_mode, page_status,
as_of_version, snapshot, canonical_name, aliases,
element, weapon, nation, affiliation, field_assertions,
identity_versions, events, relations, memory_records,
affiliation_states, conflicts, test_refs
```

约束：

- `schema_version` SHALL 为 `0.2.0-draft`；
- `data_mode` SHALL 与 bundle 一致；
- 生成页 SHALL 为 `page_status=draft`、`as_of_version=null`；
- 事实状态 SHALL 为 `candidate` / `unknown` / `disputed`，不得由生成器写 `accepted` / `reviewed` / `verified_in`；
- `element` / `weapon` / `nation` 无证据时 SHALL 为 `null`，并在 `field_assertions` 中为 `unknown`；字符串 `"unknown"` 不是合法值。

#### Scenario: 未知战斗属性

- **WHEN** raw 未提供元素/武器信息
- **THEN** 字段为 `null` + `field_assertions[].status="unknown"`，不得编造。

#### Scenario: fixture 不可生产

- **WHEN** `data_mode=fixture`
- **THEN** 页面可生成用于回归，但不得作为生产内容发布。

---

### Requirement: 正文确定性渲染与 source-span 契约

实现锚点：`ingest.py::render_body`、`ingest.py::page_text`、`lint/wiki-lint-rules.yaml`

正文 SHALL 由结构化记录确定性编译，模型/外部生成器 SHALL NOT 直接书写正文：

- 页面 SHALL 恰好按顺序包含四个二级标题：`## 身份与归属`、`## 时间线`、`## 关系网络`、`## 争议与冲突`；
- 每条事实行 SHALL 带 `[^source:URL#pNNN]`；
- 脚注定义 SHALL 为 `[^source:URL#pNNN]: [原文](URL#pNNN) — span:...`；
- 定义的 URL/span SHALL 与引用记录的证据一致；
- 不存在的事实 SHALL NOT 通过正文补写；`[数据缺口]` 行是唯一允许不带引用的展示行。

#### Scenario: 非法章节

- **WHEN** 正文缺少或调换四个固定标题之一
- **THEN** L1 拒绝。

#### Scenario: 引用与定义不一致

- **WHEN** 事实行引用 URL A，脚注定义指向 URL B 或不同 span
- **THEN** L2 拒绝。

---

### Requirement: 身份阶段与快照

实现锚点：`wiki/__schema__/entity.schema.json`、`ingest.py::schema_check`

系统 SHALL 用一个 `entity_id` 表示一个连续主体；身份阶段由 `identity_versions` 表达：

- 每个身份阶段 SHALL 至少有一个 primary NameRecord；
- `predecessor_ids` SHALL 形成无环图；
- `snapshot.identity_version_id` SHALL 指向存在的身份阶段；
- `canonical_name` SHALL 是 snapshot 身份的 primary name；
- 名称/别名的 evidence quote SHALL 出现该名字。

#### Scenario: 两阶段连续主体

- **WHEN** 散兵（`identity:scaramouche`）与流浪者（`identity:wanderer`）为同一连续主体
- **THEN** 两项 `identity_versions` 共用 `char:wanderer`，`wanderer.predecessor_ids=["identity:scaramouche"]`。

#### Scenario: 单阶段角色

- **WHEN** 钟离、桂木等只需一个身份阶段
- **THEN** `schema_check` 允许 `identity_versions` 非空且不强制固定 Wanderer 阶段；Wanderer 专属要求由 `wiki/__schema__/entities/char_wanderer.json` 提供；`ingest()`/CLI 支持 `--prompt` 与 profile 驱动的通用入口。

---

### Requirement: 事件、版本与时间线

实现锚点：`ingest.py::schema_check`、`wiki_lint.py::_check_l5`

系统 SHALL 用 `events` 记录事实级时间线节点：

- 每个 event SHALL 有 `event_id`、`title`、`description`、`version.introduced_in`、`evidence`；
- `version.introduced_in` SHALL 是数字版本字符串（如 `"3.3"`），且被引用 span 的 `game_version` 明确支持；
- 事件版本缺失或与引用 span 不匹配时 SHALL 拒绝；
- 故事先后 DAG SHALL 无环。

#### Scenario: 3.3 世界树事件

- **WHEN** 页面记录 `event:irminsul-erasure`
- **THEN** `version.introduced_in="3.3"`，引用 span 的 `game_version="3.3"`，正文显示 `[版本 3.3]`。

#### Scenario: 非数字版本

- **WHEN** BWiki 所属版本为“月之三”“月之七”等非数字标签
- **THEN** raw manifest 段落 SHALL 保留 `game_version_label`；事件 `version.label` SHALL 与引用 span 的 `game_version_label` 一致；L5 SHALL 接受该事件并显示 `[版本 月之三]`。数字版本映射与排序注册表仍未实现；真实 Wanderer 页已把月之三/月之七事实迁回 events。

---

### Requirement: 组织归属与 unknown

实现锚点：`ingest.py::schema_check`、`wiki/__schema__/entity-template.md`

组织归属 SHALL 通过 `affiliation_states` 表达认识状态与覆盖度：

- 当前状态未知时 `affiliation=[]` 且 `state="unknown"`；空数组不代表“无组织”；
- `explicit_none` SHALL NOT 在 Wave0 由生成器写入；
- `unknown` / `disputed` SHALL NOT 同时填 `organization_ids`；
- `coverage.source_ids` 与 `reviewed_sources` SHALL 与真实核对的来源一致；
- `authoritative_sources` SHALL ≤ `reviewed_sources`。

#### Scenario: 当前归属未知

- **WHEN** raw 没有流浪者当前组织的直接陈述
- **THEN** `affiliation=[]`、`state="unknown"`、正文显示 `[数据缺口] 当前组织归属未确认`。

#### Scenario: 多状态投影

- **WHEN** 一个主体有多个 `affiliation_states` 且数组顺序不同
- **THEN** 系统 SHALL 先排除身份、版本、story_scope、observed_at 不适用的状态，再按 snapshot 选择；双端 unknown 的 scope 只有在 observed_at 明确匹配时才适用；数字版本按 valid_from 含起点、valid_to 不含终点比较，introduced_in 不作为精确过滤；无适用状态时顶层 affiliation SHALL 保持空。若同等适用状态给出互斥组织集合，SHALL 报 `ambiguous affiliation projection`。

---

### Requirement: 关系与谓词白名单

实现锚点：`ingest.py::schema_check`、`lint/wiki-lint-rules.yaml`

关系 SHALL 使用确认谓词白名单，并校验对象类型：

```text
affiliated_with, mentor_of, parent_of, sibling_of,
participates_in, occurs_at, located_in, precedes
```

- `affiliated_with` 对象 SHALL 为 `org:`；
- `participates_in` / `precedes` 对象 SHALL 为 `event:`；
- `mentor_of` / `parent_of` / `sibling_of` 对象 SHALL 为 `char:`；
- 未列入白名单的谓词 SHALL 拒绝。

#### Scenario: 历史任职

- **WHEN** 记录散兵在愚人众第六席
- **THEN** 使用 `affiliated_with` + `org:fatui` + `role="第六席"`，限定 `identity_version_ids=["identity:scaramouche"]`，结束边界写 `StoryScope`。

#### Scenario: 造物主关系

- **WHEN** 雷电将军与造物之间存在创造关系
- **THEN** 使用 `created_by`，客体类型 SHALL 为 `char:` 或 `org:`；造物主与造物分别建实体，不得据此合并身份阶段。

---

### Requirement: 名称、aliases 与歧义

实现锚点：`wiki_lint.py::_check_l4`、`lint/wiki-lint-rules.yaml`

系统 SHALL 对 aliases 做全量检查：

- `aliases` 数组中的每个值 SHALL 在 `identity_versions[].names` 中存在 NameRecord；
- 该 NameRecord 的 evidence quote SHALL 包含该值；
- 当前 Wanderer 的 required aliases SHALL 为 `散兵`、`流浪者`、`国崩`、`斯卡拉姆齐`；
- 英文别名 SHALL NOT 进入契约。

#### Scenario: 别名缺失

- **WHEN** aliases 含 `国崩` 但没有对应 NameRecord 或引用 quote 不含国崩
- **THEN** L4 拒绝。

#### Scenario: 同名/称号指向歧义 [已知缺口]

- **WHEN** 某称号在原文出现，但指向另一个实体或多人共用
- **THEN** 当前“quote 出现该词”检查不足以判定归属；实体绑定与歧义冲突尚未实现。

---

### Requirement: 冲突候选与不仲裁

实现锚点：`ingest.py::schema_check`、`tests/test_wave0_contracts.py::test_q3_candidate_conflict_allowed_without_resolution`

系统 SHALL 允许 Wave0 记录候选冲突，但 SHALL NOT 仲裁：

- `conflicts[].status` SHALL ∈ {`open`, `deferred`}；
- `resolution` SHALL 为 `null`；
- 每个冲突 SHALL 至少有两个 alternatives；
- 每个 alternative SHALL 有直接 evidence；
- `unknown`/缺失信息 SHALL NOT 自动算冲突；
- 未解决的互斥断言 SHALL NOT 被提升为 accepted。

#### Scenario: 候选冲突

- **WHEN** 两个来源对同一关系给出互斥值
- **THEN** 生成 `conflict:*`，`status="open"`，两个 alternatives 各自带证据，`resolution=null`。

#### Scenario: 非法冲突目标

- **WHEN** 冲突的 `target_ref` 指向不存在的字段或记录（如 `/relations/999999`）
- **THEN** `schema_check` SHALL 解析 `field:<JSON Pointer>` / `relation:<ID>` / `event:<ID>` / `memory:<ID>` 等稳定 TargetRef；无法解析时拒绝。

#### Scenario: 候选值类型

- **WHEN** `proposed_value` 与 `target_ref` 指向的目标类型不匹配
- **THEN** `schema_check` SHALL 按目标字段/记录 Schema 校验候选值：nullable 字段允许合法枚举值；稳定 `relation:<ID>` / `event:<ID>` / `memory:<ID>` 等记录目标要求完整对应类型、ID 一致、谓词主客体语义正确，且外层 alternative 的 version/evidence 与内部记录一致；错类型、错 ID 或包装不一致拒绝。`field:` 目标限定基础字段白名单。

---

### Requirement: 规则驱动 lint

实现锚点：`wiki_lint.py::load_rules`、`wiki_lint.py::run_lint`、`lint/wiki-lint-rules.yaml`

lint SHALL 由规则文件的封闭 operation 驱动，不得使用 `eval/exec`：

- L1：四章节列表一致；
- L2：source span 引用/定义、URL/span 绑定、quote/range/hash、正文确定性；
- L3：关系谓词白名单；
- L4：canonical/aliases/NameRecord/引用 quote/snapshot primary；
- L5：事件版本数字、与引用 span 的 game_version 一致、正文版本标记。

#### Scenario: 修改规则参数生效

- **WHEN** 修改 `required_aliases`
- **THEN** L4 结果随规则文件变化，而不是硬编码。

#### Scenario: schema 预检与规则级测试

- **WHEN** CLI 正式验收
- **THEN** 先跑 schema_contract + schema_check，再执行 L1–L5；
- **WHEN** 规则级单测
- **THEN** 允许跳过 schema 预检，直接验证 L1–L5 行为。

---

### Requirement: 运行时 canonical 检索

实现锚点：`app/canonical.py::CanonicalIndex`、`app/canonical.py::canonical_query`、`app/tools/canonical.py::canonical_query_tool`、`intent_router.py::TOOL_GROUPS`

运行时 SHALL 能读取 canonical 页并返回带 citation 的候选证据：

- loader SHALL 解析 JSON front matter 与正文脚注 `span_id -> URL` 映射；
- 检索 SHALL 至少覆盖 identity、name、event、relation、memory、affiliation、conflict；
- 输出 SHALL 保留 `[^source:URL#pNNN]`，并明确标注 candidate / 未人工复核；
- 无命中时 SHALL 返回显式缺口，不得编造；
- canonical 页缺失时 SHALL NOT 崩溃；
- 工具 SHALL 支持可选 snapshot，并据此过滤 Fact；
- Answer Agent prompt SHALL 要求保留 canonical 证据的 citation 与状态语义。

#### Scenario: 关系问题

- **WHEN** 用户问“散兵在愚人众里是什么身份？”
- **THEN** 返回第六席证据 + citation。

#### Scenario: 事件问题

- **WHEN** 用户问“世界树事件后发生了什么？”
- **THEN** 返回事件/记忆证据 + citation；查询可携带 snapshot，runtime SHALL 按身份、版本、story_scope 过滤 Fact；无当前快照匹配时返回限制说明。

#### Scenario: 无命中

- **WHEN** 问题与所有 canonical 记录无关
- **THEN** 返回“canonical 知识库没有可引用的记录”，不调用 LLM 编造。

---

### Requirement: 属性记录与投影

实现锚点：`wiki/__schema__/entity.schema.json`、`wiki/__schema__/entity-template.md`、`ingest.py::schema_check`、`lint/wiki-lint-rules.yaml`、`app/canonical.py::CanonicalPage`

系统 SHALL 用 `attribute_records` 作为 element / weapon / nation 的事实维护入口；顶层 `element` / `weapon` / `nation` 是 page.snapshot 下的派生缓存，必须与期望投影双向一致，包括 null。

#### Scenario: 同值多证据合并

- **WHEN** 同一 key 存在多条同值且共同适用的记录
- **THEN** 投影为该值，并保留全部来源 attribute_id 与证据。

#### Scenario: 多值共同适用

- **WHEN** 同一 key 在 query snapshot 下存在多个不同已知值共同适用
- **THEN** 顶层投影为 null，并报告 ambiguous 与涉事记录，不得静默选第一条。

#### Scenario: unknown 与缺失区分

- **WHEN** 属性为 unknown/null 或没有记录
- **THEN** 两者 MUST 可区分；null 不表示已知没有该属性。

### Requirement: 属性适用性三态

实现锚点：`app/canonical.py::CanonicalIndex`、`ingest.py::_version_applies`

属性适用性 SHALL 由主体/身份、StoryScope、VersionScope 三轴合取决定，并区分 applicable / excluded / unresolved。

#### Scenario: 任一已知限制排除

- **WHEN** 身份、剧情范围或资料版本范围任一已知条件不满足
- **THEN** 该记录为 excluded，不得因其他轴匹配而覆盖。

#### Scenario: 无排除但上下文不足

- **WHEN** 没有已知限制排除，但必要身份、剧情或版本边界无法确定
- **THEN** 返回 unresolved，不得当作当前适用或永久有效。

### Requirement: 属性生成权限

实现锚点：`ingest.py::schema_check`、`lint/wiki-lint-rules.yaml`

生成器 SHALL 只写 candidate / disputed；MUST NOT 写 accepted / superseded / rejected 或 verified_in / verified_in_labels；这些状态由审核流程维护。

#### Scenario: 生成器写审核态

- **WHEN** 候选页面包含 accepted / superseded / rejected / verified 集合
- **THEN** ingest 拒绝，提示状态需审核流程维护。

### Requirement: 属性冲突与引用

实现锚点：`ingest.py::_validate_proposed_value`、`wiki_lint.py::_check_l7`

属性冲突 SHALL 基于完整适用集合重叠判定；TargetRef SHALL 支持 `attribute:<ID>`；完整 AttributeRecord 候选 SHALL 校验 key/主体/ID 一致；runtime 冲突 SHALL 先判集合再排序，并输出 ambiguous 诊断。

### Requirement: 公开仓发布边界

实现锚点：`docs/wave0-acceptance.md`、`reports/wave0-acceptance.json`

公开仓 SHALL 只放运行/部署/重建必需文件；LLM Wiki 的过程产物 SHALL 保持本地：

- raw 摘要/候选/评审/任务书/迁移报告 SHALL NOT 进入公开仓；
- 公开历史 SHALL NOT 含过程文件；
- canonical 页是否进入公开仓/Release SHALL 在 Wave1 发布政策中明确。

#### Scenario: 公开仓现状

- **WHEN** clone 公开仓
- **THEN** 不包含 `raw/`、`ingest.py`、`wiki/__schema__` 等本地-only Wave0 文件；运行时工具在缺页时返回显式缺口。

#### Scenario: canonical 页发布 [已知缺口]

- **WHEN** Wave2 决定批量扩充角色
- **THEN** 当前尚未决定 canonical 页是进公开仓、进 Release 资产，还是只本地维护。

---

### Requirement: 增量更新与级联脏标记 [未实现]

实现锚点：待定

系统 SHALL 在 raw/source/canonical 页变化时标记受影响页面：

- source sha256 变化 → 引用该 source 的 span 失效；
- span 变化 → 引用该 span 的记录需要重审；
- 名称/身份/事件变化 → 继承/投影字段与相关页面 dirty；
- 冲突状态变化 → 引用该 conflict 的 disputed 记录 dirty。

#### Scenario: 修改 A 页面

- **WHEN** 修改 `wiki/characters/a.md` 的 identity/event
- **THEN** 系统 SHALL 列出必须重审的页面与记录。

**当前状态：未实现，列为 Wave4 目标。**

---

### Requirement: 玩家选择绑定

实现锚点：`wiki/__schema__/entities/char_traveler.json`、`wiki/__schema__/entity.schema.json`、`ingest.py::schema_check`、`app/canonical.py::CanonicalIndex`

系统 SHALL 将「空 / 荧」建模为请求级玩家选择，而非身份阶段。选择目录以实体 profile 为单一权威；页面若保留 `player_choice_options`，只能是可校验投影。

#### Scenario: 明确选择

- **WHEN** 请求明确指定 `player_choice=aether` 或 `lumine`
- **THEN** 共同事实与对应分支事实适用，另一分支事实 excluded。

#### Scenario: 裸名称不绑定

- **WHEN** 查询只出现「空」「荧」「哥哥」「妹妹」等称谓，而没有明确选择表达
- **THEN** 不自动判定玩家选择；只识别查询对象或返回条件候选。

#### Scenario: 未指定选择

- **WHEN** 查询未提供 `player_choice` 或为 null
- **THEN** 返回共同事实与分支条件候选，并保留选择缺口；不得默认选一个，也不得合并两边事实。

### Requirement: 玩家选择范围与证据

实现锚点：`ingest.py::schema_check`、`lint/wiki-lint-rules.yaml`

相关记录 SHALL 支持 `player_choice_scope`；`[]` 表示显式共同事实；缺失 scope 的旅行者旧记录 SHALL 视为未分类迁移缺口，不得默认共同。

选择范围 SHALL 由可追溯的来源分支标记、明确条件语句或受验证的分支元数据支持；原文只出现名字不足以证明分支。

#### Scenario: 缺 scope 不隐式共同

- **WHEN** 旅行者记录缺少 `player_choice_scope`
- **THEN** 报告未分类缺口；不得在两种选择下同时当作共同事实输出。

#### Scenario: 非法选择

- **WHEN** choice_id 未在目录中，或类型错误，或非选择实体被传入选择参数
- **THEN** 入口校验失败或明确说明不适用；不得降级为未指定。

### Requirement: 玩家选择运行时一致性

实现锚点：`app/canonical.py::CanonicalIndex._player_choice_applies`、`app/tools/canonical.py::canonical_query_tool`

选择适用性 SHALL 与版本、剧情位置、身份阶段按合取判定，并在普通 Fact、身份摘要、属性投影、冲突候选、observed_at 与 field_assertions 中保持一致。

#### Scenario: 先分组后截断

- **WHEN** 共同事实命中且 top_k 截断存在条件事实
- **THEN** 选择缺口与冲突诊断 MUST 保留，不因 top_k 或文本去重被隐藏。

#### Scenario: 分支值不合成共同冲突

- **WHEN** 两个分支在同一语义键下值不同
- **THEN** 未指定选择时按分支条件分组报告；只有共同事实与某一分支在同一重叠作用域内互斥时，才构成该分支冲突。

## 已知缺口汇总（Wave1 前必须处理）

1. `schema_check` 已移除 Wanderer 硬编码并支持实体 profile；`ingest()`/CLI 已支持 `--character`、`--prompt`、`expected_entity_id` 绑定与 profile 驱动入口。非 Wanderer 角色仍需各自的 prompt/profile 与端到端内容测试。
2. 非数字 `label` 已可在 raw、event、L5 中保留；真实 Wanderer 页已迁移。B1 已通过版本契约专项评审：version-registry、并行标签字段（`valid_from_label`/`valid_to_label`/`verified_in_labels`）、统一 ResolvedVersion、L6、固定注册表快照与 runtime 版本范围均已落地；数字映射与排序关系的注册值仍可为 null，未知顺序只作未解析处理并报告缺口。
3. `created_by`、`aspect_of`、`segment_of` 已加入谓词白名单与模板边界说明；玩家选择（空/荧）绑定端到端规则与复杂主体边界端到端测试仍未实现。
4. `attribute_records` 未实现；多元素/多形态角色的带上下文属性无法表达。
5. 冲突 TargetRef 已支持稳定引用并做 Schema 类型校验；完整 conflict alternative 语义与跨记录一致性仍可继续加强。
6. aliases 全量 NameRecord + quote 检查已实现；运行时已加跨页 alias 歧义检测与回答警告；schema 级歧义规则仍未实现。运行时已过滤 rejected、默认排除 superseded、保留 disputed/冲突元数据；但自由文本检索仍不携带 snapshot，当前归属类问题需调用方提供 snapshot。
7. canonical 页与 Wave0 过程文件仍为本地-only；公开仓发布策略未定。
8. 增量更新与级联脏标记未实现。
9. B2 属性记录正在实现：首版 key 仅 element/weapon/nation；form_label/vision 与复杂主体边界归后续批次。
10. B3 玩家选择绑定正在补齐规格：请求级 choice、分支来源证明、缺 scope 迁移与 L8 契约。
9. `CanonicalIndex.search/answer` 已支持 snapshot，但自由文本工具 `canonical_query` 尚未由调用方传入 snapshot；项目级覆盖率阈值 80% 未达到（当前 15.9%）。

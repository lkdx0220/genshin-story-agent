# 现状规格：文档治理（doc-governance）

> 面向"会腐烂的文档"：README、发布说明、对外入口类文档。核心原则是**文档要么被机器消费，要么被机器校验**。

## Purpose

本能力确保对外入口文档（以 `README.md` 为首）中的**事实性数据**始终与仓库真源一致，避免出现"README 滞后若干版本"的静默腐烂。

实现锚点：`scripts/gen_readme_status.py`

### Requirement: README 现状区块由脚本生成并由 CI 校验

`README.md` SHALL 含一个机器可识别的现状区块，由 `scripts/gen_readme_status.py` 从真源计算生成：

- 区块以 `<!-- GENERATED:STATUS ... -->` 与 `<!-- /GENERATED:STATUS -->` 包裹，区块内 MUST NOT 手工编辑；
- 数据真源：工具数（`app/tools/` 的 `@tool` 装饰器与 `_HIDDEN_TOOL_NAMES`）、知识库规模（`kb_vectors/wiki_entry_graph.json`、`wiki_entity_mention_index.json`、`entity_alias_map.json`）、cast 覆盖（`kb_vectors/cast_coverage.json`）、数据快照日期（图文件 mtime）、最新 KB Release（`gh release list`，取不到时回退为脚本内记录值）；
- 提供 `--check` 模式：生成结果与文件不一致时以非零退出，供 CI 使用；
- 发布快照与工作区数据 MUST 分别标注（发布资产是快照，不是当前状态）。

#### Scenario: CI 校验一致性

- **WHEN** README 的现状区块与真源不一致（例如数据更新后未刷新 README）
- **THEN** CI 的 `readme-status` 作业失败并指出差异；修复方式为运行 `python scripts/gen_readme_status.py` 后提交。

#### Scenario: 数据更新后的刷新

- **WHEN** `kb_vectors/` 下真源发生变化
- **THEN** 维护者（或每周定时作业）重跑生成器刷新区块，使 README 与真源一致。

#### Scenario: 发布与当前状态不混淆

- **WHEN** 存在已发布快照（`kb-*` Release）且工作区数据比它更新
- **THEN** 现状区块同时给出"最新 Release = 快照"与"工作区数据日期"，不得让读者误以为发布资产等于当前状态。

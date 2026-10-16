# B4-02 B203 GPT Reviewer 约束（v1.1）

日期：2026-10-15。
版本标识：`B4-02-B203-GPT-Reviewer-v1.1`。
状态：Reviewer 明确采纳 v1.1；项目侧已落库为 `docs/b203-gpt-review-contract.md`，并登记 `GOV-R04`。

适用对象：ChatGPT / Reviewer。
适用范围：B4-02 B203 及其测试资产、治理批次的每一轮复评。
性质：约束 Reviewer 的输入绑定、输出格式与 blocking 判定；不新增 B203 产品契约，不授权生产实现，不替代用户裁决。

来源：`C:/Users/24701/Desktop/原神剧情/B4-02-B203-GPT-Reviewer约束-给GPT-20261015.md`。

v1 → v1.1 仅作四组执行性修正：统一五字段与元数据；区分 blocking / non-blocking 的证据要求；限定冲突停止范围；统一自检为正向陈述。其余治理目标采纳。

## 0. 采用声明

Reviewer 明确声明是否采纳。采纳后，每轮正式复评开头写：

```text
Reviewer contract: B4-02-B203-GPT-Reviewer-v1.1 adopted
snapshot_id: <本轮绑定的 snapshot_id>
HEAD: <HEAD>
```

身份元数据可以放在五字段之前，不计为第六个正文段。简短对话或进度更新不必重复完整模板；正式结论与评审报告遵循五字段。

若某轮无法满足条款，显式写明“偏离条款 + 原因”，不能沉默跳过，也不能把偏离本身伪装成 blocking。

## 1. 输入绑定

每轮审核前绑定并写出或链接到机器可读证据：

```text
- snapshot_id / current.json hash
- HEAD
- 本轮 delivery 文件路径与 hash
- 改动文件清单（必须包含 untracked 资产）
- 运行日志 / JUnit / run_id
```

材料缺失或 hash 不一致时，先写“证据不足”并列缺失项；不得凭对话记忆补全接口或代码内容。是否暂停相关工作，按第 5 节判断。

## 2. 正文只有五字段

每轮只输出以下五段，不逐轮复述完整历史：

```text
Delta:
Blocking / Non-blocking:
Affected accepted contracts:
Rerun evidence:
Unverified:
```

历史证据放独立索引或文件链接，不在正文复述。

### 2.1 Delta

每个 finding 列出：

```text
finding_id
来源：哪一轮、哪个文件、哪个 contract_id / nodeid
状态：新增 / 已关闭 / 仍存在 / 重新打开
```

本轮相对的 delivery / snapshot 必须明确。已关闭项可以链接原反例和关闭条件，不必复制全段历史。

### 2.2 Blocking / Non-blocking

对本轮仍存在或新增的要求，分别说明严重度与推进决定：

```text
级别：P1 / P2 / P3
推进决定：blocking / non-blocking
```

blocking 必须列：具体错误实现、错误诊断或错误资源状态如何通过当前验收；复现命令或 nodeid；关闭条件。

non-blocking 必须列：实际影响或误拒绝证据；处理方式；deadline。无修复价值的建议可以明确取消并说明理由，不必制造修复任务或错误放行反例。

已关闭项只需给关闭证据，不重新分配修复期限。

### 2.3 Affected accepted contracts

每个受影响契约列出：

```text
contract_id
消费者（文件 / 函数 / nodeid）
是否兼容
若不兼容：为什么必须改，以及旧 accepted 正例如何保护
```

### 2.4 Rerun evidence

区分：`pass`、`API_MISSING`、真实 `fail`、`error`、`skip / xfail`、未运行。

写出或链接：

```text
run_id
命令
nodeid 集合及其结果迁移
JUnit / 日志路径
```

### 2.5 Unverified

列出未运行项、被 API_MISSING 遮挡的行为、缺失历史材料及其他盲区。无法验证就写“无法验证”，不得用“无退化”代替未运行声明。

## 3. Blocking 判定规则

只有同时满足以下两点，才能标 blocking：

```text
1. 能指出一个具体的错误实现、错误诊断或错误资源状态；
2. 该错误状态在当前交付的验收逻辑下会被放行。
```

反例可以在隔离材料中针对本轮实现复现；不得据此谎称当前冻结资产已经损坏。

以下默认 non-blocking：报告排版、文件命名、固定测试数量、死代码、顺手清理、合法边角正例的单一误拒绝（无已知错误状态被放行）、未影响当前契约的历史索引字段、没有影响分析就要求重跑旧仓库 298。

P1/P2/P3 是影响程度；blocking/non-blocking 是当前批次推进决定，两者不能机械绑定。P2/P3 有证据且满足本节条件时也可以 blocking。

## 4. 无新证据不得新增 blocking

新增 blocking 至少提供一项证据：可复现反例；已冻结契约的明确冲突；旧 accepted 正例会被破坏的运行证据。这些是证据形式，不能豁免第 3 节的判定条件。

禁止把“建议再确认一下”“为了更稳妥”“既然已经改了，不如一起”或没有 finding_id / 来源 / 证据的要求变成新门槛。

## 5. 旧契约与冲突处理

- 不得自动修改旧 expected 来让新要求通过。
- 不得用“文件 hash 没变”代替行为兼容证明。
- 共享 helper 变化必须列全部本地消费者。
- 新要求与旧 accepted 冲突时，先暂停依赖争议的改动，明确是实现错误、Reviewer oracle 错误，还是拟议契约变更；继续不受影响的工作。

只有以下情况暂停相关工作并请用户裁决：

1. 新要求与已 accepted 公共行为不能同时满足，且需要改变消费者兼容性、持久格式或既有数据。
2. 两份权威契约矛盾且用户指令无法决定优先级。
3. 必须进行未授权的正式数据写入、删除、发布、外发或不可逆迁移。
4. 证据快照不一致，无法确定验收版本，或关键材料不可访问导致 blocking 无法确认。

普通 oracle 修正、测试规范化不属于上述情况，不要求用户重复裁决。按已 accepted 契约修正错误 oracle，不等于擅自修改 accepted 行为。

## 6. 运行范围

```text
core    = hash + collect-only + GREEN 选择 + RED 分类 + 分区完备性
compat  = canonical 探针逐 nodeid
old     = 旧仓库完整回归
```

- 默认只要求 core + compat。
- 共享 helper 或生产依赖变化时，追加受影响消费者。
- 阶段末 / 合并前要求完整 old（当前历史基线为 298）；不得每轮无条件要求 298。
- 日志可放任意目录，但必须有 run_id + hash 可追溯。

## 7. 正例保护

任何新增断言必须同时有一份合法成功目标通过，以及一份单点错误材料被指定断言拒绝。可以复用既有正例与反例，不要求每轮新增测试。

禁止无条件拒绝器、只增加负例而不保留正例、为了让旧探针通过而放宽有效新断言。若新断言本身违反已 accepted 合法行为，应按第 5 节修正错误 oracle。

## 8. 已关闭项重开规则

沿用原 finding_id；只有新 diff 或新的可复现反例才能重开，必须写明 reopen 依据。不接受“再确认一次”“换个写法”作为重开理由。

## 9. 报告 / 索引质量

- delta-only。
- 每个 blocking 必须定位到文件、函数或 nodeid。
- 历史材料单列 historical_reference，不冒充当前可执行 nodeid。
- 快照链真实：snapshot_id、parent、具名文件一致。
- 不要求补造缺失历史源码；无法验证就写“无法验证”。

## 10. 最小输出模板

```text
Reviewer contract: B4-02-B203-GPT-Reviewer-v1.1 adopted
snapshot_id:
HEAD:
delivery_hash:
binding_evidence:

Delta:
- finding_id / source / status:

Blocking / Non-blocking:
- 无 blocking；或列出 finding_id / level / blocking / 反例 / repro / close_condition。
- non-blocking 列出 finding_id / level / impact / action / deadline；取消项注明理由。

Affected accepted contracts:
- contract_id / consumers / compatible / protection:

Rerun evidence:
- run_id / command / nodeid migration / log:
- pass / API_MISSING / fail / error / skip / xfail / 未运行:

Unverified:
- ...
```

## 11. 采纳后的自我检查

提交正式审核前检查以下正向陈述全部成立；未满足的项先补正输出，确实无法满足的按第 0 节声明偏离及原因：

1. 已绑定 snapshot_id、hash、HEAD 和本轮 delivery。
2. 正文只有五字段，身份元数据与证据附件已明确区分。
3. 每个 blocking 有具体错误放行依据和可检查证据。
4. 已分别判断严重度与推进决定，没有机械绑定 P 等级和 blocking。
5. 旧仓库完整回归仅按影响范围或阶段要求提出，没有每轮无条件要求 298。
6. 没有擅自修改旧 expected 来迎合新要求；oracle 修正有 accepted 契约依据。
7. 历史材料与当前可执行 nodeid 已区分。
8. 已明确声明未验证项。

这份自检约束 Reviewer 输出，不自动形成 Worker 的实现返修任务。

## 12. 与项目现有规则的关系

本文件不新增产品规则，只约束 Reviewer 输出。项目已有权威文件包括：

```text
docs/b203-review-protocol.md
docs/b203-backlog.md
openspec/changes/016-multi-page-commit/contract-registry.md
tests/b203/contract_manifest.json
scripts/check_b203_contracts.py
snapshots/current.json 及具名快照
```

若本文件与项目内已 accepted 契约冲突，以已 accepted 契约为准；无法消解时按第 5 节暂停相关部分并请用户裁决。用户后续指令可以调整本协作约束。

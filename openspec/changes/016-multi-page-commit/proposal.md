# B4-02 B203 多页提交事务方案 v0.2

状态：已按《B4-02 B203 方案评审与 Q1–Q5 决策回复》修订；D1 设计证据稿
日期：2026-10-15
前序：B201=accepted；B202=accepted（单页工程范围）。
范围：只定义 B203 方案、journal/build 契约、恢复矩阵与任务映射；本文件不实现代码。
基线回归：仓库 298 / 历史 251 / B202 独立 48（45 + 真实进程 3）。

---

## 0. Q1–Q5 决策摘要

| 决策 | 定稿 |
|---|---|
| Q1 full_required | 有条件纳入：权威重算、有效且属于本项目的 state、无 blocked/unknown/removal、候选集合严格等于全部当前管理页、全部完整校验成功；不能由文件自报 affected_pages 覆盖全部就放行。B203 本批限 build set >=2；full_required 但仅 1 页保持 B202 原拒绝语义；缺失/损坏 state 仍拒绝 bootstrap |
| Q2 journal | 单个 plan 级 journal + pages[]；完整 payload 在首次正式发布 provenance/output 前持久化；页进度标签只辅助诊断，恢复依据实际 hash；新增独立多页 journal 版本/协议；兼容旧 B202 |
| Q3 顺序 | 规范化 page_key 固定排序；回滚逆序；恢复中再次中断必须可从磁盘 hash 继续，不依赖进程内已替换列表 |
| Q4 capability | B203 实现并验收后才声明 `multi_page_commit=true`；capability 表示支持能力，不等于当前请求 executable；单页与只读接口边界保持 |
| Q5 真实进程 | 必须覆盖：中间页 replace 后、全部页 replace 后、state replace 后三个退出位置，以及同请求双进程竞争 |

0 页 no_op 只适用于完整预检通过且无阻断的权威空 build set；不得提前绕过坏 state、未解决 journal、删除或坏候选等拒绝条件。页数判断以完整 page_key 集合为依据，不只比较数量。

---

## 1. 目标与非目标

### 1.1 目标

```text
1. 一次事务提交一个权威 plan 的完整 build set（>=2 页）
2. 全部候选锁外冻结并完成完整 staging 校验
3. state replace 是唯一提交判定点
4. state 未提交前，任意页失败 → 全部回滚到完整旧版
5. state 已提交且全页 hash 匹配 → 后续 journal/cleanup 错误只保留新版并报告 pending
6. 崩溃/中断后恢复为完整旧版或完整新版；未完成时 fail closed
7. 重放 already_committed 字节不变
8. journal/provenance/graph/state 来自同一冻结候选集合
```

### 1.2 非目标

```text
删除页面墓碑
runtime stale / index freshness token
字段级重算
LLM 自动生成
自动修 manifest/proof/schema-lock/hash
真实 raw 内容质量
跨页内容自动合并/推理
无锁读者瞬时跨文件原子可见
```

---

## 2. build set 与入口

```text
权威重算后：

完整 page_key 集合为空         → no_op（仅无阻断时）
集合大小 1                     → B202 single_page
集合大小 >=2                   → B203 multi_page

full_required 纳入 B203 的条件：
  权威重算
  有效且 project_root 匹配的 state
  无 blocked / unknown / removal
  候选集合严格等于全部当前管理页
  全部页完整校验成功

full_required 但仅 1 页：
  保持 B202 原拒绝语义，不暗改 B202。
state 缺失/损坏：
  仍 bootstrap_not_supported / state_corrupt。
```

受控入口：

```text
B201：保持只读、拒绝 multi/full、不写锁/journal、不执行恢复。
B203：新增显式多页执行预检/提交入口；
      capability.multi_page_commit 仅验收完成后置 true；
      当前请求 executable 仍由 CAS/候选/blocked/unknown 等逐项决定。
```

---

## 3. 三层身份与协议版本

### 3.1 身份

```text
request_payload（稳定请求的完整规范化原像）包含：
  project_root_id / protocol
  inputs 路径及登记摘要
  state 精确相对路径
  plan_generation / parent_generation
  规范化完整 page_key 集合
  每页 response 精确路径 / output 精确路径 / candidate hash
  完整执行字段原像：
    observed / dirty / full_required / blocked / unknown / affected_pages

规范化编码 C：
  json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
  .encode("utf-8")

request_id = SHA256(C(request_payload))

规范化规则：
  先检查字段类型，不用 bool()/str() 静默转换。
  observed 保留完整权威对象。
  dirty 缺省为 {}，值保留权威 planner 的完整 reason 列表。
  blocked / unknown / affected_pages 缺省为 []，字符串排序、去重。
  full_required 必须是布尔值。
  非 full plan 缺少 affected_pages 时编码为 []，不能编码为 null。
  page_keys 使用权威 build set：
    非 full = 规范化 dirty 键集合；full = 全部当前管理页。
  候选集合必须严格等于该 scope；
  page_keys == [page.page_key for page in pages]，两者均排序。

execution_token：
  每次调用随机生成；request_id 不包含 execution_token
  精确绑定 staging/<transaction_id>/<execution_token>

transaction_id = SHA256(C({"request_id": request_id, "execution_token": execution_token}))
  用于 journal 文件名与本次执行身份。
  固定结构化编码，不使用裸字符串拼接。

排除规则：
  built_at 不参与 request_id，也不参与 transaction_id。
  execution_token 不参与 request_id，必须参与 transaction_id。
```

### 3.2 协议版本

```text
成功 state 保持 format_version=1。
journal 版本：
  format_version=1 → B202 单页 journal
  format_version=2 → B203 多页 journal
  未知/坏版本 → fail closed。

共享恢复入口 dispatcher：
  识别 v1 / v2；
  含待恢复 v2 时，B202 单页 commit 也必须先受项目恢复阻断；
  v1 旧恢复与单页重放不因 v2 扩展失效。
```

---

## 4. 完整 journal schema（v2）

### 4.1 示例

```json
{
  "format_version": 2,
  "protocol": "b4-02-multi-commit-v1",
  "project_root_id": "<sha256 of resolved project root>",
  "request_payload": {
    "project_root_id": "<same>",
    "protocol": "b4-02-multi-commit-v1",
    "inputs": {"path": "config/incremental-inputs.json", "sha256": "<raw sha256>"},
    "state": "reports/incremental/state.json",
    "plan_generation": "<plan generation>",
    "parent_generation": "<baseline generation>",
    "page_keys": ["<sorted full page_key>"],
    "pages": [
      {"page_key": "<key>", "response": "<response path>", "output": "<output path>", "candidate_sha256": "<raw sha256>"}
    ],
    "execution": {
      "observed": "<canonical observed object>",
      "dirty": "<canonical dirty map>",
      "full_required": false,
      "blocked": [],
      "unknown": [],
      "affected_pages": ["<sorted page_key>"]
    }
  },
  "request_id": "<stable request hash = SHA256(C(request_payload))>",
  "execution_token": "<random token>",
  "transaction_id": "<hash(request_id, execution_token)>",
  "plan_generation": "<plan generation>",
  "parent_generation": "<baseline generation>",
  "inputs": {
    "path": "config/incremental-inputs.json",
    "sha256": "<raw bytes sha256>"
  },
  "state": {
    "path": "reports/incremental/state.json",
    "old_sha256": "<canonical JSON sha256>",
    "new_sha256": "<canonical JSON sha256>"
  },
  "staging_root": "reports/incremental/staging/<transaction_id>/<execution_token>",
  "created_at": "<iso8601>",
  "status": "prepared",
  "pages": [
    {
      "page_key": "<full page_key>",
      "response": {
        "path": "<registered response relative path>",
        "sha256": "<raw bytes sha256>"
      },
      "output": {
        "path": "<registered output relative path>",
        "old_exists": true,
        "old_sha256": "<raw bytes sha256 or null>",
        "new_sha256": "<raw bytes sha256>"
      },
      "backup": {
        "path": "reports/incremental/backups/<transaction_id>/<page_slug>.bak",
        "sha256": "<raw bytes sha256 or null>"
      },
      "provenance": {
        "dir": "reports/incremental/provenance/<transaction_id>/<page_slug>",
        "manifest_sha256": "<canonical manifest sha256>",
        "members": [
          {"path": "<relative>", "sha256": "<raw sha256>"}
        ]
      }
    }
  ]
}
```

字段规则：

```text
1. 所有路径均为项目根下相对路径；绝对路径/越界/目录不符 → 拒绝。
2. 所有 sha256 默认是原始字节 SHA256。
3. state_sha256 与 graph/state 结构摘要使用规范化 JSON：
   json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")。
4. missing 与 unreadable 必须区分：null 仅表示“明确不存在”，
   不可读/解析失败为独立错误，不能当 null。
5. pages 必须非空、page_key 唯一、固定排序、完整覆盖 request 页集合。
6. status 只允许 prepared / pages_replaced / committed / recovery_required；
   页进度标签只用于诊断，恢复以实际资源 hash 为准。
7. journal 必须能重算 request_id / transaction_id 与全部资源绑定；
   仅字段齐全不算校验通过。
8. request_payload 必填；journal 的 pages[]、state、inputs、generation
   必须与 request_payload 的对应投影完全一致。
   先做类型/集合/一致性校验，再重算身份与派生路径。
9. request_payload 只含稳定字段：
   不包含 execution_token、built_at、随机 staging/provenance 路径。
10. v2 provenance 摘要统一字段名 manifest_sha256；
    journal.pages[].provenance.members 必须与 manifest.files 完全一致。
    B202/v1 原字段不改。
11. provenance manifest.files 按相对路径排序、唯一，
    记录目录内所有证据文件，不包含 manifest.json 自身。
12. 候选绑定使用 response 原始文件字节 SHA256；
    output 摘要使用 ingest 实际生成字节 SHA256。
    两者不可混用，也不因换行归一化放松原始候选绑定。
13. 路径比较统一使用项目根下 posix 相对路径；
    Windows 反斜杠只作为显示层，不进入身份/成员/白名单比较。

### 4.3 provenance manifest 契约（D03）

```text
每页 provenance 目录定义 canonical manifest：
  provenance/<transaction_id>/<page_slug>/manifest.json

manifest 内容：
  {
    "protocol": "b4-02-provenance-v1",
    "files": [
      {"path": "<relative path within page provenance dir>",
       "sha256": "<raw bytes sha256>"}
    ]
  }

规则：
  files 按 path 排序，唯一，不允许目录/符号链接逃逸；
  成员必须逐一核对实际字节，不能只 hash manifest 自身；
  预期成员清单同时写入 journal.pages[].provenance.members，
  该成员清单或不可提前删除的可验证原像必须在恢复期间保留；
  正常发布要求成员完整且匹配；
  必须包含 sources.json 引用的全部 raw snapshots；
    每个 snapshot 的 source_id、artifact_path、原始字节 hash 与冻结 bundle 一致；
  回滚/cleanup 可重入判定允许预期成员已缺失，
    剩余成员仍须匹配；额外/改写成员 fail closed；
  全计划恢复预检完成后才删除可确认归属的成员；
  恢复清单不能随 provenance 首先删除。
```

### 4.4 journal 内 provenance 字段最小扩展

```json
"provenance": {
  "dir": "reports/incremental/provenance/<transaction_id>/<page_slug>",
  "manifest_sha256": "<canonical manifest sha256>",
  "members": [
    {"path": "<relative>", "sha256": "<raw sha256>"}
  ]
}
```
```

### 4.2 转态规则

```text
prepared：
  完整恢复意图已持久化；
  允许零页、部分页或全部页 output 已替换；
  不能把“尚未替换任何 output”当作重启依据。

pages_replaced：
  全部页 output 已确认替换；
  state 尚未替换；
  state 已新而 journal 仍 prepared/pages_replaced 时按实际字节判定。

committed：
  state 已替换且全页 output/provenance hash 匹配；
  只剩 cleanup。

recovery_required：
  第三方改写、歧义、备份不可信、回滚失败；
  保留 journal/backup/provenance 证据，不做破坏性自动恢复。
```

---

## 5. 成功 state.build schema（v2）

```json
{
  "build": {
    "version": 2,
    "request_id": "<stable request hash>",
    "transaction_id": "<execution id>",
    "execution_token": "<token>",
    "parent_generation": "<parent>",
    "plan_generation": "<plan>",
    "generation": "<new generation>",
    "built_at": "<iso8601>",
    "pages": {
      "<page_key>": {
        "response": {
          "path": "<response relative path>",
          "sha256": "<raw sha256>"
        },
        "output": {
          "path": "<output relative path>",
          "sha256": "<raw sha256>"
        },
        "provenance": {
          "dir": "<provenance relative dir>",
          "manifest_sha256": "<canonical manifest sha256>"
        },
        "generation": "<new generation>"
      }
    }
  }
}
```

规则：

```text
state 顶层仍 format_version=1。
build.version=2 仅由 B203 成功多页提交写入。
built_at 不参与 request_id，也不参与 transaction_id。
execution_token 不参与 request_id，必须参与 transaction_id。
重放校验使用成功 state.build，不要求重新生成的 provenance
必须与上次随机 execution_token 路径一致；
重放按稳定 request_id + 已提交 build.pages 资源 hash 判定。
```

---

## 6. 提交判定点与状态标签

```text
state replace 是唯一提交判定点：

state 未提交前：
  任意页失败 → 回滚全部已替换页，state 保持旧版。
state 已替换成功且全页 output/provenance hash 匹配：
  后续 committed journal 写失败 / cleanup 失败
  → 保留完整新版，返回 committed_cleanup_pending；
  不得回滚 output。
state replace 异常后必须重读实际 state：
  仍为 old → 回滚 output；
  实际为 new 且页资源匹配 → 保留新版 + pending；
  其他 → recovery_required。
state 已新但页资源非预期：
  recovery_required，不得自动回滚已提交 output。
```

删除“部分状态只允许极短窗口”的时间保证。承诺：

```text
1. 使用项目锁 + journal + 资源 hash 判定。
2. 未解决 journal 时提交/恢复接口 fail closed。
3. 不宣称任意无锁读者瞬时只见全旧或全新。
```

---

## 6.1 B203 入口最低契约

```text
app.rebuild_multi 接口：
  precheck_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None)
  commit_rebuild(plan_path, state_path, inputs_path, candidate_manifest_path=None)
  recover_transaction(txid=None)

合法多页 precheck：
  action="multi_page"
  executable=True
  build_set 精确排序
  errors=[]
  candidate_binding_passed=True
  full_validation_passed="not_run"

拒绝 precheck：
  action="reject"
  executable=False
  errors 含稳定诊断

合法空集合 precheck：
  action="no_op"
  executable=False
  build_set=[]
  errors=[]

正常 commit：
  status="committed"
  request_id / transaction_id 与 state.build 一致

合法重放：
  status="already_committed"
  文件字节不变

输入/CAS 拒绝：
  status="rejected"

未解决 journal：
  status="recovery_required"
  errors 含 journal_unresolved:<文件名>

合法空集合 commit：
  status="no_op"
  零写入

precheck 只读：
  不创建锁/staging/journal，不执行 ingest/恢复。

单页例外：
  单页请求继续委托既有 B201/B202 公共接口，
  保留其原返回结构与状态；
  不套用本多页返回表，也不修改单页事务语义。
```

---

## 6.2 v2 recover 最低返回与稳定诊断

`recover_transaction(txid=None)` v2：

| 场景 | 返回 |
|---|---|
| 指定 txid 的一个 journal 成功回滚/清理 | status="ok", resolved=1 |
| 无 journal / 成功恢复后再次全部恢复 | status="ok", resolved=0 |
| 未提交且无法完成 / 资源身份不可信 | status="recovery_required", errors=list[str] 非空 |
| 新 state 和全部新资源可信，仅清理失败 | status="committed_cleanup_pending", errors=list[str] 非空 |
| 别人持有项目锁 | status="lock_busy" |

v2 稳定诊断（精确 errors 成员，`<name>` 为 journal basename）：

| 主故障 | errors 必须含 |
|---|---|
| request_payload 缺失 / request_id / transaction_id 重算不符 | journal_identity_invalid:<name> |
| journal 与 request_payload 投影不符 / 页缺多重复乱序 | journal_projection_mismatch:<name> |
| 派生目录 / 登记目标 / 保护路径不符 | journal_resource_ownership:<name> |
| 需恢复旧版页 backup 缺失 / 摘要错误 | journal_backup_invalid:<name> |
| output/state/provenance 非预期 / 额外或改写成员 | journal_resource_conflict:<name> |
| 已存在资源读取失败 | journal_resource_unreadable:<name> |
| 资源已验证但清理操作失败 | journal_cleanup_failed:<name> |

v1/B202 返回结构不改；本表只适用于 v2 多页 journal。

## 7. 资源所有权与路径冲突

### 7.1 资源所有权表

| 资源 | 精确目标 | 删除/覆盖条件 |
|---|---|---|
| state | request 绑定的 state 相对路径 | 仅 commit 判定点 replace |
| output[i] | 该页登记 output | 仅 journal 本事务该页 + hash 匹配 |
| backup[i] | `backups/<transaction_id>/<page_slug>.bak` | 仅本 tx 且 hash==old_sha256 |
| provenance[i] | `provenance/<transaction_id>/<page_slug>/` | 仅本 tx 且 manifest hash 匹配 |
| staging | `staging/<transaction_id>/<execution_token>/` | 仅本执行精确等值 |
| journal | `journal/<transaction_id>.json` | 最后清理；失败保留 |
| lock | `reports/incremental/commit.lock` | 只释放本进程持有 |

### 7.2 page_slug

```text
page_slug = sha256(page_key.encode("utf-8"))[:16]
不得使用 output.basename 直接命名；
避免不同目录同名文件在 backup/provenance 下碰撞。
```

### 7.3 冲突检查

```text
提交前与恢复入口统一检查：
  跨页 output 重复
  Windows 大小写/路径别名等价
  output 与 response/state/inputs/manifest/proof/profile/schema/lock/
  journal/staging/backup/provenance 的相等或目录覆盖
  staging 完整等值，不接受 owner 根、兄弟、嵌套重定向、其他执行目录
  每页 backup/provenance 精确派生唯一
坏 journal 在读取/写入/删除其指定目标前拒绝。
活登记改变/不可读 → fail closed 诊断，不自动用新登记解释旧目标。
```

---

## 8. 执行流程

```text
锁外：
1. 路径/协议/登记预检（复用 B201/B202）
2. 解析候选 manifest：集合严格等于完整 build set
3. 每页 candidate hash == 登记 response hash
4. 计算 request_id；生成 execution_token / transaction_id
5. 每页冻结候选到 staging/<txid>/<token>/pages/<i>/
6. 每页完整 staging：
     schema_check / L1-L8 / B3 proof / applicability
     entity/data_mode 一致；publication=draft_only
7. 使用全部冻结候选构造冻结 inputs 与全局 graph
8. 锁外准备每页 output 字节、new state 草稿、provenance 草稿

持锁：
9. 项目锁
10. 处理已有 journal（v1/v2 dispatcher）
11. 重放识别：
      load_baseline 有效
      state.build.request_id == request_id
      全部页 output/provenance/登记路径/hash 匹配
      → already_committed
12. 最终 CAS：
      权威 plan 完整执行字段 == request
      baseline generation == parent_generation
      observed == 冻结快照
      每页活 response hash == 冻结 candidate hash
      全部候选仍等于冻结字节
13. 逐页备份旧 output；逐页验证 backup hash
14. 写完整 prepared journal（pages[] 完整字段）
15. 逐页落地 provenance；逐页校验 manifest hash
16. 按 page_key 固定顺序逐页 replace
      任一步失败 → 逆序回滚已替换页
17. 全部页 replace 完成 → 写 pages_replaced
18. state replace（唯一提交判定点）
19. 写 committed
20. cleanup：staging → backups → journal 最后
21. 释放锁
```

---

## 9. 恢复预检与判定表（R03）

恢复必须先读取并验证全局身份与所有页资源，构造恢复动作清单：

```text
1. 校验 journal 版本/协议/字段/路径/页集合/身份重算。
2. 读取 state、全部页 output、全部 backup、全部 provenance。
3. 区分 missing / unreadable / hash mismatch / third-party。
4. 发现第三方改写、歧义、需恢复页 backup 不可信
   → recovery_required，不先回滚其他页、不删 provenance、不销毁证据。
5. 全部判定完成后才执行回滚/清理。
```

### 9.1 恢复判定表

| 全局 state | 页输出/资源 | 动作 |
|---|---|---|
| old | 页输出均匹配 old | 不写输出；检查并清理本事务 provenance；可重试清理 |
| old | 部分页仅匹配 new，其余匹配 old | 仅回滚需恢复页，逆序；全部所需 backup 先验证 |
| old | 某页 old_sha256==new_sha256 且当前匹配 | 该页无需写；不能单靠它判定是否 replace 过；全局看 state |
| old | old_exists=false 且当前缺失 | 该页已是旧版，无需 backup |
| old | old_exists=false 且当前仅匹配 new | 删除本事务新建 output；删除后验证不存在 |
| old | 已是 old 的页缺 backup | 若回滚已完成且其他资源可信，可继续清理；不按缺 backup 一概阻塞 |
| old | 需恢复旧版的页缺/坏 backup | recovery_required，不先执行部分破坏性恢复 |
| new | 所有页匹配 new 且 provenance 完整匹配 | 保留新版，可重试 cleanup；不再要求旧 backup 仍在 |
| new | 任一 output/provenance 非预期或缺失 | recovery_required，禁止自动回滚已提交 output |
| 其他/不可判定 | 任意 | recovery_required，保留材料 |

恢复中再次失败：

```text
允许暂时留下 old/new 混合；
必须保留尚需的 backup、journal 与明确状态；
下一次恢复可从磁盘 hash 继续；
不承诺故障后立即无条件全部旧版。
```

---

## 10. provenance 与 cleanup 可重入（R04）

```text
每页独立 provenance 目录：
  provenance/<transaction_id>/<page_slug>/
每页独立 manifest hash。

部分发布：
  第 1 页已发布、第 2 页失败；
  或某页目录部分文件落地；
  journal 记录每页预期 dir、manifest hash、members[]；
  恢复按 members[] 逐文件核对：
    全部预期成员存在且匹配 → 本页 provenance 完整；
    预期成员缺失但剩余成员匹配 → 属 cleanup 可重入；
    额外成员 / 已存在成员改写 → recovery_required。
  不能仅凭整体 manifest hash 判定“第三方改写 vs 自己删了一半”。

旧 state 分支：
  覆盖 output 已替换；
  也覆盖 output 全旧但 provenance 已部分/全部发布；
  正确清理残留后才允许重试。

cleanup 顺序：
  staging → backups → journal 最后；
  每资源幂等；
  已完成的回滚/提交后，重启不得再要求已不需要的 backup 一定存在；
  不得 ignore_errors 隐藏失败；
  不得删除仍可能需要的唯一旧副本。

结果状态：
  未提交且恢复未完成 → recovery_required
  新版已提交仅剩清理 → committed_cleanup_pending
  完整回滚后的失败不得误报 committed
  每个出口有明确 JSON 结果与重试行为
```

---

## 11. 冻结图、最终 CAS、重放与并发（R06）

```text
1. 所有 build set 候选先冻结并独立验证。
2. graph 构造只使用冻结候选；
   未重建页使用有效 baseline 或同一 observed 校验的快照；
   不混入活 response 新内容而 state.observed 记录旧 hash。
3. graph 诊断、候选 schema/proof、登记与实际读取依赖纳入拒绝/CAS。
4. 锁内比较完整规范化执行字段与完整 build set，不只 action/页数；
   检查 baseline generation、observed、每页 response/candidate 对应关系。
5. 备份全部验证成功后才发布正式 journal。
6. graph 与 new state 可锁外准备；
   锁内 CAS 后必须确保其 parent/observed 与准备时一致；
   不允许提交基于旧 parent 的草稿。
7. request_id 重放必须：
     load_baseline 有效
     build.generation == state.generation
     完整已提交页集合
     当前正式 output/provenance/登记目标匹配
   合法重放不重新要求旧 baseline CAS；
   损坏 state 不得短路为 already_committed。
8. 并发：
     最多一次 committed；
     竞争者 lock_busy；
     合法重试才 already_committed；
     不同请求共享项目锁，后续过时请求 CAS 拒绝；
     不增加隐式无限重试。
```

---

## 12. 故障矩阵

| 故障点 | 预期磁盘/结果 |
|---|---|
| 某页候选不匹配 | reject，零正式写入 |
| 某页 schema/lint/B3 失败 | reject，零正式写入 |
| lock busy | lock_busy；只清自己 staging |
| backup 第 i 页失败 | 不写 journal；零正式替换 |
| prepared 后 provenance 第 i 页失败 | recovery/cleanup；旧版保持；部分 provenance 可验证清理 |
| 第 1/中间/最后页 replace 失败 | 逆序回滚已替换页；state 旧 |
| 全部 replace 后 state 失败 | 回滚全部 output |
| state 后 committed journal 失败 | 保留完整新版；committed_cleanup_pending |
| cleanup 任一步失败 | journal 保留；可重试 |
| 进程在 prepared/pages_replaced/state 后退出 | 恢复完整旧版或新版 |
| old==new | 不写该页；全局看 state |
| old_exists=false 且新版存在 | 删除新建 output |
| 第三方改 output/provenance | recovery_required |
| journal 缺页/多页/重复/乱序/身份坏 | recovery_required |
| 同请求并发 | 一 committed；其余 lock_busy；重试 already_committed |
| state replace 异常但实际已 new | 重读 state；全页资源匹配 → 保留新版 + pending，不回滚 |
| 三页中最后检查页第三方改写 | 全计划恢复预检拒绝；output/provenance/backup/journal 全部不变 |
| 完整回滚后部分 backup 已清理再中断 | 不索要不再需要的 backup；保留 journal 可重试 |
| 完整提交后部分 backup 已清理再中断 | 保留新版；继续 cleanup 可重试 |

---

## 13. 不变量

```text
1. 任何提交/重放先通过统一 baseline loader 有效性校验
2. state replace 是唯一提交判定点
3. state 未提交前，任意失败不推进 baseline
4. 只操作本事务拥有且绑定到登记目标的资源
5. output/graph/provenance 使用同一冻结候选集合
6. 恢复未完成不丢 journal/backup/provenance 证据
7. journal 最后清理
8. 多页恢复先全计划预检，再逆序回滚
9. 不按锁年龄抢锁
10. 不自动 accepted/published
11. B201 只读与 B202 单页契约不退化
12. 不宣称无锁读者瞬时跨文件原子可见
```

---

## 14. 分阶段推进

```text
D1 设计证据：本 v0.2 + journal/build 示例 + 恢复矩阵 + 测试映射
T1 RED：按 tasks 映射逐项失败，禁止 collection error/mock 直接返回目标结果
I1 冻结/身份：request_id/txid、journal pages[]、资源所有权（最小实现）
I2 持锁提交：CAS、backup、provenance、逐页 replace（最小实现）
C3/T1-D：真实底层异常、逆序轨迹、cleanup 再中断、进程退出点与竞争
I3 恢复/cleanup：恢复预检、逆序回滚、幂等 cleanup
I4 真实进程/双进程：三个退出位置 + 竞争
Q1 验收：仓库 298 + 历史 251 + B202 48 + B203 新探针
```

顺序约束：C3/T1-D 的真实故障与进程验证依赖 I1/I2 的最小可运行实现；
没有实现时，只交付对应 RED 资产与夹具，不标行为 GREEN。


---

## 15. 开放问题

```text
无阻塞开放问题。
Q1–Q5 已按评审决策冻结；
若实现中发现与决策冲突，先回到 D1 更新本文档，不直接在代码中暗改边界。
```

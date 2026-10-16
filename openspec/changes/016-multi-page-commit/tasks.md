# Tasks: 016-multi-page-commit（B4-02 B203 多页提交事务）v0.2

spec_ref: llm-wiki/spec.md
状态：D1 设计证据稿；先补契约与测试映射，再进入 RED/实现
前序：B201=accepted；B202=accepted（单页工程范围）
回归基线：仓库 298 / 历史 251 / B202 独立 48（45 + 真实进程 3）

## 0. D1 设计证据（先完成）

- [ ] T001 输出完整 journal v2 示例与字段/类型/摘要语义
- [ ] T002 输出完整 state.build v2 示例与重放校验语义
- [ ] T003 输出提交判定点与状态标签定义（prepared/pages_replaced/committed/recovery_required）
- [ ] T004 输出资源所有权表（state/output/backup/provenance/staging/journal/lock）
- [ ] T005 输出恢复预检判定表（R03 全表）
- [ ] T006 输出 provenance 部分发布与 cleanup 可重入表
- [ ] T007 输出三层身份与 v1/v2 dispatcher 规则
- [ ] T008 输出故障矩阵到测试名映射（本文件 §2）
- [ ] T009 确认 Q1–Q5 决策冻结，不再重新询问
- [ ] T010 确认 B201 只读、B202 单页契约不受扩展破坏
- [ ] T011 输出 request_payload 完整原像与 request_id 重算规则（D01）
- [ ] T012 输出 transaction_id 结构化编码：execution_token 必参与、built_at 不参与（D02）
- [ ] T013 输出 provenance manifest members[] 契约与 cleanup 可重入规则（D03）
- [ ] T014 输出 D04 可区分断言：state 实际 new/old、全资源集不变、部分 backup 清理重试

## 0.1 T1-A 首批执行映射（当前批次）

- [ ] T1A-G01 三页真实 ingest/schema/L1–L8/proof、draft_only、原证据字节未变
- [ ] T1A-G02 有效 baseline、先 clean 后仅 A/B dirty、C 不变、图无诊断
- [ ] T1A-G03 现有 inc.precheck_rebuild 对多页拒绝 multi_page_not_supported 且只读
- [ ] T1A-G04 旧仓库回归 298 保持
- [ ] T1A-R01 两页成功第三页不动 | T201 + T203
- [ ] T1A-R02 候选集合错误 4 参数 | T204
- [ ] T1A-R03 候选原始字节未绑定 | T205
- [ ] T1A-R04 同请求重放 | T232
- [ ] T1A-R05 完整执行字段篡改 9 参数 | T236
- [ ] T1A-R06 父 baseline 变化 | T237
- [ ] T1A-R07 缺失/损坏 state 2 参数 | T238
- [ ] T1A-R08 no_op 与提前返回防护 4 参数 | T248
- [ ] T1A 停止点：不进入 I1，不写生产实现/stub
- [ ] T1A 交付：测试差异、GREEN/旧回归/RED 日志、逐 case 证据表、delivery 文档

## 0.2 T1-A 复评 R01–R04 修订

- [ ] R01 请求身份 oracle 使用权威 scope（非 full=dirty 键），候选集合严格等于 scope；补独立 GREEN
- [ ] R02 GREEN 读取真实 ingest-report.json，保留初始 provenance；补完整 raw/schema 字节核对
- [ ] R03 precheck / commit 分别断言精确诊断；precheck 后立即比较文件与目录集合
- [ ] R04 A01 成功路径核对未改写旧证据、精确登记路径、provenance 归属与必需成员；补空证据负例
- [ ] 重跑 5 GREEN + 旧仓库 298 + 23 RED，记录退出码与分类

## 0.3 T1-A S01–S04 剩余修订

- [ ] S01 快照/白名单/前缀统一 posix 相对路径；补合法/禁止变更 GREEN
- [ ] S02 诊断按 errors 列表精确成员判断；补前缀/后缀不可冒充 GREEN
- [ ] S03 affected_pages 仅缺省为 []，显式 None/''/0/False/{} 严格拒绝
- [ ] S04 provenance 校验绑定预期 raw snapshots；补完整证据正例与缺失/改写负例
- [ ] 重跑 collect/GREEN/旧回归/RED，更新交付

## 0.4 T1-B 测试映射（本轮）

- [ ] T1B-G10 三页身份/顺序 oracle：候选乱序，scope 排序 A/B/C
- [ ] T1B-G11 真实 planner 生成 full / blocked / unknown / removal 字段
- [ ] T1B-G12 provenance 重新封装负例：缺 snapshot / 改 raw / 改来源路径 / 改来源摘要
- [ ] T1B-R01 三页提交顺序：候选乱序，state.build.pages 按 page_key 排序
- [ ] T1B-R02 单页校验失败整计划拒绝：output/state/既有 provenance 字节不变
- [ ] T1B-R03 真实 full/blocked/unknown/removal 入口拒绝
- [ ] T1B 停止点：不进入 I1
- [ ] T1B 交付：场景 → nodeid → 预期 → 实际失败位置映射

## 0.5 T1-C 测试资产批次（C1/C2/C3）

- [ ] C1 合法恢复 fixture：old/new state、output、provenance、backup、journal v2
- [ ] C1 G01–G12 独立断言自检：身份/投影/摘要分离/部分 provenance/路径攻击/无变化断言
- [ ] C1 场景矩阵 RC01–RC16 证据级别标注
- [ ] C2 合法公共 recover 有限组 RED（C1 复评后）
- [ ] C3 真实底层异常命中、逆序轨迹、cleanup 再中断（实现后）
- [ ] C1 停止点：不进入 C2/I1，不改生产代码

## 0.6 治理与顺序契约（governance batch）

- [x] docs/b203-review-protocol.md + docs/b203-backlog.md
- [x] contract-registry.md + contract_manifest.json
- [x] scripts/check_b203_contracts.py（core/compat/old/all/freeze）
- [x] C2-NB01 空父目录判定落库 + 正式测试
- [x] compat 探针 canonical 副本：94 项外部回归
- [x] 回归范围策略：影响矩阵，取消每轮无条件 298
- [x] Reviewer v1.1 约束落库：docs/b203-gpt-review-contract.md
- [x] GOV-R04 登记：registry + manifest + 10 项治理回归
- [x] T1-C2 accepted + C2-NB01 记录（B4 全量验证通过后登记）

Q1 收口与 capability 启用（2026-10-16）：

```text
- Q1 accepted（B203 既定工程与 fixture 验收范围）
- multi_page_commit=true；single_page_commit/bootstrap/removal/runtime_stale 保持 false
- I5 capability/CLI/单页/只读节点复跑：4 + core153 + compat96 + old298
- NB02：proposal/tasks/contract-registry 强制纳入 Git HEAD，保证可恢复
- 新快照 b203-capability-enabled-20261016
```

Q1-R02 完整扫描证据（2026-10-16）：

```text
- clean HEAD 全量 CQ 使用 --timeout 1800，radon/pytest 不再 timeout
- import-linter 无配置 skipped 单列
- 新增 blocker/high 仅来自 B203 测试辅助函数，已拆分 5 个 E/F
- core 153 / compat 96 / old 298 / 历史 251 / B202 48 全绿
- 待最终全量 run 冻结 diff/gate 证据
```

Q1-R01 修复（2026-10-16）：

```text
- _publish 恢复 state replace 已成功执行的 state_replaced 证据
- 异常处理传入 state_replaced；成功提交后即使 state 不可读/committed journal 写失败，
  也必须 committed_cleanup_pending，不回滚 output、不删 journal
- 同时保留 _state_digest 磁盘摘要兜底，保护 C3-R01/T258 的 os.replace 已完成路径
- 新增组合故障正式回归；tests/b203 153 passed
```

Q1 工程清理候选（2026-10-16，待复评）：

```text
- 提交模块 _replay_result / _final_cas / _publish 拆分，radon 无 D/E/F
- 恢复模块 _validate_journal / _recover_one / _cleanup_owned / _provenance_valid 拆分，radon 无 D/E/F
- 新模块 mypy 0 error、vulture 0、ruff check 0
- core 152 / compat 96 / old 298 / B3 72+B4 130+B201 49+B202 48 全绿
- CQ adapters 子集扫描 + baseline diff + gate PASS；radon/pytest 全量编排仍 timeout，未记为零
- multi_page_commit 保持 false，Q1 待独立复评
```

I5 CLI / capability（2026-10-16）：

```text
- app/rebuild_multi.py 增加 precheck / commit / recover 显式 CLI
- precheck 报告写 capability；multi_page_commit 保持 false（Q1/T109 前不启用）
- 单页仍委托 B202；B201 保持只读拒绝 multi
- tests/b203 152 passed
```

NB02-COMPAT-01 旧 writer 兼容（2026-10-16）：

```text
- state.build.provenance.members 缺失视为旧 v2 合法形态
- 仍以 journal members + 磁盘 manifest/成员严格校验为准
- members 存在时必须与 journal 完全一致
- 旧 writer 状态无需迁移即可完成恢复 cleanup
tests/b203 148 passed
```

NB02-R1/R2 复评残留修复（2026-10-16）：

```text
- new state 分支交叉校验 state.build 的 request/transaction/页集合，
  以及每页 provenance dir/manifest_sha256/members 与 journal 完全一致
- provenance manifest 成员校验拒绝重复 path、目录/符号链接/非法路径与类型
- 新增 2 项正式回归；tests/b203 147 passed
```

I12-NB02/NB03/NB04 兼容性收口（2026-10-16）：

```text
NB02：new state 必须全部页 new；provenance manifest canonical hash
      与全部成员实际 bytes 必须匹配，否则 recovery_required；
      新增 state 旧 output / 成员损坏拒绝回归。
NB03：公共 recover 按 journal format_version 委派 v1 到 B202，
      v2 继续走新实现；v1 prepared journal 可恢复。
NB04：非默认 state path 在 precheck/commit 明确拒绝
      state_path_not_supported，不再生成自身无法恢复的 journal。
tests/b203 145 passed；compat 96；旧仓库 298。
```

C3-R01/R02 提交异常路径修复（2026-10-16）：

```text
- 页 output 实际 replace 成功后抛错：按磁盘 hash 识别并逆序回滚，
  或保留 journal/backup 返回 recovery_required
- provenance move 已落地后抛错：按实际目录清理，不再遗漏
- 清理失败不 ignore，保留 journal，可重试
- 新增正式回归 3 项；tests/b203 141 passed
```

C3/T1-D 真实故障与进程验证（2026-10-16）：

```text
tests/b203/test_recovery_faults.py：12 项
  T211–T215 replace/provenance 故障与逆序回滚
  T219–T222 journal/cleanup 失败、pending、重试
  T257 provenance 清理失败重试
  T258 state 实际 new 后异常
  T260 已消费 backup 后重试
tests/b203/test_process_faults.py：5 项
  T341–T343 os._exit 三个退出点
  T344 双进程同请求竞争
  T345 遗留锁显式清理
tests/b203 138 passed；compat 96；旧仓库 298
```

I12-R03 重放互斥修复（2026-10-16）：

```text
重放资源检查在真实持有项目锁期间进行；
持锁调用 _replay_result(lock_held=True)，返回后释放；
调用前已有他人锁继续 lock_busy。
tests/b203 121 passed；compat 96；旧仓库 298。
```

I12-R02/R03/R04 残留修复（2026-10-16）：

```text
最终 CAS 移到 state 草稿之后并绑定冻结候选字节；
重放补 provenance 成员、登记目标、transaction_id 与项目锁边界；
恢复 state 目标独立绑定默认 reports/incremental/state.json。
tests/b203 120 passed；compat 96；旧仓库 298。
```

I12-R01–R05 修复（2026-10-16）：

```text
登记输出保护、staging 后最终 CAS、完整重放校验、
journal 投影/所有权校验、恢复项目锁、journal prepared/pages_replaced/committed 阶段
tests/b203 111 passed；compat 96；旧仓库 298
```

I1/I2 实现状态（2026-10-16）：

```text
app/rebuild_multi.py + app/rebuild_multi_recovery.py
tests/b203 95 passed；compat 96 passed；旧仓库 298 passed
待 GPT 复评；C3/T1-D 真实故障/进程尚未验证
```

顺序契约（覆盖旧收口文字）：

```text
RED 测试资产 → 最小实现（I1/I2）→ C3/T1-D 真实故障与进程验证 → 继续 I3/I4 → Q1 验收
```

真实 crash、双进程、并发行为在没有可运行实现前一律标未验证，不得要求先交行为 GREEN。

## 1. 契约先行

- [ ] T101 确认 build set 规则：0 no_op / 1 B202 / >=2 B203
- [ ] T102 确认 full_required 有条件纳入与 1 页保持 B202 拒绝
- [ ] T103 确认 request_id / execution_token / transaction_id
- [ ] T104 确认 journal format_version=2 与 v1 兼容 dispatcher
- [ ] T105 确认 page_slug 与精确 staging/backup/provenance 路径
- [ ] T106 确认规范化 page_key 固定顺序、逆序回滚
- [ ] T107 确认 state replace 唯一提交判定点
- [ ] T108 确认重放使用统一 load_baseline + 全页资源 hash
- [x] T109 Q1 accepted 后置 capability.multi_page_commit=true

## 2. RED 测试映射

| 编号 | 测试名 | 故障注入位置 | 预期磁盘资源 |
|---|---|---|---|
| T201 | test_multi_happy_two_pages | 无 | 两页 output 新、state 新、journal 清 |
| T202 | test_multi_happy_three_pages_order | 固定顺序 | 两页/三页按序 replace，plan clean |
| T203 | test_multi_unrelated_page_unchanged | 第三方页 sentinel | 未涉及页字节不变 |
| T204 | test_multi_candidate_set_mismatch | 缺页/多页/重复/未知 | reject，零正式写入 |
| T205 | test_multi_candidate_binding_mismatch | 某页不同字节候选 | reject，零正式写入 |
| T206 | test_multi_schema_lint_proof_failure | 第 i 页 staging 失败 | reject，零正式写入 |
| T207 | test_multi_cas_after_staging_candidate | staging 后改 candidate | reject，零正式写入 |
| T208 | test_multi_cas_after_staging_baseline | staging 后改 baseline | reject，零正式写入 |
| T209 | test_multi_cas_after_staging_output | staging 后改 output | reject，零正式写入 |
| T210 | test_multi_graph_uses_all_frozen_candidates | staging 后改活 response | graph/output 同源 |
| T211 | test_multi_first_page_replace_failure | 第 1 页 os.replace 失败 | 全页旧版、state 旧 |
| T212 | test_multi_middle_page_replace_failure | 中间页失败 | 已替换页逆序回滚 |
| T213 | test_multi_last_page_replace_failure | 最后页失败 | 已替换页回滚 |
| T214 | test_multi_state_replace_failure | state os.replace 失败 | 全部 output 回滚 |
| T215 | test_multi_prepared_provenance_partial | 第 i 页 provenance move 失败 | recovery/cleanup，旧版保持 |
| T216 | test_multi_backup_missing_or_corrupt | 需恢复页 backup 缺失/坏 | recovery_required，不覆盖 |
| T217 | test_multi_third_party_output | 某页 output 第三方改写 | recovery_required，保留证据 |
| T218 | test_multi_third_party_provenance | 某页 provenance 第三方改写 | recovery_required |
| T219 | test_multi_committed_journal_failure | state 后 committed journal 写失败 | 完整新版 + committed_cleanup_pending |
| T220 | test_multi_cleanup_backup_failure | backup.unlink 失败 | journal 保留，可重试 |
| T221 | test_multi_cleanup_staging_failure | staging.rmtree 失败 | journal 保留，可重试 |
| T222 | test_multi_rollback_again_failure | 回滚第 i 页后下一页失败 | 保留 journal/backup，可继续 |
| T223 | test_multi_recovery_old_same_hash | old_hash==new_hash | 不写该页，全局看 state |
| T224 | test_multi_recovery_new_output_without_old | old_exists=false | 删除新建 output 并验证不存在 |
| T225 | test_multi_recovery_old_page_missing_backup | 已是 old 页缺 backup | 可继续清理，不误阻塞 |
| T226 | test_multi_recovery_pages_all_old_provenance_published | output 全旧、provenance 部分/全部 | 清理本事务 provenance，可重试 |
| T227 | test_multi_journal_missing_extra_duplicate_page | journal pages[] 坏 | recovery_required |
| T228 | test_multi_journal_path_alias | 大小写/路径别名 | reject/recovery_required |
| T229 | test_multi_journal_staging_owner_root | staging 指向 owner 根 | recovery_required，不删 sentinel |
| T230 | test_multi_journal_staging_sibling | staging 指向同 tx 兄弟 | recovery_required，不删 sentinel |
| T231 | test_multi_journal_state_redirect | journal.state 重定向 | recovery_required，保留新版 |
| T232 | test_multi_replay_all_pages | 合法重复请求 | already_committed，零字节变化 |
| T233 | test_multi_replay_invalid_state | format_version/validated/graph 坏 | 不 already_committed |
| T234 | test_multi_replay_output_tamper | 某页 output 改 | 不 already_committed |
| T235 | test_multi_replay_provenance_tamper | 某页 provenance 改 | 不 already_committed |
| T236 | test_multi_plan_tamper | dirty/observed/full/blocked/affected 改 | reject |
| T237 | test_multi_plan_stale | baseline generation 改 | reject，零正式写入 |
| T238 | test_multi_state_missing_or_corrupt | 缺/坏 state | bootstrap/reject |
| T239 | test_multi_blocked_unknown_removal | blocked/unknown/removal | reject |
| T240 | test_multi_full_required_all_pages | 完整管理页集合 | 可执行 B203 |
| T241 | test_multi_full_required_single_page | full 但 1 页 | 保持 B202 拒绝 |
| T242 | test_multi_lock_busy | 他人持锁 | lock_busy，不改正式文件 |
| T243 | test_multi_concurrent_same_request | 双进程同请求 | 一 committed；另一 lock_busy；重试 already_committed |
| T244 | test_multi_process_exit_after_middle_page | 子进程 os._exit(77) | 新进程恢复完整旧版 |
| T245 | test_multi_process_exit_after_all_pages | 子进程 os._exit(77) | 新进程恢复完整旧版 |
| T246 | test_multi_process_exit_after_state | 子进程 os._exit(77) | 新进程保留完整新版，清 journal；重放 already_committed |
| T247 | test_multi_stale_lock_no_age_steal | 遗留锁 | 不按年龄接管；显式清锁后恢复 |
| T248 | test_multi_zero_page_noop | 权威空 build set | no_op，不推进 |
| T249 | test_multi_zero_page_with_blocker | 空 dirty + blocked/unknown | reject，不 no_op |
| T250 | test_multi_journal_request_payload_missing | journal 无 request_payload | recovery_required |
| T251 | test_multi_journal_request_payload_tamper | 改执行字段原像 | request_id 重算失败 |
| T252 | test_multi_journal_projection_mismatch | pages[] 与 request_payload 不一致 | recovery_required |
| T253 | test_multi_journal_valid_without_live_plan | 新 state + 原 plan 不可用 | journal 单独可验证身份 |
| T254 | test_multi_transaction_id_token_binding | 改 token / txid / 派生路径 | 身份不一致，恢复拒绝 |
| T255 | test_multi_same_request_different_token | 同请求不同执行 token | request_id 相同；txid 不同；合法重放仍按 request_id |
| T256 | test_multi_provenance_partial_members | 仅部分成员落地 | 预检拒绝或可重入清理，不误判第三方 |
| T257 | test_multi_provenance_cleanup_partial | 完整 provenance 删部分成员后中断 | 解除故障后可完成清理，幂等 |
| T258 | test_multi_state_replace_actually_new | state 实际 new 后抛异常 | 重读 state 保留新版 + pending，不回滚 |
| T259 | test_multi_error_after_last_check_preserves_all | 三页最后检查页错误 | output/provenance/backup/journal 全部不变 |
| T260 | test_multi_rollback_cleanup_partial_backup | 完整回滚后部分 backup 已清 | 不索要不需要 backup；journal 可重试 |
| T261 | test_multi_commit_cleanup_partial_backup | 完整提交后部分 backup 已清 | 保留新版；cleanup 可重试 |

## 3. 实现批次

### I1 身份 / journal / 资源所有权
- [x] T301 request_id / execution_token / transaction_id
- [x] T302 journal v2 完整 schema 与校验
- [x] T303 v1/v2 dispatcher、旧 journal 兼容（NB03 已实现 v1 委派）
- [x] T304 page_slug、精确 staging/backup/provenance
- [x] T305 跨页路径冲突与登记目标校验

### I2 冻结 / CAS / 提交
- [x] T311 多页候选冻结与完整 staging
- [x] T312 全部冻结候选构造 graph
- [x] T313 锁内最终 CAS
- [x] T314 逐页 backup 验证
- [x] T315 prepared journal 持久化
- [x] T316 逐页 provenance 落地 + hash
- [x] T317 固定顺序逐页 replace
- [x] T318 pages_replaced
- [x] T319 state replace
- [x] T320 committed

### I3 恢复 / cleanup
- [x] T331 恢复全计划预检
- [x] T332 逆序逐页回滚
- [x] T333 old==new / old_exists=false 分支
- [x] T334 provenance 部分发布清理
- [x] T335 cleanup 幂等、journal 最后
- [x] T336 recovery_required / committed_cleanup_pending 状态机
- [x] T337 恢复中再次中断可继续

### I4 真实进程
- [x] T341 中间页 replace 后 os._exit
- [x] T342 全部页 replace 后 os._exit
- [x] T343 state 后 os._exit
- [x] T344 双进程同请求竞争
- [x] T345 遗留锁处理（显式人工清锁，不自动按年龄）

### I5 CLI / capability
- [x] T351 B203 显式执行入口
- [x] T352 capability.multi_page_commit
- [x] T353 单页仍走 B202
- [x] T354 B201 保持只读拒绝

## 4. 验收

- [ ] T401 仓库 298+ 不退化
- [ ] T402 历史 251 不退化（B3 72 + B4/B201 179）
- [ ] T403 B202 48 不退化（45 + 进程 3）
- [ ] T404 B203 T201–T261 全绿
- [ ] T405 I4 真实进程/双进程全绿
- [ ] T406 新模块 radon 无 D/E/F
- [ ] T407 mypy/vulture 有可核验基线 diff，不能只看总数
- [ ] T408 CQ clean HEAD、扫描范围含实现模块
- [ ] T409 import-linter skipped 单列
- [ ] T410 不自动 accepted/published
- [ ] T411 B201/B202 契约不退化
- [ ] T412 未交付：删除 / runtime stale / 字段级 / 真实 raw

## 5. 后续

- [ ] B204 删除页面 / removed_pages 墓碑
- [ ] B205 runtime stale / index freshness token
- [ ] B206 字段级重算
- [ ] B207 真实 raw

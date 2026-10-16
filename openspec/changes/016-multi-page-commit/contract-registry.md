# B203 契约登记表（governance registry）

来源：GPT 自审基线 JSON（snapshot_id=B203-review-audit-20261015-selected）与 DeepSeek 自查。
生产状态：I1/I2 已 accepted；C3/T1-D 真实故障与进程验证已落地，待复评。

## 状态

| 批次 | 状态 |
|---|---|
| T1-A | accepted（测试资产） |
| T1-B | accepted（测试资产） |
| T1-C1 | accepted（恢复 fixture 与独立断言） |
| T1-C2 | accepted（有限组 RED 测试资产；C2-NB01 已落库，compat 96 通过） |
| 治理批次 | accepted（GOV-R01–R04；Reviewer v1.1 已落库） |
| I1/I2 | accepted（最小实现范围；tests/b203 121 passed） |
| C3/T1-D | accepted（C3-R01/R02 已复评关闭） |
| I3 兼容性收口 | tested（NB02-COMPAT-01 修复后 tests/b203 148 passed；待复评） |
| I5 CLI/capability | accepted（独立复评通过） |
| Q1 工程清理 | accepted（Q1-R01/R02 已关闭） |
| capability | multi_page_commit=true（Q1 accepted 后启用）；其余能力保持 false |

## 治理 gate 契约

| contract_id | 含义 | 消费者 | 正例 | 反例 |
|---|---|---|---|---|
| GOV-R01 | core/compat 用 JUnit 逐 nodeid 集合校验，计数只是摘要；GREEN/RED 精确分区 collect | scripts/check_b203_contracts.py | core/compat PASS | test_governance_gate_contract 10 项 |
| GOV-R02 | compat 探针从自身位置派生当前 checkout | tests/b203/compat | probe_portability 2 项 | 源码不得含固定 Desktop 路径 |
| GOV-R03 | snapshot 缺失/空/自指/重复 ID 拒绝；具名历史链存在性、身份、parent 校验 | snapshots/ + check freeze | selected → r2 → r3 → r4 | validate_snapshot / validate_history_chain / plan_freeze_parent 负例 |
| GOV-R04 | Reviewer v1.1 输出约束：输入绑定、五字段、blocking 判定、证据与冲突规则 | docs/b203-gpt-review-contract.md | Reviewer 正式复评 | test_governance_gate_contract reviewer contract 检查 | 无新反例不得新增 blocking |

## 契约索引

| contract_id | 含义 | 来源 | 消费者 | 正例 nodeid | 变异/反例 nodeid |
|---|---|---|---|---|---|
| B203-BUILD-OUTPUT-SHA256 | build.pages[].output 用 sha256；journal output 用 new_sha256 | proposal §5 | support.assert_multi_success、recovery_support | tests/b203/test_multi_entry_contract.py::test_multi_happy_two_pages | tests/b203/compat/probe_residual_c1.py::test_build_output_matches_proposal_sha256_field |
| B203-GRAPH-JSON-COMPARISON | 图比较经 support.C，保留 generation 排除 | T1-B 图比较 patch | 两页/三页成功测试 | tests/b203/test_multi_entry_contract.py::test_multi_happy_two_pages | tests/b203/test_t1b_contract.py::test_t1b_three_page_order_entry（历史: work/b203-t1b-residual-review/test_closure.py） |
| B203-RESEALED-JOURNAL-OWNERSHIP | 重封装身份不得授权 owner-root / sibling-token / output 重定向 | proposal | validate_journal_projection | tests/b203/test_recovery_fixture_contract.py::test_c1_g01_legal_master_fixture | tests/b203/test_recovery_fixture_contract.py::test_c1_g15_resealed_journal_ownership；tests/b203/compat/probe_g10_closure.py |
| B203-CLEANUP-FOREIGN-RESOURCES | 精确清理 owned；保护 inputs/旧证据/兄弟 token/其他事务 | proposal | _assert_ok | tests/b203/compat/probe_legacy_adapted_c2.py::test_success_assertion_accepts_preserved_sibling_token | tests/b203/compat/probe_c2_residual.py::test_old_success_assertion_rejects_out_of_scope_resources |
| B203-CLEANUP-EMPTY-PARENTS | 空允许父目录可删可留；受保护后代必须保留（C2-NB01，已落库） | C2-R2 修订单 | _assert_ok/_assert_cleanup_directories | tests/b203/test_recovery_entry_contract.py::test_c2_g_optional_empty_cleanup_parents | tests/b203/compat/probe_c2_residual.py::test_old_success_assertion_rejects_out_of_scope_resources[unexpected_directory] |
| B203-RECOVER-RESULT-CONTRACT | v2 recover 状态码/精确诊断表 | proposal §6.2 | 未来 app.rebuild_multi | pending_implementation | tests/b203/test_recovery_entry_contract.py::test_c2_public_recover |
| B203-RED-CLASSIFICATION | 仅 ModuleNotFoundError.name==app.rebuild_multi 归 B203_API_MISSING | C2 约束 | load_recover_api | tests/b203/compat/probe_legacy_adapted_c2.py::test_loader_classification[present] | tests/b203/compat/probe_legacy_adapted_c2.py::test_loader_classification[missing_api] |
| B203-PREPARE-BEFORE-IMPORT | 物化后磁盘场景校验先于 API 导入 | C2 R01 | _prepare/_assert_prepared | tests/b203/test_recovery_entry_contract.py::test_c2_public_recover | tests/b203/compat/probe_c2_residual.py::test_prepare_validates_materialized_scenario |
| B203-REJECTION-DIAGNOSTICS | errors 必须 list[str] 非空且含精确成员 | C2 R02 | _assert_recovery_required | tests/b203/compat/probe_c2_residual.py::test_exact_diagnostics_and_rejection_preservation[correct] | tests/b203/compat/probe_c2_residual.py::test_exact_diagnostics_and_rejection_preservation[wrong_basename] |
| B203-SUCCESS-RESOURCE-ORACLE | 成功目标严格等于独立期望文件/目录集合 | C2 R03 | _assert_ok | tests/b203/compat/probe_legacy_adapted_c2.py::test_success_assertion_accepts_preserved_sibling_token | tests/b203/compat/probe_c2_residual.py::test_new_success_assertion_rejects_cleanup_gaps |

## 证据与运行范围

- 每轮 core：hash 核对 + collect-only + GREEN 选择 + RED 分类
- 相关集：受影响契约的正/负例 + helper 全部本地消费者
- 阶段末/合并前：旧仓库 298
- compat 探针 canonical 副本：tests/b203/compat/，原始来源与 hash 见 compat/README.md

## 顺序契约

```text
RED 测试资产 → I1/I2 最小实现 → C3/T1-D 真实故障与进程验证 → I3/I4 → Q1
```

在公共 recover 实现存在前，任何真实故障、进程、并发行为都不标行为 GREEN。

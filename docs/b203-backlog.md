# B203 非阻断 backlog

原则：backlog 项不阻塞当前批次；进入实现或转 GREEN 前按 deadline 处理。

| ID | 内容 | 级别 | deadline | 状态 |
|---|---|---|---|---|
| C2-NB01 | 空父目录合法正例误拒绝（_assert_cleanup_directories） | P2 | 公共 recover 转 GREEN 前 | 已落库（test_c2_g_optional_empty_cleanup_parents 3 参数通过） |
| B-CLEANUP-01 | `_write_manifest` 嵌套同名 manifest.json 排除逻辑 | P3 | 下一次同文件修改 | 待处理 |
| B-DEAD-01 | G11 `if False else case` 死分支 | P3 | 顺手清理 | 待处理 |
| B-GRAPH-01 | tuple/list 图比较两行修正 | P3 | 已修 | 已关闭（T1-B 收口） |
| C1-G10-01 | G10 三攻击正式入库 | P2 | 已随 C1 收口入库 | 已关闭 |
| RC10-01 | RC10 journal status committed 标签 | P3 | 已修 | 已关闭 |
| PROTO-01 | 取消每轮无条件旧仓库 298 | P2 | 已生效 | 已关闭（治理批次） |

规则：

```text
- 新增 backlog 项必须带 ID、来源轮次、最小反例、deadline
- 解决时给出 commit/nodeid 和运行证据，不写“已修”了事
```


## 治理复评关闭项（2026-10-15）

| ID | 内容 | 级别 | 状态 |
|---|---|---|---|
| GOV-R01 | gate 结果分类与覆盖门禁改为 JUnit 逐 nodeid 集合校验，并校验 GREEN/RED 恰好分区 collect | P1 | 已关闭（test_governance_gate_contract 10 项） |
| GOV-R02 | compat 探针从自身位置派生 checkout，并加来源正/负例 | P1 | 已关闭（probe_portability 2 项） |
| GOV-R03 | snapshot 缺失/空/自指/重复拒绝；具名历史链存在性、身份、parent 校验 | P2 | 已关闭（snapshot 链 selected → r2 → r3 → r4） |
| GOV-NB02 | manifest 历史引用与可执行 nodeid 分离 | P2 | 已修正 |
| GOV-NB03 | freeze 排除 __pycache__/*.pyc | P3 | 已关闭 |
| GOV-R04 | Reviewer v1.1 约束落库并登记 | P1 | 已关闭（docs/b203-gpt-review-contract.md + registry/manifest + 10 项治理回归） |


## I1/I2 复评非阻断项（2026-10-16）

| ID | 内容 | 级别 | deadline | 状态 |
|---|---|---|---|---|
| I12-NB01 | journal prepared 前 backup 顺序与阶段写入 | P2 | 已修 | 已关闭（prepared/pages_replaced/committed 阶段已写） |
| I12-R02-R04残留 | 最终 CAS 位置、冻结候选绑定、重放资源/身份/锁、恢复 state 独立绑定 | P1 | 已修 | 已关闭（正式回归 9 项） |
| I12-NB02 | 新 state 搭配旧 output / 损坏 provenance 成员仍需完整恢复预检 | P1 | I3 收口 / Q1 前 | 已知，未关闭 |
| I12-NB03 | v1 journal dispatcher 缺失；公共 recover 误拒 B202 v1 prepared journal | P2 | 统一恢复兼容验收前 | 已知，未关闭 |


## I1/I2 第三轮复评非阻断项（2026-10-16）

| ID | 内容 | 级别 | deadline | 状态 |
|---|---|---|---|---|
| I12-NB04 | 提交接受自定义 state，但恢复默认绑定 STATE_REL，导致自身 journal 被恢复拒绝 | P2 | 统一恢复兼容验收 / I3 收口前明确支持范围 | 已关闭（非默认 state path 预检明确拒绝） |
| I12-NB02 | 新 state 搭配旧 output / 损坏 provenance 成员 / 重封装 provenance | P1 | I3 收口 / Q1 / 正式使用前 | 已关闭（state.build 交叉绑定 + 全成员/重复 path 校验，R1/R2） |
| I12-NB03 | v1 journal dispatcher；T303 完成标记已改为未完成 | P2 | 统一恢复兼容验收前 | 已关闭（按格式版本委派 B202） |


## NB02 兼容性补充（2026-10-16）

| ID | 内容 | 级别 | deadline | 状态 |
|---|---|---|---|---|
| NB02-COMPAT-01 | 旧 v2 writer 的 state.build 缺少 members 时被误拒 | P2 | Q1 / 启用前 | 已关闭（缺失 members 兼容，仍以 journal+manifest 严格校验） |

# B203 Worker / Reviewer 每轮协作协议

生效范围：B4-02 B203 及其测试资产。本协议是治理约束，不替代 openspec proposal。

## 1. 每轮 Reviewer 输出五字段

Reviewer 每轮只输出 delta，不逐轮复述完整历史：

```text
Delta:
  相对哪个 delivery/hash；新增/关闭/仍有的 finding_id

Blocking / Non-blocking:
  每项理由、具体坏实现/诊断/资源会通过的最小反例、
  复现结果、退出条件、backlog 期限

Affected accepted contracts:
  contract_id、消费者、是否兼容

Rerun evidence:
  run_id、命令、nodeid、JUnit/日志、nodeid 结果迁移

Unverified:
  未运行、受 API 缺失遮挡、历史证据、其他盲区
```

## 2. Blocking 判定规则

- 只有能说明“一个具体错误实现、错误诊断或错误资源状态会通过验收”的项才是 blocking。
- 风格、报告格式、编号、固定测试数量、合法边角正例的单一误拒绝，默认 non-blocking。
- P1/P2/P3 是影响程度；blocking/non-blocking 是当前批次推进决定，两者不机械绑定。
- 无证据的项目标记 suspected，不得写成已证实。

## 3. Worker / Reviewer 职责

| 规则 | Worker 交付 | Reviewer 动作 |
|---|---|---|
| 冻结输入 | round_id、parent_round、HEAD、完整 status、old/new 内容或 patch、改动文件 hash | 报告绑定同一 snapshot_id |
| 明确范围 | finding_id → diff → 新增/复用 nodeid，只列本轮 delta | 不追加无编号/来源/反例的新门槛 |
| Blocking | 冻结契约 + 最小反例 + 复现 + 关闭条件 | 分类独立于 P 等级；suspected 不冒充已证实 |
| 正例保护 | 每个新增断言至少一个合法成功目标 | 确认正例先过、单点负例被指定断言捕获 |
| 冲突处理 | 列受影响 accepted 字段/路径/返回值与消费者 | 不自动改旧 expected；先归因再定协议变更 |
| 兼容矩阵 | 更新受影响行，保留未受影响行与历史 run | 共享 helper 变化列出全部本地消费者 |
| hash 基线 | 新 manifest 引用上一轮，不覆盖旧 snapshot | 新快照验收后才晋升 baseline |
| 运行证据 | collect、命令、退出码、nodeid/JUnit | 分清 pass / API_MISSING / fail / error / skip |
| 运行范围 | 影响矩阵选相关集；共享或生产改动扩大 | 阶段末/合并前跑完整回归 |
| 收口 | 交付后冻结该轮 | 无 blocking 则 accepted 该范围；non-blocking 进 backlog |

## 4. 回归范围策略

```text
默认：core = hash 核对 + collect-only + GREEN 选择 + RED 分类
相关集：本轮改动 helper 的全部本地消费者 + 受影响 contracts 的正/负例
扩展集：共享 helper 或 app/ 生产改动时，追加 B201/B202 相关回归
阶段末 / 合并前：完整旧仓库回归（298）
取消：每轮无条件跑旧仓库 298
```

## 5. 冲突与停止裁决

只有以下情况暂停相关工作并请用户裁决：

1. 新请求与已 accepted 公共行为不能同时满足，且需要改变消费者兼容性、持久格式或破坏既有数据；
   纯测试规范化、修正明显错误 oracle 不属于此类。
2. 两份权威契约互相矛盾，且现有用户指令不能决定优先级。
3. 继续必须动用本轮未授权的正式数据写入、删除、发布、外发或不可逆迁移。
4. 证据快照不一致且无法确定验收版本，或关键材料不可访问导致 blocking 无法确认。

## 6. 已关闭项重开规则

- 沿用原 finding_id。
- 只有出现新 diff 或新的可复现反例才能重开，并写明 reopen 依据。
- 不接受“再确认一次”“换个写法”作为重开理由。

## 7. 证据要求

```text
- 未运行项必须写“未运行”，不得写“无退化”
- 缓存证据必须标注来源版本 hash
- 未动文件 hash 相同只证明文件相同，不等于行为兼容
- API_MISSING 不得掩盖 fixture/依赖/装配错误
- 修改测试文件后必须先 collect-only，核对 nodeid 迁移
```

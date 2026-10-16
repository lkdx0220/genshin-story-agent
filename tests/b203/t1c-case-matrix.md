# T1-C 恢复场景矩阵（C1 测试资产）

状态：C1 夹具与断言就绪；生产未实现，真实恢复/故障/逆序行为未验证。
缩写：O=保存旧 bytes/明确不存在；N=预期新 bytes；X=第三方值；P=完整/部分 provenance；B=backup。

| scenario_id | T编号 | state | A/B/C output | provenance | backup | journal | 恢复目标/不得发生 | 允许写删 | 必须保留 | C1 证据级别 |
|---|---|---|---|---|---|---|---|---|---|---|
| RC01 | T212/T222 | O | N,N,O | 完整 | 所需好 | prepared | 回滚 B,A；state 不变；清本 tx 残留 | B/A output、本 tx provenance、staging、backup、journal | C output、旧证据、state | planned |
| RC02 | T214 | O | N,N,N | 完整 | 好 | prepared | 回滚 C,B,A；不能报 committed | output、本 tx provenance、staging、backup、journal | 旧证据、state | planned |
| RC03 | T253/T261 | N | N,N,N | 完整 | 好 | prepared/committed | 保留新版；只清临时资源 | staging、backup、journal | 新 state/output/provenance | planned |
| RC04 | T226/T256/T257 | O | O,O,O | 部分 | 好 | prepared | 不写 output；清可验证本 tx provenance | 本 tx provenance、staging、backup、journal | state、旧证据 | planned |
| RC05 | T216 | O | N,N,N | 完整 | 需回滚页缺/坏 | prepared | recovery_required；全资源保留 | 无 | 全部 | planned |
| RC06 | T217/T218/T259 | O | O,O,X 或 provenance X | 第三方 | 好 | prepared | recovery_required；不得先改前两页 | 无 | 全部 | planned |
| RC07 | T223 | O | 含 old==new 页 | 完整 | 好 | prepared | 相等页不写；其余逆序回滚 | 需回滚 output、本 tx provenance、staging、backup、journal | 相等页 state | fixture_ready（G08 conservative_full） |
| RC08 | T224 | O | old_exists=false 且 N | 完整 | null | prepared | 删除本 tx 新建 output；验证不存在 | 新建 output、本 tx provenance、staging、journal | state、旧证据 | fixture_ready（G08） |
| RC09 | T225/T260 | O | O,O,O | 完整 | 部分已删 | prepared | 不索要无用 backup，继续清理 | 本 tx provenance、staging、剩余 backup、journal | state、旧证据 | planned |
| RC10 | T261 | N | N,N,N | 完整 | 部分已删 | committed | 保留新版；继续清理 | staging、剩余 backup、journal | 新 state/output/provenance | planned |
| RC11 | T256 | N | 缺 output/成员 | 完整/缺成员 | 好 | prepared/committed | recovery_required；不自动回滚 | 无 | 全部 | planned |
| RC12 | T227/T250-T252 | O/N | 任意 | 任意 | 任意 | journal 坏 | 身份/投影精确拒绝；引用资源不动 | 无 | 全部 | planned |
| RC13 | T228-T231/T254 | 任意 | 任意 | 任意 | 任意 | token/txid/路径攻击 | 身份/所有权精确拒绝；sentinel 不动 | 无 | 全部 | planned |
| RC14 | T253 | N | N,N,N | 完整 | 好 | prepared | 原 plan 删除后 journal 仍可验证；登记可读 | staging、backup、journal | 新 state/output/provenance | planned |
| RC15 | T255 | 任意 | 任意 | 任意 | 任意 | 两 token 合法材料 | request_id 同，txid/staging 不同 | 无 | 全部 | fixture_ready（G03） |
| RC16 | T258 | 实际 N | N,N,N | 完整 | 好 | prepared | 保留新版 + committed_cleanup_pending | staging、backup、journal | state/output/provenance | planned |

说明：

```text
planned：只有映射，未构造夹具。
fixture_ready：C1 已构造合法材料并有独立断言自检。
red_api_missing：入口待实现，后置断言未执行。
fault_hit：真实异常边界命中（C3）。
behavior_verified：真实入口结果与全部资源断言通过（C2/C3 后）。
```

C1 强制完成 G01–G12；RC01–RC16 不要求全部 fixture_ready。

# M2-02a FIX-01：营业例外完整性前置判断

基线：`d656c01f5713d48c3b7662dfadad2eb3db6582c5`。分支：`codex/m2-02a-opening-feasibility`。本次仅修改独立 F02 Domain 规则、相关测试及说明；完成后等待 FIX-01 复验。

## 修复内容

`exceptions_complete` 的正式语义为：是否已完整掌握该事实 `applicable_scope` 内判断所需的日期例外。它不是来源核验状态，也不表示完整掌握所有真实营业情况。

旧实现在周常规则已输出 FAIL 后才追加例外不完整的 UNKNOWN；周常关闭分支甚至直接提前返回。因此 FAIL 优先聚合放大了一个前提不足的判断。本次把完整性判断移到目标日期例外解析之后、周常规则求值之前。

- 目标日期有明确、适用且未过期的 OVERRIDE：采用完整替代日规则，允许检查明确关闭、调整后的窗口和停止入场；其他日期的例外不完整不影响该目标例外。
- 目标日期有 UNRESOLVED：返回 UNKNOWN / unresolved_exception，不回退周常。
- 无目标日期例外且 `exceptions_complete=false`：返回 UNKNOWN / exceptions_incomplete，保留 fact_id；不继续依据周常日计算关闭、窗口、入场、最短游览或预约结论。
- 多来源中存在上述未确定日规则时，营业相关检查保守停止，不通过另一个来源升级 PASS 或 FAIL；可确定的多来源日规则冲突仍为 UNKNOWN / source_conflict。
- 例外完整时，周常日正常生效。独立时间倒序、时区错误、身份绑定错误的 FAIL 以及不同活动间 FAIL > UNKNOWN 聚合保持不变。

规则版本更新为 `F02-synthetic-v1-fix01`，输入/输出结构版本不变。Schema 和报告记录新的规则版本；原版报告按历史产物保留，不自动改写或重新赋予含义。来源的 synthetic / SYNTHETIC_UNVERIFIED、权限字段及不能进入真实行程/确认快照的限制均未改变。

## 反例与回归

| 输入 | 修复后结果 |
|---|---|
| 周常关闭，无目标例外，例外不完整 | UNKNOWN，保留 opening_a，不输出 closed_on_date |
| 活动超出周常窗口，例外不完整 | UNKNOWN，不输出 outside_opening_windows |
| 晚于周常停止入场，例外不完整 | UNKNOWN，不输出 after_last_entry |
| 周常最短游览/预约条件不满足，例外不完整 | UNKNOWN，不将可被日期例外替换的限制当作确定性失败 |
| 目标日明确 OVERRIDE 关闭，其他例外不完整 | FAIL / closed_on_date |
| 目标日 OVERRIDE 调整窗口/停止入场，活动冲突 | FAIL / outside_opening_windows 或 after_last_entry |
| 周常关闭，但目标日明确 OVERRIDE 开放且活动符合 | PASS，仅 synthetic F02 |
| 例外完整，周常关闭 | FAIL / closed_on_date |
| 目标日 UNRESOLVED | UNKNOWN / unresolved_exception |
| 只有另一天的 OVERRIDE，目标日例外资料不完整 | UNKNOWN / exceptions_incomplete |
| 非法时间与不完整营业资料并存 | FAIL；跨活动的 UNKNOWN 仍保留 |
| 开放/关闭来源矛盾，其中任一来源例外不完整 | UNKNOWN，不因来源排列或选择而升级；完整来源互相冲突也保持 UNKNOWN |

原 `test_unknown_exception_coverage_cannot_hide_known_gap` 保留并修正：其“已知 gap”实际仅为周常模板缺口，断言改为 UNKNOWN、不得输出窗口冲突 FAIL，并检查 fact_id。新增 22 个参数化反例/边界实例，不删原有测试。

首次增加反例后，在修复前规则上运行得到 **13 failed / 235 passed**，证明旧规则确实被反例击中；之后完善了调整窗口和多来源双向完整性覆盖。最终全部 **250 passed（0.34 秒）**：F02 **120 项**，原 M2-01a/c 地点 **130 项**。不是仅以测试数量验收；上表同时解释 UNKNOWN 和确定性 FAIL 的事实依据。

## 验证产物

- [最终离线测试原始日志](离线测试原始日志.txt)
- [最终无外网调用证据](无外网调用证据.json)：DNS/TCP/UDP 尝试均为 0；守卫自测各阻断一次，212 次标准库 asyncio 本机唤醒单独统计
- [修复前反例原始日志](修复前反例/离线测试原始日志.txt)及[当次网络守卫记录](修复前反例/无外网调用证据.json)
- [当前契约 Schema](F02契约Schema.json)、[当前规则的报告示例](ValidationReport示例.json)
- [源码与契约检查原始日志](契约核查原始日志.txt)、[FIX-01 范围核查](FIX-01范围核查.json)
- [原 M1 基线保护检查](基线与契约核查.json)：复用原检查器对 M2-01c 基线做保护；FIX-01 范围核查另以 d656c01 为基线限定本次既有文件改动

仅一个 runtime 文件 `easytravel/backend/domain/opening_feasibility.py` 改动。Application、正式 M1 业务、数据库、预算、确认快照、迁移、旧 Spike、地点适配器和供应商门禁未变，也没有将 F02 接入正式流程。因此按本轮授权，**不重跑 454 项后端数据库回归**，沿用基线中的 [454 项原始日志](../后端454项回归.txt) 和 [数据库隔离记录](../数据库隔离.json)。本次未启动 Docker、未访问或写入数据库；此前证据未改写。

复现最终验证（仓库根目录，不需数据库、Key 或公网）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_m202a_fix01.py offline
.\.venv\Scripts\python.exe scripts/verify_m202a_fix01.py contract
```

包装脚本复用原离线网络守卫及契约导出器，将输出定向至 FIX-01，不覆盖首轮证据。`before` 模式仅用于开发时保存修复前观测；在修复后执行不会还原旧代码，勿用其覆盖已归档反例日志。

F01、F03～F08 仍未在本阶段实现，F02 PASS 不表示整体行程 READY。未调用真实供应商、未启动 M2-02b；停止等待 FIX-01 复验。

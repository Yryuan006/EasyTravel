# M2-02a：离线营业时间与活动可行性校验

基线：`c822490b02c9eac07456ddc3d02dc4cd4a4a8307`。核查日期：2026-11-18（Asia/Shanghai）。本阶段仅为合成事实的独立 F02 校验，等待 Review。

验证结果：新增 F02 **98 项**、原有地点 **130 项**离线测试通过（合计 228 项）；原有后端 **454 项**隔离回归通过。离线测试 DNS/TCP/UDP 请求尝试均为 0。

- [实施报告与设计对应表](实施与验收报告.md)
- [Domain 契约与确定性规则](../../easytravel/backend/domain/opening_feasibility.py)
- [离线 Application 入口](../../easytravel/backend/application/opening_validation.py)
- [完全虚构夹具](../../tests/feasibility/fixtures/opening_fabricated.json)
- [行为与边界测试](../../tests/feasibility/test_opening_feasibility.py)
- [完整输入及 ValidationReport 示例](ValidationReport示例.json)
- [严格 JSON Schema](F02契约Schema.json)
- [离线测试原始日志](离线测试原始日志.txt)、[网络守卫证据](无外网调用证据.json)
- [原有 454 项回归原始日志](后端454项回归.txt)、[数据库隔离证据](数据库隔离.json)、[环境恢复记录](回归环境说明.json)
- [基线与契约核查](基线与契约核查.json)

复现入口为仓库根目录的 `scripts/verify_m202a_offline.py`、`scripts/verify_m202a_contract.py`、`scripts/verify_m202a_regression.py`。第三个脚本需要 Docker 及已验收隔离门禁条件，每次创建新测试库；前两个无需数据库、密钥或公网。

没有真实营业数据采集、真实 API、正式生成/确认集成或数据库迁移。F02 PASS 只表示该合成输入下未发现本规则覆盖的冲突；F01、F03～F08 未在本阶段实现，整趟行程不标 READY。完成后停止，不启动 M2-02b。

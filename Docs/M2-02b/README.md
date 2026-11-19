# M2-02b：离线跨地点时间可行性校验 F03

日期：2026-11-19（Asia/Shanghai）。开始时 HEAD：`b68531b417570d257ec3da5be97f95be9e295b36`，`git status --short` 为空。该基线包含已验收的 M2-02a FIX-01。本轮仅新增独立 Domain/Application、夹具、测试及交付文件；完成后停止，等待架构 Review。

新增 F03 **85 项通过**；原 F02 FIX-01 **120 项**和地点 M2-01a/c **130 项**，共 **250 项离线回归通过**。两组均在进程级 DNS/TCP/UDP 阻断守卫下执行，测试请求尝试均为 0。守卫自测分别阻断一次 DNS/TCP/UDP；回归另计标准库 asyncio socketpair 本机唤醒 212 次，F03 组为 0。

- [Domain 源码](../../easytravel/backend/domain/travel_feasibility.py)、[Application 源码](../../easytravel/backend/application/travel_validation.py)
- [完全虚构夹具](../../tests/feasibility/fixtures/travel_fabricated.json)、[85 项合成测试](../../tests/feasibility/test_travel_feasibility.py)
- [10 个严格 Schema](F03契约Schema.json)、[6 份完整输入与报告示例](ValidationReport示例.json)
- [新增测试原始日志](new/离线测试原始日志.txt)、[新增测试网络证据](new/无外网调用证据.json)
- [250 项回归原始日志](regression/离线测试原始日志.txt)、[回归网络证据](regression/无外网调用证据.json)
- [基线与契约核查](基线与契约核查.json)、[契约核查原始日志](契约核查原始日志.txt)
- [实施说明、M2-00 对应表及未实现能力](实施与验收报告.md)

本次 **未重跑 454 项后端数据库回归**，沿用 [M2-02a 已验收原始日志](../M2-02a/后端454项回归.txt) 和 [数据库隔离记录](../M2-02a/数据库隔离.json)。本轮未启动 Docker、未访问数据库，既有正式 M1、PostgreSQL、授权、预算、ConfirmedSnapshot、旧 Spike 和应用迁移均未改动。未改写以前的验证证据。

复现（仓库根目录，无密钥、数据库或公网）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_m202b_offline.py new
.\.venv\Scripts\python.exe scripts/verify_m202b_offline.py regression
.\.venv\Scripts\python.exe scripts/verify_m202b_contract.py
```

最终 Commit 和提交后工作区状态见交付回复（避免文档自包含 Commit Hash 造成递归）。**停止于 M2-02b，等待 Review；未进入 F04 或真实交通服务。**

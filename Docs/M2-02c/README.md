# M2-02c：离线公共交通衔接 F04

日期：2026-11-19（Asia/Shanghai）。授权基线及开始 HEAD：`532db3adf1319b91f96217e52fa22ea2d3c8ea50`；开始工作区干净。M2-02b 已验收关闭，本次仅新增 F04 文件。完成后停止，等待架构 Review。

新增 **128 项 F04 测试通过**；原 **335 项离线回归通过**（F02 FIX-01 120 + F03 85 + 地点 130）。两组均启用进程级 DNS/TCP/UDP 阻断守卫，自测各阻断一次，测试期间尝试均为 0。新增 F04 组本机唤醒 0，原回归标准库 asyncio 本机唤醒 212 次单独统计。

- [Domain 源码](../../easytravel/backend/domain/transit_feasibility.py)、[Application 源码](../../easytravel/backend/application/transit_validation.py)
- [完全虚构夹具](../../tests/feasibility/fixtures/transit_fabricated.json)、[128 项测试](../../tests/feasibility/test_transit_feasibility.py)
- [12 个严格 Schema](F04契约Schema.json)、[13 个完整输入及报告示例](ValidationReport示例.json)
- [新增测试原始日志](new/离线测试原始日志.txt)、[新增测试网络证据](new/无外网调用证据.json)
- [335 项回归原始日志](regression/离线测试原始日志.txt)、[回归网络证据](regression/无外网调用证据.json)
- [契约核查原始日志](契约核查原始日志.txt)、[基线与边界核查](基线与边界核查.json)
- [规则、M2-00 对应、F03/F04 边界及后续前置条件](实施与验收报告.md)

本次 **没有重新执行 454 项数据库回归**，沿用 [M2-02a 已验收日志](../M2-02a/后端454项回归.txt) 和 [数据库隔离记录](../M2-02a/数据库隔离.json)。理由：正式 M1、PostgreSQL、预算、授权、确认、历史快照、001～007 迁移及旧 Spike 均未修改，无生产接线。本轮未启动 Docker、未访问数据库，未改写已有交付证据。

复现（仓库根目录，无密钥、数据库或公网）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_m202c_offline.py new
.\.venv\Scripts\python.exe scripts/verify_m202c_offline.py regression
.\.venv\Scripts\python.exe scripts/verify_m202c_contract.py
```

规则版本 `F04-synthetic-v1`，政策版本 `F04-explicit-walk-plus-buffer-seconds-v1`。最终 Git Commit、分支及提交后工作区状态见交付回复，避免 Commit 自包含递归。

**F04 PASS 只表示给定合成班次及政策下的衔接算术通过，不表示真实运营可用、F03 通过或整趟 READY。停止于 M2-02c，等待架构 Review，不启动真实公共交通 API 或 F05～F08。**

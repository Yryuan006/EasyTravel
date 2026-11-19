# M2-02d：离线酒店入住、退房与住宿覆盖 F05

日期：2026-11-19（Asia/Shanghai）。授权基线与开始 HEAD：`40ce99c3aec1e107e9bee8758e13fb1aa431e654`；开始工作区干净。已核对 M2-00《04_可行性校验》和 F02 FIX-01、F03、F04 契约。本轮仅新增独立 F05 文件，完成后停止等待 Review。

新增 F05 **105 项通过**；原有 **463 项离线回归通过**（F02 120、F03 85、F04 128、地点 130）。两组 DNS/TCP/UDP 阻断守卫自检通过，测试期间请求尝试均为 0；标准库 asyncio 本机唤醒分别为 0/212，单独记录。

- [Domain 源码](../../easytravel/backend/domain/hotel_feasibility.py)、[Application 源码](../../easytravel/backend/application/hotel_validation.py)
- [完全虚构夹具](../../tests/feasibility/fixtures/hotel_fabricated.json)、[105 项新增测试](../../tests/feasibility/test_hotel_feasibility.py)
- [11 个严格 Schema](F05契约Schema.json)、[17 份完整输入和报告示例](ValidationReport示例.json)
- [F05 原始测试日志](new/离线测试原始日志.txt)、[F05 网络阻断证据](new/无外网调用证据.json)
- [463 项回归原始日志](regression/离线测试原始日志.txt)、[回归网络阻断证据](regression/无外网调用证据.json)
- [契约核查原始日志](契约核查原始日志.txt)、[基线与边界核查](基线与边界核查.json)
- [实施说明、M2-00 对应及能力边界](实施与验收报告.md)

本轮 **没有重新运行 454 项后端数据库回归**；沿用 [M2-02a 已验收日志](../M2-02a/后端454项回归.txt) 和 [数据库隔离证据](../M2-02a/数据库隔离.json)。正式 M1/PostgreSQL、预算、确认、授权、历史快照、001～007 迁移、旧 Spike 均未修改；未访问数据库或启动 Docker，也未改写以前的证据。

复现（仓库根目录，无数据库、密钥或公网）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_m202d_offline.py new
.\.venv\Scripts\python.exe scripts/verify_m202d_offline.py regression
.\.venv\Scripts\python.exe scripts/verify_m202d_contract.py
```

规则版本：`F05-synthetic-v1`。最终 Git Commit、分支及提交后工作区状态见交付回复，避免 Commit 自包含递归。

**PASS 仅表示给定合成政策下的酒店事件、人数容量及计划住宿夜范围未发现本规则覆盖的冲突。库存、价格和其他检查均未被提升；真实出行、确认快照和整体 READY 权限恒 false。完成 M2-02d 后停止等待 Review，不进入 F06、真实酒店接入或其他阶段。**

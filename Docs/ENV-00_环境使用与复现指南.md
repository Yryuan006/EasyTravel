# ENV-00 环境使用与复现指南

日期：2026-11-09。范围仅为 Architecture Spike 的开发环境；未实现旅行规划或 Spike 工作流。

## 项目内环境

项目使用 Python 3.12.16 x64、uv 0.13.7 和 PostgreSQL 16.15。Python/uv 均在项目目录内，不要求系统 python/uv 命令可用，也不修改系统 PATH 或注册表。

| 文件/目录 | 用途 | 是否进入 Git |
|---|---|---|
| pyproject.toml | 最小直接依赖和开发依赖 | 是 |
| uv.lock | 包版本、来源及分发哈希 | 是 |
| .python-version | 精确 Python 版本 | 是 |
| .tools/ | uv 与托管 Python | 否 |
| .venv/、.venv-repro/ | 主环境和复现实验环境 | 否 |
| .cache/uv/ | 本地依赖缓存 | 否 |
| .env | 专用数据库连接凭据 | 否 |
| .local/ | 数据库初始化 SQL 与管理员密码文件 | 否 |
| .env.example | 无真实凭据的配置示例 | 是 |

本地密钥文件是受 Git 忽略的明文文件，忽略规则不等于加密。请勿分享整个工作目录、.env、.local 或输出完整 DSN。不需要模型 API Key。

## 常用命令

在项目根目录使用 PowerShell 7。以下命令不要求修改 ExecutionPolicy；若组织策略阻止脚本，请先处理授权，不使用 bypass。

```powershell
# 现有环境导入验证，无数据库连接
pwsh -NoProfile -File scripts/env00.ps1 -Action Imports

# 严格按现有锁文件同步环境并检查依赖
pwsh -NoProfile -File scripts/env00.ps1 -Action Sync

# 从本机缓存创建第二个独立虚拟环境，不删除原环境
pwsh -NoProfile -File scripts/env00.ps1 -Action Reproduce

# 只连接 dedicated ENV-00 本地测试库，SELECT 后关闭
pwsh -NoProfile -File scripts/env00.ps1 -Action Database
```

脚本将缓存与托管 Python 的路径限定在项目内。`verify_env.py` 使用 Python 3.12 的 asyncio.Runner 和 Windows SelectorEventLoop，只做包导入和只读数据库查询，不执行 saver.setup、不建业务表、不运行 Agent。

Reproduce 使用 `uv sync --frozen --offline`；需要已有托管 Python 和缓存。冷启动新机器需要在线下载，不能声称无缓存也能离线安装。`uv pip check` 只验证依赖声明的一致性，不替代 Spike 行为验证。

## 冷启动工具与依赖

```powershell
pwsh -NoProfile -File scripts/bootstrap_env00.ps1
```

该脚本将已固定版本的官方 uv wheel 下载到 .tools，并校验已记录 SHA256；安装 `.python-version` 指定的托管 Python，使用 `--no-bin --no-registry`，再执行 frozen sync。所有下载保持证书验证，不写系统 PATH、注册表或全局 Git 配置。脚本在当前已有环境上重跑成功；全新机器/空缓存的完整冷启动仍未独立验证。

uv 官方方法参考：[安装 uv](https://docs.astral.sh/uv/getting-started/installation/)、[项目与锁文件](https://docs.astral.sh/uv/guides/projects/)。

## 独立 PostgreSQL 实例

使用已安装 Docker Desktop；如果尚未启动，可正常打开 Desktop 后再运行准备脚本。脚本不安装 Docker、不修改系统服务。

```powershell
docker version
pwsh -NoProfile -File scripts/prepare_env00_db.ps1
pwsh -NoProfile -File scripts/env00.ps1 -Action Database
```

| 项目 | 值 |
|---|---|
| Compose 文件 | compose.env00.yaml |
| Compose 项目 | easytravel-env00 |
| 容器 | easytravel-env00-postgres |
| 镜像 | postgres:16，固定 sha256:f9a7b9ebf6f56201f949db745e47bc423c0f0f8294bafe991cc10bf74b029657 |
| 本机端口 | 127.0.0.1:55432 → 容器 5432 |
| 数据库 | easytravel_env00_test |
| 应用测试账号 | easytravel_env00，非超级用户，无创建数据库/角色权限 |
| 数据卷 | easytravel_env00_pgdata |

管理员密码通过 Compose file secret 提供，应用密码随机生成后仅保存在被忽略的文件中。应用账号拥有专用测试库，可在之后获准的 Spike-02 中建 checkpoint 表。

准备脚本只管理带 ENV-00 标签的同名资源，名字冲突会停止；已有数据卷缺失本地凭据或凭据文件不完整时拒绝覆盖。初始化 SQL 只在空数据卷首次启动时运行。不要删除数据卷来修复密码问题。

需要暂停测试库时，可以手动运行：

```powershell
docker compose -f compose.env00.yaml stop
```

不需要删除容器或卷。不要使用带 `-v` 的 down，不要重置 Docker Desktop。数据库没有配置自动重启，下一次 Desktop 启动后可以重新执行准备脚本。

## Git

仓库已在项目根目录初始化为 main，配置了仓库级作者身份；未创建远程、提交或推送。

沙箱创建的 .git 与宿主目录分属不同 Windows 主体，Git 可能报告 dubious ownership。验收采用仅对明确项目路径生效的命令级信任，不添加全局 safe.directory，不修改所有权/ACL：

```powershell
git -c safe.directory=D:/Project/EasyTravel status --short
git -c safe.directory=D:/Project/EasyTravel check-ignore .env .local/probe .tools/probe .venv/probe
```

请只将实际复核的项目路径设为信任项，不使用通配符。提交时逐项添加项目文件；.env.example、uv.lock 和复现脚本可跟踪，真实 .env 和 .local 不应进入索引。

## 已知限制

- 系统 WindowsApps Python 别名仍不可用；通过项目脚本运行，不修改系统设置。
- 沙箱内 HTTPS/部分宿主资源访问会失败；本次成功下载、Docker 和连接验证使用获准的宿主执行环境，保持 TLS 校验。
- Docker Desktop 启动时会按已有策略恢复其他项目的容器。本次未连接或修改其数据库，只使用新建 ENV-00 实例。
- 复现已证明同机、相同 Python、缓存可用条件下可重新建立相同包环境；未验证不同 Windows 机器或操作系统。
- PostgreSQL 连接成功不代表 LangGraph checkpoint、中断、恢复或旅行业务已验证。

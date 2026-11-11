# EasyTravel 前端

M1-02：在既有 App Shell 与健康检查上提供旅行创建及只读重新打开。仅访问 health、POST `/api/v1/trips`、GET `/api/v1/trips/{trip_id}`，不提供更新、澄清或生成。

## 环境

采用 Node.js **24.22.2 LTS (Krypton)**、npm **11.9.0**。Node 版本见 `.node-version`，允许范围见 `package.json`；`.npmrc` 开启引擎检查。依赖使用精确版本和 npm lockfile v3，安装时使用 `npm ci`。

| 依赖 | 版本 | 用途 |
|---|---|---|
| vue | 3.5.45 | 唯一直接运行依赖 |
| typescript | 5.9.3 | 严格类型检查 |
| vite / @vitejs/plugin-vue | 8.3.7 / 6.0.10 | 开发与构建 |
| vue-tsc / @types/node | 3.3.14 / 24.10.1 | Vue SFC 与配置类型 |
| vitest / @vue/test-utils / jsdom | 5.1.0 / 2.5.1 / 31.0.0 | 离线单元与组件测试 |
| @playwright/test | 1.65.0 | 真实浏览器验证；开发依赖 |

Vite 的 Node 要求为 `^20.19.0 || >=22.12.0`；本次锁定的测试工具也已在 Node 24.22.2 验证。未安装全局包，未修改系统 PATH。缓存位于仓库 `.cache/`，运行时位于 ignored 的 `.tools/`。

## 启动与验证

以下命令以已选择 Node 24.22.2 的终端为前提：

```powershell
cd frontend
npm ci
npm run dev
# http://127.0.0.1:5173；端口被占用时明确失败，不自动换端口。
```

仅健康检查无需数据库。旅行功能需在另一个终端从仓库根启动 M1-01 本地后端，使用专用已迁移数据库和本地身份配置；不得用于公开部署：

```powershell
.tools/uv/uv.exe run --frozen --no-sync --env-file .local/my-business-db.env --env-file .local/my-identity.env python -m easytravel.backend.local_server
# 数据库与凭据准备参见 Docs/M1-01_实施与验证报告_2026-11-11.md。
# 打开 http://127.0.0.1:5173/trips/new，手动输入本地 Token。
```

```powershell
# frontend 目录
npm run typecheck
npm run build
npm test
npm run test:watch
npm run preview -- --host 127.0.0.1

# 浏览器测试要求前后端开发服务器已经启动；该命令不管理服务器生命周期。
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Resolve-Path ..) '.cache/ms-playwright'
npx playwright install chromium
npm run test:browser
```

`npm test` 完全离线，通过 fetch mock 覆盖成功、失败、超时和异常响应，不需要数据库、LLM 或外部服务。浏览器测试通过真实后端验证成功路径，使用浏览器请求拦截验证失败与恢复路径。截图输出到 `../Docs/M1-02_*.png`。完整真实浏览器运行方法见下文。

当前 Windows 主机的 `npm.ps1` 固定调用全局相邻 Node。若使用本次保留的项目局部运行时，从仓库根执行以下会话级设置，避免修改系统环境：

```powershell
$projectRoot = (Get-Location).Path
# 仅在局部运行时尚未安装时执行；没有 -g。
$env:npm_config_cache = Join-Path $projectRoot '.cache/npm'
npm install --prefix .tools/node-m003 --no-save --package-lock=false --no-audit --no-fund node@24.22.2
$projectNode = Join-Path $projectRoot '.tools/node-m003/node_modules/node/bin/node.exe'
$npmCli = 'D:/NodeJs/node_modules/npm/bin/npm-cli.js'
$env:PATH = (Split-Path $projectNode) + ';' + $env:PATH
function npm { & $projectNode $npmCli @args }
node --version
npm --version
cd frontend
# 然后使用上面的 npm ci / npm run dev / npm test 等命令。
# 此主机安装浏览器也可用：npm exec -- playwright install chromium
```

## API 与配置边界

浏览器固定请求同源 `/api/v1/health` 和 `/api/v1/trips`。开发代理默认指向 `http://127.0.0.1:8000`，可通过 `frontend/.env.local` 中的 `EASYTRAVEL_API_PROXY_TARGET` 更改（示例见 `.env.example`）。此变量只用于 Vite 配置，不注入客户端。不要在任何前端代码、环境变量或配置中放入密码/API Key；`VITE_*` 会暴露给浏览器。

生产构建只生成静态文件，**不包含 Vite 开发代理**。部署或使用 preview 做联通验证时，需由同源反向代理将 `/api/` 转发至 FastAPI，并且保留路径。不满足此条件时页面会展示失败。preview 用于检查构建产物，不能自行连接默认后端。

`src/api/health.ts` 依据 `Docs/M0-01_openapi.json` 实现字面量类型、unknown 边界运行时校验、超时和安全错误。契约字段可省略，客户端按已归档 schema 的默认值正规化；显式冲突、null、数组、非 JSON 和非 2xx 均拒绝。`alive` 只表示 HTTP 存活；数据库、模型、旅游数据始终为未检查，不能解释为就绪。

## 文件清单与复用边界

```text
frontend/
  .env.example
  .gitignore
  .node-version
  .npmrc
  README.md
  index.html
  package.json
  package-lock.json
  tsconfig.json
  tsconfig.node.json
  vite.config.ts
  playwright.config.ts
  src/
    main.ts
    App.vue
    styles.css
    api/health.ts
    composables/useHealth.ts
    components/HealthStatus.vue
  tests/
    health.test.ts
    app.test.ts
  e2e/
    health.spec.ts
```

`App.vue` 提供应用标题、导航和内容壳；`styles.css` 提供颜色、布局、焦点、移动适配和 reduced-motion 基础；`HealthStatus.vue` 是无请求副作用的展示组件。`useHealth` 管理加载/成功/失败联合状态、刷新、过期请求保护与卸载取消。API 错误类型、fetch 注入方式及测试/构建配置可供后续正式交互复用，健康类型与解析器仅限本接口。没有引入路由、状态管理、UI 框架或地图 SDK。

本次完整 npm audit 有 **5 项 moderate、0 high、0 critical**，来源为测试依赖 `@vue/test-utils → js-beautify → editorconfig/glob/minimatch`；运行依赖审计为 **0 告警**。原始审计 JSON 在 Docs。未采用跨主版本强制 overrides；这些开发依赖不打入生产包，后续升级需复验组件测试。


## M1-02 真实闭环与凭证

`/`、`/trips/new` 为创建页，`/trips/{UUID}` 为只读旅行页。两个页面状态通过 History API + popstate 管理，无新增依赖。Vite 的 SPA fallback 支持直接打开与刷新；未来生产认证接入时，托管服务也需配置 SPA fallback。

仅 `import.meta.env.DEV` 且 loopback hostname 显示本地凭证和旅行功能。生产 build/preview 不开放此凭证入口；它们不属于当前本地身份验收路径。Token 只在页面内存，提交后清空输入缓冲，不使用存储、URL、日志或 VITE 环境变量。更换身份会清除表单、结果和待重试命令，并丢弃旧请求响应。刷新后在原 URL 输入凭证，发起 GET，不自动 POST。

全部 Answer 字段显式提交；金额输入为元，严格转换为整数分。原始文本只原样保存，不提取结构化事实。客户端做基本格式校验，日期/天数不一致或目标预算大于硬上限由后端以 blocking_fields 表达。没有无法保存的编辑入口。

写请求的 UUID key 与完整序列化 body 只保存在页面内存。超时、断网、非法成功响应均不宣称成功；明确点击原请求重试会保留同 key/body。修改后提交使用新 key，页面提醒原请求可能已经落库。409 key_reused 禁止原样重试；in_progress 支持人工稍后同键重试。没有自动重试或刷新后自动创建。

从仓库根运行真实浏览器验收（需本机已安装 Chromium 和空闲端口 8000/5173）：

```powershell
# 只新增专用库；不要清空或重置已有库。
.venv/Scripts/python.exe scripts/prepare_m002_test_db.py --env-file .local/review-m102-browser.env
.venv/Scripts/python.exe scripts/verify_m102.py --database-env .local/review-m102-browser.env
```

脚本应用既有迁移、启动真实 FastAPI/Vite，在进程内生成两种身份的随机凭证并运行全部 Playwright。凭证只通过进程环境供测试输入，不进入 Vite 编译变量或输出。结束时只终止本次启动的两个进程，保留数据库和生成的证据。默认复用 `.cache/ms-playwright`（可用 PLAYWRIGHT_BROWSERS_PATH 覆盖）。首次失败若因缺 Chromium，按上文安装。后端全量回归可附加两个新空库参数 `--backend-api-env` 与 `--backend-regression-env`；同时读取 `.env` 中既有实验测试连接及 `.local/m002-final.env` 的只读保留库连接，不清库。

模块职责：`api/contracts.ts` 对应实际 OpenAPI DTO 并校验 unknown 响应；`api/trips.ts` 负责同源请求、超时和脱敏错误（复用已有 ApiError）；`requirements.ts` 仅序列化明确输入；`useTrip.ts` 管理读写状态、幂等命令及过期响应保护；`RequirementsForm` / `TripSummary` 分别输入与只读展示。

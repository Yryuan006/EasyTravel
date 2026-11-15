# M1-08：最终 API 与数据契约核查

日期：2026-11-15。可重复运行 `.venv/Scripts/python.exe -m scripts.verify_m108_contract`；结构化结果为 `M1-08_架构与契约核查.json`。

## API 与类型

工厂生成的 `M1-08_OpenAPI.json`、运行中 FastAPI `/openapi.json` 保存的 `M1-08_运行中OpenAPI.json`，与已验收 M1-07 OpenAPI 完全相同。本轮没有新增或删除端点、变更请求/响应字段、状态码或安全 scheme。

正式业务共有 15 个方法/路径组合，另有 health。创建、需求更新、澄清回答、生成请求/读取、generation-state、确认预览/确认、修改意图、提案读取/决定、历史列表/详情均经过同一可信 actor 依赖。新增全端点测试循环覆盖这 15 组及 66 个反例，结果见 `M1-08_全端点身份安全证据.json`。

前端合并的 71 个运行时 schema 中，70 个公共 schema 与实际 Pydantic OpenAPI 逐个完全一致。另一个旧 WorkingDraft schema 是 WorkingView 的无 context 只读子集，所有共有字段完全一致，维持旧数据读取兼容，不是第二套当前工作稿。TypeScript 严格检查通过；本阶段没有修改任何类型定义或运行时验证规则，仅修正 stale_intake 文案。

状态语义保持：GET 200，创建/确认 201，异步接受 202；401 缺少/无效本地凭证；403 本地回环/Host/Origin 环境拒绝；404 不存在、跨 owner 或错父资源；409 版本/摘要/授权/幂等/业务状态冲突；422 形状、额外字段和非法游标；503 真实业务数据库不可达。错误只暴露静态 code/message。health alive 明确不表示数据库、模型或旅游数据已就绪。

## 单一业务权威

| 事实 | 正式模型/计算与持久化 | 联合核对 |
|---|---|---|
| 当前工作稿 | domain.models.WorkingDraft；draft_versions + working_drafts | CAS 追加 r1～r5；前端无权推进修订 |
| 确认历史 | domain.models.ConfirmedSnapshot；confirmed_snapshots 引用不可变源修订，关联 confirmation_disclosures | S1/r1、S2/r4 保留各自预算、输入与披露；r5 未确认仍能读取 |
| 预算 | easytravel.rules.BudgetResult/evaluate_budget | Mock 不为 VERIFIED；未知 null；浏览器不重算历史预算 |
| L3 授权 | domain.models.SpecificAuthorization；specific_authorizations | 酒店和 v2 两份授权，精确影响集合、各消费一次；整体确认没有创建授权 |
| 用户需求 | 不可变 trip_intake_versions，正式 TripRequirements 与 DraftContext | intake i4、正式 requirements r2 与 working r5 分开，源数据可还原 |
| 运行/提案/决定 | generation_runs、change_proposals、上下文表、proposal_decisions、回执 | 生成不等于应用；拒绝不改稿；重启仍可读决定 |

应用用例持有 UoW；批准、授权消费、修订、上下文、决定和回执在同一 PostgreSQL 事务中提交。模型输出先严格解析及领域验证，不能直接发出批准或确认。前端只表达用户意图，不能指定弱化的权限级别。

正式 backend 无旧 Spike/M0-00 导入；domain/application 无 FastAPI、psycopg 或 LangGraph 依赖。共享根 contracts/rules/model_adapter 是正式入口复用的纯契约/规则，不将旧实验 Repository 或 Checkpoint 作为业务权威。M1-00 允许当前 Mock 切片不重建实验图，单执行者沿用正式 GenerationWorker。

## 迁移与历史兼容

001～007 SQL 与授权基线逐字节一致，未追加迁移。SHA256 在架构核查 JSON 中；生命周期测试将专用浏览器库实际 schema_migrations 的 7 条 checksum 与该清单逐条对照。

旧快照缺 DraftContext/RiskDisclosure 时返回明确 null，页面显示未记录，既有兼容测试完整执行。历史 GET 在 READ ONLY 事务内读取，不重新评估当前预算或规则、不修改指针。最新确认指针只指 S2，不将当前 r5 误标已确认、已预订或就绪。

本地 bearer、回环访问与页面内存凭证没有被表述为生产登录；没有生产多租户/RLS、运行迁移角色分离或多进程游标签名部署的验收结论。

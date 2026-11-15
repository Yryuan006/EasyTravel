# Evidence 与价格新鲜度契约

本文件为拟议 V2，尚未改动现有 frozen/extra=forbid 模型、OpenAPI 或数据库。所有供应商响应均是不可信输入；可信的是服务端完成的结构/身份/范围/时间/权限核查及其审计记录，而不是供应商返回的数字或模型措辞。

## 与基线代码逐项对照

| 现有实现 | 保留行为 | V2 需补足 |
|---|---|---|
| `easytravel/contracts.py::PriceQuery` | 资源、起止日期、CNY、房型/房数；金额已展开，禁止再次乘房晚 | 缺人数/入住分配/票种/税费/餐标/班次等；新增 PriceScopeV2，不能只给旧字段加默认人数 |
| `CostLine` | quote/estimate/unknown；整数分；unknown 必须 null+原因；is_mock；evidence_id；项目与 query 精确匹配 | 区分未核验报价、冲突、过期；补费用用途/包含关系和完整覆盖清单；来自模型的 evidence_id 必须拒绝或剥离后由服务端绑定 |
| 现有 `Evidence` | 服务端拥有，source_type 为 mock/supplier_quote；query、金额、obtained_at/valid_until | 没有 provider、验证过程或人数，supplier_quote 标签本身不足以验证。需新不可变版本，不直接沿用为真实核验记录 |
| `easytravel/rules.py::evaluate_budget` | 服务端 mapping；校验引用、非 Mock、query、金额、有效期；未知时总额 null；已知超限优先 | 旧规则对满足上述条件的 supplier_quote 可判 VERIFIED，但不含新语义。V2 必须显式质量门控，不能通过构造旧 Evidence 绕过 |
| `backend/domain/confirmation.py`、`application/confirmations.py` | 当前 preview、confirm 均以 `{}` 评估，risk/disclosure 双哈希、blocker 拦截、快照不可变 | 用同一受所有权约束的 EvidenceSet/ValidationReport 替换空 mapping；哈希覆盖证据版本和生效状态；GET 不调用第三方 |
| `domain/generation.py::validate_initial` | 当前只允许 Mock、无 evidence_id、非 quote；逐日/单酒店覆盖 | 不删此防线。真实来源走显式 V2 路径，保留旧 Mock 验收，模型 DTO 继续不拥有核验权 |

旧 CostLine/PriceQuery 查询范围以项目日期匹配，但未编码“该金额是两人三晚、税费齐全”。仅补 provider 并不安全。旧 BudgetResult 也不能表达覆盖遗漏，所以“所有已列费用已核验”必须进一步要求“必要费用覆盖完整”，才允许整体预算 VERIFIED。

## EvidenceV2 的最小结构

记录按事实类型分为 PLACE、OPENING、ROUTE、PRICE、AVAILABILITY、HOTEL_POLICY，同一地点的不同字段可引用不同证据。source/provider 的命名空间与 EasyTravel 内部 resource_id 分离，避免不同供应商 ID 相撞。

| 字段 | 契约 |
|---|---|
| evidence_id / schema_version | 服务端生成、全局唯一、不可变；`evidence-v2` |
| source / provider / environment | official_operator、licensed_api、manual_official、synthetic；稳定 provider ID；live、sandbox、synthetic、replay 明确分开 |
| resource_id / provider_resource_id | 内部 ID 与供应商 ID；资源映射版本、城市、入口/出口 identity，歧义未解不得通过 |
| observed_at / provider_updated_at | 服务端实际观察时刻（带时区，UTC 存储）；供应商更新时间可空，不能冒充 observed_at；未来时刻拒绝 |
| applicable_date / valid_for | 事实适用的旅行日期、时间窗、星期规则/季节/例外；与缓存时间不同。营业为分段区间，不能只存一句字符串 |
| price_scope / currency | 价格证据必填 PriceScopeV2/CNY；非价格证据为空，不混用金额语义 |
| travelers / rooms / nights | 价格 scope 中明确 1–2 人、房间与入住分配、晚数；非住宿 rooms/nights 为 null；不从标题解析。晚数=end-start，酒店 end 为离店日，不按含首尾天数计算 |
| claim / amount_cents | 规范化事实、单位、未知字段及原因；价格为已展开总额整数分；无充分依据 null |
| verification_state | 观察时的 SOURCE_UNVERIFIED、VERIFIED、ESTIMATED、EXPIRED、CONFLICT、UNKNOWN；规则输出，不从供应商或模型接收 |
| verification_record | verifier_type（deterministic/manual）、规则版本、verified_at、检查项结果、核验对象摘要、父证据、人工核查身份/理由；LLM 不在允许的核验者集合 |
| expiration / freshness | provider_valid_until 可空、policy_max_age、effective_expires_at、freshness_policy_version；无明确适用性不能仅靠 TTL 判 VERIFIED |
| source_reference | 官方 URL 或允许保存的脱敏请求 ID/报价 ID、响应/事实摘要哈希、页内定位、文档/条款版本；不含 Key、Token、个人证件 |
| legal_policy / binding | retention_permission/version、retain_until（允许永久则显式 permanent）、录制/模型输入/显示权限；trip/owner/run/intake/working 与查询摘要；哈希不自动豁免数据许可 |

记录的 claim 和核验事件只追加。过期和冲突可由读取时派生 effective_state；不能 UPDATE 旧记录把历史 VERIFIED 改写掉。当前有效状态与“当时曾通过”分别展示。

## PriceScopeV2 精确匹配

候选输入包含：resource_id、start_date/end_date、currency、product_kind、traveler_count/成人资格、occupancy_by_room、rooms、nights、room_type_id、rate_plan_id、ticket_type/slot、service/seat_class、出发到达站、direction、meal_basis、tax/mandatory_fee_scope、amount_basis、inclusions、cancellation_terms_reference。不同产品无关字段显式 null，必要字段未知则不能生成精确报价结论。

amount_basis 固定保存为 `expanded_total`；供应商每人/每晚单价作为可选计算输入与单位另存，服务端 Decimal 计算并记录舍入规则与算式，只展开一次。包含/排除费用分别列出，必选但未定价的附加费单独 CostLine.unknown。税费不全、币种非 CNY、仅“起价”或人均消费均不能映射成已核验总额。V2 最小阶段不引入汇率；外币为不支持/待核查。

旅客数、日期、房型/房数、入住分配、席别、场次、关键税费范围任一改变，query_digest 必变，原证据不得匹配。多人门票不能仅改旅行输入不改 query。模糊房型名/“豪华房”不是稳定匹配键。每项合计到预算时按 cost_id 去重，included_in 图禁止循环与重复计费。

## 六种状态与迁移

| 有效状态 | 成立条件/迁移 | 当前费用映射 |
|---|---|---|
| SOURCE_UNVERIFIED 真实来源未核验 | 已取得获许可真实来源，但身份/口径/适用日期等未全部检查；通过服务端核查才可转 VERIFIED | 有依据的参考金额可为 estimate，必须写“未核验参考”；不能放 verified_cost_ids |
| VERIFIED 已核验且当前适用 | 真实 live、权限允许、身份/范围/金额齐全、适用日期匹配、核验过程通过、未过期、无未消解冲突 | quote + 服务端 V2 证据；只证明该事实/该时刻，不保证下单或出行日库存 |
| ESTIMATED 估算 | 明确假设、单位和方法，可来自模型建议但由服务端计价；不得凭空给 quote | estimate；有金额可计已知小计，显示估算；无金额 unknown |
| EXPIRED 来源过期 | now >= effective_expires_at，或已变更日期/适用范围不再匹配（另标 scope_mismatch），不能沿用有效核验 | 当前精确价置 unknown；若有独立适用的估算方法则新建 estimate。旧价保存在历史参考区，不冒充当前成本 |
| CONFLICT 数据冲突 | 同一范围的经营主体公告/报价相互不一致或强证据相反；保留所有父证据 | 无法定当前价格则 unknown；不取最便宜值或平均值。确定必付的无争议费用仍保留，已知超支照常拦截 |
| UNKNOWN 无法确定 | 无数据、超时、范围不全、来源不可读、权限不足、未到开售期等带 reason_code | unknown/null；总额 null；不能把未知项省略或计零 |

优先级：身份/范围/授权无效先排除；仍存在未消解的当前事实冲突→CONFLICT；超期→EXPIRED；仅参考方法→ESTIMATED；完整核验→VERIFIED；有来源未核查→SOURCE_UNVERIFIED；无可用事实→UNKNOWN。单一枚举之外保留 issue_codes，避免一个状态遮蔽多个问题。当前有效关闭公告可消解旧营业窗口，但必须记录规则与依据；来源较新本身不足以消解内容范围差异。

## 新鲜度政策与示例

以下是**拟议内部上限，不是供应商缓存许可或准确性承诺**。仅在获相应使用权后启用。有效期取 provider 的有效截止、内部上限与事实声明截止三者中最早值；使用半开区间 `[observed_at, expires_at)`，到边界即失效。供应商不给 TTL 时可由已批准内部上限约束，但动态报价必须完成精确范围与复核过程，不能自动赋 15 分钟 VERIFIED。

| 事实 | 内部最大观察年龄建议 | 独立适用性条件 |
|---|---|---|
| 地点名称/位置 | 30 天 | 入口明确、无搬迁冲突；关键路线生成前检查异常 |
| 常规营业/挂牌票价 | 24 小时 | 必须覆盖目标日期；同时核查日期例外/关闭公告，缺例外覆盖只能未核验 |
| 临时关闭/公告 | 生成/确认前检查，最多 1 小时 | 在有效日期内的关闭事实保留至有新公告消解；不能因抓取时间变老把关闭自动当作开放 |
| 同城路线 | 30 分钟 | 起终点/模式/目标出发时间匹配；若接口不支持目标日期，只能静态路线建议 |
| 酒店/库存/动态票价 | 最多 15 分钟 | 精确日期、人数、产品、税费；报价与库存独立，有价无房不表示可订 |
| 城际班次/余量 | 班次最多 24 小时，余量最多 5 分钟 | 服务日、开售窗口和站点匹配；无授权 API 时不启用此政策 |
| 餐饮估算 | 7 天复审估算参数 | 始终 ESTIMATED，不能通过未过期变成 VERIFIED |

合成示例（非真实报价）：2 人、1 间房、2026-12-01 入住至 12-03 离店、2 晚，基础每房晚 30000 分，已含必选费，总额 60000 分。换至 12-08 或变成 2 间房，即使只过去 1 秒也 scope_mismatch；正确结果为重查或 unknown。观察于 10:00Z 且 10:15Z 截止，10:15Z 确认必须失效。若税费缺失，60000 分仅可表示无争议基础费，税费另列 unknown，不能宣称完整总价。

## BudgetResult 与确认披露

保留现有七字段及语义：known_subtotal 是当前有效费用行有金额的合计（含估算，UI 明示）；有 unknown 则 total=null、verification=INDETERMINATE；无 unknown 且非全部有效核验则 ESTIMATED；全部费用经 V2 核验且必要覆盖完整才 VERIFIED。OVER_LIMIT 判断优先于未知；已知小计 > hard_limit 即拦截，等于上限但仍有未知只能 UNDETERMINED。覆盖缺口应实体化为 unknown 行，而不是借助“现有费用全 VERIFIED”掩盖遗漏。

旧价不再适用于新日期时不作为该日期已知消费；独立存在的当前必付费用不得被一起清零。过去价格高于上限只说明历史价，不据此伪造本次必付超支，但要提示预算风险。未经证实的低价不能作为安全下界证明充足。

新增并行 EvidenceAssessment/FeasibilityReport：逐 cost 的 state、reason、证据版本和覆盖项；现有 evidence_issues 保存机器码，UI 显示解释。BudgetResult 的 VERIFIED 仅为预算判断，绝不推广成“路线已核验”“已预订”。

确认 GET 读取持久化证据集，在服务端当前时刻重算 effective_state，返回 evidence_set_digest、assessment_digest、规则版本、assessment_valid_until。哈希不含每秒变化的 evaluated_at，但必须覆盖实际证据/状态/适用范围、费用覆盖、路线校验及有效截止。POST 在短事务内以一个 now 再评估，比较 intake/working revision、双哈希及证据集版本；期间到期、新证据或冲突导致状态/摘要变化则 409 要求重看。需要外查时通过单独刷新任务完成，不在确认事务中等待网络。

未知费用可明确接受的既有规则保留；关键路线 UNKNOWN/确定不可行等 blocker 不能被确认豁免。确认保存的预算、评估状态、证据版本、风险与披露为当时事实；以后报价变动只在新工作修订中重算，历史页不重新渲染成当前 VERIFIED。旧格式快照保留 v1/资料缺失标识，不补造证据或用户接受事实。

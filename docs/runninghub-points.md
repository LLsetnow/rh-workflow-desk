# RunningHub 积分兼容

RunningHub 已逐步上线积分版会员体系。项目使用账户状态接口中的 `remainPoint` 作为积分余额；旧 API 响应没有该字段时，回退读取 `remainCoins`。响应同时包含两个字段时只把 `remainPoint` 作为积分显示，不把两者相加。

任务结果文档目前仍提供 `consumeCoins`。项目保留此字段作为兼容输入，并优先识别 `consumePoint`（若接口提供）。内部任务记录和旧数据库继续保留 `cost_type: "coins"` 等历史值；仪表盘及任务界面把这类 API 用量统一显示为积分。积分消耗不套用旧版 RH 币人民币换算。

CLI 的 `rh check` 也优先显示 `remainPoint`，保留 `remainCoins` / `balance_coins` 输出兼容性。`rh pricing` 表格只是项目保存的旧费率参考，RunningHub 积分版官方文档没有给出通用工作流按秒费率；真实消耗以 API 返回值或账单为准。任务 API 未返回费用时，CLI 不再按旧费率补估。

## 官方资料

- [RunningHub 首页：积分版会员体系公告](https://www.runninghub.cn/)
- [RunningHub API：获取账户信息](https://www.runninghub.cn/runninghub-api-doc-cn/api-425748943)（示例包含 `remainPoint` 与 `remainCoins`）
- [RunningHub API：生成任务提交结果](https://www.runninghub.cn/runninghub-api-doc-cn/schema-252329734)（当前结果模型仍包含 `consumeCoins`）
- [RunningHub 付费服务协议](https://www.runninghub.cn/defray-protocol)（服务权益和消耗以平台实际提供及页面说明为准）

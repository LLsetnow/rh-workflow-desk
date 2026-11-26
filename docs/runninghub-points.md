# RunningHub 积分兼容

RunningHub 账户状态接口新增了 remainPoint。本项目将它保存为积分余额，并将旧 remainCoins 单独保留为 RH 币余额；两者不会相加或互相覆盖。

任务输出兼容 consumePoint、consumePoints、旧 consumeCoins 和 consumeMoney。新积分结果保存为 cost_type points，旧 RH 币任务仍保存为 cost_type coins。任务队列、详情、成片和仪表盘按类型区分显示；积分和 RH 币统计也分别汇总。现有 SQLite 任务和用量记录无需迁移。

CLI 的余额检查和工作流结果同样区分积分与 RH 币。rh pricing 保留旧 RH 币费率作为历史参考，不把它当作当前积分官方费率；未返回消耗时不再按旧费率推算本次任务费用。

## 官方资料

- RunningHub API：获取账户信息 https://www.runninghub.ai/runninghub-api-doc-en/api-425761030 。示例包含 remainPoint 和 remainCoins。
- RunningHub API：任务结果示例 https://www.runninghub.ai/call-api/api-detail/2037365179167543298?apiType=4 。当前公开结果模型仍列出旧 consumeCoins 字段。
- RunningHub 付费服务协议 https://www.runninghub.ai/defray-protocol 。

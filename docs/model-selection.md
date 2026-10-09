# 工作流收藏模型选择

任务提交页自动识别字符串类型的 `lora_name` 和 `unet_name`，分别显示 LoRA 与扩散模型的收藏下拉框，例如 `Load LoRA · lora_name` 和 `Load Diffusion Model · unet_name`。工作流库及临时快照的输入配置使用同一控件选择默认模型；已有手动输入配置仍按用户选择的字段显示。

列表读取工作流绑定账号的 API Key，未绑定时使用当前账号。优先使用该账号的个人 Key；账号没有 Key 时不会借用其他账号。未选择账号且存在多个归属或站点时，需先选定账号。Desk 与 PS 分别使用自己的本地配置和缓存。

打开下拉框不会更换工作流原值。原值不在收藏列表内时仍显示为当前值；点击「手动」可以输入完整模型文件名，点击「列表」返回下拉框。收藏为空或读取失败时，当前值、手动输入和导出仍可用；「重试」重新读取列表。选择值随草稿、库内默认值、任务快照和历史回放保存。

## 本地 API

`GET /api/models/favorites?account_id=<本地账号 ID>&refresh=1`

两个参数均可省略。响应只包含模型名称和本地归属信息：

```json
{
  "models": {
    "lora": ["example-lora.safetensors"],
    "diffusion": ["example-unet.safetensors"]
  },
  "account_id": "local-account-id",
  "site": "cn",
  "source": "favorites",
  "cached": false
}
```

后端通过 RunningHub 的 [原生 ComfyUI API](https://www.runninghub.cn/runninghub-api-doc-cn/doc-8287337) 读取完整 `object_info`，提取 `LoraLoader` / `UNETLoader` 的文件名选项。节点级 `object_info` 路径未返回可用选项，因此完整结果由服务端解析并仅缓存名称。缓存按账号、站点、Key 指纹隔离，有效期 5 分钟，并合并同一归属的并发读取；「刷新」跳过缓存。浏览器只访问本地接口，凭证与代理 URL 不进入响应及错误消息。读取模型不会创建生成任务。

## 验证（2026-10-10）

- Python 全量：453 项通过；Node Web 测试：47 项通过。
- 合成工作流在 320、460、900、1260px 检查下拉选择、当前值、草稿恢复、导出、手动输入、空列表、失败重试和默认模型配置；控件及按钮均在可视宽度内。
- 延迟返回的旧账号列表不能覆盖新账号列表；服务端测试覆盖缓存失效、刷新、并发合并及凭证错误脱敏。
- 测试使用隔离数据目录和模拟模型列表，没有提交真实付费任务。

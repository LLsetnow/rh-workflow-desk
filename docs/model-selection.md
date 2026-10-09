# 工作流收藏模型选择

任务提交页自动识别字符串类型的 `lora_name` 和 `unet_name`，分别显示 LoRA 与扩散模型的收藏下拉框，例如 `Load LoRA · lora_name` 和 `Load Diffusion Model · unet_name`。工作流库及临时快照的输入配置使用同一控件选择默认模型；已有手动输入配置仍按用户选择的字段显示。

列表读取工作流绑定账号的 API Key，未绑定时使用当前账号。优先使用该账号的个人 Key；账号没有 Key 时不会借用其他账号。未选择账号且存在多个归属或站点时，需先选定账号。Desk 与 PS 分别使用自己的本地配置和缓存。

打开下拉框不会更换工作流原值。原值不在收藏列表内时仍显示为当前值；点击「手动」可以输入完整模型文件名，点击「列表」返回下拉框。收藏为空或读取失败时，当前值、手动输入和导出仍可用；「重试」重新读取列表。选择值随草稿、库内默认值、任务快照和历史回放保存。

LoRA 选项显示基础模型标签，例如 `[qwen-image-2.1] COS.safetensors`。标签来自模型版本的 `baseModel`，与 RunningHub 模型卡上的基础模型标签一致；下拉选项的值和导出仍是 `COS.safetensors`。完整选中标签也显示在控件的悬停标题中。未公开、无标签、同名文件的标签冲突或标签查询失败时显示 `[适配模型未知]`，仍可选择该模型。

LoRA 收藏选项先按适配基础模型名称 A–Z 排序，再按 LoRA 文件名 A–Z 排序，忽略大小写；未知适配模型排在最后，并按文件名排序。当前选择值保持不变，收藏之外的当前值仍单独保留。任务提交页、默认值配置、刷新和手动切回列表使用相同顺序。

## 本地 API

`GET /api/models/favorites?account_id=<本地账号 ID>&refresh=1`

两个参数均可省略。文件名数组保持兼容，`model_metadata.lora` 以原始文件名为键提供适配标签；空字符串表示未知：

```json
{
  "models": {
    "lora": ["example-lora.safetensors"],
    "diffusion": ["example-unet.safetensors"]
  },
  "model_metadata": {
    "lora": {
      "example-lora.safetensors": {"base_model": "qwen-image-2.1"}
    }
  },
  "account_id": "local-account-id",
  "site": "cn",
  "source": "favorites",
  "cached": false
}
```

后端通过 RunningHub 的 [原生 ComfyUI API](https://www.runninghub.cn/runninghub-api-doc-cn/doc-8287337) 读取完整 `object_info`，提取 `LoraLoader` / `UNETLoader` 的文件名选项。节点级 `object_info` 路径未返回可用选项，因此完整结果由服务端解析。

LoRA 标签通过同一账号、同一站点的 [公共模型列表 API](https://www.runninghub.cn/runninghub-api-doc-cn/api-437269263) 按文件名查询，仅使用 `versions[].versionResourceName` 精确匹配版本的 `baseModel`；标准 `models/loras/` 前缀和路径分隔符会规范化，子目录与大小写保持区别。公共模型搜索结果不会加入收藏下拉框。每个名称检查至多 3 页；搜索未读完、多个匹配版本的标签不同或标签缺失时保持未知。每次最多查询 64 个 LoRA，4 个并发，12 秒查询预算；没有获得标签的收藏仍保留在列表中。

文件名与标签一起缓存，按账号、站点、Key 指纹隔离，有效期 5 分钟，并合并同一归属的并发读取；「刷新」跳过缓存并重新查询标签。浏览器只访问本地接口，凭证与代理 URL 不进入响应及错误消息。读取模型不会创建生成任务。

## 验证（2026-10-10）

- Python 全量：466 项通过；Node Web 测试：47 项通过。
- 合成工作流在 320、460、900、1260px 检查适配标签、未知标签、下拉选择、当前值、草稿恢复、导出、手动输入、空列表、失败重试和默认模型配置；控件及按钮均在可视宽度内。
- 延迟返回的旧账号列表不能覆盖新账号列表；服务端测试覆盖缓存失效、刷新、并发合并及凭证错误脱敏。
- 测试使用隔离数据目录和模拟模型列表，没有提交真实付费任务。
- PS 使用自己的本地配置进行只读实查，9 个收藏 LoRA 均取得标签，`COS.safetensors` 对应 `qwen-image-2.1`；没有复制账号或凭证到 Desk。
- 全量测试首次遇到既有产物排序用例的偶发失败；原目录和功能分支单独复查通过，全量复查通过，没有修改产物代码或该用例。
- 排序复验：前端规范 79 项、Node Web 47 项通过；两项目在 320、460、900、1260px 使用乱序、多种适配模型、大小写混合和未知标签检查排序，刷新、手动切回列表及默认值配置顺序一致，选中值与导出保持不变。

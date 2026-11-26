# 本地开发与验证

## 启动

在仓库根目录启动纯 Web 版：

```bash
./scripts/start.sh --no-browser
```

macOS Electron 开发版：

```bash
uv sync --extra test
npm ci
npm run electron
```

Electron 会启动本地 Python 服务并打开桌面窗口。输入文件通过原生选择器获取本机绝对路径；真正的远程上传只在任务提交时发生。

## 修改前的检查

1. 先确认目标页面和已有的公共类名，避免在多个页面重复定义同一控件。
2. 涉及任务字段时，检查前端草稿、`backend/server.py` 请求处理和 `backend/app.py` 持久化是否仍使用同一字段名。
3. 涉及文件输入时，区分“保存路径”“本地预览”和“远程上传”三个阶段，不要为普通输入文件增加项目内复制。
4. 涉及任务历史时，保留现有的 API 快照、阶段日志、`error_detail` 和重启恢复逻辑。

资源库初始化必须通过 `POST /api/resource-library` 或设置页“新建资源库”执行；目标目录必须为空，初始化器负责生成 `Resources.json` 和所有空索引。

## 推荐验证命令

```bash
# Web Python 测试
uv run --extra test pytest -q

# 前端脚本语法
node --check static/app.js
node --check static/prompt.js
node --check static/outputs.js
node --check static/workflows.js
node --check static/compare.js
node --check static/motion.js

# 前端风格回归检查
uv run --extra test pytest -q tests/test_frontend_style.py

# 阿里云翻译接口与自由文本状态测试（不会访问真实阿里云）
uv run --extra test pytest -q tests/test_translation.py tests/test_prompt_store.py

# 检查补丁中是否有空白错误
git diff --check
```

如果只修改了前端样式，可以先运行对应测试文件和 `node --check`，再决定是否运行完整测试集。完整测试失败时要区分本次改动引入的问题和已有基线失败。

## 安全约定

- 不在日志、截图、测试输出、文档或 git 中打印 API Key、账号凭证、代理密码和带 token 的 URL。
- 不把用户原始输入文件复制进项目目录；需要落盘的输入统一保存到 `data/input/<来源>/`，剪贴板图片位于 `data/input/pasted/`。
- 不为了修复单个任务删除整个 `data/` 或 SQLite 数据库；先备份并定位具体记录。
- 测试默认不创建真实远程任务，不产生积分、RH 币或费用。

## UI 交付清单

- 任务提交页的输入框和下拉框遵循公共控件字号 token。
- 桌面端左侧导航保持固定，不因右侧内容高度变化而跳动。
- 日夜主题下均检查文本、边框、按钮、弹窗和错误状态的对比度。
- 拖放区覆盖完整输入卡片，悬停、预览、路径写回和选择文件行为不能互相覆盖。
- 图标按钮有 `aria-label`，键盘操作可用，减少动效设置下仍能完成主要流程。

## 连接 ComfyUI

1. 打开「设置 → 平台配置 → ComfyUI 服务器」。填写本机地址（如 `127.0.0.1:8188`）、局域网主机端口，或云端 ComfyUI 的 HTTP(S) 地址。反向代理部署可以填写路径前缀，例如 `https://comfy.example.com/comfy`。
2. 如网关使用 Bearer 认证，将令牌填在独立的 Token 字段。保存配置后点击「测试已保存的连接」。
3. 在「设置 → 平台配置 → ComfyUI 服务器」保存默认执行平台和 ComfyUI 地址。在「任务提交」导入 ComfyUI 导出的 API 格式 JSON，并在本次任务的「执行平台」中选择 RunningHub 或本地 ComfyUI。选择本地文件时，Desk 会上传后替换节点输入；留空的文件输入可沿用服务器已有的相对文件名。ComfyUI 服务器需要安装工作流所用的节点和模型。
4. 加入任务队列后，Desk 会提交工作流、查询进度并把产物保存到任务输出目录。已提交的任务按原服务器地址恢复查询。从历史任务加载工作流后，执行平台默认选回该任务原来的平台，仍可在提交前切换。

任务页的选择决定当前工作流任务实际提交到的平台；设置中保存的平台作为默认值，也用于没有指定平台的 Telegram 入站和 API 请求。RunningHub 任务使用工作流的 workflowId 和 API Key 调度。Desk 可撤销服务器上仍在排队的 ComfyUI 任务。对已开始的任务，取消会停止本地等待，远程任务可能继续运行。

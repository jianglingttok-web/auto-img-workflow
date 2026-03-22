# TK 自动上架工作流

基于 [TK自动上架系统项目说明文件_v0.3_Codex版.md](./TK自动上架系统项目说明文件_v0.3_Codex版.md) 调整后的 MVP 项目骨架。

当前方案吸收了 v0.3 的核心分层：

- 阶段 A：AI 内容生产
- 阶段 B：自动上架执行

系统主线固定为：

```text
product_brief.json
  -> image_assets.json
  -> copy_assets.json
  -> listing_package.json
  -> publish result + screenshots + logs
```

## 设计原则

- AI 只生成内容，不直接操作页面
- 浏览器执行层只消费校验通过的 `listing_package.json`
- 所有关键步骤必须落日志、状态、截图、结构化错误
- Python 自动化优先，RPA 仅作为可替换执行层
- MVP 优先单站点、单类目、单商品串行

## 目录结构

```text
docs/                     架构与实施说明
schemas/                  JSON Schema
scripts/                  本机部署检查脚本
templates/                业务规则模板
src/tk_listing_workflow/  工作流代码
runtime/tasks/            任务运行产物
config.example.yaml       配置模板
AGENTS.md                 仓库执行规则
```

## 本机部署检查

先在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_env.ps1
```

这个脚本会检查：

- Python 3.11+
- `pip` 与 `Pillow`
- 项目包是否可从 `src/` 导入
- `config.example.yaml`、`config.yaml`、`.env` 是否齐全
- `ARK_API_KEY` 等当前真实使用到的环境变量
- `runtime/` 目录是否可写

当前 CLI 启动时会自动读取项目根目录下的 `.env` 和 `config.yaml`，再把配置注入运行环境。
`ARK_API_KEY` 放在 `.env` 里即可被项目自动加载。

## 初始化开发环境

推荐使用项目内虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

如果你的机器还没有可用的 Python 解释器，请先安装 Python 3.11 或更高版本，再重新运行 `.\scripts\check_env.ps1`。

## 环境变量

可以先参考 `.env.example` 或直接编辑项目根目录的 `.env`：

```powershell
ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_TEXT_MODEL=doubao-seed-2-0-lite-260215
ARK_TEXT_TEMPERATURE=0.3
SEEDREAM_MODEL=doubao-seedream-4-5-251128
```

其中：

- `ARK_API_KEY`：执行 `run-seedream-jobs` 和 Doubao 文本 prompt 生成时都会使用
- `ARK_TEXT_MODEL`：当前推荐 `doubao-seed-2-0-lite-260215`，用于 `build-oc-output` 阶段生成主图和副图 prompts
- `ARK_TEXT_TEMPERATURE`：默认 `0.3`，控制 prompt 文本发散度
- `ARK_BASE_URL`、`SEEDREAM_MODEL` 等：可选覆盖默认值
- 默认推荐 `SEEDREAM_RESPONSE_FORMAT=b64_json`，在当前机器上比 `url` 模式更稳定
- 项目会优先使用根目录下的 `.env` 和 `config.yaml`
- 飞书初测至少需要补齐 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_IMAGE_TASK_APP_TOKEN`、`FEISHU_IMAGE_TASK_TABLE_ID`

## 飞书初测

当前已支持两条最小命令：

```powershell
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli feishu-list-image-tasks --page-size 5
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli import-feishu-image-task-live --record-id <record_id>
```

说明：

- `feishu-list-image-tasks`：读取飞书多维表记录并输出任务摘要，方便先确认字段映射是否正确。
- `import-feishu-image-task-live`：把单条飞书记录落到 `runtime/tasks/<任务ID>/`，并自动生成 `product_brief.json`、`image_task.json` 等首批工作流文件。
- 当前版本已支持“生成结果回传 + 审核结果回拉”的联调命令：

```powershell
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli backfill-feishu-image-task --task-dir .\runtime\tasks\<任务ID> --round 1 --task-status 待审核副图 --review-status 待审核
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli notify-feishu-image-review --task-dir .\runtime\tasks\<任务ID> --round 1
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli sync-feishu-image-review --task-dir .\runtime\tasks\<任务ID>
```

- 如果 `backfill-feishu-image-task` 返回 403，通常说明当前飞书应用还没有这张多维表的写权限；这种情况下仍可以先用 `notify-feishu-image-review` 发预览图，再手动在飞书表里改审核状态，最后用 `sync-feishu-image-review` 把结论拉回本地工作流。

## 快速开始

```powershell
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli init-task --task-id TK-20260318-0001 --product-id P001 --market TH --shop-id shop_demo --category "Beauty & Personal Care"
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli show-task --task-dir .\runtime\tasks\TK-20260318-0001
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli advance --task-dir .\runtime\tasks\TK-20260318-0001 --to image_generation_pending
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli build-package --task-dir .\runtime\tasks\TK-20260318-0001
.\.venv\Scripts\python.exe -m tk_listing_workflow.cli preflight --task-dir .\runtime\tasks\TK-20260318-0001
```

## 当前已落地内容

- 新版状态机
- `product_brief.json`、`image_assets.json`、`copy_assets.json`、`listing_package.json` 初始化
- `build-package` 组装链路
- 基础 preflight 校验
- OpenClaw 执行器接口占位
- 紫鸟启动器与浏览器会话占位
- TikTok 发布执行器占位
- Feishu 通知与统一错误结构占位

## 下一步建议

1. 固定首个站点、类目、店铺和紫鸟 profile。
2. 补齐 `templates/` 下的业务规则文件。
3. 把真实 `product_brief.json` 样例放进任务目录。
4. 接入飞书审核回调。
5. 把 OpenClaw、紫鸟 API、Playwright 发布流程逐个接上。






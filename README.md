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
templates/                业务规则模板
src/tk_listing_workflow/  工作流代码
runtime/tasks/            任务运行产物
config.example.yaml       配置模板
AGENTS.md                 仓库执行规则
```

## 快速开始

```powershell
$env:PYTHONPATH = ".\src"
py -3 -m tk_listing_workflow.cli init-task --task-id TK-20260318-0001 --product-id P001 --market TH --shop-id shop_demo --category "Beauty & Personal Care"
py -3 -m tk_listing_workflow.cli show-task --task-dir .\runtime\tasks\TK-20260318-0001
py -3 -m tk_listing_workflow.cli advance --task-dir .\runtime\tasks\TK-20260318-0001 --to image_generation_pending
py -3 -m tk_listing_workflow.cli build-package --task-dir .\runtime\tasks\TK-20260318-0001
py -3 -m tk_listing_workflow.cli preflight --task-dir .\runtime\tasks\TK-20260318-0001
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

# 架构说明

## 1. 总体目标

本项目按 v0.3 拆成两个明确阶段：

- 阶段 A：AI 内容生产
- 阶段 B：自动上架执行

阶段间唯一执行输入是 `listing_package.json`。这条边界必须稳定，避免 AI 输出直接驱动浏览器操作。

## 2. 核心主线

```text
飞书生图任务表
  -> product_brief.json
  -> image_assets.json
  -> image review
  -> copy_assets.json
  -> copy review
  -> listing_package.json
  -> publish executor
  -> Feishu notify + logs + screenshots
```

## 3. 状态机

```text
product_created
image_generation_pending
image_review_pending
image_review_passed
copy_generation_pending
copy_review_pending
copy_review_passed
listing_ready
publish_pending
publishing
publish_success
publish_failed
manual_check_pending
completed
```

要求：

- 每次状态变化必须写日志
- 每次状态变化必须可推送飞书
- 失败时必须带 `error_code`、`error_message`、`screenshot_path`

## 4. 模块职责

### Feishu Intake Layer
- 飞书多维表作为当前阶段任务入口
- 只收集生图必需字段，不承载全流程所有字段
- 通过 `运营组 + 提交人 + 负责人 + 视图隔离` 区分多团队协作

### AI Layer
- `ai/image_workflow.py`
- `ai/copy_workflow.py`
- `executors/openclaw.py`

职责：

- 读取 `product_brief.json`
- 生成结构化素材与文案
- 输出标准 JSON
- 推送审核

### Review Layer
- `review/feishu_review.py`
- `integrations/feishu_notifier.py`

职责：

- 审核通知
- 审核结果回写
- 状态推进

### Data Layer
- `data/build_listing_package.py`
- `utils/validator.py`

职责：

- 汇总图片、文案、属性、SKU、价格
- 组装 `listing_package.json`
- 做发布前校验

### Executor Layer
- `executor/launcher.py`
- `executor/browser_session.py`
- `executor/publish_flow.py`

职责：

- 连接紫鸟环境
- 管理浏览器会话
- 执行 TikTok 草稿发布
- 保留截图和错误证据

## 5. 任务目录规范

```text
runtime/tasks/<task_id>/
  manifest.json
  product_brief.json
  image_assets.json
  copy_assets.json
  listing_package.json
  intake/
  media/
  copy/
  review/
  publish/
  logs/
  screenshots/
```

## 6. 飞书表设计原则

- 入口表和进度表分离
- `生图任务表` 只服务第一步套图制作
- `项目进度表` 只服务管理看板与跨阶段汇总
- 不建议每个运营组单独复制一套底表
- 推荐一张统一底表 + 多视图隔离

详见：`docs/feishu_tables.md`

## 7. 采纳与调整结论

直接采纳 v0.3 的部分：

- 两阶段分层
- `product_brief -> image_assets -> copy_assets -> listing_package` 文件链路
- 紫鸟 API + 浏览器执行层边界
- 统一结构化失败输出
- `config.example.yaml` 与 `AGENTS.md`

继续保留当前骨架的部分：

- 任务目录式运行时结构
- `manifest.json` 作为全局状态与证据索引
- 模块化替换能力
- 先做单任务 MVP 再扩展

## 8. 后续需要补齐的外部资产

- 主图规则
- 副图规则
- 标题规则
- 详情模板
- A+ 图模板
- TikTok 类目模板
- 价格公式
- 飞书审批规范
- 店铺映射表
- 紫鸟环境映射

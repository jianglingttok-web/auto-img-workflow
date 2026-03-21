# TK 自动上架系统项目说明文件 v0.3（Codex执行版）

## 1. 项目目标

构建一套面向 TikTok Shop（TK）的自动化上架系统，分为两个阶段：

### 阶段 A：AI 内容生产
负责：
- 生成商品主图、副图、详情图、A+图
- 生成标题、卖点、详情文案、A+文案
- 通过飞书完成人工审核
- 生成结构化的 `listing_package.json`

### 阶段 B：自动上架执行
负责：
- 读取 `listing_package.json`
- 通过紫鸟 API 打开指定指纹浏览器环境
- 自动进入 TK 店铺后台
- 自动填写商品信息、上传图片、填写属性/SKU/价格/库存
- 保存草稿或发布
- 回传飞书通知、日志、截图、错误信息

---

## 2. 给 Codex 的执行原则

1. 先做 MVP，再扩展，不要一上来做全量复杂功能。
2. 所有功能必须模块化，可单测，可替换。
3. AI 负责生成内容，不直接驱动页面操作。
4. 页面操作层只消费结构化数据，不参与内容判断。
5. 默认优先 Python 自动化执行；若后续评估 RPA 更稳，执行层需要可替换。
6. 所有关键节点必须记录日志、截图、状态、错误码。
7. 任何外部平台选择，优先复用现有接口：紫鸟 API、飞书 webhook / OpenAPI。
8. 输出代码时，优先可运行、可调试、可观测，而不是抽象概念设计。

---

## 3. 总体架构

```text
product_brief.json
    ↓
AI image workflow
    ↓
image review (Feishu)
    ↓
AI copy workflow
    ↓
copy review (Feishu)
    ↓
listing_package.json
    ↓
executor (Python first, RPA optional)
    ↓
TK Seller Center
    ↓
Feishu notify + logs + screenshots
```

---

## 4. 系统边界

## 本项目包含
- 商品素材生成工作流
- 商品文案生成工作流
- 飞书审核通知与状态流转
- Listing Package 结构化数据层
- TK 后台自动上架执行层
- 日志、截图、异常回传

## 本项目暂不包含
- 自动投流
- 广告创建
- 自动改价
- ERP 全量整合
- 多平台泛化
- 复杂库存中台
- 财务结算系统

---

## 5. 输入输出标准

## 5.1 输入：product_brief.json

```json
{
  "product_id": "P001",
  "product_name": "example product",
  "shop_id": "SHOP001",
  "target_market": "TH",
  "category_hint": "Beauty & Personal Care",
  "selling_points": [
    "small size",
    "easy to carry",
    "good price",
    "fits summer use"
  ],
  "target_user": "female 18-30",
  "price_range": "10-15",
  "style": "native, conversion-oriented, platform-compliant",
  "image_rules": {
    "main_image_count": 1,
    "sub_image_count": 8
  },
  "attribute_template": {},
  "sku_template": [],
  "competitor_links": [],
  "compliance_rules": [],
  "notes": ""
}
```

## 5.2 中间输出：image_assets.json

```json
{
  "product_id": "P001",
  "main_images": [],
  "sub_images": [],
  "detail_images": [],
  "a_plus_images": [],
  "generation_meta": {
    "tool": "",
    "version": "",
    "created_at": ""
  }
}
```

## 5.3 中间输出：copy_assets.json

```json
{
  "product_id": "P001",
  "title": "",
  "bullet_points": [],
  "description_blocks": [],
  "a_plus_copy": []
}
```

## 5.4 最终输出：listing_package.json

```json
{
  "shop_id": "SHOP001",
  "product_id": "P001",
  "target_market": "TH",
  "category": "",
  "title": "",
  "main_images": [],
  "sub_images": [],
  "detail_images": [],
  "a_plus_images": [],
  "description_blocks": [],
  "attributes": {},
  "skus": [
    {
      "sku_id": "SKU001",
      "variant": {},
      "price": 19.9,
      "inventory": 100
    }
  ],
  "price_strategy": {},
  "publish_mode": "draft",
  "review_status": {
    "image_review": "passed",
    "copy_review": "passed"
  }
}
```

---

## 6. 状态机设计

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
- 每次状态变化必须写入日志
- 每次状态变化必须可推送飞书
- 失败状态必须附带 `error_code`、`error_message`、`screenshot_path`

---

## 7. 模块拆解

## 7.1 ai/image_workflow.py
职责：
- 读取 `product_brief.json`
- 构建主图、副图、详情图、A+图的 prompt
- 调用图像生成接口或本地适配器
- 保存图片到标准目录
- 输出 `image_assets.json`
- 通知飞书进入图片审核

输入：
- `product_brief.json`

输出：
- `image_assets.json`

注意：
- 默认不用 RPA
- 只有当某工具没有 API 且必须网页登录时，才为该工具单独增加适配器，不影响主流程

## 7.2 ai/copy_workflow.py
职责：
- 读取 `product_brief.json` + 已审核图片信息
- 生成标题、卖点、详情文案、A+文案
- 输出 `copy_assets.json`
- 通知飞书进入文案审核

## 7.3 review/feishu_review.py
职责：
- 推送审核消息
- 接收审核结果
- 更新状态
- 审核通过后允许进入下一阶段

审核粒度：
- 图片审核：通过 / 打回 / 备注
- 文案审核：通过 / 打回 / 备注

## 7.4 data/build_listing_package.py
职责：
- 读取审核通过的图片、文案、属性、SKU、价格信息
- 组装统一 `listing_package.json`
- 校验必填字段
- 输出校验结果

校验项至少包括：
- title 非空
- 主图数量符合要求
- SKU 至少 1 条
- 价格为正数
- inventory 非负
- 审核状态必须通过

## 7.5 executor/launcher.py
职责：
- 根据 `shop_id` 调用紫鸟 API
- 启动对应浏览器环境
- 返回调试端口、ws endpoint 或浏览器连接信息

## 7.6 executor/browser_session.py
职责：
- 管理浏览器连接
- 创建页面会话
- 封装通用等待、截图、重试、刷新逻辑

建议能力：
- `connect()`
- `new_page()`
- `save_screenshot(step_name)`
- `retry(operation_name, times=2)`
- `wait_for_stable()`

## 7.7 executor/publish_flow.py
职责：
- 读取 `listing_package.json`
- 进入 TK 发布页
- 填写标题、类目、属性、SKU、价格、库存
- 上传主图/副图/详情图/A+图
- 保存草稿或发布
- 获取发布结果链接
- 回传执行结果

输出：
```json
{
  "status": "success",
  "product_url": "",
  "draft_id": "",
  "error_code": "",
  "error_message": "",
  "screenshots": []
}
```

## 7.8 integrations/feishu_notifier.py
职责：
- 发送阶段通知
- 发送成功通知
- 发送失败通知
- 附带日志摘要、链接、截图路径

## 7.9 utils/logger.py
职责：
- 统一日志格式
- 同时输出到控制台和文件

推荐日志字段：
- timestamp
- level
- product_id
- shop_id
- stage
- step
- message
- error_code

## 7.10 utils/validator.py
职责：
- 校验输入 JSON
- 校验字段类型
- 校验路径是否存在
- 校验图片数量、SKU 数量、价格范围

---

## 8. 推荐目录结构

```text
project/
├── input/
│   └── product_brief.json
├── assets/
│   └── P001/
│       ├── main/
│       ├── sub/
│       ├── detail/
│       └── a_plus/
├── ai/
│   ├── image_workflow.py
│   ├── copy_workflow.py
│   └── prompt_builder.py
├── review/
│   └── feishu_review.py
├── data/
│   ├── image_assets.json
│   ├── copy_assets.json
│   ├── listing_package.json
│   └── build_listing_package.py
├── executor/
│   ├── launcher.py
│   ├── browser_session.py
│   ├── publish_flow.py
│   ├── uploader.py
│   └── form_filler.py
├── integrations/
│   └── feishu_notifier.py
├── utils/
│   ├── logger.py
│   ├── validator.py
│   ├── retry.py
│   └── paths.py
├── logs/
├── screenshots/
├── tests/
│   ├── test_validator.py
│   ├── test_build_listing_package.py
│   └── test_publish_flow_mock.py
├── config.example.yaml
├── AGENTS.md
└── main.py
```

---

## 9. 建议技术栈

## 核心
- Python 3.11+
- Playwright（优先）
- requests / httpx
- pydantic
- loguru 或 logging
- pytest

## 可选
- RPA 工具作为执行层备选
- queue / scheduler 作为批量任务扩展
- sqlite 或轻量数据库记录任务状态

说明：
- Codex 官方文档强调配置、AGENTS.md、Skills、非交互运行与长任务组织，这个项目应优先按“可配置、可拆分、可自动运行”的方式组织。citeturn438905search0turn438905search1

---

## 10. AGENTS.md 建议内容

Codex 仓库根目录建议放置 `AGENTS.md`，至少包含：

```md
# AGENTS.md

## Goal
Build a TK listing automation system with:
1. AI image workflow
2. AI copy workflow
3. Feishu review flow
4. Listing package builder
5. Browser-based publish executor

## Rules
- Do not let AI-generated text directly operate browser actions.
- Browser executor only consumes validated listing_package.json.
- All steps must log, screenshot, and return structured errors.
- Build MVP first, then expand.
- Prefer Python implementations first.
- Keep executor swappable so RPA can replace the browser executor later.

## Definition of Done
- Can generate listing_package.json from one sample product
- Can open browser via 紫鸟 API
- Can fill minimal publish form
- Can upload test images
- Can save draft
- Can push result to Feishu
```

---

## 11. 配置文件建议

`config.example.yaml`

```yaml
env: dev

ziniu:
  base_url: ""
  api_key: ""
  browser_profile_id: ""

feishu:
  webhook_url: ""
  app_id: ""
  app_secret: ""

paths:
  assets_root: "./assets"
  logs_root: "./logs"
  screenshots_root: "./screenshots"

publish:
  mode: "draft"
  retry_times: 2
  timeout_seconds: 30
```

---

## 12. MVP 交付顺序

## Phase 1
目标：
- 完成 `product_brief.json` -> `image_assets.json`
- 完成飞书图片审核通知
- 人工确认图片通过

## Phase 2
目标：
- 完成文案生成
- 完成文案审核通知
- 输出 `copy_assets.json`

## Phase 3
目标：
- 完成 `listing_package.json`
- 完成字段校验器

## Phase 4
目标：
- 打通紫鸟 API 启动浏览器
- 进入 TK 发布页
- 自动填写标题
- 自动上传 3 张测试图
- 自动填写 1 个 SKU
- 保存草稿

## Phase 5
目标：
- 补全属性、价格、库存、详情图、A+图
- 获取商品链接或草稿信息
- 飞书回传成功/失败结果

---

## 13. 失败处理规范

所有失败返回统一结构：

```json
{
  "status": "failed",
  "stage": "publish",
  "step": "upload_main_images",
  "error_code": "UPLOAD_TIMEOUT",
  "error_message": "image upload timeout after 30 seconds",
  "screenshot_path": "./screenshots/P001/upload_main_images.png",
  "retryable": true
}
```

错误分类建议：
- VALIDATION_ERROR
- ZINIU_API_ERROR
- BROWSER_CONNECT_ERROR
- PAGE_LOAD_TIMEOUT
- SELECTOR_NOT_FOUND
- IMAGE_UPLOAD_TIMEOUT
- FORM_SUBMIT_FAILED
- FEISHU_NOTIFY_FAILED

---

## 14. 测试要求

至少实现：
- 输入校验单测
- Listing Package 构建单测
- 执行层 mock 测试
- 关键步骤截图验证
- 一次真实环境草稿发布测试

成功验收标准：
1. 单品流程可跑通
2. 草稿发布连续成功 5 次
3. 所有失败有结构化错误
4. 日志和截图完整
5. 飞书通知可用

---

## 15. 实施优先级

P0：
- 输入结构
- 图片生成
- 文案生成
- 审核通知
- listing_package 生成
- 浏览器启动
- 草稿保存

P1：
- 完整属性填写
- 完整 SKU 流程
- 发布成功回传
- 重试机制
- 批量任务

P2：
- 执行层替换成 RPA
- 多店铺并发
- 更复杂的类目适配
- 调度与任务队列

---

## 16. 最终结论

本项目采用以下策略：

- 前置 AI 内容生产工作流：默认不使用 RPA
- 后置 TK 自动上架执行层：优先 Python 自动化，保留 RPA 替换能力
- 全流程以 `listing_package.json` 为唯一执行输入
- 必须保留飞书人工审核节点
- 必须保证状态、日志、截图、错误回传完整

一句话定义：

这是一个面向 TK 的“AI生成 + 人审 + 结构化数据 + 自动执行”的标准化上架流水线。

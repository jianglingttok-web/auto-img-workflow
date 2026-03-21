# 生图工作流规范

## 1. 目标与范围

当前版本只覆盖第一阶段：`Seedream 图生图`。

范围内：

- 飞书接收生图任务
- Python 工作流做任务编排
- OC 做 prompt 分流、变量提取、副图职责规划
- Seedream 执行图生图
- 飞书回传图片给 `提交人`
- 提交人审核通过或打回
- 支持最多 3 轮重做

范围外：

- 文生图
- listing package 组装
- 紫鸟/TikTok 发布
- 指定副图粒度的单图重做

## 2. 角色分工

### 飞书

职责：

- 任务提交入口
- 任务状态展示
- 图片结果回传
- 审核动作入口

边界：

- 不负责执行图生图
- 不负责编排模型调用

### Python 工作流

职责：

- 读取飞书记录
- 校验字段
- 生成任务目录
- 推进状态机
- 调用 OC
- 调用 Seedream
- 保存结果
- 回传飞书

### OpenClaw

职责：

- 判断 `task_mode`
- 判断 `prompt_mode`
- 提取并整理 prompt 变量
- 输出主图 prompt
- 输出副图职责规划 `sub_image_plan`
- 输出副图 prompt 列表
- 根据打回原因重写 prompt

边界：

- 不直接操作飞书
- 不直接操作 Seedream
- 不负责文件归档
- 不负责状态推进

### Seedream

职责：

- 执行图生图
- 返回生成图片结果

## 3. 飞书入口字段

当前第一版建议飞书生图任务表至少包含以下字段：

| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| 任务ID | 文本 | 是 | 系统唯一标识 |
| 提交人 | 创建人 | 自动 | 用于结果定向回传 |
| 运营组 | 单选 | 是 | 团队归属 |
| 店铺 | 单选/关联记录 | 是 | 任务归属店铺 |
| 站点 | 单选 | 是 | TH/ID/MY/PH/VN 等 |
| 产品名称 | 文本 | 是 | 产品基础名称 |
| 产品卖点 | 多行文本 | 是 | 一行一个卖点 |
| 风格要求 | 多行文本 | 否 | 视觉风格限制 |
| 合规要求 | 多行文本 | 否 | 一行一个约束 |
| 产品白底图 | 附件 | 是 | 产品主体真实性基准 |
| 使用图 | 附件 | 否 | 场景、动作、构图参考 |
| 已有主图/风格参考图 | 附件 | 否 | 仅副图模式时必需 |
| 主图数量 | 数字 | 是 | 用于自动判定任务模式 |
| 副图数量 | 数字 | 是 | 用于自动判定任务模式 |
| 营销表达 | 多行文本 | 否 | benefit_copy 可选输入 |
| 数字信息 | 多行文本 | 否 | benefit_copy 可选输入 |
| 补充说明 | 多行文本 | 否 | 额外要求 |
| 图片任务状态 | 单选 | 是 | 流程主状态 |
| 图片审核状态 | 单选 | 是 | 审核结果 |
| 审核意见 | 多行文本 | 否 | 打回原因或补充说明 |
| 主图预览 | 附件 | 否 | 当前轮次主图预览 |
| 副图拼图预览 | 附件 | 否 | 当前轮次副图总览拼图 |
| 完整图片包链接 | 多行文本 | 否 | 完整图片目录、对象存储或压缩包链接 |
| 套图结果 | 附件 | 否 | 最终图片产物，可选保留 |
| 套图结果链接 | 多行文本 | 否 | 图片目录或对象存储链接，可选保留 |
| 最新轮次 | 数字 | 否 | 当前产出轮次 |
| 异常原因 | 多行文本 | 否 | 错误原因 |

填写约束：

- `产品卖点`、`合规要求`、`营销表达`、`数字信息` 建议一行一条。
- `主图数量`、`副图数量` 必须为非负整数。
- `主图数量 = 0` 且 `副图数量 = 0` 直接拦截。
- `仅副图` 模式时，`已有主图/风格参考图` 必须有值。

## 4. 任务模式判定

系统根据数量自动推断：

- `主图数量 > 0` 且 `副图数量 = 0`
  - `task_mode = main_only`
- `主图数量 = 0` 且 `副图数量 > 0`
  - `task_mode = sub_only`
- `主图数量 > 0` 且 `副图数量 > 0`
  - `task_mode = full_set`

说明：

- 第一版不开放 `指定副图` 模式。
- `full_set` 默认先跑主图，主图通过后再跑副图。

## 5. Prompt Mode 判定

OC 负责把任务归为以下三种之一：

- `visual_only`
- `benefit_copy`
- `strict_packaging_only`

判定原则：

- 饰品、展示型产品默认 `visual_only`
- 功效型产品可走 `benefit_copy`
- 包装文字必须严格保留、不能新增文字时走 `strict_packaging_only`

人工可以覆盖 OC 的 `prompt_mode` 判断结果。

## 6. 工作流主线

### `full_set`

1. 校验飞书字段
2. 创建任务目录
3. 写入标准化输入文件
4. 调用 OC 生成主图 prompt
5. 调用 Seedream 生成主图
6. 回传主图给提交人审核
7. 审核通过后，调用 OC 输出 `sub_image_plan`
8. 调用 OC 生成副图 prompts
9. 调用 Seedream 生成副图
10. 回传副图给提交人审核
11. 审核通过后交付整套图并标记 `已交付`

### `main_only`

1. 校验飞书字段
2. 创建任务目录
3. 调用 OC 生成主图 prompt
4. 调用 Seedream 生成主图
5. 回传主图给提交人审核
6. 通过后直接交付

### `sub_only`

1. 校验飞书字段与风格基准
2. 创建任务目录
3. 调用 OC 输出 `sub_image_plan`
4. 调用 OC 生成副图 prompts
5. 调用 Seedream 生成副图
6. 回传副图给提交人审核
7. 通过后直接交付

## 7. OC 输入规范

建议传给 OC 的输入 JSON：

```json
{
  "task_id": "IMG-20260321-0001",
  "task_mode": "full_set",
  "product_name": "示例产品",
  "site": "TH",
  "shop_id": "TH_shop_01",
  "selling_points": ["卖点1", "卖点2"],
  "style_requirements": ["高级感", "浅色背景"],
  "compliance_requirements": ["不得夸大功效"],
  "marketing_phrases": ["买一送一"],
  "numeric_claims": ["1700ml"],
  "reference_images": {
    "product_white_background": ["runtime/tasks/.../intake/product_white_01.jpg"],
    "usage_images": ["runtime/tasks/.../intake/usage_01.jpg"],
    "style_reference_images": ["runtime/tasks/.../intake/style_ref_01.jpg"]
  },
  "requested_output": {
    "main_count": 1,
    "sub_count": 8
  },
  "rework": {
    "round": 1,
    "reason": "",
    "scope": ""
  }
}
```

## 8. OC 输出规范

### 主图阶段输出

```json
{
  "task_mode": "full_set",
  "prompt_mode": "visual_only",
  "reason": "饰品类，适合纯展示型表达",
  "variables": {
    "style_summary": "高级、干净、偏生活化",
    "usage_scene_summary": "日常佩戴场景",
    "marketing_phrases": [],
    "numeric_claims": []
  },
  "main_image_prompt": "..."
}
```

### 副图阶段输出

```json
{
  "task_mode": "full_set",
  "prompt_mode": "visual_only",
  "sub_image_plan": [
    { "slot": "sub_01", "role": "核心卖点图" },
    { "slot": "sub_02", "role": "使用场景图" },
    { "slot": "sub_03", "role": "细节特写图" }
  ],
  "sub_image_prompts": [
    { "slot": "sub_01", "prompt": "..." },
    { "slot": "sub_02", "prompt": "..." },
    { "slot": "sub_03", "prompt": "..." }
  ]
}
```

要求：

- OC 必须先给出 `sub_image_plan`，再给出 `sub_image_prompts`。
- `sub_image_prompts` 的数量应与 `副图数量` 一致。
- 不得生成超出当前任务范围的图位。

## 9. Seedream 执行输入规范

Python 在调用 Seedream 前，需要把上游输入整理为稳定执行包。

建议结构：

```json
{
  "task_id": "IMG-20260321-0001",
  "round": 1,
  "image_type": "main",
  "slot": "main_01",
  "prompt_mode": "visual_only",
  "prompt": "...",
  "reference_images": {
    "product_white_background": ["runtime/tasks/.../intake/product_white_01.jpg"],
    "usage_images": ["runtime/tasks/.../intake/usage_01.jpg"]
  },
  "output_spec": {
    "ratio": "1:1",
    "count": 1
  }
}
```

副图执行时：

- `image_type = sub`
- `slot = sub_01 ~ sub_08`
- 每个槽位单独执行，便于记录和重做

## 10. 结果目录规范

```text
runtime/tasks/<task_id>/
  manifest.json
  product_brief.json
  intake/
    product_white_01.jpg
    usage_01.jpg
    style_ref_01.jpg
  prompts/
    round_01_main.json
    round_01_sub_plan.json
    round_01_sub_prompts.json
  media/
    round_01/
      main/
        main_01.jpg
      sub/
        sub_01.jpg
        sub_02.jpg
      preview/
        main_preview.jpg
        sub_contact_sheet.jpg
    round_02/
      ...
  review/
    round_01_main_review.json
    round_01_sub_review.json
  logs/
```

原则：

- 不覆盖历史轮次
- 主图、副图、prompt、review 分开存放
- 每轮都可追溯
- `preview/` 目录专门存飞书回传所需预览图

## 11. 状态机

建议图片任务状态：

- `待处理`
- `生图中`
- `待审核主图`
- `待生成副图`
- `待审核副图`
- `重做中`
- `已通过`
- `已交付`
- `异常`
- `待人工处理`

图片审核状态：

- `待审核`
- `已通过`
- `已打回`

规则：

- 主图审核通过后，`full_set` 才允许进入副图阶段。
- 全部结果交付给提交人后，自动标记 `已交付`。
- 最多允许 3 轮重做，超过后进入 `待人工处理`。

## 12. 回传与审核规则

回传目标：

- 默认只回传给 `提交人`
- 异常时可扩展同时通知 `负责人`

### 飞书回传格式

第一版采用 `方案 A`，不在飞书里一次铺开全部 9 张图。

回传内容固定为：

- `任务ID`
- `产品名称`
- `当前轮次`
- `主图预览`
- `副图拼图预览`
- `完整图片包链接`
- `审核状态`
- `通过 / 打回`

说明：

- `主图预览` 默认取当前轮次主图第一张。
- `副图拼图预览` 为一张 `2 x 4` 或 `4 x 2` 的副图总览图。
- `完整图片包链接` 指向完整图片目录、对象存储链接或压缩包链接。
- 飞书只负责看最新结果和执行审核动作，不承担完整图库浏览。

### 副图拼图预览规则

- 拼图应包含当前轮次全部副图。
- 每张副图角标标记 `sub_01 ~ sub_08`。
- 如果当前任务副图数量不足 8，则按实际数量拼图。
- 拼图只作为快速预览，不替代完整图片包。

审核动作：

- `通过`
- `打回`

审核通过：

- `main_only` 直接交付主图
- `sub_only` 直接交付副图
- `full_set` 主图通过后继续跑副图；副图通过后交付整套图

审核打回：

- 记录 `审核意见`
- 生成新一轮输入
- 调用 OC 基于打回原因重写 prompt

## 13. Token 与成本控制原则

OC 使用原则：

- 只传当前轮次必要信息
- 不把完整历史任务全文反复传给 OC
- 局部重做时只传本轮问题和必要上下文
- 规则化动作不用 OC

Seedream 使用原则：

- 主图和副图分开执行
- 副图按槽位逐张生成，避免大批量混跑难追踪
- 记录每轮输入与输出，便于分析成本与效果

## 14. 第一版落地建议

第一版先做这几个稳定接口：

1. `feishu_record -> standardized task input`
2. `standardized task input -> oc_input.json`
3. `oc_output.json -> seedream jobs`
4. `seedream results -> feishu return payload`
5. `review decision -> next round input`

先把接口打稳，再考虑：

- 文生图路线
- 多模型路由
- 自动质量初筛
- 更细粒度重做

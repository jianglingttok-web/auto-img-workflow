# TK 裂变素材工厂 - v4 项目规划

## 项目目标

解决团队裂变图片生产痛点：
- ❌ 原来：运营在多个第三方后台切换 → 管理混乱
- ❌ 原来：每个平台单独充值 → 成本不可控
- ❌ 原来：没有统一提示词模板 → 质量不稳定
- ✅ 目标：统一入口 + 统一成本 + 统一质量 + 异步批量生成 → 提升裂变素材生产效率

最终服务于：TikTok Shop 广告裂变素材批量生产，图片做为视频制作前置素材，即用即弃。

---

## 核心设计决策

| 设计项 | 决策 |
|---|---|
| 输入要求 | `product_image` **必填** + `reference_image` **必填**（裂变质量依赖参考，不能省略） |
| 模型选择 | 运营**直接选具体模型**，每个选项显示 `模型名称 - $价格/张`，直白不绕弯 |
| 模型分层 | `prompt provider`（生成优化提示词）+ `image provider`（生成图片）两层分离，解耦方便扩展 |
| 裂变类型 | 固定枚举：`same_product_fission`（同一产品裂变，V1 正式支持）+ `same_style_product_swap`（同风格换产品，接口保留不保证质量） |
| 并发控制 | 最大并发可配置，任务队列持久化 SQLite，服务重启不丢任务，超出自动排队返回位置 |
| 成本记录 | `estimated_cost`（配置估算）+ `actual_cost`（API 返回实际）分开存储，方便后续对账 |
| 幂等保护 | `request_fingerprint` 防重复提交，避免重复花钱 |
| 过期清理 | 配置 TTL（推荐 48 小时），定时自动清理过期任务，节省磁盘空间 |
| 飞书通知 | 可选功能，配置了就发通知，不配置不影响核心流程 |
| V1 范围 | 架构支持多 provider，但 V1 只接火山，后续逐个扩展 |

---

## 配置文件格式 (`config.yaml`)

```yaml
# 网页服务配置
web:
  host: "0.0.0.0"
  port: 8000
  max_concurrent: 4         # 最大并发生成数
  cleanup_ttl_hours: 48      # 结果保留时间（小时）
  cleanup_interval_hours: 24 # 清理间隔（小时）
  runtime_dir: "runtime/web-tasks"
  data_dir: "runtime/web-data"

# ========== 两层模型分离 ==========
# prompt 生成（整理文案、填充模板）
prompt_engine:
  provider: volcengine
  model_id: doubao-seed-2-0-lite-260215
  temperature: 0.3

# 提供商配置（架构预留，V1 只开火山）
providers:
  volcengine:
    enabled: true
    api_key: $VOLCANO_ENGINE_API_KEY
    base_url: https://ark.cn-beijing.volces.com/api/v3
    models:
      - id: doubao-seedream-fast
        name: 豆包 Seedream (快速)
        price_per_image: 0.04
      - id: doubao-seedream-4-5-251128
        name: 豆包 Seedream (标准)
        price_per_image: 0.08
  # nanobanana:
  #   enabled: false
  #   api_key: $NANO_BANANA_API_KEY
  #   base_url: https://api.nanobanana.ai/v1
  #   models:
  #     - id: nano-banana-v1
  #       name: Nano Banana
  #       price_per_image: 0.12
  # openai:
  #   enabled: false
  #   api_key: $OPENAI_API_KEY
  #   base_url: https://api.openai.com/v1
  #   models:
  #     - id: dall-e-3
  #       name: DALL-E 3 (1024x1792)
  #       price_per_image: 0.04

# 飞书通知（可选，默认关闭）
feishu_web:
  enabled: false
  webhook: $FEISHU_WEBHOOK
```

---

## 项目结构

```
tk_listing_workflow/
├── web/
│   ├── __init__.py
│   ├── app.py              # FastAPI 主应用入口
│   ├── schemas.py          # Pydantic 模型定义
│   ├── queue.py            # 持久化任务队列 + 并发控制
│   ├── routes.py           # API 路由
│   └── static/
│       ├── index.html      # 提交页面
│       └── style.css       # 样式
├── providers/
│   ├── __init__.py
│   ├── base.py             # 抽象基类：PromptProvider / ImageProvider
│   └── volcengine.py       # V1 火山实现
├── services/
│   ├── __init__.py
│   ├── task_service.py     # 任务生命周期管理
│   ├── usage_service.py    # 成本记录和汇总
│   └── cleaner.py          # 过期任务自动清理
```

---

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | - | 返回提交页面 HTML |
| `POST /api/tasks` | multipart/form-data | 创建任务，返回 `{ task_id, position_in_queue }` |
| `GET /api/tasks/{id}` | - | 查询任务状态、进度 |
| `GET /api/tasks/{id}/download` | - | 下载结果 ZIP |
| `GET /api/stats/summary` | - | 成本汇总（按月份、按模型）|
| `GET /api/options` | - | 获取选项（站点、裂变类型、可用模型带价格）|

---

## 创建任务请求参数 (multipart/form-data)

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| site | 是 | string | 站点 |
| fission_type | 是 | string | `same_product_fission` / `same_style_product_swap` |
| provider | 是 | string | 图片提供商 |
| model_id | 是 | string | 模型ID |
| count | 是 | int | 生成数量 1-10 |
| notes | 否 | string | 补充说明 |
| product_image | 是 | file | 产品白底图 |
| reference_image | 是 | file | 参考图 |

---

## 数据库 Schema (SQLite)

### `tasks` 表 (任务队列 + 状态)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| task_id | TEXT UNIQUE NOT NULL | 唯一任务ID |
| request_fingerprint | TEXT NOT NULL | 幂等指纹（防重复提交）|
| site | TEXT NOT NULL | 站点 |
| fission_type | TEXT NOT NULL | 枚举 |
| provider | TEXT NOT NULL | 图片提供商 |
| model_id | TEXT NOT NULL | 模型ID |
| price_per_image | REAL NOT NULL | 单张价格 |
| prompt_provider | TEXT NOT NULL | prompt提供商 |
| prompt_model_id | TEXT NOT NULL | prompt模型ID |
| count | INTEGER NOT NULL | 生成数量 |
| estimated_cost | REAL NOT NULL | 估算总价 |
| actual_cost | REAL | 实际总价（可为空）|
| status | TEXT NOT NULL | `pending` / `running` / `succeeded` / `failed` / `expired` |
| product_image_path | TEXT NOT NULL | 上传原图路径 |
| reference_image_path | TEXT NOT NULL | 上传参考图路径 |
| result_zip_path | TEXT | 生成结果ZIP路径 |
| expires_at | REAL NOT NULL | 过期时间戳 |
| notes | TEXT | 补充说明 |
| error_message | TEXT | 失败原因 |
| created_at | REAL NOT NULL | |
| updated_at | REAL NOT NULL | |

### `usage` 表 (每张图消费记录)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| task_id | TEXT NOT NULL | 关联任务 |
| prompt_provider | TEXT NOT NULL | |
| prompt_model_id | TEXT NOT NULL | |
| image_provider | TEXT NOT NULL | |
| image_model_id | TEXT NOT NULL | |
| price_estimated | REAL NOT NULL | 单张估算价格 |
| price_actual | REAL | 单张实际价格 |
| image_path | TEXT NOT NULL | 生成图片路径 |
| created_at | REAL NOT NULL | |

---

## V1 完成标准

- [x] Web 提交页面
- [x] `product_image` + `reference_image` 双必填
- [x] 运营直接选具体模型，显示单价
- [x] SQLite `tasks` 表 + `usage` 表
- [x] 单机异步 worker 并发控制
- [x] 幂等防重复提交
- [x] 生成结果 ZIP 下载
- [x] 48 小时自动清理
- [x] 成本汇总统计（估算 + 实际分开）
- [x] `prompt provider` + `image provider` 两层分离
- [x] `fission_type` 枚举，V1 正式支持 `same_product_fission`
- [x] 架构预留多provider，V1 只接火山
- [x] 飞书通知可选，不配置也能完整运行

## V1 不做（后续扩展）

- [ ] Nano Banana 接入
- [ ] OpenAI 接入
- [ ] `same_style_product_swap` 正式支持
- [ ] 用户登录权限（内网信任访问，V1 不做）

---

## 部署推荐

### 服务器规格
- 厂商：火山引擎 轻量应用服务器
- 配置：**2核 4GB**
- 系统：**Ubuntu 22.04 LTS**
- 带宽：**5M**
- 价格：约 56 元/月（包年更便宜）

### 部署步骤

完整步骤见 [`DEPLOY.md`](./DEPLOY.md)

1. 创建服务器
2. 克隆项目
3. 创建虚拟环境，安装依赖
4. 配置 `config.yaml` + `.env`
5. 配置 `systemd` 开机自启
6. 配置 Nginx 反向代理
7. 配置防火墙

---

## 现有代码复用

**完全复用：**
- 现有 prompt 模板引擎
- 现有火山生图调用逻辑
- 现有配置读取方式
- 现有 runtime 目录结构

**不复用为主链：**
- 飞书表单入口
- 飞书审核状态机

---

## 版本历史

- v1 - 飞书表格入口 + 飞书状态机
- v2 - 拆分项目结构，分层设计
- v3 - 讨论架构，方向调整
- v4 - **当前版本**：收敛为 Web 入口 + 直接选模型 + 持久化队列 + 成本统计

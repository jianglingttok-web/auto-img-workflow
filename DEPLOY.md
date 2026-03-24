# 部署文档 - TK 裂变素材工厂

## 服务器选择

推荐 **火山引擎 轻量应用服务器**：
- 配置：**2核 4GB**
- 系统：**Ubuntu 22.04 LTS**
- 带宽：**5M** 足够
- 价格：约 56 元/月（包年更便宜）

## 部署步骤

### 1. 登录服务器

```bash
ssh root@<your-server-ip>
```

### 2. 克隆项目

```bash
cd /root
git clone <your-repo-url> auto-img-workflow
cd auto-img-workflow
```

### 3. 安装系统依赖

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git -y
```

### 4. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 5. 配置

```bash
# 复制配置文件
cp config.example.yaml config.yaml
nano config.yaml
# 编辑配置，检查 web.max_concurrent, cleanup_ttl_hours 等

# 复制环境变量
cp .env.example .env
nano .env
# 填写 VOLCANO_ENGINE_API_KEY 等
```

### 6. 配置 systemd 开机自启

创建 `/etc/systemd/system/tk-factory.service`:

```ini
[Unit]
Description=TK Fission Image Factory
After=network.target

[Service]
User=root
WorkingDirectory=/root/auto-img-workflow
ExecStart=/root/auto-img-workflow/.venv/bin/python -m uvicorn tk_listing_workflow.web.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tk-factory
sudo systemctl start tk-factory
```

Check status:

```bash
sudo systemctl status tk-factory
```

Check logs:

```bash
journalctl -u tk-factory -f
```

### 7. 配置 Nginx 反向代理（推荐）

Install Nginx:

```bash
sudo apt install nginx -y
```

Create `/etc/nginx/sites-available/tk-factory`:

```nginx
server {
    listen 80;
    server_name <your-domain-or-ip>;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 600s;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/tk-factory /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 8. 配置防火墙

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

### 9. （可选）配置 HTTPS （有域名时）

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

## 访问

如果配置了域名：`http://your-domain.com`  
如果直接 IP：`http://your-server-ip`

## 使用

1. 打开网页
2. 选择站点、裂变类型、模型
3. 上传产品白底图和参考图（两张都必须）
4. 点击提交
5. 等待生成完成，下载 ZIP

## 维护

查看日志：
```bash
journalctl -u tk-factory -f
```

重启服务：
```bash
sudo systemctl restart tk-factory
```

查看成本统计：
- 打开网页就能看到统计卡片，显示总张数和总成本

## 说明

- 任务结果默认保留 48 小时，自动清理
- 支持 10 人排队使用，最大并发由 `max_concurrent` 控制
- 飞书通知可选，配置 `feishu_web.enabled: true` 和 `webhook` 即可

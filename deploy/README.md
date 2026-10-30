# 部署到阿里云 ECS

把「智能产品设计智能体」从本机搬到服务器。两个服务：

| 服务 | 端口 | 说明 |
|---|---|---|
| `worker` | 8001 | FastAPI + SSE，智能体内核、EDA/CAD 工具、sqlite 索引 |
| `web` | 3000 | Next.js 15（App Router），`/api/agent/*` 是服务端代理，转发到 worker |
| `nginx` | 80/443 | 唯一对外入口，反向代理 + SSE 长连接透传 |

> 前端**不直接连** worker：所有请求走 Next.js 的 route handler（读 `AGENT_WORKER_URL`）。
> 所以 8001 永远不需要暴露到公网——这也是默认不映射它端口的原因。

---

## 0. 先选方案

| | Docker Compose（推荐） | 裸机 systemd |
|---|---|---|
| 上手 | `bash deploy/agentctl.sh init` | `sudo bash deploy/bare/setup.sh` |
| 隔离 | 镜像固定依赖，不污染系统 | 直接用系统 python/node |
| 回滚 | 重新 build 旧 tag | 靠 git checkout |
| 适合 | 一台机器跑全套、要可复现 | 已有服务器、不便装 docker |

下面以 **Docker Compose** 为主流程，裸机方案见文末第 8 节。

---

## 1. 阿里云侧准备

### 1.1 ECS 选型

| 项目 | 建议 | 原因 |
|---|---|---|
| 架构 | **x86_64**（g7 / c7 / t7 / u1） | build123d 的 OCP 内核只有 x86_64 wheel；倚天 710（ARM）装不上本地 CAD |
| 规格 | **2C4G 起，推荐 4C8G** | Next.js build 峰值约 1.5GB；worker 常驻 300MB~1GB |
| 系统盘 | 60GB+（`workspace/` 每次运行会落 STEP/STL/渲染图） | 见第 7 节清理 |
| 带宽 | 3~5Mbps 按量，或按使用流量 | 页面资源不大，主要是模型 API 出网 |
| 地域 | 任意（大陆地域访问硅基流动/百炼更快） | — |

### 1.2 安全组

入方向只放行：

```
22/tcp   来源：你的 IP（SSH）
80/tcp   来源：0.0.0.0/0
443/tcp  来源：0.0.0.0/0（有证书后）
```

**不要放行 8001 / 3000**——它们只在容器内网/本机可达。

### 1.3 出网（决定能不能跑通）

worker 需要访问模型网关。大陆 ECS 实测：

- ✅ `api.siliconflow.cn`、`dashscope.aliyuncs.com`、`api.deepseek.com` — 直连正常
- ⚠️ `apihub.agnes-ai.com` — 部分网络下 TLS 被拦（本机就踩过）。**图像生成因此失败不影响主流程**，
  但想要渲染图就换 `DASHSCOPE_API_KEY`（阿里百炼）或在前端「模型设置」里临时切图床
- ✅ E2B 云端沙箱（建模）需要访问 `api.e2b.dev`，若不通就在 `.env` 里配本地 CAD

### 1.4 装 Docker（Ubuntu 22.04 / 24.04）

```bash
curl -fsSL https://get.docker.com | sh          # 官方脚本，已带 compose v2 插件
sudo usermod -aG cloud-user docker              # Ubuntu 镜像默认用户可能是 ubuntu/cloud-user
sudo systemctl enable --now docker
newgrp docker                                   # 免重登拿组权限；或断开 SSH 重连
docker version && docker compose version
```

国内拉镜像慢就配加速（阿里云容器镜像服务 → 镜像加速器，每人一个专属地址）：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
EOF
sudo systemctl restart docker
```

---

## 2. 部署（5 步）

```bash
# 1) 取代码（vendor/text-to-cad 是子模块，--recursive 才会带建模规范）
sudo mkdir -p /opt && cd /opt
sudo git clone --recursive https://github.com/lhbzx1984/smarthardware_agent.git pda
cd pda

# 2) 生成并填写 .env（唯一权威配置，config.py 以 override=True 加载）
cp deploy/env.prod.example .env
vi .env            # 至少填 SILICONFLOW_API_KEY；要渲染图再填 DASHSCOPE_/AGNES_API_KEY
                   # AGENT_HOST 必须保持 0.0.0.0，否则容器内 nginx 连不上 worker

# 3) 一键部署（构建镜像 + 启动 + 健康检查，首次 5~10 分钟）
bash deploy/agentctl.sh init

# 4) 看状态
bash deploy/agentctl.sh status

# 5) 浏览器打开 http://<公网IP>/
```

关键配置说明（都在 `.env`）：

| 变量 | 生产建议 | 说明 |
|---|---|---|
| `AGENT_HOST` | `0.0.0.0` | **本次为部署新增**。原来是硬编码 `127.0.0.1`，容器内会导致 nginx 连不上 |
| `AGENT_CAD_RUNTIME` | `auto` | 配了 `E2B_API_KEY` 就走云端沙箱；小内存机器别用 `local` |
| `E2B_API_KEY` | 建议配 | 建模放云端，省 700MB OCP 与本机内存（本机 8GB 并发建模必崩，血泪） |
| `AGENT_MOCK` | `0` | 设 1 是演示模式（不真调模型），用于验证部署是否通 |
| `AGENT_MAX_SECONDS` | `900` | 单次运行时限；nginx 的 `proxy_read_timeout` 已按 3600s 留足 |

---

## 3. 部署后验证清单

```bash
bash deploy/agentctl.sh status          # 三个容器 Up (healthy)
curl -s http://127.0.0.1/healthz        # nginx: ok
curl -s http://127.0.0.1/api/agent/health | head   # 应返回 mode=live / provider=...
```

浏览器侧走一遍：

1. 打开首页 → 能看到模型名与「就绪」状态（不是 offline）
2. 右上角「模型设置」→ 测试连接（用你自己的 key 拉模型列表）
3. 输入一句需求 → 点「开始设计」→ **事件流应该逐条滚动（打字机）**，而不是憋 30 秒吐一大段
   - 如果不是逐条：nginx 的 `proxy_buffering` 没关，检查 `deploy/nginx/conf.d/agent.conf` 是否挂载生效
4. 跑到结构阶段 → 看是否出 STEP/STL
5. 跑到硬件阶段 → 看 PCB 预览与 `.epro` 能下载

> 第 3 条是部署最容易翻车的点：SSE 被缓冲后功能其实没坏，但体感像卡死。

---

## 4. HTTPS（有域名时）

```bash
# 域名先解析到 ECS 公网 IP
bash deploy/agentctl.sh ssl your-domain.com
```

脚本会：装 certbot → 临时停 nginx 让出 80 → 签证书 → 拷到 `deploy/nginx/certs/` →
生成 `agent-ssl.conf` → 重启 nginx。

然后手动放开 443（编辑 `deploy/docker-compose.yml`，取消 `- "443:443"` 的注释）并
`bash deploy/agentctl.sh restart`。

想用阿里云免费证书也行：控制台申请 → 下载 nginx 版 → 把 `fullchain.pem`/`privkey.pem`
放到 `deploy/nginx/certs/`，再把 `agent-ssl.conf.example` 复制为 `agent-ssl.conf` 改域名。

证书 90 天到期：`sudo certbot renew` 后重跑 `ssl` 命令。

---

## 5. 日常运维

```bash
bash deploy/agentctl.sh up | down | restart
bash deploy/agentctl.sh logs worker        # 只看 worker（web / nginx 同理）
bash deploy/agentctl.sh update             # git pull + 重建镜像 + 滚动启动
bash deploy/agentctl.sh backup             # 备份到 backups/，保留最近 7 份
bash deploy/agentctl.sh status
```

备份做了两件事：容器内用 `sqlite3.Connection.backup()` 做在线快照（WAL 模式下直接 tar
会拿到撕裂的库），再打包 `data/` 与 `workspace/`。

---

## 6. 数据与持久化

| 路径 | 内容 | 能否丢 |
|---|---|---|
| `data/agent.db` | 会话、事件索引、产物清单 | ❌ 丢了历史列表全空（产物还在磁盘） |
| `workspace/` | 每次运行的真实产物（STEP/PCB/图） | ⚠️ 可清，但用户会看不到历史产物 |
| `.env` | 密钥 | ❌ |

两个目录都是 bind mount 到宿主机，容器删了数据还在。迁机时整目录拷贝 + 重新 `init` 即可。

---

## 7. 磁盘与清理

`workspace/` 增长很快（一次完整流程几十 MB）。建议加个 crontab：

```bash
# 每天 3 点清 30 天前的工作区产物，只动 workspace，不动 data（索引）
0 3 * * * cd /opt/pda && find workspace -maxdepth 1 -type d -mtime +30 -exec rm -rf {} + && \
          docker exec pda-worker python - <<'PY'
import sqlite3
c = sqlite3.connect("/app/data/agent.db")
c.execute("PRAGMA journal_mode=WAL")
c.close()
PY
```

sqlite 只增不减，`VACUUM` 可回收（需停写，放到低峰）：
`docker exec pda-worker python -c "import sqlite3;sqlite3.connect('/app/data/agent.db').execute('VACUUM')"`

---

## 8. 裸机方案（不用 Docker）

```bash
sudo PDA_DIR=/opt/pda bash deploy/bare/setup.sh
```

会做：rsync 代码到 `/opt/pda` → 建 venv 装 Python 依赖 → `npm ci && npm run build` →
写 `/etc/systemd/system/pda-{worker,web}.service` → 配 `/etc/nginx/conf.d/agent.conf` → 启动。

```bash
journalctl -u pda-worker -f          # 日志
systemctl restart pda-worker pda-web # 重启
```

裸机下 worker 建议绑 `127.0.0.1`（`.env` 里 `AGENT_HOST=127.0.0.1`），nginx 同机反代，端口不对外。

---

## 9. 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| 页面能开，`/api/agent/health` 返回 offline | worker 没起来：`docker logs pda-worker`；多半是 `.env` 里 `AGENT_HOST` 不是 `0.0.0.0`，或依赖装失败 |
| nginx 502 | web 容器在 build 未完成就重启，或 `AGENT_WORKER_URL` 写成 `localhost`（容器里那是它自己）→ 必须是 `http://worker:8001` |
| 事件流不逐条、憋一大段 | `proxy_buffering off` / `gzip off` 没生效；确认 compose 挂载的是 `deploy/nginx/conf.d` 而不是系统默认配置 |
| 跑到一半事件流断 | nginx `proxy_read_timeout` 被改小；单次运行超 `AGENT_MAX_SECONDS` 会走续跑逻辑，属正常 |
| 模型调用一直 401 | `.env` 改了没重启容器（`config.py` 只在启动时读）；或 base_url 与 key 不是同一家 |
| 渲染图不出来，其余正常 | Agnes 网关不可达 → 换 `DASHSCOPE_API_KEY`，或在前端设置里改图像服务商 |
| 结构阶段建模失败/卡住 | 没配 `E2B_API_KEY` 且本机没装 build123d：`AGENT_CAD_RUNTIME=local` + 装 `deploy/requirements-cad.txt`（需 x86_64 + 足够内存） |
| 数据库 locked | 起了多个 worker 副本（sqlite 模块级单例，只能单进程）；`docker compose ps` 确认只有一个 |
| `docker build` 卡在 Sending context | `.dockerignore` 没生效（必须在仓库根，且 build context 是 `.`） |
| 端口打不开 | 阿里云安全组没放行 80/443（不是服务器内防火墙） |

---

## 10. 本次为部署做的代码改动

- `agent/config.py`：新增 `HOST = os.getenv("AGENT_HOST", "127.0.0.1")`
- `agent/server.py`：`uvicorn.run(host=config.HOST, ...)`（原来硬编码 `127.0.0.1`）
- 新增 `.dockerignore`（构建上下文瘦身）、`deploy/` 全套部署件

本机开发不受影响：`AGENT_HOST` 不设时仍是 `127.0.0.1`。

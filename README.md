# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数、捆扎/拆捆 |
| watcher | watch123456 | 观察岗，只读列表、可翻包不能捆扎 |

## 装车捆扎

顶栏「装车捆扎」落地页分三块：

- **待捆列表**：仅列出 `done` 已办结读数；候审（pending）/处理中（processing）读数不得入捆。
- **已捆包**：记录员勾选读数、填装车单号「一点捆扎」，包内读数以快照定格在捆扎当刻；可「翻包」查看。
- **拆捆流水**：拆捆后读数回到待捆列表，流水保留单号、读数、操作人、时间、原因。

冻包语义：

- 总表的现行数字允许变化，但包内读数始终停在捆扎当刻（快照独立存储）。
- 包内读数禁止经由任何写接口改写：`PATCH/PUT /api/readings/{id}` 返回 409，后台 worker 的认领/办结/回退语句也带冻包保护。
- 观察岗可 `GET` 翻阅全部数据，捆扎与拆捆接口返回 403。

接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/loading/overview` | 待捆列表 + 已捆包（含定格快照与现行值）+ 拆捆流水 |
| POST | `/api/loading/packages` | 记录员捆扎（`loading_no`、`reading_ids`） |
| POST | `/api/loading/packages/{id}/unbundle` | 记录员拆捆 |
| PATCH/PUT | `/api/readings/{id}` | 人为修订温度；冻包读数返回 409 |


## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。

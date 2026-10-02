# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

记录员还可把多笔**已办结**读数用一个装车单号**捆扎**成装车包：读数在捆扎当刻生成不可变快照冻进包内，顶栏「装车捆扎」落地页含**待捆列表 / 已捆包 / 捆扎拆捆流水**。候审、处理中的读数不得入捆；观察岗能翻包不能捆扎。

## 冻结语义

- 总表是**现行数字**：读数捆入密封包后仍可由记录员改温，总表即时变化。
- 包内读的是 `shipment_items` **快照表**（温度、结论、冻存时刻），与现行行分离；改温、拆捆都不动快照——重开包永远停在捆扎当刻。
- 快照表、流水表在数据库层有**只追加触发器**（`BEFORE UPDATE/DELETE` 直接抛错），写接口无法改写；装车包单号不可改、密封只能单向变拆捆。仅冻展示而放过写口不算数，因此冻结落在存储层。
- 拆捆后读数回到待捆列表、可再改温或重新捆扎；原包快照与流水原样留档。

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
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/readings` | 登录 | 读数总表（现行数字 + 密封包标记） |
| POST | `/api/readings` | 记录员 | 提交读数，入队候审 |
| PATCH | `/api/readings/{id}` | 记录员 | 改现行温度（已入包读数也可改，仅动总表，不影响快照） |
| GET | `/api/shipments` | 登录 | 落地页：待捆列表 + 已捆包（含当刻快照）+ 流水 |
| POST | `/api/shipments/bundle` | 记录员 | 装车单号 + 勾选读数，一点捆扎（仅 `done` 可入） |
| POST | `/api/shipments/packages/{id}/unbundle` | 记录员 | 拆捆：读数回待捆池，快照与流水保留 |

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

# 风机偏航对中台

现场技师登记机组编号与偏航误差（度）；后台 worker 用数据库行锁认领待处理记录，按 ±1.5° 阈值写入「合格」或「偏航超差」。前端为 Lit 组件 + Vite，接口为 Quart + Hypercorn。

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3199 |
| 接口 | http://localhost:8199 |
| PostgreSQL | localhost:54399（库名 `yawalign`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| technician | tech123456 | 可提交 |
| observer | obs123456 | 只读 |

## 启动

```bash
cd projects/20-yaw-align-log
docker compose up --build
```

健康检查：`GET http://localhost:8199/api/health` → `{"status":"ok","service":"yaw-align-log"}`。

## 验收

1. 种子数据：机组 W01 误差 0.4° 结论「合格」；机组 W07 误差 3.2° 结论「偏航超差」。
2. technician 提交新记录后，列表先显示「待处理」，数秒内 worker 处理后变为对应结论。
3. observer 可查看列表，无提交表单。
4. 顶栏进入「均值合成入队」专页：勾选两笔同机组已办结记录 → 预览后台均值 → 确认入队，新单误差等于后台均值、状态「待处理」，合成流水同步可查；observer 可预览但无入队权限。

## 均值合成入队

多笔已办结偏航点可合成一笔新的待处理单，均值一律由后台 SQL 计算，前端不做求和平均。

| 接口 | 权限 | 说明 |
|------|------|------|
| `POST /api/merge/preview` `{ids:[...]}` | 登录即可（观察员可预览） | 返回机组、笔数、后台均值 |
| `POST /api/merge/enqueue` `{ids:[...]}` | 仅技师 | 生成新待处理单 |
| `GET /api/merges` | 登录即可 | 合成流水列表 |

校验规则：少于两笔、编号不存在、混入未办结记录、跨机组混选均拒收（400）；观察员入队（403）。入队新单与合成流水（`yaw_merges` 表）在同一事务写入，缺一边即整体回滚。


## 技术栈

- 后端：Quart、psycopg、`worker.py`（`FOR UPDATE SKIP LOCKED`）、Hypercorn
- 前端：Lit、TypeScript、Vite；生产镜像内 nginx 反代 `/api`
- 镜像源：DaoCloud 基础镜像、清华 PyPI、npmmirror npm

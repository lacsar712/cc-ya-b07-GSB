# 风机偏航对中台

现场技师登记机组编号与偏航误差（度）；后台 worker 用数据库行锁认领待处理记录，按 ±1.5° 阈值写入「合格」或「偏航超差」。前端为 Lit 组件 + Vite，接口为 Quart + Hypercorn。

## 多笔均值合成入队

顶栏「多笔均值合成入队」进入专页：勾选至少两笔**同一结论类型**的已办结记录 → 「预览均值」→「确认入队」，后台求均值生成一笔新的待处理单。

- 均值一律由后台 `AVG()` 计算，前端只展示预览结果，不自行心算平均。
- 不足两笔、编号不存在、混入未办结记录、结论类型不一致，一律拒收（400）。
- 入队结果（`yaw_logs` 新待处理单）与合成流水（`yaw_merges` + `yaw_merge_items`）在同一事务记账，缺一边即整体回滚。
- 观察员可勾选并预览均值，但看不到入队按钮；接口侧 `POST /api/merges` 对非技师返回 403。

### 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/merges/preview` | 登录即可 | 入参 `{log_ids:[...]}`，返回后台均值预览 |
| POST | `/api/merges` | 仅技师 | 同事务写入新待处理单与合成流水，201 返回 `{merge, log}` |
| GET | `/api/merges` | 登录即可 | 合成流水列表（含来源编号） |

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
4. 合成入队：technician 提交两笔小误差记录（如 W09 的 0.4° 与 0.8°），待办结均为「合格」后进专页勾选这两笔 → 预览显示后台均值 0.6° → 确认入队 → 新单偏航等于后台均值、状态「待处理」，合成流水同步多一笔；worker 随后认领判定。observer 登录可勾选预览，但无入队按钮，直接调 `POST /api/merges` 返回 403。

## 技术栈

- 后端：Quart、psycopg、`worker.py`（`FOR UPDATE SKIP LOCKED`）、Hypercorn
- 前端：Lit、TypeScript、Vite；生产镜像内 nginx 反代 `/api`
- 镜像源：DaoCloud 基础镜像、清华 PyPI、npmmirror npm

# 工业时序建模与软测量平台 Demo

单机运行的软测量 Demo：用本地数据文件和真实算法跑通
"选数据 → 分工况 → 加工输入 → 训练模型 → 检验效果 → 上线投用 → 在线校核 → 跟踪改进"。
示例场景：乙烯裂解炉 1 号炉（BA-101），估算"炉管壁温 · 出口段平均"。

需求见 `SPEC.md`（不在仓库中，由需求方提供）与交互原型；各阶段说明见 `docs/`。

## 一键启动

需要 Python 3.11+、Node.js 18+。

```bash
./scripts/start.sh
```

浏览器打开 <http://127.0.0.1:8000>。脚本会：创建 `backend/.venv` 并安装依赖 → 如缺少示例数据则生成 →
构建前端 → 启动后端（同时托管前端页面，单端口）。首次运行约需几分钟。

常用变量：`PORT=8080 ./scripts/start.sh`；前端改动后 `REBUILD=1 ./scripts/start.sh`。

### 开发模式

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

开发时访问 <http://127.0.0.1:5173>（`/api` 代理到 8000）。

### 测试

```bash
cd backend && .venv/bin/python -m pytest -q tests
```

### 重新生成示例数据

```bash
backend/.venv/bin/python scripts/gen_demo_data.py
```

固定随机种子，结果可重复。运行时数据（SQLite、数据版本、导入文件）在 `var/`，删除即恢复初始状态。

## 目录

```
scripts/gen_demo_data.py   示例数据生成（有物理规律，含事件和数据质量问题）
data/                      输入数据（只读）：参数表、时序宽表、校核数据、外部滞后参考、场景模板
  _truth/                  生成器内部真值，只用于测试和验收，平台界面不读
backend/app/
  core/                    算法，训练和上线共用一份：数据源、处理规则、工况判定、校核样本……
  services/                场景上下文、页面计算、数据版本生成
  api/                     FastAPI 路由
  store/                   SQLite 元数据
backend/tests/             单元测试（含"不用未来数据"测试）
frontend/                  React + TypeScript + Vite + ECharts
docs/                      各阶段说明与截图
```

## 数据文件约定（摘要）

| 文件 | 内容 |
|---|---|
| `data/tags.csv` | 参数表：tag, name, device, part, desc, unit, period, range_lo, range_hi, recommended, is_target, **furnace**（新增列，标明所属炉） |
| `data/timeseries/<炉>.parquet` | 宽表：timestamp + 各位号，1 分钟间隔，缺失为空；2026-01-01 至 2026-11-30 |
| `data/calib/*.csv` | measured_at, position, value, source, instrument, operator, status, **entered_at**（新增，录入时刻，用于模拟晚到） |
| `data/calib/_sources.json` | 校核来源清单：所属炉、类型、说明、默认是否使用、用途（训练 / 上线回放 / 跨炉） |
| `data/lag_refs/*.json` | `{位号: {"dead_time": 分钟, "tau": 分钟}}`，另有 `_meta` 说明来源 |
| `data/templates/*.json` | 场景模板 |

所有时间均为本地时间（不带时区）。

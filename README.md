# 工业时序建模与软测量平台 Demo

单机运行的软测量 Demo：用本地数据文件和真实算法跑通
"选数据 → 分工况 → 加工输入 → 训练模型 → 检验效果 → 上线投用 → 在线校核 → 跟踪改进"。
示例场景：乙烯裂解炉 1 号炉（BA-101），估算"炉管壁温 · 出口段平均"。

需求见 `SPEC.md`（不在仓库中，由需求方提供）与交互原型；各阶段说明见 `docs/`。

## 一键启动

需要 **Python 3.11–3.13** 和 **Node.js 18+**。示例数据已随仓库提供，不需要重新生成。

### Windows

先安装：

- Python：<https://www.python.org/downloads/>，安装时勾选 "Add python.exe to PATH"
- Node.js：<https://nodejs.org/>，选 LTS 版本

在仓库目录打开"命令提示符"或 PowerShell，运行：

```bat
scripts\start.bat
```

也可以在资源管理器里直接双击 `scripts\start.bat`。

脚本会依次：在 `backend\.venv` 创建虚拟环境并安装依赖 → 构建前端 → 启动服务，十几秒后自动打开浏览器
<http://127.0.0.1:8000>。首次运行需要下载依赖，约几分钟；之后再启动只需几秒。按 Ctrl+C 停止。

- 每次启动都会重新构建前端（约 10 秒）；`requirements.txt` 有变化时会自动重新安装 Python 依赖。拉取新代码后直接重新运行即可。
- 换端口：先 `set PORT=8080`，再运行 `scripts\start.bat`（PowerShell 里写 `$env:PORT=8080`）。
- 想跳过前端构建、加快启动：先 `set SKIP_BUILD=1`。
- 依赖装坏了：删除 `backend\.venv` 文件夹后重新运行。
- 想回到初始状态（清空所有项目、数据版本、模型）：停止服务，删除 `var` 文件夹后重新运行。

### Linux / macOS

```bash
./scripts/start.sh
```

### 开发模式

后端（Windows）：

```bat
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

后端（Linux / macOS）：

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

前端（另开一个终端）：

```bash
cd frontend
npm run dev
```

开发时访问 <http://127.0.0.1:5173>（`/api` 代理到 8000）。

### 测试

Windows：

```bat
cd backend
.venv\Scripts\python -m pytest -q tests
```

Linux / macOS：

```bash
cd backend && .venv/bin/python -m pytest -q tests
```

### 重新生成示例数据

Windows：

```bat
backend\.venv\Scripts\python scripts\gen_demo_data.py
```

Linux / macOS：

```bash
backend/.venv/bin/python scripts/gen_demo_data.py
```

固定随机种子，结果可重复。运行时数据（SQLite、数据版本、导入文件）在 `var/`，删除即恢复初始状态。

## 演示上线投用（回放时钟）

上线投用用一个"回放时钟"把 2026-11 的历史数据当作实时数据流：

1. 先完成前面各步并在"检验效果"中审批发布一个模型。
2. "上线投用" → "新增运行实例"：部署到 1 号炉（在线）；也可以部署到 4 号炉，会先做跨炉检验，再以影子模式运行。
3. 顶部或"上线投用"页点"开始回放"。倍速可选 60 / 600 / 3600 倍（3600 倍时 1 秒 = 1 小时，整个 11 月约 12 分钟）。
4. 实时页、在线校核、场景概览的三个指标、跟踪改进的样本库都随时钟更新。

上线时所有数据都通过 `ReplayDataSource` 读取，它拒绝读取晚于回放时钟的数据；
运行实例调用与训练相同的计算链（`core/pipeline.py`），并且只用发布包（模型 + 特征定义 + 处理规则 + 工况方案 + 适用范围）。

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

所有时间均为本地时间（不带时区）。CSV 用带 BOM 的 UTF-8 保存，Windows 上用 Excel 直接打开不会乱码；导入校核数据时 UTF-8 和 GBK 编码都支持。

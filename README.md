# 客商准入系统

> 客商风控评分 + 审批工作流一体化系统

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Qing-Leee/customer-admission-system)

## 在线体验

部署完成后，将通过 Render 提供公网访问地址，支持多人同时访问。

**一键部署步骤：**
1. 点击上方「Deploy to Render」按钮
2. 使用 GitHub 账号登录 Render
3. 确认配置（render.yaml 已预设好），点击创建
4. 等待 1-2 分钟构建完成，获取 `https://customer-admission-system.onrender.com` 公网地址

## 项目简介

本系统为客商运营管理办法的落地实现，提供完整的客商准入管理流程，包括：
- **客商管理**：客商基础信息录入、对接人管理、附件上传
- **财务数据录入**：单条录入 + Excel 批量导入
- **风控评分引擎**：多维度评分（主体资质 / 财务 / 项目质量 / 履约质量），自动评级（AAA ~ B）
- **审批工作流**：部门负责人复核 → 市场/法务/财务并行会签 → 公司领导终审

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 |
| 数据库 | SQLite（开发）/ MySQL（生产） |
| 数据校验 | Pydantic 2.10 |
| 前端 | 原生 HTML/CSS/JS（SPA，无需构建） |

## 项目结构

```
customer-admission-system/
├── backend/
│   ├── main.py                 # 应用入口（路由挂载 + 前端托管）
│   ├── database.py             # 数据库配置与初始化
│   ├── models.py               # ORM 数据模型
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── dao.py                  # 数据访问层
│   ├── seed_data.py            # 种子数据（规则配置 + 演示客商）
│   ├── scoring_engine.py       # 评分引擎核心
│   ├── rating_mapper.py        # 评级与风控系数映射
│   ├── ratio_calculator.py     # 财务比率计算
│   ├── dimension_rules.py      # 维度组合规则
│   ├── indicator_resolver.py   # 指标值解析器
│   ├── segment_scorer.py       # 阶梯计分器
│   ├── batch_id.py             # 批次ID生成器
│   ├── merchant_routes.py      # 客商管理路由
│   ├── merchant_service.py     # 客商管理服务
│   ├── financial_routes.py     # 财务数据路由
│   ├── financial_service.py    # 财务数据服务
│   ├── approval_routes.py      # 审批工作流路由
│   ├── approval_service.py     # 审批工作流服务
│   ├── requirements.txt        # Python 依赖
│   ├── migration_merchant.sql  # 客商模块 DDL
│   └── migration_approval.sql  # 审批模块 DDL
├── frontend/
│   └── index.html              # 前端 SPA（内嵌 CSS/JS）
├── .gitignore
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动服务

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后：
- 前端页面：http://localhost:8000
- API 文档（Swagger）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 3. 使用系统

系统内置 3 个演示客商和完整的评分规则配置，启动后可直接使用。

**审批流程角色切换**（前端左下角角色选择器）：

| 角色 | 职责 |
|------|------|
| 业务员小王 | 提交审批申请 |
| 部门负责人 | 复核申请材料 |
| 市场部审批人 | 审核客商资信 |
| 法务部审批人 | 审核法律风险 |
| 财务部审批人 | 审核财务数据 |
| 公司领导 | 终审决策 |

## API 接口

### 客商管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/merchant` | 创建/更新客商 |
| GET | `/api/merchant/list` | 分页列表查询 |
| GET | `/api/merchant/detail` | 客商详情 |
| POST | `/api/merchant/attachment` | 上传附件 |

### 财务数据

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/financial/single` | 单条录入 |
| POST | `/api/financial/batch` | Excel 批量导入 |

### 审批工作流

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/approval/submit` | 提交审批 |
| POST | `/api/approval/task/approve` | 任务通过 |
| POST | `/api/approval/task/reject` | 任务驳回 |
| GET | `/api/approval/my-tasks` | 我的待办 |
| GET | `/api/approval/history` | 审批历史 |
| GET | `/api/approval/status` | 审批状态查询 |

### 系统接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/stats` | 看板统计 |
| GET | `/health` | 健康检查 |

## 评分体系

### 评级与风控系数

| 评级 | 分数范围 | 风控系数 |
|------|----------|----------|
| AAA | ≥ 90 | 0.50 |
| AA | 80 ~ 89 | 0.70 |
| A | 70 ~ 79 | 0.85 |
| BBB | 60 ~ 69 | 1.00 |
| BB | 50 ~ 59 | 1.20 |
| B | 40 ~ 49 | 1.50 |
| UNQUALIFIED | < 40 | 2.00 |

### 评分维度

| 维度 | 首次场景 | 动态场景 | 权重 |
|------|----------|----------|------|
| 主体资质 | ✓ | ✓ | 30% |
| 财务 | 授信时 | ✓ | 30% |
| 项目质量 | 授信时 | ✗ | 20% |
| 履约质量 | ✗ | ✓ | 20% |

## 审批状态流转

```
业务员提交 → pending_dept（部门负责人复核）
                ↓
         parallel_signing（市场/法务/财务并行会签）
                ↓
         final_signing（公司领导终审）
                ↓
              approved（审批通过，写入评分快照）
```

## 云端部署

### 方式一：Render 一键部署（推荐，免费）

1. 点击 README 顶部「Deploy to Render」按钮
2. 使用 GitHub 账号登录 Render（自动关联仓库）
3. 确认配置，点击 **Create** 开始部署
4. 1-2 分钟后获取公网地址：`https://customer-admission-system.onrender.com`

> Render 免费版说明：15 分钟无访问会自动休眠，下次访问自动唤醒（约30秒）。

### 方式二：Docker 部署

```bash
# 构建镜像
docker build -t customer-admission-system .

# 运行容器
docker run -d -p 8000:8000 --name ca-system customer-admission-system

# 访问 http://localhost:8000
```

### 方式三：生产环境部署

1. 修改 `database.py` 中的数据库连接为 MySQL/PostgreSQL
2. 执行 `migration_merchant.sql` 和 `migration_approval.sql` 建表
3. 使用 Gunicorn + Uvicorn worker 部署：
   ```bash
   gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
   ```
4. 配置 Nginx 反向代理和 HTTPS

## License

MIT

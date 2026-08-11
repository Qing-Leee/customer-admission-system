"""
客商准入系统 — FastAPI 应用入口。

启动方式：
    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

功能：
    1. 初始化 SQLite 数据库并写入种子数据
    2. 挂载客商管理 / 财务数据 / 审批工作流路由
    3. 配置 CORS 跨域
    4. 托管前端静态资源（SPA）
    5. 提供系统看板统计接口
"""

import os
from pathlib import Path

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import SessionLocal, get_db, init_db
from seed_data import run_seed

# ---- 路由模块 ----
from merchant_routes import router as merchant_router
from financial_routes import router as financial_router, set_db_session_factory
from approval_routes import router as approval_router

# ---- ORM 模型（用于看板统计）----
from models import (
    MerchantBasicInfo,
    ApprovalOrder,
    ApprovalTask,
    ScoreSnapshot,
)

# ======================================================================
# 应用初始化
# ======================================================================

app = FastAPI(
    title="客商准入系统",
    description="客商风控评分 + 审批工作流系统 — 前后端一体化部署",
    version="2.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """启动时初始化数据库并写入种子数据。"""
    init_db()
    run_seed()
    # 为 financial_routes 注入 Session 工厂
    set_db_session_factory(SessionLocal)


# ======================================================================
# API 路由挂载
# ======================================================================
app.include_router(merchant_router, tags=["客商管理"])
app.include_router(financial_router)
app.include_router(approval_router)


# ======================================================================
# 系统接口
# ======================================================================

@app.get("/api/dashboard/stats", summary="系统看板统计")
def dashboard_stats(db: Session = Depends(get_db)):
    """返回系统首页看板所需的汇总统计数据。"""
    merchant_total = db.execute(
        select(func.count()).select_from(MerchantBasicInfo)
    ).scalar() or 0

    # 各状态审批单数
    status_counts = {}
    rows = db.execute(
        select(ApprovalOrder.status, func.count())
        .group_by(ApprovalOrder.status)
    ).all()
    for status, count in rows:
        status_counts[status] = count

    pending_tasks = db.execute(
        select(func.count()).select_from(ApprovalTask)
        .where(ApprovalTask.task_status == "pending")
    ).scalar() or 0

    scored = db.execute(
        select(func.count()).select_from(ScoreSnapshot)
    ).scalar() or 0

    # 各评级客商数
    rating_rows = db.execute(
        select(ScoreSnapshot.rating, func.count())
        .group_by(ScoreSnapshot.rating)
    ).all()
    rating_counts = {r: c for r, c in rating_rows}

    return {
        "merchant_total": merchant_total,
        "approval_status": status_counts,
        "pending_tasks": pending_tasks,
        "scored_merchants": scored,
        "rating_distribution": rating_counts,
    }


@app.get("/health", summary="健康检查")
def health():
    return {"status": "ok", "service": "customer-admission-system"}


# ======================================================================
# 前端静态资源托管
# ======================================================================

# 前端目录：项目根目录下的 frontend/
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/")
async def serve_index():
    """返回前端 SPA 入口页面。"""
    return FileResponse(FRONTEND_DIR / "index.html")


# 挂载静态资源目录（CSS / JS / 图片）
static_dir = FRONTEND_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

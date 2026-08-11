"""
数据库配置模块。

使用 SQLite 作为默认数据库（便于演示与快速启动），
生产环境可通过环境变量 DATABASE_URL 切换至 MySQL/PostgreSQL。
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 数据库连接配置：
#   优先读取环境变量 DATABASE_URL（支持 MySQL / PostgreSQL），
#   未设置时使用本地 SQLite 文件。
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./customer_admission.db")

# SQLite 需要 check_same_thread=False 以支持 FastAPI 多线程
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI 依赖：提供数据库 Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有数据表（基于 ORM 模型定义）。"""
    # 延迟导入避免循环依赖
    from models import Base
    Base.metadata.create_all(bind=engine)

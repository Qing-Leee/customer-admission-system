"""
财务数据录入 FastAPI 路由模块。

提供两个接口：
    POST /api/financial/single   单条财务数据录入
    POST /api/financial/batch    Excel 批量导入

使用方式：
    from fastapi import FastAPI
    from scoring_engine.financial_routes import router, set_db_session_factory

    app = FastAPI()
    # 设置数据库 Session 工厂（由调用方提供）
    set_db_session_factory(sessionmaker(bind=engine))
    app.include_router(router)

    # 启动：uvicorn main:app --reload
"""

import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from financial_service import FinancialService

# ==================================================================
# 路由定义
# ==================================================================

router = APIRouter(prefix="/api/financial", tags=["财务数据"])

# 全局 Session 工厂，由 set_db_session_factory 设置
_db_session_factory: Optional[sessionmaker] = None


def set_db_session_factory(factory: sessionmaker) -> None:
    """
    设置数据库 Session 工厂。

    在应用启动时调用一次，将 SQLAlchemy sessionmaker 注入路由模块。
    示例：
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("mysql+pymysql://user:pass@host/db")
        set_db_session_factory(sessionmaker(bind=engine))
    """
    global _db_session_factory
    _db_session_factory = factory


def get_db() -> Session:
    """
    FastAPI 依赖：提供数据库 Session，请求结束自动关闭。

    依赖 set_db_session_factory 已被调用；否则抛出 500。
    """
    if _db_session_factory is None:
        raise HTTPException(
            status_code=500,
            detail="数据库未初始化，请先调用 set_db_session_factory()",
        )
    session = _db_session_factory()
    try:
        yield session
    finally:
        session.close()


# ==================================================================
# 请求 / 响应模型
# ==================================================================

class FinancialRecordIn(BaseModel):
    """单条财务数据录入请求体。"""

    period_year: int = Field(..., ge=2000, le=2100, description="会计年度（必填，2000-2100）")
    period_quarter: Optional[int] = Field(None, ge=1, le=4, description="季度（可选，1-4，空表示年度数据）")
    total_asset: Optional[float] = Field(None, ge=0, description="总资产（可选，若填写必须 ≥ 0）")
    total_liability: Optional[float] = Field(None, ge=0, description="总负债")
    current_asset: Optional[float] = Field(None, ge=0, description="流动资产")
    current_liability: Optional[float] = Field(None, ge=0, description="流动负债")
    revenue: Optional[float] = Field(None, ge=0, description="营业收入")
    net_profit: Optional[float] = Field(None, ge=0, description="净利润")
    inventory: Optional[float] = Field(None, ge=0, description="存货")
    prepay: Optional[float] = Field(None, ge=0, description="预付账款")
    operating_cash_flow: Optional[float] = Field(None, ge=0, description="经营性现金流净额")
    net_asset: Optional[float] = Field(None, ge=0, description="净资产")


class SingleResultOut(BaseModel):
    """单条录入响应。"""

    success: bool
    rows_affected: Optional[int] = None
    error: Optional[str] = None


class FailedDetail(BaseModel):
    """批量导入失败明细。"""

    row: int
    reason: str


class BatchResultOut(BaseModel):
    """批量导入响应。"""

    total_rows: int
    success_rows: int
    failed_rows: int
    failed_details: List[FailedDetail]
    rows_affected: int
    error: Optional[str] = None


# ==================================================================
# 常量
# ==================================================================

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".xlsx"}


# ==================================================================
# 接口
# ==================================================================

@router.post("/single", response_model=SingleResultOut, summary="单条财务数据录入")
def add_single(
    merchant_id: int = Query(..., description="客商 ID"),
    data: FinancialRecordIn = ...,
    db: Session = Depends(get_db),
):
    """
    录入或更新单条财务数据。

    - **merchant_id**: 客商 ID（Query 参数）
    - **data**: 财务数据请求体（period_year 必填，其余可选）

    存在相同 (merchant_id, period_year, period_quarter) 的记录则更新，否则插入。
    """
    service = FinancialService(db)
    # Pydantic model -> dict，排除未设置的可选字段（避免覆盖已有值）
    record = data.model_dump(exclude_unset=True)
    result = service.add_or_update_single(merchant_id, record)

    if result["success"]:
        db.commit()
    return result


@router.post("/batch", response_model=BatchResultOut, summary="Excel 批量导入")
async def import_batch(
    merchant_id: int = Query(..., description="客商 ID"),
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: Session = Depends(get_db),
):
    """
    通过 Excel 文件批量导入财务数据。

    - **merchant_id**: 客商 ID（Query 参数）
    - **file**: Excel 文件，仅支持 .xlsx 格式，最大 10MB

    Excel 表头须符合 B-2 模板规范（会计年度、季度、总资产...）。
    """
    # ---- 文件格式校验 ----
    filename = file.filename or ""
    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()
    if ext_lower not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{ext}'，仅支持 .xlsx",
        )

    # ---- 读取文件内容 ----
    contents = await file.read()

    # ---- 文件大小校验 ----
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小 {len(contents) / 1024 / 1024:.1f}MB 超过限制 10MB",
        )

    # ---- 调用 Service 层 ----
    service = FinancialService(db)
    result = service.import_from_excel(merchant_id, contents, filename)

    # 仅当有成功写入时才提交
    if result.get("rows_affected", 0) > 0:
        db.commit()

    return result

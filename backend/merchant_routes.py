"""
客商管理 FastAPI 路由模块。

提供以下接口：
    POST /api/merchant            创建或更新客商（含对接人）
    GET  /api/merchant/list       分页列表查询
    GET  /api/merchant/detail     客商详情
    POST /api/merchant/attachment 上传附件

使用方式：
    from merchant_routes import router
    app.include_router(router)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from merchant_service import MerchantService

router = APIRouter()


# ======================================================================
# 数据库会话依赖
# ======================================================================
# 优先复用项目中已有的 get_db 依赖；若不存在，则从环境变量读取连接串。
# 禁止在代码中硬编码数据库账号密码。
try:
    from database import get_db  # type: ignore  # 若项目已有 database.py
except ImportError:  # pragma: no cover - 依赖项目环境
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # 从环境变量 DATABASE_URL 读取连接串，未设置时给出明确报错提示
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise RuntimeError(
            "未找到数据库配置：请在环境变量中设置 DATABASE_URL，"
            "或提供 database.py 模块并定义 get_db 依赖函数。"
        )

    _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    def get_db():
        """数据库会话依赖（基于环境变量 DATABASE_URL）。"""
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


# ======================================================================
# 请求 / 响应模型
# ======================================================================
class ContactCreate(BaseModel):
    """对接人创建模型。"""

    name: str = Field(..., min_length=1, max_length=64, description="姓名")
    position: Optional[str] = Field(None, max_length=64, description="职务")
    phone: Optional[str] = Field(None, max_length=32, description="联系方式")
    email: Optional[str] = Field(None, max_length=128, description="邮箱")
    business_role: Optional[str] = Field(None, max_length=32, description="业务职责")
    is_primary: bool = Field(False, description="是否主联络人")
    remark: Optional[str] = Field(None, max_length=256, description="备注")


class MerchantCreateUpdate(BaseModel):
    """客商创建/更新请求模型。"""

    merchant_name: str = Field(..., min_length=1, max_length=128, description="商户名称")
    tax_number: str = Field(
        ...,
        min_length=15,
        max_length=18,
        description="统一社会信用代码",
    )
    legal_person: str = Field(..., min_length=1, max_length=64, description="法定代表人")
    registered_address: Optional[str] = Field(None, max_length=256, description="注册地址")
    actual_controller: Optional[str] = Field(None, max_length=64, description="实际控制人")
    registered_capital: Optional[float] = Field(None, ge=0, description="注册资金")
    paid_in_capital: Optional[float] = Field(None, ge=0, description="实缴资本")
    establish_date: Optional[str] = Field(None, description="成立日期 YYYY-MM-DD")
    tax_completion_status: Optional[int] = Field(None, ge=0, le=100, description="纳税完成度")
    lawsuit_total: Optional[int] = Field(0, ge=0, description="涉诉总数")
    defendant_count: Optional[int] = Field(0, ge=0, description="作为被告次数")
    executed_count: Optional[int] = Field(0, ge=0, description="被执行次数")
    executed_amount: Optional[float] = Field(0, ge=0, description="被执行金额")
    avg_bank_flow: Optional[float] = Field(0, ge=0, description="平均银行流水")
    avg_approved_fund: Optional[float] = Field(0, ge=0, description="平均授信额度")

    contacts: Optional[List[ContactCreate]] = Field(None, description="对接人列表")


class MerchantListResponse(BaseModel):
    """分页列表响应模型。"""

    total: int
    page: int
    page_size: int
    list: List[dict]


# ======================================================================
# 接口
# ======================================================================
@router.post("/api/merchant", summary="创建或更新客商（含对接人）")
def create_or_update_merchant(
    payload: MerchantCreateUpdate,
    db: Session = Depends(get_db),
) -> dict:
    """创建或更新客商基础信息，可同时携带对接人列表。"""
    # 转换为字典，剔除未提供的可选字段（None）
    # 使用 Pydantic V2 的 model_dump，替代已废弃的 dict()
    data = payload.model_dump(exclude_none=True)
    contacts = None
    if payload.contacts:
        contacts = [c.model_dump(exclude_none=True) for c in payload.contacts]

    service = MerchantService(db)
    result = service.create_or_update(data, contacts)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "操作失败"))
    return result


@router.get("/api/merchant/list", summary="分页查询客商列表")
def list_merchants(
    keyword: Optional[str] = Query(None, description="模糊搜索商户名称/统一社会信用代码"),
    tax_number: Optional[str] = Query(None, description="精确匹配统一社会信用代码"),
    rating: Optional[str] = Query(None, description="精确匹配最新评级"),
    start_date: Optional[str] = Query(None, description="建档时间范围起 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="建档时间范围止 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
) -> dict:
    """分页查询客商列表，支持关键词、税号、评级及建档时间筛选。"""
    filters: dict = {}
    if keyword:
        filters["keyword"] = keyword
    if tax_number:
        filters["tax_number"] = tax_number
    if rating:
        filters["rating"] = rating
    if start_date:
        filters["start_date"] = start_date
    if end_date:
        filters["end_date"] = end_date

    service = MerchantService(db)
    return service.list_merchants(filters, page, page_size)


@router.get("/api/merchant/detail", summary="查询客商详情")
def get_merchant_detail(
    merchant_id: int = Query(..., ge=1, description="客商ID"),
    db: Session = Depends(get_db),
) -> dict:
    """查询客商详情（含基础信息、对接人、附件、最新评分、财务数据）。"""
    service = MerchantService(db)
    result = service.get_detail(merchant_id)
    if not result:
        raise HTTPException(status_code=404, detail="客商不存在")
    return result


@router.post("/api/merchant/attachment", summary="上传客商附件")
async def upload_attachment(
    merchant_id: int = Form(..., description="客商ID"),
    file: UploadFile = File(..., description="附件文件"),
    file_type: str = Form(..., description="文件类型：营业执照/财报/银行流水/其他"),
    uploaded_by: Optional[str] = Form(None, description="上传人"),
    db: Session = Depends(get_db),
) -> dict:
    """上传客商附件，文件落盘并记录附件信息。"""
    file_content = await file.read()
    service = MerchantService(db)
    result = service.upload_attachment(
        merchant_id=merchant_id,
        file_content=file_content,
        file_name=file.filename or "",
        file_type=file_type,
        uploaded_by=uploaded_by,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "上传失败"))
    return result

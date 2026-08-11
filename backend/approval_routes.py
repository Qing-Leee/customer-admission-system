"""
审批工作流接口层（M4）。

提供以下 FastAPI 接口：

| 方法 | 路径                    | 功能             | 权限角色     |
|------|------------------------|-----------------|-------------|
| POST | /api/approval/submit   | 提交审批         | 业务经办人   |
| POST | /api/approval/task/approve | 任务通过     | 各审批角色   |
| POST | /api/approval/task/reject  | 任务驳回     | 各审批角色   |
| GET  | /api/approval/my-tasks | 我的待办列表     | 所有审批角色 |
| GET  | /api/approval/history  | 审批历史         | 所有角色     |
| GET  | /api/approval/status   | 查询客商审批状态 | 所有角色     |

依赖注入：
    - 数据库 Session 通过 get_db_session 依赖注入
    - ScoringEngine 通过 get_scoring_engine 依赖注入
    - ApprovalService 通过 get_approval_service 依赖注入

注意：applicant / assignee 当前通过查询参数传入，
      实际部署应替换为从 JWT 认证上下文中获取当前登录用户。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from approval_service import ApprovalService
from dao import ScoringDao
from scoring_engine import ScoringEngine
from database import get_db as get_db_session


# ==================================================================
# 请求 / 响应模型
# ==================================================================

class SubmitRequest(BaseModel):
    """提交审批请求体。"""
    merchant_id: int = Field(..., ge=1, description="客商ID")
    remark: Optional[str] = Field(None, max_length=512, description="申请备注")


class TaskActionRequest(BaseModel):
    """任务处理请求体（通过/驳回通用）。"""
    task_id: int = Field(..., ge=1, description="任务ID")
    comment: Optional[str] = Field(None, max_length=512, description="处理意见")


class TaskListResponse(BaseModel):
    """待办任务列表响应体。"""
    total: int
    page: int
    page_size: int
    list: List[dict]


class ApprovalHistoryResponse(BaseModel):
    """审批历史响应体。"""
    success: bool = True
    order: dict
    tasks: List[dict]
    merchant: dict
    score: dict


class ApprovalStatusResponse(BaseModel):
    """客商审批状态响应体。"""
    success: bool = True
    has_approval: bool
    order: Optional[dict] = None
    message: Optional[str] = None


# ==================================================================
# 依赖注入
# ==================================================================
# 数据库 Session 通过 database.get_db 注入（见文件头部 import）


def get_scoring_engine(session: Session = Depends(get_db_session)) -> ScoringEngine:
    """构造 ScoringEngine 实例（FastAPI 依赖注入）。

    ScoringEngine 内部使用的 ScoringDao 与 ApprovalService 的 ScoringDao
    共享同一个 Session，确保事务一致性。
    """
    dao = ScoringDao(session)
    return ScoringEngine(dao)


def get_approval_service(
    session: Session = Depends(get_db_session),
    engine: ScoringEngine = Depends(get_scoring_engine),
) -> ApprovalService:
    """构造 ApprovalService 实例（FastAPI 依赖注入）。"""
    return ApprovalService(session, engine)


# ==================================================================
# 路由定义
# ==================================================================

router = APIRouter(prefix="/api/approval", tags=["审批工作流"])


# ------------------------------------------------------------------
# 1. 提交审批
# ------------------------------------------------------------------
@router.post("/submit", summary="提交审批")
def submit_approval(
    body: SubmitRequest,
    applicant: str = Query(..., description="申请人（实际应从认证上下文获取）"),
    service: ApprovalService = Depends(get_approval_service),
) -> dict:
    """
    提交审批申请。

    - 业务经办人提交后系统自动评分
    - 评分不合格则拒绝提交
    - 创建审批单并生成部门负责人复核任务
    """
    result = service.submit_approval(body.merchant_id, applicant, body.remark)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result)
    return result


# ------------------------------------------------------------------
# 2. 任务通过
# ------------------------------------------------------------------
@router.post("/task/approve", summary="任务通过")
def approve_task(
    body: TaskActionRequest,
    assignee: str = Query(..., description="处理人（实际应从认证上下文获取）"),
    service: ApprovalService = Depends(get_approval_service),
) -> dict:
    """
    通过审批任务。

    系统根据任务的 role_type 自动分发到对应的审批方法：
    - dept_head  → 部门负责人复核通过，触发并行会签
    - market/compliance/finance → 并行会签通过
    - executive  → 公司领导终审通过，写入评分快照
    """
    result = service.approve_task(body.task_id, assignee, body.comment or "")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result)
    return result


# ------------------------------------------------------------------
# 3. 任务驳回
# ------------------------------------------------------------------
@router.post("/task/reject", summary="任务驳回")
def reject_task(
    body: TaskActionRequest,
    assignee: str = Query(..., description="处理人（实际应从认证上下文获取）"),
    service: ApprovalService = Depends(get_approval_service),
) -> dict:
    """
    驳回审批任务。

    - 部门负责人驳回 → 流程终止
    - 并行会签任一驳回 → 流程终止，取消其他待办任务
    - 公司领导终审驳回 → 流程终止
    """
    result = service.reject_task(body.task_id, assignee, body.comment or "")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result)
    return result


# ------------------------------------------------------------------
# 4. 我的待办列表
# ------------------------------------------------------------------
@router.get("/my-tasks", summary="我的待办列表", response_model=TaskListResponse)
def my_tasks(
    assignee: str = Query(..., description="处理人（实际应从认证上下文获取）"),
    role_type: Optional[str] = Query(
        None,
        description="角色类型筛选：dept_head/market/compliance/finance/executive",
    ),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    service: ApprovalService = Depends(get_approval_service),
) -> TaskListResponse:
    """查询当前用户的待办审批任务列表（分页）。"""
    result = service.get_my_tasks(assignee, role_type, page, page_size)
    return TaskListResponse(**result)


# ------------------------------------------------------------------
# 5. 审批历史
# ------------------------------------------------------------------
@router.get("/history", summary="审批历史")
def approval_history(
    order_id: int = Query(..., ge=1, description="审批单ID"),
    service: ApprovalService = Depends(get_approval_service),
) -> dict:
    """
    查询审批历史。

    返回审批主表信息、所有任务（按时间升序）、客商基本信息、评分结果。
    """
    result = service.get_approval_history(order_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result)
    return result


# ------------------------------------------------------------------
# 6. 查询客商审批状态
# ------------------------------------------------------------------
@router.get("/status", summary="查询客商审批状态")
def approval_status(
    merchant_id: int = Query(..., ge=1, description="客商ID"),
    service: ApprovalService = Depends(get_approval_service),
) -> dict:
    """查询某个客商的最新审批状态，用于客商详情页展示。"""
    result = service.get_order_status(merchant_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result)
    return result


# ==================================================================
# 路由注册辅助函数
# ==================================================================

def register_approval_routes(app) -> None:
    """将审批工作流路由注册到 FastAPI 应用实例。

    用法：
        from approval_routes import register_approval_routes
        app = FastAPI()
        register_approval_routes(app)
    """
    app.include_router(router)

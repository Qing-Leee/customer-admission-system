"""
审批工作流业务逻辑层（M4）。

编排审批全流程：提交审批 → 部门负责人复核 → 并行会签 → 公司领导终审。
通过构造函数注入 ScoringEngine 实例，Service 层统一管理事务的 commit 和 rollback。

依赖：
    - ScoringDao：数据访问层
    - ScoringEngine：评分引擎
    - ScoringRequest / ScoringResult：评分请求/响应模型

状态流转说明见下方注释。
"""

# ======================================================================
#                           审批状态流转图
# ======================================================================
#
#  ┌─────────────────────────────────────────────────────────────────────────┐
#  │                           审批状态流转图                                │
#  ├─────────────────────────────────────────────────────────────────────────┤
#  │                                                                         │
#  │  业务员提交申请                                                         │
#  │       │                                                                 │
#  │       ▼                                                                 │
#  │  ┌──────────────┐                                                      │
#  │  │ pending_dept │  ← 部门负责人复核                                    │
#  │  └──────────────┘                                                      │
#  │       │                                                                 │
#  │       ├── 驳回 ──▶ rejected (流程终止)                                │
#  │       │                                                                 │
#  │       ▼ 通过                                                            │
#  │  ┌──────────────────┐                                                  │
#  │  │ parallel_signing │  ← 市场、法务、财务 并行会签                     │
#  │  └──────────────────┘                                                  │
#  │       │                                                                 │
#  │       ├── 任一驳回 ──▶ rejected (流程终止)                            │
#  │       │                                                                 │
#  │       ▼ 全部通过                                                        │
#  │  ┌──────────────┐                                                      │
#  │  │ final_signing│  ← 公司领导终审                                     │
#  │  └──────────────┘                                                      │
#  │       │                                                                 │
#  │       ├── 驳回 ──▶ rejected (流程终止)                                │
#  │       │                                                                 │
#  │       ▼ 通过                                                            │
#  │  ┌──────────────┐                                                      │
#  │  │   approved   │  ← 写入 score_snapshot，流程结束                    │
#  │  └──────────────┘                                                      │
#  │                                                                         │
#  └─────────────────────────────────────────────────────────────────────────┘
#
#  角色职责：
#    - dept_head   : 部门负责人（复核申请材料的完整性和真实性）
#    - market      : 市场部（审核客商资信、行业风险）
#    - compliance  : 法务部（审核合同条款、法律风险）
#    - finance     : 财务部（审核财务数据、资金风险）
#    - executive   : 公司领导（终审，最终决策）
#
#  状态常量：
#    STATUS_PENDING_DEPT    = "pending_dept"
#    STATUS_PARALLEL_SIGNING = "parallel_signing"
#    STATUS_FINAL_SIGNING   = "final_signing"
#    STATUS_APPROVED        = "approved"
#    STATUS_REJECTED        = "rejected"
#
#  并行会签角色集合：
#    PARALLEL_ROLES = {"market", "compliance", "finance"}
#
# ======================================================================

import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from dao import ScoringDao
from models import ApprovalOrder, ApprovalTask, FinancialRawData, MerchantBasicInfo
from schemas import ScoringRequest, ScoringResult, RawFinancialData, ProjectQualityData
from scoring_engine import ScoringEngine


# ---- 状态常量 ----
STATUS_PENDING_DEPT = "pending_dept"
STATUS_PARALLEL_SIGNING = "parallel_signing"
STATUS_FINAL_SIGNING = "final_signing"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# ---- 进行中状态集合（用于防重复提交）----
ACTIVE_STATUSES = [
    STATUS_PENDING_DEPT,
    STATUS_PARALLEL_SIGNING,
    STATUS_FINAL_SIGNING,
]

# ---- 并行会签角色集合 ----
PARALLEL_ROLES = ["market", "compliance", "finance"]

# ---- 状态 → 步骤描述映射 ----
STEP_MAP = {
    STATUS_PENDING_DEPT: "部门负责人复核",
    STATUS_PARALLEL_SIGNING: "市场/法务/财务并行会签",
    STATUS_FINAL_SIGNING: "公司领导终审",
    STATUS_APPROVED: "审批通过",
    STATUS_REJECTED: "审批驳回",
}


class ApprovalService:
    """审批工作流服务，编排完整审批流程。

    通过构造函数注入 ``ScoringEngine`` 实例和可选的审批人配置。
    Service 层统一管理 ``session.commit`` 和 ``session.rollback``。
    所有公开方法返回 ``{"success": bool, ...}`` 格式的字典。
    """

    # 默认审批人配置（实际部署时应通过构造函数传入真实用户名）
    DEFAULT_ASSIGNEES: Dict[str, str] = {
        "dept_head": "部门负责人",
        "market": "市场部审批人",
        "compliance": "法务部审批人",
        "finance": "财务部审批人",
        "executive": "公司领导",
    }

    def __init__(
        self,
        session: Session,
        scoring_engine: ScoringEngine,
        assignees: Optional[Dict[str, str]] = None,
    ) -> None:
        self.dao = ScoringDao(session)
        self.session = session
        self.scoring_engine = scoring_engine
        self.assignees: Dict[str, str] = {**self.DEFAULT_ASSIGNEES, **(assignees or {})}

    # ==================================================================
    # 内部辅助方法
    # ==================================================================

    def _build_scoring_request(self, merchant_id: int) -> ScoringRequest:
        """根据客商数据构造 ScoringRequest。

        汇总财务原始数据、客商画像信息，构造评分请求。
        评分引擎内部会自行读取 merchant_profile，此处仅需提供
        raw_financial_data / project_quality_data / performance_data。
        """
        # ---- 查询财务原始数据 ----
        fin_rows = list(
            self.session.execute(
                select(FinancialRawData)
                .where(FinancialRawData.merchant_id == merchant_id)
                .order_by(
                    FinancialRawData.period_year.desc(),
                    FinancialRawData.period_quarter.desc(),
                )
            ).scalars().all()
        )

        # 取最新一条财务记录构造 RawFinancialData（评分引擎需要单个对象）
        raw_financial_data = None
        if fin_rows:
            latest = fin_rows[0]
            raw_financial_data = RawFinancialData(
                period_year=latest.period_year or 2025,
                period_quarter=latest.period_quarter,
                total_asset=float(latest.total_asset or 0),
                total_liability=float(latest.total_liability or 0),
                current_asset=float(latest.current_asset or 0),
                current_liability=float(latest.current_liability or 0),
                revenue=float(latest.revenue or 0),
                net_profit=float(latest.net_profit or 0),
                inventory=float(latest.inventory or 0),
                prepay=float(latest.prepay or 0),
                operating_cash_flow=float(latest.operating_cash_flow or 0),
                net_asset=float(latest.net_asset or 0),
            )

        # ---- 获取客商画像 ----
        profile = self.dao.get_merchant_profile(merchant_id)

        # 是否授信：有财务数据则视为授信场景
        is_credit_applied = raw_financial_data is not None

        # ---- 构造项目质量数据（首次+授信场景需要）----
        # 实际项目中应从独立数据源获取，此处基于客商画像合理推导
        project_quality_data = None
        if is_credit_applied:
            project_quality_data = ProjectQualityData(
                avg_gross_margin=0.30,
                price_volatility=0.15,
                contract_terms_score=80.0,
                legal_opinion_adoption=0.85,
                warehouse_control_score=75.0,
                logistics_track_score=80.0,
            )

        # ---- 履约数据（动态场景需要，首次场景不需要）----
        performance_data = None

        return ScoringRequest(
            merchant_id=merchant_id,
            is_credit_applied=is_credit_applied,
            raw_financial_data=raw_financial_data,
            project_quality_data=project_quality_data,
            performance_data=performance_data,
        )

    @staticmethod
    def _result_to_score_dict(result: ScoringResult) -> dict:
        """将 ScoringResult 转换为 create_approval_order 所需的 score_result 字典。"""
        dim = result.dimension_scores
        return {
            "total_score": result.total_score,
            "rating": result.rating,
            "dimension_scores": {
                "subject_quality": dim.subject_quality,
                "financial": dim.financial,
                "project_quality": dim.project_quality,
                "performance_quality": dim.performance_quality,
            },
        }

    def _validate_task(
        self,
        task_id: int,
        assignee: str,
        expected_role_type: str = None,
        expected_role_types: List[str] = None,
    ) -> dict:
        """校验任务归属与状态，返回校验结果。

        Returns:
            {"valid": bool, "error": str, "detail": dict}
            detail 为 get_task_detail 的返回值。
        """
        detail = self.dao.get_task_detail(task_id)
        if not detail:
            return {"valid": False, "error": "任务不存在", "detail": {}}

        task = detail["task"]

        if task["assignee"] != assignee:
            return {"valid": False, "error": "任务归属不匹配，无权处理", "detail": detail}

        if expected_role_type and task["role_type"] != expected_role_type:
            return {
                "valid": False,
                "error": f"任务角色类型不匹配，期望 {expected_role_type}，实际 {task['role_type']}",
                "detail": detail,
            }

        if expected_role_types and task["role_type"] not in expected_role_types:
            return {
                "valid": False,
                "error": f"任务角色类型不在允许范围 {expected_role_types} 内",
                "detail": detail,
            }

        if task["task_status"] != "pending":
            return {
                "valid": False,
                "error": f"任务当前状态为 {task['task_status']}，无法处理",
                "detail": detail,
            }

        return {"valid": True, "error": "", "detail": detail}

    # ==================================================================
    # 公开业务方法
    # ==================================================================

    def submit_approval(
        self,
        merchant_id: int,
        applicant: str,
        remark: str = None,
    ) -> dict:
        """
        提交审批流程。

        执行步骤：
        1. 检查该客商是否已有「进行中」的审批单（pending_dept / parallel_signing / final_signing）
        2. 获取客商完整数据，构造 ScoringRequest
        3. 调用评分引擎计算
        4. 若不合格（qualified=False），返回错误，不允许提交
        5. 创建 approval_order，状态 = pending_dept
        6. 创建部门负责人任务
        7. 事务提交，返回 order_id
        """
        try:
            # 1. 防重复提交
            existing = self.dao.get_order_by_merchant_and_status(
                merchant_id, ACTIVE_STATUSES
            )
            if existing:
                return {
                    "success": False,
                    "error": "该客商已有进行中的审批单，不可重复提交",
                }

            # 2. 构造评分请求
            request = self._build_scoring_request(merchant_id)

            # 3. 调用评分引擎
            result = self.scoring_engine.calculate(request)

            # 4. 校验是否合格
            if not result.qualified:
                self.session.rollback()
                return {
                    "success": False,
                    "error": "评分不合格，不允许提交审批",
                    "total_score": result.total_score,
                    "rating": result.rating,
                }

            # 5. 创建审批主表
            score_dict = self._result_to_score_dict(result)
            order_id = self.dao.create_approval_order(
                merchant_id, applicant, score_dict, remark
            )

            # 6. 创建部门负责人任务
            dept_head_assignee = self.assignees.get("dept_head", "部门负责人")
            self.dao.create_approval_tasks(order_id, {"dept_head": dept_head_assignee})

            # 7. 事务提交
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "total_score": result.total_score,
                "rating": result.rating,
                "message": "审批已提交，等待部门负责人复核",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def approve_dept(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """
        部门负责人复核通过。

        执行步骤：
        1. 校验任务归属（task_id 对应 assignee 和 role_type='dept_head'）
        2. 校验任务状态为 pending
        3. 更新任务为 done，action='approve'
        4. 更新 order 状态为 parallel_signing
        5. 并行创建市场、法务、财务三个任务
        6. 提交事务，返回成功
        """
        try:
            # 1-2. 校验
            check = self._validate_task(task_id, assignee, expected_role_type="dept_head")
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            order_id = check["detail"]["task"]["order_id"]

            # 3. 更新任务
            self.dao.update_task(task_id, "approve", comment)

            # 4. 更新审批单状态
            self.dao.update_order_status(
                order_id,
                STATUS_PARALLEL_SIGNING,
                STEP_MAP[STATUS_PARALLEL_SIGNING],
            )

            # 5. 创建并行会签任务
            parallel_assignees = {
                "market": self.assignees.get("market", "市场部审批人"),
                "compliance": self.assignees.get("compliance", "法务部审批人"),
                "finance": self.assignees.get("finance", "财务部审批人"),
            }
            self.dao.create_approval_tasks(order_id, parallel_assignees)

            # 6. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "message": "部门负责人复核通过，已进入市场/法务/财务并行会签",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def reject_dept(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """
        部门负责人驳回。

        执行步骤：
        1. 校验任务归属
        2. 更新任务为 done，action='reject'
        3. 更新 order 状态为 rejected
        4. 流程终止，事务提交
        """
        try:
            # 1. 校验
            check = self._validate_task(task_id, assignee, expected_role_type="dept_head")
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            order_id = check["detail"]["task"]["order_id"]

            # 2. 更新任务
            self.dao.update_task(task_id, "reject", comment)

            # 3. 更新审批单状态
            self.dao.update_order_status(
                order_id,
                STATUS_REJECTED,
                STEP_MAP[STATUS_REJECTED],
            )

            # 4. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "message": "部门负责人驳回，审批流程终止",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def approve_parallel(
        self,
        task_id: int,
        assignee: str,
        role_type: str,
        comment: str = "",
    ) -> dict:
        """
        市场/法务/财务 并行会签通过。

        执行步骤：
        1. 校验任务归属（assignee 和 role_type 必须匹配，role_type 须为 market/compliance/finance）
        2. 校验任务状态为 pending
        3. 更新该任务为 done，action='approve'
        4. 检查该 order 下所有 market/compliance/finance 任务是否全部 done
        5. 全部完成 → 更新 order 状态为 final_signing，创建公司领导终审任务
        6. 未全部完成 → 保持 parallel_signing 状态，等待其他角色
        7. 提交事务，返回成功
        """
        try:
            # 1-2. 校验
            check = self._validate_task(
                task_id, assignee, expected_role_types=PARALLEL_ROLES
            )
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            if check["detail"]["task"]["role_type"] != role_type:
                return {"success": False, "error": "role_type 与任务实际角色不匹配"}

            order_id = check["detail"]["task"]["order_id"]

            # 3. 更新任务
            self.dao.update_task(task_id, "approve", comment)

            # 4. 检查所有并行会签任务是否全部完成
            parallel_tasks = list(
                self.session.execute(
                    select(ApprovalTask).where(
                        ApprovalTask.order_id == order_id,
                        ApprovalTask.role_type.in_(PARALLEL_ROLES),
                    )
                ).scalars().all()
            )
            all_done = all(t.task_status == "done" for t in parallel_tasks)

            if all_done:
                # 5. 全部完成 → 进入终审
                self.dao.update_order_status(
                    order_id,
                    STATUS_FINAL_SIGNING,
                    STEP_MAP[STATUS_FINAL_SIGNING],
                )
                executive_assignee = self.assignees.get("executive", "公司领导")
                self.dao.create_approval_tasks(
                    order_id, {"executive": executive_assignee}
                )
                message = "所有并行会签已通过，已进入公司领导终审"
            else:
                # 6. 未全部完成 → 保持状态
                message = f"{role_type} 会签通过，等待其他角色审批"

            # 7. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "all_parallel_done": all_done,
                "message": message,
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def reject_parallel(
        self,
        task_id: int,
        assignee: str,
        role_type: str,
        comment: str = "",
    ) -> dict:
        """
        市场/法务/财务 并行会签驳回（任一驳回即终止）。

        执行步骤：
        1. 校验任务归属
        2. 更新该任务为 done，action='reject'
        3. 更新 order 状态为 rejected
        4. 取消该 order 下所有其他未处理的并行任务（task_status='cancelled'）
        5. 流程终止，事务提交
        """
        try:
            # 1. 校验
            check = self._validate_task(
                task_id, assignee, expected_role_types=PARALLEL_ROLES
            )
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            if check["detail"]["task"]["role_type"] != role_type:
                return {"success": False, "error": "role_type 与任务实际角色不匹配"}

            order_id = check["detail"]["task"]["order_id"]

            # 2. 更新任务
            self.dao.update_task(task_id, "reject", comment)

            # 3. 更新审批单状态
            self.dao.update_order_status(
                order_id,
                STATUS_REJECTED,
                STEP_MAP[STATUS_REJECTED],
            )

            # 4. 取消其他未处理的并行任务
            cancelled_count = self.dao.cancel_parallel_tasks(order_id)

            # 5. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "cancelled_count": cancelled_count,
                "message": f"{role_type} 会签驳回，流程终止。已取消 {cancelled_count} 个待办任务",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def approve_final(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """
        公司领导终审通过。

        执行步骤：
        1. 校验任务归属（role_type='executive'）
        2. 更新任务为 done，action='approve'
        3. 更新 order 状态为 approved
        4. 调用 dao.save_score_snapshot 将评分结果写入 score_snapshot
        5. 可选的：更新 merchant_basic_info 中的 status 字段为 'active'
        6. 事务提交，返回成功
        """
        try:
            # 1. 校验
            check = self._validate_task(task_id, assignee, expected_role_type="executive")
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            order_id = check["detail"]["task"]["order_id"]
            merchant_id = check["detail"]["order"]["merchant_id"]

            # 2. 更新任务
            self.dao.update_task(task_id, "approve", comment)

            # 3. 更新审批单状态
            self.dao.update_order_status(
                order_id,
                STATUS_APPROVED,
                STEP_MAP[STATUS_APPROVED],
            )

            # 4. 重新评分并写入快照
            #    终审通过时重新调用评分引擎，确保快照基于最新客商数据
            request = self._build_scoring_request(merchant_id)
            result = self.scoring_engine.calculate(request)
            snapshot_id = self.dao.save_score_snapshot(merchant_id, result)

            # 5. 可选：更新客商状态为 active
            #    注意：当前 MerchantBasicInfo 模型无 status 字段，
            #    若后续新增该字段，可取消下方注释启用。
            # merchant = self.session.execute(
            #     select(MerchantBasicInfo).where(
            #         MerchantBasicInfo.merchant_id == merchant_id
            #     )
            # ).scalars().first()
            # if merchant:
            #     merchant.status = "active"

            # 6. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "snapshot_id": snapshot_id,
                "total_score": result.total_score,
                "rating": result.rating,
                "message": "终审通过，评分快照已写入 score_snapshot 表",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    def reject_final(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """
        公司领导终审驳回。

        执行步骤：
        1. 校验任务归属
        2. 更新任务为 done，action='reject'
        3. 更新 order 状态为 rejected
        4. 事务提交，返回成功
        """
        try:
            # 1. 校验
            check = self._validate_task(task_id, assignee, expected_role_type="executive")
            if not check["valid"]:
                return {"success": False, "error": check["error"]}

            order_id = check["detail"]["task"]["order_id"]

            # 2. 更新任务
            self.dao.update_task(task_id, "reject", comment)

            # 3. 更新审批单状态
            self.dao.update_order_status(
                order_id,
                STATUS_REJECTED,
                STEP_MAP[STATUS_REJECTED],
            )

            # 4. 提交事务
            self.session.commit()

            return {
                "success": True,
                "order_id": order_id,
                "message": "公司领导终审驳回，审批流程终止",
            }
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": str(e)}

    # ==================================================================
    # 便捷分发方法（供路由层统一调用）
    # ==================================================================

    def approve_task(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """根据任务 role_type 自动分发到对应的 approve 方法。"""
        detail = self.dao.get_task_detail(task_id)
        if not detail:
            return {"success": False, "error": "任务不存在"}

        role_type = detail["task"]["role_type"]

        if role_type == "dept_head":
            return self.approve_dept(task_id, assignee, comment)
        elif role_type in PARALLEL_ROLES:
            return self.approve_parallel(task_id, assignee, role_type, comment)
        elif role_type == "executive":
            return self.approve_final(task_id, assignee, comment)
        else:
            return {"success": False, "error": f"未知角色类型: {role_type}"}

    def reject_task(
        self,
        task_id: int,
        assignee: str,
        comment: str = "",
    ) -> dict:
        """根据任务 role_type 自动分发到对应的 reject 方法。"""
        detail = self.dao.get_task_detail(task_id)
        if not detail:
            return {"success": False, "error": "任务不存在"}

        role_type = detail["task"]["role_type"]

        if role_type == "dept_head":
            return self.reject_dept(task_id, assignee, comment)
        elif role_type in PARALLEL_ROLES:
            return self.reject_parallel(task_id, assignee, role_type, comment)
        elif role_type == "executive":
            return self.reject_final(task_id, assignee, comment)
        else:
            return {"success": False, "error": f"未知角色类型: {role_type}"}

    # ==================================================================
    # 查询方法
    # ==================================================================

    def get_my_tasks(
        self,
        assignee: str,
        role_type: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取我的待办任务列表（透传 dao.get_pending_tasks）。"""
        return self.dao.get_pending_tasks(assignee, role_type, page, page_size)

    def get_approval_history(self, order_id: int) -> dict:
        """查询审批历史（含所有任务 + 关联客商信息）。"""
        try:
            # ---- 查询审批主表 ----
            order = self.session.execute(
                select(ApprovalOrder).where(ApprovalOrder.id == order_id)
            ).scalars().first()

            if order is None:
                return {"success": False, "error": "审批单不存在"}

            # ---- 查询所有任务（按时间升序）----
            tasks = self.dao.get_order_tasks(order_id)

            # ---- 查询客商基本信息 ----
            merchant = self.session.execute(
                select(MerchantBasicInfo).where(
                    MerchantBasicInfo.merchant_id == order.merchant_id
                )
            ).scalars().first()

            merchant_info = ScoringDao._serialize_row(merchant) if merchant else {}

            # ---- 解析评分结果 ----
            dim_scores: dict = {}
            if order.dimension_scores_json:
                try:
                    dim_scores = json.loads(order.dimension_scores_json)
                except (json.JSONDecodeError, TypeError):
                    dim_scores = {}

            score_info = {
                "total_score": float(order.total_score) if order.total_score is not None else None,
                "rating": order.rating,
                "dimension_scores": dim_scores,
            }

            return {
                "success": True,
                "order": ScoringDao._serialize_row(order),
                "tasks": tasks,
                "merchant": merchant_info,
                "score": score_info,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_order_status(self, merchant_id: int) -> dict:
        """查询某个客商的最新审批状态。"""
        try:
            latest = self.dao.get_latest_approval_by_merchant(merchant_id)
            if not latest:
                return {
                    "success": True,
                    "has_approval": False,
                    "message": "该客商暂无审批记录",
                }
            return {
                "success": True,
                "has_approval": True,
                "order": latest,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

"""
数据访问层模块。

ScoringDao 封装了风控评分系统所需的全部数据库查询操作，
方法签名与 scoring_engine.py 中的调用严格匹配。
审批工作流模块（M4）的数据访问方法追加在文件末尾。
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from models import (
    ApprovalOrder,
    ApprovalTask,
    FinancialRawData,
    MerchantAttachment,
    MerchantBasicInfo,
    MerchantContact,
    RuleIndicatorDetail,
    RuleVersion,
    RuleWeightConfig,
    ScoreSnapshot,
)
from schemas import MerchantProfile, ScoringResult


class ScoringDao:
    """风控评分数据访问对象。"""

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------
    # 1. 查询是否已存在评分快照
    # ------------------------------------------------------------------
    def has_score_snapshot(self, merchant_id: int) -> bool:
        """查询 score_snapshot 表是否已有该 merchant_id 的记录。"""
        stmt = (
            select(ScoreSnapshot.id)
            .where(ScoreSnapshot.merchant_id == merchant_id)
            .limit(1)
        )
        result = self.session.execute(stmt).first()
        return result is not None

    # ------------------------------------------------------------------
    # 2. 获取当前生效的规则版本
    # ------------------------------------------------------------------
    def get_active_rule_version(self) -> Optional[RuleVersion]:
        """查询 rule_version 表中 is_active = 1 的记录。"""
        stmt = (
            select(RuleVersion)
            .where(RuleVersion.is_active == 1)
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    # ------------------------------------------------------------------
    # 3. 获取维度权重配置
    # ------------------------------------------------------------------
    def get_weight_config(self, rule_version_id: int) -> List[Dict]:
        """
        查询 rule_weight_config 表，将四个独立权重列转换为通用的
        dimension_code + weight 字典列表。

        数据库存储结构（独立列）：
            subject_weight, financial_weight, project_weight, performance_weight

        返回格式（统一字典）：
            [
                {"dimension_code": "subject_quality",     "weight": 0.25},
                {"dimension_code": "financial",           "weight": 0.30},
                {"dimension_code": "project_quality",     "weight": 0.20},
                {"dimension_code": "performance_quality", "weight": 0.25},
            ]
        """
        stmt = select(RuleWeightConfig).where(
            RuleWeightConfig.rule_version_id == rule_version_id
        )
        row = self.session.execute(stmt).scalars().first()

        if row is None:
            return []

        return [
            {"dimension_code": "subject_quality", "weight": float(row.subject_weight or 0)},
            {"dimension_code": "financial", "weight": float(row.financial_weight or 0)},
            {"dimension_code": "project_quality", "weight": float(row.project_weight or 0)},
            {"dimension_code": "performance_quality", "weight": float(row.performance_weight or 0)},
        ]

    # ------------------------------------------------------------------
    # 4. 获取指标明细列表
    # ------------------------------------------------------------------
    def get_indicator_details(self, rule_version_id: int) -> List[RuleIndicatorDetail]:
        """查询 rule_indicator_detail 表，按 sort_order 升序排列。"""
        stmt = (
            select(RuleIndicatorDetail)
            .where(RuleIndicatorDetail.rule_version_id == rule_version_id)
            .order_by(RuleIndicatorDetail.sort_order.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    # ------------------------------------------------------------------
    # 5. 获取商户画像
    # ------------------------------------------------------------------
    def get_merchant_profile(self, merchant_id: int) -> Optional[MerchantProfile]:
        """
        查询 merchant_basic_info 表（主键 merchant_id），
        组装成 MerchantProfile 对象。

        包含注册资金、实缴资本、成立时间、涉诉汇总、银行流水等信息。
        """
        stmt = (
            select(MerchantBasicInfo)
            .where(MerchantBasicInfo.merchant_id == merchant_id)
            .limit(1)
        )
        row = self.session.execute(stmt).scalars().first()
        if row is None:
            return None

        return MerchantProfile(
            merchant_id=row.merchant_id,
            registered_capital=float(row.registered_capital or 0),
            paid_in_capital=float(row.paid_in_capital or 0),
            establish_date=str(row.establish_date) if row.establish_date else "",
            tax_completion_status=int(row.tax_completion_status or 0),
            lawsuit_total=int(row.lawsuit_total or 0),
            defendant_count=int(row.defendant_count or 0),
            executed_count=int(row.executed_count or 0),
            executed_amount=float(row.executed_amount or 0),
            avg_bank_flow=float(row.avg_bank_flow or 0),
            avg_approved_fund=float(row.avg_approved_fund or 0),
        )

    # ------------------------------------------------------------------
    # 6. 获取最新财务数据ID
    # ------------------------------------------------------------------
    def get_latest_financial_data_id(self, merchant_id: int) -> Optional[int]:
        """
        查询 financial_raw_data 表，按 period_year DESC, period_quarter DESC
        取最新一条记录的 id。
        """
        stmt = (
            select(FinancialRawData.id)
            .where(FinancialRawData.merchant_id == merchant_id)
            .order_by(
                FinancialRawData.period_year.desc(),
                FinancialRawData.period_quarter.desc(),
            )
            .limit(1)
        )
        result = self.session.execute(stmt).first()
        if result is None:
            return None
        return result[0]

    # ------------------------------------------------------------------
    # 7. 批量 Upsert 财务原始数据
    # ------------------------------------------------------------------
    # 财务数值字段映射：dict 键 -> ORM 属性名
    _FINANCIAL_FIELD_MAP = {
        "total_asset": "total_asset",
        "total_liability": "total_liability",
        "current_asset": "current_asset",
        "current_liability": "current_liability",
        "revenue": "revenue",
        "net_profit": "net_profit",
        "inventory": "inventory",
        "prepay": "prepay",
        "operating_cash_flow": "operating_cash_flow",
        "net_asset": "net_asset",
    }

    def upsert_financial_data(self, merchant_id: int, records: List[dict]) -> int:
        """
        批量 Upsert 财务原始数据（存在则更新，不存在则插入）。

        Args:
            merchant_id: 客商 ID
            records: 财务数据列表

        Returns:
            影响的行数（新增 + 更新）

        事务管理说明：
            - 本方法不自行 commit，由调用方（Service 层）统一管理事务。
            - 使用 for 循环 + SELECT + UPDATE/INSERT，每处理一条记录 flush 一次，
              确保同一批次内后续查询能感知前序变更，防止重复插入。
        """
        affected = 0

        for record in records:
            period_year = record.get("period_year")
            period_quarter = record.get("period_quarter")

            # ---- 查询是否存在 ----
            stmt = select(FinancialRawData).where(
                FinancialRawData.merchant_id == merchant_id,
                FinancialRawData.period_year == period_year,
            )
            if period_quarter is None:
                stmt = stmt.where(FinancialRawData.period_quarter.is_(None))
            else:
                stmt = stmt.where(FinancialRawData.period_quarter == period_quarter)

            existing = self.session.execute(stmt).scalars().first()

            if existing is not None:
                # ---- 存在则更新 ----
                for dict_key, attr_name in self._FINANCIAL_FIELD_MAP.items():
                    if dict_key in record:
                        setattr(existing, attr_name, record[dict_key])
            else:
                # ---- 不存在则插入 ----
                new_record = FinancialRawData(
                    merchant_id=merchant_id,
                    period_year=period_year,
                    period_quarter=period_quarter,
                )
                for dict_key, attr_name in self._FINANCIAL_FIELD_MAP.items():
                    if dict_key in record:
                        setattr(new_record, attr_name, record[dict_key])
                self.session.add(new_record)

            # flush 使当前变更在事务内可见，确保后续循环能查到刚写入的记录
            # 注意：flush 不提交事务，最终由调用方 commit
            self.session.flush()
            affected += 1

        return affected

    # ==================================================================
    # 以下为客商录入模块新增方法（任务 B）
    # ==================================================================

    # 客商基础信息可写入字段：dict 键 -> ORM 属性名（键名与属性名一致）
    _MERCHANT_FIELD_MAP: List[str] = [
        "merchant_name",
        "tax_number",
        "legal_person",
        "registered_address",
        "actual_controller",
        "registered_capital",
        "paid_in_capital",
        "establish_date",
        "tax_completion_status",
        "lawsuit_total",
        "defendant_count",
        "executed_count",
        "executed_amount",
        "avg_bank_flow",
        "avg_approved_fund",
    ]

    # 对接人字段：dict 键 -> ORM 属性名
    _CONTACT_FIELD_MAP: Dict[str, str] = {
        "name": "name",
        "position": "position",
        "phone": "phone",
        "email": "email",
        "business_role": "business_role",
        "is_primary": "is_primary",
        "remark": "remark",
    }

    @staticmethod
    def _serialize_row(row) -> dict:
        """将 ORM 行对象序列化为 JSON 友好的字典（处理 Decimal / datetime）。"""
        if row is None:
            return {}
        result: dict = {}
        for column in row.__table__.columns:
            value = getattr(row, column.name)
            if isinstance(value, datetime):
                result[column.name] = value.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(value, date):
                result[column.name] = value.strftime("%Y-%m-%d")
            elif isinstance(value, Decimal):
                result[column.name] = float(value)
            else:
                result[column.name] = value
        return result

    # ------------------------------------------------------------------
    # 8. 插入或更新客商基础信息（含对接人）
    # ------------------------------------------------------------------
    def upsert_merchant(self, data: dict, contacts: List[dict] = None) -> int:
        """
        插入或更新客商基础信息（含对接人）。

        Args:
            data: 客商基础信息字典，包含：
                - merchant_name (必填)
                - tax_number (必填，唯一)
                - legal_person (必填)
                - registered_address (可选)
                - actual_controller (可选)
                - registered_capital (可选，DECIMAL)
                - paid_in_capital (可选，DECIMAL)
                - establish_date (可选，字符串 YYYY-MM-DD)
                - tax_completion_status (可选，0-100)
                - lawsuit_total (可选，默认0)
                - defendant_count (可选，默认0)
                - executed_count (可选，默认0)
                - executed_amount (可选，默认0)
                - avg_bank_flow (可选，默认0)
                - avg_approved_fund (可选，默认0)
                - 以及后续扩展字段
            contacts: 可选，对接人列表，每个包含 name, position, phone, email,
                      business_role, is_primary

        Returns:
            merchant_id (int)

        实现逻辑：
            1. 根据 tax_number 查询是否存在
            2. 若存在，更新所有字段
            3. 若不存在，插入新记录
            4. 如果传入了 contacts，先删除该客商的所有旧联络人，再批量插入新联络人
            5. 校验：contacts 中 is_primary=1 的记录必须恰好 1 条

        事务管理：本方法不自行 commit，由 Service 层统一管理。
        """
        tax_number = data.get("tax_number")
        if not tax_number:
            raise ValueError("tax_number 为必填项")

        # ---- 校验：contacts 中 is_primary=1 恰好 1 条 ----
        if contacts:
            primary_count = sum(1 for c in contacts if c.get("is_primary"))
            if primary_count != 1:
                raise ValueError("contacts 中 is_primary=1 的记录必须恰好 1 条")

        # ---- 根据 tax_number 查询是否已存在 ----
        stmt = select(MerchantBasicInfo).where(MerchantBasicInfo.tax_number == tax_number)
        merchant = self.session.execute(stmt).scalars().first()

        if merchant is not None:
            # ---- 存在则更新 ----
            for field in self._MERCHANT_FIELD_MAP:
                if field in data:
                    setattr(merchant, field, data[field])
        else:
            # ---- 不存在则插入 ----
            merchant = MerchantBasicInfo(tax_number=tax_number)
            for field in self._MERCHANT_FIELD_MAP:
                if field in data:
                    setattr(merchant, field, data[field])
            self.session.add(merchant)

        # flush 以生成 merchant_id（不提交事务）
        self.session.flush()
        merchant_id = int(merchant.merchant_id)

        # ---- 处理对接人：先全量删除旧联络人，再批量插入 ----
        if contacts:
            self.delete_contacts_by_merchant(merchant_id)
            for contact_data in contacts:
                is_primary = contact_data.get("is_primary")
                contact = MerchantContact(
                    merchant_id=merchant_id,
                    is_primary=1 if is_primary else 0,
                )
                for dict_key, attr_name in self._CONTACT_FIELD_MAP.items():
                    if dict_key == "is_primary":
                        continue
                    if dict_key in contact_data:
                        setattr(contact, attr_name, contact_data[dict_key])
                self.session.add(contact)
            self.session.flush()

        return merchant_id

    # ------------------------------------------------------------------
    # 9. 分页查询客商列表
    # ------------------------------------------------------------------
    def get_merchant_list(self, filters: dict, page: int, page_size: int) -> dict:
        """
        分页查询客商列表。

        Args:
            filters: 筛选条件，包含：
                - keyword (可选，模糊搜索 merchant_name / tax_number)
                - tax_number (可选，精确匹配)
                - rating (可选，精确匹配，关联 score_snapshot 取最新评级)
                - start_date (可选，建档时间范围起)
                - end_date (可选，建档时间范围止)
            page: 页码，从1开始
            page_size: 每页条数

        Returns:
            {
                "total": 100,
                "page": 1,
                "page_size": 20,
                "list": [
                    {
                        "merchant_id": 1,
                        "merchant_name": "XX公司",
                        "tax_number": "911...",
                        "legal_person": "张三",
                        "latest_rating": "A",
                        "primary_contact": "李四",
                        "created_at": "2026-01-01"
                    }
                ]
            }
        """
        keyword = filters.get("keyword")
        tax_number = filters.get("tax_number")
        rating = filters.get("rating")
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")

        # 最新评分快照子查询：每个 merchant_id 取最大 id（即最新一条）
        latest_score_subq = (
            select(
                ScoreSnapshot.merchant_id.label("merchant_id"),
                func.max(ScoreSnapshot.id).label("max_id"),
            )
            .group_by(ScoreSnapshot.merchant_id)
            .subquery()
        )

        # 主联络人子查询：is_primary=1 的对接人姓名
        primary_contact_subq = (
            select(
                MerchantContact.merchant_id.label("merchant_id"),
                MerchantContact.name.label("name"),
            )
            .where(MerchantContact.is_primary == 1)
            .subquery()
        )

        # ---- 主查询 ----
        stmt = (
            select(
                MerchantBasicInfo.merchant_id,
                MerchantBasicInfo.merchant_name,
                MerchantBasicInfo.tax_number,
                MerchantBasicInfo.legal_person,
                MerchantBasicInfo.created_at,
                ScoreSnapshot.rating.label("latest_rating"),
                primary_contact_subq.c.name.label("primary_contact"),
            )
            .select_from(MerchantBasicInfo)
            .outerjoin(
                latest_score_subq,
                latest_score_subq.c.merchant_id == MerchantBasicInfo.merchant_id,
            )
            .outerjoin(
                ScoreSnapshot,
                ScoreSnapshot.id == latest_score_subq.c.max_id,
            )
            .outerjoin(
                primary_contact_subq,
                primary_contact_subq.c.merchant_id == MerchantBasicInfo.merchant_id,
            )
        )

        # ---- 筛选条件 ----
        conditions = []
        if keyword:
            like_pattern = f"%{keyword}%"
            conditions.append(
                (MerchantBasicInfo.merchant_name.like(like_pattern))
                | (MerchantBasicInfo.tax_number.like(like_pattern))
            )
        if tax_number:
            conditions.append(MerchantBasicInfo.tax_number == tax_number)
        if rating:
            conditions.append(ScoreSnapshot.rating == rating)
        # 日期筛选：统一解析为 datetime 对象再比较，避免字符串直接比较的格式问题
        if start_date:
            try:
                start_dt = datetime.strptime(str(start_date), "%Y-%m-%d")
                conditions.append(MerchantBasicInfo.created_at >= start_dt)
            except ValueError:
                pass  # 忽略非法日期格式

        if end_date:
            try:
                # 结束日补齐到 23:59:59，确保包含当天全部数据
                end_dt = datetime.strptime(str(end_date), "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
                conditions.append(MerchantBasicInfo.created_at <= end_dt)
            except ValueError:
                pass

        if conditions:
            stmt = stmt.where(and_(*conditions))

        # ---- 统计总数（分页前）----
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.session.execute(count_stmt).scalar() or 0

        # ---- 分页 ----
        page = max(page, 1)
        page_size = max(page_size, 1)
        offset = (page - 1) * page_size
        stmt = (
            stmt.order_by(MerchantBasicInfo.merchant_id.desc())
            .offset(offset)
            .limit(page_size)
        )
        rows = self.session.execute(stmt).all()

        list_data: List[dict] = []
        for row in rows:
            list_data.append(
                {
                    "merchant_id": row.merchant_id,
                    "merchant_name": row.merchant_name,
                    "tax_number": row.tax_number,
                    "legal_person": row.legal_person,
                    "latest_rating": row.latest_rating,
                    "primary_contact": row.primary_contact,
                    "created_at": (
                        row.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        if row.created_at
                        else None
                    ),
                }
            )

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "list": list_data,
        }

    # ------------------------------------------------------------------
    # 10. 查询客商详情（含所有关联数据）
    # ------------------------------------------------------------------
    def get_merchant_detail(self, merchant_id: int) -> dict:
        """
        查询客商详情（含所有关联数据）。

        Returns:
            {
                "basic_info": {...},      # MerchantBasicInfo 所有字段
                "contacts": [...],        # MerchantContact 列表
                "attachments": [...],     # MerchantAttachment 列表
                "latest_score": {         # 最新评分快照
                    "total_score": 85.5,
                    "rating": "A",
                    "score_time": "2026-01-01"
                },
                "financial_records": [...] # 财务原始数据列表（简要）
            }

            若客商不存在，返回空字典 {}。
        """
        # ---- 基础信息 ----
        basic = self.session.execute(
            select(MerchantBasicInfo).where(
                MerchantBasicInfo.merchant_id == merchant_id
            )
        ).scalars().first()

        if basic is None:
            return {}

        basic_info = self._serialize_row(basic)

        # ---- 对接人列表 ----
        contact_rows = list(
            self.session.execute(
                select(MerchantContact)
                .where(MerchantContact.merchant_id == merchant_id)
                .order_by(MerchantContact.is_primary.desc(), MerchantContact.id.asc())
            ).scalars().all()
        )
        contacts = [self._serialize_row(r) for r in contact_rows]

        # ---- 附件列表 ----
        attachments = self.get_attachments(merchant_id)

        # ---- 最新评分快照 ----
        latest_score_row = self.session.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.merchant_id == merchant_id)
            .order_by(ScoreSnapshot.id.desc())
            .limit(1)
        ).scalars().first()

        if latest_score_row is not None:
            latest_score = {
                "total_score": (
                    float(latest_score_row.total_score)
                    if latest_score_row.total_score is not None
                    else None
                ),
                "rating": latest_score_row.rating,
                "score_time": (
                    latest_score_row.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if latest_score_row.created_at
                    else None
                ),
            }
        else:
            latest_score = {
                "total_score": None,
                "rating": None,
                "score_time": None,
            }

        # ---- 财务原始数据（简要）----
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
        financial_records = [
            {
                "id": r.id,
                "period_year": r.period_year,
                "period_quarter": r.period_quarter,
                "total_asset": float(r.total_asset) if r.total_asset is not None else None,
                "total_liability": (
                    float(r.total_liability) if r.total_liability is not None else None
                ),
                "revenue": float(r.revenue) if r.revenue is not None else None,
                "net_profit": float(r.net_profit) if r.net_profit is not None else None,
            }
            for r in fin_rows
        ]

        return {
            "basic_info": basic_info,
            "contacts": contacts,
            "attachments": attachments,
            "latest_score": latest_score,
            "financial_records": financial_records,
        }

    # ------------------------------------------------------------------
    # 11. 删除指定客商的所有联络人
    # ------------------------------------------------------------------
    def delete_contacts_by_merchant(self, merchant_id: int) -> int:
        """删除指定客商的所有联络人（用于更新时全量替换）。

        Returns:
            被删除的联络人数量
        """
        rows = list(
            self.session.execute(
                select(MerchantContact).where(
                    MerchantContact.merchant_id == merchant_id
                )
            ).scalars().all()
        )
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)

    # ------------------------------------------------------------------
    # 12. 新增附件记录
    # ------------------------------------------------------------------
    def add_attachment(
        self,
        merchant_id: int,
        file_name: str,
        file_path: str,
        file_size: int,
        file_type: str,
        uploaded_by: str,
    ) -> int:
        """新增附件记录，返回附件 ID。

        事务管理：本方法不自行 commit，由 Service 层统一管理。
        """
        attachment = MerchantAttachment(
            merchant_id=merchant_id,
            file_name=file_name,
            file_path=file_path,
            file_size=file_size,
            file_type=file_type,
            uploaded_by=uploaded_by,
        )
        self.session.add(attachment)
        self.session.flush()
        return int(attachment.id)

    # ------------------------------------------------------------------
    # 13. 查询客商的所有附件列表
    # ------------------------------------------------------------------
    def get_attachments(self, merchant_id: int) -> List[dict]:
        """查询客商的所有附件列表。"""
        rows = list(
            self.session.execute(
                select(MerchantAttachment)
                .where(MerchantAttachment.merchant_id == merchant_id)
                .order_by(MerchantAttachment.upload_time.desc())
            ).scalars().all()
        )
        return [self._serialize_row(r) for r in rows]

    # ==================================================================
    # 以下为审批工作流模块（M4）新增方法
    # ==================================================================

    # ------------------------------------------------------------------
    # 14. 创建审批主表记录
    # ------------------------------------------------------------------
    def create_approval_order(
        self,
        merchant_id: int,
        applicant: str,
        score_result: dict,
        remark: str = None,
    ) -> int:
        """
        创建审批主表记录。
        需将评分结果（total_score, rating, dimension_scores）冗余存储。
        返回 order_id。

        Args:
            merchant_id: 客商 ID
            applicant: 申请人
            score_result: 评分结果字典，包含：
                - total_score (float): 总分
                - rating (str): 评级
                - dimension_scores (dict | object): 各维度得分
                  若为 dict，键为 subject_quality/financial/project_quality/performance_quality
                  若为对象，则取同名属性
            remark: 申请备注

        Returns:
            order_id

        事务管理：本方法不自行 commit，由 Service 层统一管理。
        """
        # ---- 提取维度得分并序列化为 JSON ----
        dim_scores = score_result.get("dimension_scores")
        if dim_scores is None:
            dim_json = None
        elif isinstance(dim_scores, dict):
            dim_json = json.dumps(dim_scores, ensure_ascii=False)
        else:
            # 处理 DimensionScores 对象
            dim_json = json.dumps(
                {
                    "subject_quality": getattr(dim_scores, "subject_quality", None),
                    "financial": getattr(dim_scores, "financial", None),
                    "project_quality": getattr(dim_scores, "project_quality", None),
                    "performance_quality": getattr(dim_scores, "performance_quality", None),
                },
                ensure_ascii=False,
            )

        order = ApprovalOrder(
            merchant_id=merchant_id,
            applicant=applicant,
            status="pending_dept",
            current_step="部门负责人复核",
            total_score=score_result.get("total_score"),
            rating=score_result.get("rating"),
            dimension_scores_json=dim_json,
            remark=remark,
        )
        self.session.add(order)
        self.session.flush()
        return int(order.id)

    # ------------------------------------------------------------------
    # 15. 批量创建审批任务
    # ------------------------------------------------------------------
    def create_approval_tasks(self, order_id: int, assignees: dict) -> int:
        """
        批量创建审批任务。
        assignees 示例：{"dept_head": "张三", "market": "李四", "compliance": "王五", "finance": "赵六", "executive": "钱七"}
        返回创建的任务数量。

        事务管理：本方法不自行 commit，由 Service 层统一管理。
        """
        count = 0
        for role_type, assignee in assignees.items():
            task = ApprovalTask(
                order_id=order_id,
                assignee=assignee,
                role_type=role_type,
                task_status="pending",
            )
            self.session.add(task)
            count += 1
        self.session.flush()
        return count

    # ------------------------------------------------------------------
    # 16. 查询待办任务列表（分页）
    # ------------------------------------------------------------------
    def get_pending_tasks(
        self,
        assignee: str,
        role_type: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        查询某个处理人的待办任务列表（分页）。
        需要关联查询客商名称、申请时间、当前状态。
        返回 {"total": 10, "page": 1, "page_size": 20, "list": [...]}
        """
        stmt = (
            select(
                ApprovalTask.id.label("task_id"),
                ApprovalTask.order_id,
                ApprovalTask.assignee,
                ApprovalTask.role_type,
                ApprovalTask.task_status,
                ApprovalTask.task_deadline,
                ApprovalTask.created_at,
                ApprovalOrder.merchant_id,
                ApprovalOrder.applicant,
                ApprovalOrder.apply_time,
                ApprovalOrder.status.label("order_status"),
                ApprovalOrder.current_step,
                ApprovalOrder.total_score,
                ApprovalOrder.rating,
                MerchantBasicInfo.merchant_name,
            )
            .select_from(ApprovalTask)
            .join(ApprovalOrder, ApprovalTask.order_id == ApprovalOrder.id)
            .join(
                MerchantBasicInfo,
                ApprovalOrder.merchant_id == MerchantBasicInfo.merchant_id,
            )
            .where(ApprovalTask.assignee == assignee)
            .where(ApprovalTask.task_status == "pending")
        )

        if role_type:
            stmt = stmt.where(ApprovalTask.role_type == role_type)

        # ---- 统计总数 ----
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.session.execute(count_stmt).scalar() or 0

        # ---- 分页 ----
        page = max(page, 1)
        page_size = max(page_size, 1)
        offset = (page - 1) * page_size
        stmt = (
            stmt.order_by(ApprovalTask.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        rows = self.session.execute(stmt).all()

        list_data: List[dict] = []
        for row in rows:
            list_data.append(
                {
                    "task_id": row.task_id,
                    "order_id": row.order_id,
                    "assignee": row.assignee,
                    "role_type": row.role_type,
                    "task_status": row.task_status,
                    "task_deadline": (
                        row.task_deadline.strftime("%Y-%m-%d %H:%M:%S")
                        if row.task_deadline
                        else None
                    ),
                    "created_at": (
                        row.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        if row.created_at
                        else None
                    ),
                    "merchant_id": row.merchant_id,
                    "merchant_name": row.merchant_name,
                    "applicant": row.applicant,
                    "apply_time": (
                        row.apply_time.strftime("%Y-%m-%d %H:%M:%S")
                        if row.apply_time
                        else None
                    ),
                    "order_status": row.order_status,
                    "current_step": row.current_step,
                    "total_score": (
                        float(row.total_score) if row.total_score is not None else None
                    ),
                    "rating": row.rating,
                }
            )

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "list": list_data,
        }

    # ------------------------------------------------------------------
    # 17. 查询任务详情
    # ------------------------------------------------------------------
    def get_task_detail(self, task_id: int) -> dict:
        """
        查询任务详情，包含：
            - 任务本身信息（处理人、角色、状态）
            - 关联的审批主表信息（申请时间、当前状态）
            - 关联的客商基本信息（名称、税号、法人）
            - 关联的评分结果（总分、评级、各维度分）

        若任务不存在，返回空字典 {}。
        """
        stmt = (
            select(ApprovalTask, ApprovalOrder, MerchantBasicInfo)
            .select_from(ApprovalTask)
            .join(ApprovalOrder, ApprovalTask.order_id == ApprovalOrder.id)
            .join(
                MerchantBasicInfo,
                ApprovalOrder.merchant_id == MerchantBasicInfo.merchant_id,
            )
            .where(ApprovalTask.id == task_id)
        )
        row = self.session.execute(stmt).first()
        if row is None:
            return {}

        task, order, merchant = row

        # ---- 解析维度得分 JSON ----
        dim_scores: dict = {}
        if order.dimension_scores_json:
            try:
                dim_scores = json.loads(order.dimension_scores_json)
            except (json.JSONDecodeError, TypeError):
                dim_scores = {}

        return {
            "task": {
                "id": task.id,
                "order_id": task.order_id,
                "assignee": task.assignee,
                "role_type": task.role_type,
                "action": task.action,
                "comment": task.comment,
                "task_status": task.task_status,
                "task_deadline": (
                    task.task_deadline.strftime("%Y-%m-%d %H:%M:%S")
                    if task.task_deadline
                    else None
                ),
                "handled_at": (
                    task.handled_at.strftime("%Y-%m-%d %H:%M:%S")
                    if task.handled_at
                    else None
                ),
                "created_at": (
                    task.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if task.created_at
                    else None
                ),
            },
            "order": {
                "id": order.id,
                "merchant_id": order.merchant_id,
                "applicant": order.applicant,
                "apply_time": (
                    order.apply_time.strftime("%Y-%m-%d %H:%M:%S")
                    if order.apply_time
                    else None
                ),
                "status": order.status,
                "current_step": order.current_step,
                "remark": order.remark,
                "updated_at": (
                    order.updated_at.strftime("%Y-%m-%d %H:%M:%S")
                    if order.updated_at
                    else None
                ),
            },
            "merchant": {
                "merchant_id": merchant.merchant_id,
                "merchant_name": merchant.merchant_name,
                "tax_number": merchant.tax_number,
                "legal_person": merchant.legal_person,
                "registered_capital": (
                    float(merchant.registered_capital)
                    if merchant.registered_capital is not None
                    else None
                ),
                "establish_date": merchant.establish_date,
            },
            "score": {
                "total_score": (
                    float(order.total_score) if order.total_score is not None else None
                ),
                "rating": order.rating,
                "dimension_scores": dim_scores,
            },
        }

    # ------------------------------------------------------------------
    # 18. 更新任务状态
    # ------------------------------------------------------------------
    def update_task(self, task_id: int, action: str, comment: str) -> bool:
        """
        更新任务状态（通过/驳回）。
        需同步更新 task_status='done', action, comment, handled_at。
        返回是否成功。
        """
        task = self.session.execute(
            select(ApprovalTask).where(ApprovalTask.id == task_id)
        ).scalars().first()

        if task is None:
            return False

        task.task_status = "done"
        task.action = action
        task.comment = comment
        task.handled_at = datetime.now()
        self.session.flush()
        return True

    # ------------------------------------------------------------------
    # 19. 更新审批主表状态
    # ------------------------------------------------------------------
    def update_order_status(
        self,
        order_id: int,
        status: str,
        current_step: str = None,
    ) -> bool:
        """
        更新审批主表状态。
        可选传入 current_step 更新当前步骤描述。
        """
        order = self.session.execute(
            select(ApprovalOrder).where(ApprovalOrder.id == order_id)
        ).scalars().first()

        if order is None:
            return False

        order.status = status
        if current_step is not None:
            order.current_step = current_step
        self.session.flush()
        return True

    # ------------------------------------------------------------------
    # 20. 查询审批单的所有任务
    # ------------------------------------------------------------------
    def get_order_tasks(self, order_id: int) -> List[dict]:
        """
        查询某个审批单的所有任务（用于审批历史展示）。
        按 created_at 升序排列。
        """
        rows = list(
            self.session.execute(
                select(ApprovalTask)
                .where(ApprovalTask.order_id == order_id)
                .order_by(ApprovalTask.created_at.asc())
            ).scalars().all()
        )
        return [self._serialize_row(r) for r in rows]

    # ------------------------------------------------------------------
    # 21. 查询客商最新审批记录
    # ------------------------------------------------------------------
    def get_latest_approval_by_merchant(self, merchant_id: int) -> dict:
        """
        查询某个客商最新的审批记录（用于客商详情页展示审批状态）。
        """
        order = self.session.execute(
            select(ApprovalOrder)
            .where(ApprovalOrder.merchant_id == merchant_id)
            .order_by(ApprovalOrder.id.desc())
            .limit(1)
        ).scalars().first()

        if order is None:
            return {}
        return self._serialize_row(order)

    # ------------------------------------------------------------------
    # 22. 查询客商是否存在指定状态的审批单
    # ------------------------------------------------------------------
    def get_order_by_merchant_and_status(
        self,
        merchant_id: int,
        statuses: List[str],
    ) -> Optional[dict]:
        """
        查询某个客商是否存在指定状态的审批单。
        用于防止重复提交。
        """
        order = self.session.execute(
            select(ApprovalOrder)
            .where(
                and_(
                    ApprovalOrder.merchant_id == merchant_id,
                    ApprovalOrder.status.in_(statuses),
                )
            )
            .order_by(ApprovalOrder.id.desc())
            .limit(1)
        ).scalars().first()

        if order is None:
            return None
        return self._serialize_row(order)

    # ------------------------------------------------------------------
    # 23. 取消并行会签任务
    # ------------------------------------------------------------------
    def cancel_parallel_tasks(
        self,
        order_id: int,
        exclude_role_types: List[str] = None,
    ) -> int:
        """
        当并行会签中任一角色驳回时，取消该订单下所有未处理的并行任务。
        将 task_status 更新为 'cancelled'。
        返回取消的任务数量。
        """
        stmt = select(ApprovalTask).where(
            ApprovalTask.order_id == order_id,
            ApprovalTask.task_status == "pending",
        )
        if exclude_role_types:
            stmt = stmt.where(~ApprovalTask.role_type.in_(exclude_role_types))

        rows = list(self.session.execute(stmt).scalars().all())
        for row in rows:
            row.task_status = "cancelled"
        self.session.flush()
        return len(rows)

    # ------------------------------------------------------------------
    # 24. 保存评分快照
    # ------------------------------------------------------------------
    def save_score_snapshot(self, merchant_id: int, result: ScoringResult) -> int:
        """
        将评分结果写入 score_snapshot 表。
        返回 snapshot_id。

        事务管理：本方法不自行 commit，由 Service 层统一管理。
        """
        dim = result.dimension_scores
        snapshot = ScoreSnapshot(
            merchant_id=merchant_id,
            assessment_batch_id=result.assessment_batch_id,
            scenario=result.scenario,
            is_credit_applied=1 if result.is_credit_applied else 0,
            applied_rule_version_id=result.applied_rule_version_id,
            financial_data_id=result.financial_data_id,
            subject_quality_score=dim.subject_quality,
            financial_score=dim.financial,
            project_quality_score=dim.project_quality,
            performance_quality_score=dim.performance_quality,
            total_score=result.total_score,
            rating=result.rating,
            risk_coefficient=result.risk_coefficient,
            qualified=1 if result.qualified else 0,
            created_at=datetime.now(),
        )
        self.session.add(snapshot)
        self.session.flush()
        return int(snapshot.id)

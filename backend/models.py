"""
SQLAlchemy ORM 模型定义模块。

映射到已由 DBA 创建的 MySQL 表，仅定义模型类，不执行建表操作。
所有字段名与数据库表定义严格保持一致。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

# SQLite 不支持 BigInteger 自增，需降级为 Integer
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

Base = declarative_base()


class MerchantBasicInfo(Base):
    """商户基础信息表模型，映射 merchant_basic_info。

    注意：主键为 merchant_id（BIGINT 自增），无单独 id 列。
    """

    __tablename__ = "merchant_basic_info"

    # ---- 既有字段（保持不变）----
    merchant_id = Column(BigIntPK, primary_key=True, autoincrement=True, comment="商户ID(主键)")
    merchant_name = Column(String(128), nullable=True, comment="商户名称")
    registered_capital = Column(Numeric(18, 2), nullable=True, comment="注册资金")
    paid_in_capital = Column(Numeric(18, 2), nullable=True, comment="实缴资本")
    establish_date = Column(String(32), nullable=True, comment="成立日期")
    tax_completion_status = Column(Integer, nullable=True, comment="纳税完成度(0~100)")
    lawsuit_total = Column(Integer, nullable=True, default=0, comment="涉诉总数")
    defendant_count = Column(Integer, nullable=True, default=0, comment="作为被告次数")
    executed_count = Column(Integer, nullable=True, default=0, comment="被执行次数")
    executed_amount = Column(Numeric(18, 2), nullable=True, default=0, comment="被执行金额")
    avg_bank_flow = Column(Numeric(18, 2), nullable=True, default=0, comment="平均银行流水")
    avg_approved_fund = Column(Numeric(18, 2), nullable=True, default=0, comment="平均授信额度")
    # ---- 新增业务字段（客商录入模块）----
    tax_number = Column(String(18), nullable=False, unique=True, index=True, comment="统一社会信用代码")
    legal_person = Column(String(64), nullable=False, comment="法定代表人")
    registered_address = Column(String(256), nullable=True, comment="注册地址")
    actual_controller = Column(String(64), nullable=True, comment="实际控制人")
    created_at = Column(DateTime, default=datetime.now, comment="建档时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class FinancialRawData(Base):
    """财务原始数据表模型，映射 financial_raw_data。"""

    __tablename__ = "financial_raw_data"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    merchant_id = Column(BigInteger, nullable=False, index=True, comment="商户ID")
    period_year = Column(Integer, nullable=False, comment="会计年度")
    period_quarter = Column(Integer, nullable=True, comment="季度(1~4)")
    total_asset = Column(Numeric(18, 2), nullable=True, comment="总资产")
    total_liability = Column(Numeric(18, 2), nullable=True, comment="总负债")
    current_asset = Column(Numeric(18, 2), nullable=True, comment="流动资产")
    current_liability = Column(Numeric(18, 2), nullable=True, comment="流动负债")
    revenue = Column(Numeric(18, 2), nullable=True, comment="营业收入")
    net_profit = Column(Numeric(18, 2), nullable=True, comment="净利润")
    inventory = Column(Numeric(18, 2), nullable=True, comment="存货")
    prepay = Column(Numeric(18, 2), nullable=True, comment="预付款")
    operating_cash_flow = Column(Numeric(18, 2), nullable=True, comment="经营现金流")
    net_asset = Column(Numeric(18, 2), nullable=True, comment="净资产")


class RuleVersion(Base):
    """规则版本表模型，映射 rule_version。"""

    __tablename__ = "rule_version"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    version_code = Column(String(64), nullable=False, unique=True, comment="版本编码")
    version_name = Column(String(128), nullable=True, comment="版本名称")
    is_active = Column(Integer, nullable=False, default=0, comment="是否生效(1=生效)")
    effective_date = Column(DateTime, nullable=True, comment="生效日期")
    description = Column(String(512), nullable=True, comment="版本描述")


class RuleWeightConfig(Base):
    """规则权重配置表模型，映射 rule_weight_config。

    注意：使用四个独立权重列，而非 dimension_code + weight 的通用结构。
    严禁出现 dimension_code 字段。
    """

    __tablename__ = "rule_weight_config"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    rule_version_id = Column(Integer, nullable=False, index=True, comment="规则版本ID")
    subject_weight = Column(Numeric(8, 4), nullable=False, comment="主体资质维度权重")
    financial_weight = Column(Numeric(8, 4), nullable=False, comment="财务维度权重")
    project_weight = Column(Numeric(8, 4), nullable=False, comment="立项质量维度权重")
    performance_weight = Column(Numeric(8, 4), nullable=False, comment="履约质量维度权重")


class RuleIndicatorDetail(Base):
    """规则指标明细表模型，映射 rule_indicator_detail。

    存储各指标的阶梯分段配置，供评分引擎进行阶梯计分。
    同一 indicator_code 可有多条记录（多个分段），左闭右开区间。
    """

    __tablename__ = "rule_indicator_detail"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    rule_version_id = Column(Integer, nullable=False, index=True, comment="规则版本ID")
    dimension_code = Column(String(64), nullable=False, comment="所属维度编码")
    indicator_code = Column(String(64), nullable=False, comment="指标编码")
    indicator_name = Column(String(128), nullable=True, comment="指标名称")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序序号")
    lower_limit = Column(Numeric(18, 4), nullable=False, default=0, comment="分段下限(含)")
    upper_limit = Column(Numeric(18, 4), nullable=True, comment="分段上限(不含)，NULL表示正无穷")
    unit_value = Column(Numeric(18, 4), nullable=False, default=1, comment="单位步长")
    unit_score = Column(Numeric(18, 4), nullable=False, default=0, comment="单位步长得分")
    max_score = Column(Numeric(8, 4), nullable=False, default=100, comment="该指标满分上限")
    weight_in_parent = Column(Numeric(8, 4), nullable=False, default=1, comment="指标在维度内权重")
    scoring_rule = Column(Text, nullable=True, comment="评分规则描述(JSON)")


class ScoreSnapshot(Base):
    """评分快照表模型，映射 score_snapshot。"""

    __tablename__ = "score_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    merchant_id = Column(BigInteger, nullable=False, index=True, comment="商户ID")
    assessment_batch_id = Column(String(64), nullable=False, comment="评估批次ID")
    scenario = Column(String(64), nullable=False, comment="场景编码")
    is_credit_applied = Column(Integer, nullable=False, default=0, comment="是否申请授信(1=是)")
    applied_rule_version_id = Column(Integer, nullable=False, comment="使用的规则版本ID")
    financial_data_id = Column(Integer, nullable=True, comment="关联财务数据ID")
    subject_quality_score = Column(Numeric(8, 4), nullable=True, comment="主体资质得分")
    financial_score = Column(Numeric(8, 4), nullable=True, comment="财务维度得分")
    project_quality_score = Column(Numeric(8, 4), nullable=True, comment="立项质量得分")
    performance_quality_score = Column(Numeric(8, 4), nullable=True, comment="履约质量得分")
    total_score = Column(Numeric(8, 4), nullable=False, comment="总分")
    rating = Column(String(16), nullable=False, comment="评级")
    risk_coefficient = Column(Numeric(8, 4), nullable=True, comment="风险系数")
    qualified = Column(Integer, nullable=False, default=0, comment="是否合格(1=合格)")
    created_at = Column(DateTime, nullable=True, comment="创建时间")


# ======================================================================
# 以下为客商录入模块新增模型（任务 A）
# ======================================================================


class MerchantContact(Base):
    """客商对接人信息表模型，映射 merchant_contact。

    约束：每个 merchant_id 只能有 1 条 is_primary=1 的记录（由应用层保证）。
    注意：merchant_id 类型与 merchant_basic_info.merchant_id 保持一致（BigInteger），
          以确保外键关联生效。
    """

    __tablename__ = "merchant_contact"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    merchant_id = Column(
        BigInteger,
        ForeignKey("merchant_basic_info.merchant_id"),
        nullable=False,
        index=True,
        comment="商户ID(外键)",
    )
    name = Column(String(64), nullable=False, comment="姓名")
    position = Column(String(64), nullable=True, comment="职务")
    phone = Column(String(32), nullable=True, comment="联系方式")
    email = Column(String(128), nullable=True, comment="邮箱")
    business_role = Column(String(32), nullable=True, comment="业务职责，如：业务对接人、财务对接人")
    is_primary = Column(Integer, nullable=False, default=0, comment="是否主联络人：1=是，0=否")
    remark = Column(String(256), nullable=True, comment="备注")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class MerchantAttachment(Base):
    """客商附件表模型，映射 merchant_attachment。"""

    __tablename__ = "merchant_attachment"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    merchant_id = Column(
        BigInteger,
        ForeignKey("merchant_basic_info.merchant_id"),
        nullable=False,
        index=True,
        comment="商户ID(外键)",
    )
    file_name = Column(String(256), nullable=False, comment="原始文件名")
    file_path = Column(String(512), nullable=False, comment="存储路径（相对路径或完整URL）")
    file_size = Column(Integer, nullable=True, comment="文件大小（字节）")
    file_type = Column(String(64), nullable=True, comment="文件类型：营业执照/财报/银行流水/其他")
    uploaded_by = Column(String(64), nullable=True, comment="上传人")
    upload_time = Column(DateTime, default=datetime.now, comment="上传时间")
    remark = Column(String(256), nullable=True, comment="备注")


# ======================================================================
# 以下为审批工作流模块新增模型（M4）
# ======================================================================


class ApprovalOrder(Base):
    """审批主表模型，映射 approval_order。

    存储审批单的核心信息，包括申请人、状态、冗余的评分结果等。
    状态流转：pending_dept → parallel_signing → final_signing → approved/rejected
    """

    __tablename__ = "approval_order"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    merchant_id = Column(
        BigInteger,
        ForeignKey("merchant_basic_info.merchant_id"),
        nullable=False,
        index=True,
        comment="客商ID(外键)",
    )
    applicant = Column(String(64), nullable=False, comment="申请人")
    apply_time = Column(DateTime, default=datetime.now, comment="申请时间")
    status = Column(
        String(32),
        nullable=False,
        default="pending_dept",
        comment="pending_dept/parallel_signing/final_signing/approved/rejected",
    )
    current_step = Column(String(32), nullable=True, comment="当前步骤描述")
    total_score = Column(Numeric(8, 4), nullable=True, comment="评分总分（冗余）")
    rating = Column(String(16), nullable=True, comment="评级（冗余）")
    dimension_scores_json = Column(Text, nullable=True, comment="各维度得分JSON")
    remark = Column(String(512), nullable=True, comment="申请备注")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class ApprovalTask(Base):
    """审批任务表模型，映射 approval_task。

    每个审批单在不同阶段会产生多个任务，分配给不同角色处理。
    角色类型：dept_head / market / compliance / finance / executive
    任务状态：pending / done / cancelled
    """

    __tablename__ = "approval_task"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    order_id = Column(
        Integer,
        ForeignKey("approval_order.id"),
        nullable=False,
        index=True,
        comment="审批单ID(外键)",
    )
    assignee = Column(String(64), nullable=False, comment="处理人")
    role_type = Column(
        String(32),
        nullable=False,
        comment="dept_head/market/compliance/finance/executive",
    )
    action = Column(String(16), nullable=True, comment="approve/reject")
    comment = Column(String(512), nullable=True, comment="处理意见")
    task_status = Column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending/done/cancelled",
    )
    task_deadline = Column(DateTime, nullable=True, comment="截止时间")
    handled_at = Column(DateTime, nullable=True, comment="处理时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

"""
种子数据初始化模块。

在应用首次启动时创建：
    1. 评分规则版本（rule_version）
    2. 维度权重配置（rule_weight_config）
    3. 指标阶梯明细（rule_indicator_detail）—— 覆盖主体资质/财务/立项质量三个维度
    4. 演示客商 + 财务数据

指标分段设计原则：
    - 「越高越好」指标：单段 [0, ∞)，unit_value=典型值/100，使典型值得满分
    - 「越低越好」指标：单段 [-1, ∞)，利用负偏移使低值得满分
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from database import SessionLocal, init_db
from models import (
    FinancialRawData,
    MerchantBasicInfo,
    MerchantContact,
    RuleIndicatorDetail,
    RuleVersion,
    RuleWeightConfig,
)


def _add_indicator(
    session: Session,
    rule_version_id: int,
    dimension_code: str,
    indicator_code: str,
    indicator_name: str,
    sort_order: int,
    lower_limit: float,
    upper_limit,
    unit_value: float,
    unit_score: float,
    max_score: float,
    weight_in_parent: float,
):
    """便捷添加一条指标明细。"""
    session.add(RuleIndicatorDetail(
        rule_version_id=rule_version_id,
        dimension_code=dimension_code,
        indicator_code=indicator_code,
        indicator_name=indicator_name,
        sort_order=sort_order,
        lower_limit=Decimal(str(lower_limit)),
        upper_limit=Decimal(str(upper_limit)) if upper_limit is not None else None,
        unit_value=Decimal(str(unit_value)),
        unit_score=Decimal(str(unit_score)),
        max_score=Decimal(str(max_score)),
        weight_in_parent=Decimal(str(weight_in_parent)),
    ))


def seed_rules(session: Session) -> int:
    """创建评分规则版本、权重配置、指标明细。返回 rule_version_id。"""
    # 若已有规则版本则跳过
    existing = session.query(RuleVersion).filter(RuleVersion.is_active == 1).first()
    if existing:
        return existing.id

    # ---- 1. 规则版本 ----
    version = RuleVersion(
        version_code="V2026_01",
        version_name="2026年客商准入评分规则",
        is_active=1,
        description="主体资质25% + 财务30% + 立项质量20% + 履约质量25%",
    )
    session.add(version)
    session.flush()
    rv_id = version.id

    # ---- 2. 维度权重 ----
    session.add(RuleWeightConfig(
        rule_version_id=rv_id,
        subject_weight=Decimal("0.25"),
        financial_weight=Decimal("0.30"),
        project_weight=Decimal("0.20"),
        performance_weight=Decimal("0.25"),
    ))

    # ---- 3. 指标明细 ----
    # === 主体资质（8 指标，各权重 1/8=0.125）===
    w_sub = 0.125
    _add_indicator(session, rv_id, "subject_quality", "registered_capital", "注册资金", 1,
                   0, None, 50000, 1, 100, w_sub)        # 典型500万→100分
    _add_indicator(session, rv_id, "subject_quality", "paid_in_ratio", "实缴比例", 2,
                   0, None, 0.01, 1, 100, w_sub)          # 0.8→80分
    _add_indicator(session, rv_id, "subject_quality", "establish_years", "成立年限", 3,
                   0, None, 0.1, 1, 100, w_sub)           # 10年→100分
    _add_indicator(session, rv_id, "subject_quality", "tax_completion", "纳税完成度", 4,
                   0, None, 1, 1, 100, w_sub)             # 80→80分
    _add_indicator(session, rv_id, "subject_quality", "defendant_ratio", "被告占比", 5,
                   -1, None, 0.01, 1, 100, w_sub)         # 0→100分（越低越好）
    _add_indicator(session, rv_id, "subject_quality", "executed_ratio", "被执行占比", 6,
                   -1, None, 0.01, 1, 100, w_sub)         # 0→100分（越低越好）
    _add_indicator(session, rv_id, "subject_quality", "executed_amount_ratio", "被执行金额比", 7,
                   -1, None, 1, 1, 100, w_sub)            # 0→100分（越低越好）
    _add_indicator(session, rv_id, "subject_quality", "bank_flow_ratio", "流水授信比", 8,
                   0, None, 0.01, 1, 100, w_sub)          # 0.8→80分

    # === 财务维度（7 指标，各权重 1/7≈0.1429）===
    w_fin = round(1 / 7, 4)
    _add_indicator(session, rv_id, "financial", "asset_liability_ratio", "资产负债率", 1,
                   -1, None, 0.01, 1, 100, w_fin)         # 0.4→100分（越低越好，负偏移）
    _add_indicator(session, rv_id, "financial", "quick_ratio", "速动比率", 2,
                   0, None, 0.01, 1, 100, w_fin)          # 1.3→100分
    _add_indicator(session, rv_id, "financial", "cash_flow_ratio", "现金流量比率", 3,
                   0, None, 0.005, 1, 100, w_fin)         # 0.6→100分
    _add_indicator(session, rv_id, "financial", "net_profit_margin", "销售净利率", 4,
                   0, None, 0.001, 1, 100, w_fin)         # 0.133→100分
    _add_indicator(session, rv_id, "financial", "roe", "净资产收益率", 5,
                   0, None, 0.001, 1, 100, w_fin)         # 0.167→100分
    _add_indicator(session, rv_id, "financial", "asset_turnover_ratio", "资产周转率", 6,
                   0, None, 0.005, 1, 100, w_fin)         # 0.75→100分
    _add_indicator(session, rv_id, "financial", "capital_operation", "资本运作能力", 7,
                   0, None, 0.005, 1, 100, w_fin)         # 0.6→100分

    # === 立项质量（6 指标，各权重 1/6≈0.1667）===
    w_proj = round(1 / 6, 4)
    _add_indicator(session, rv_id, "project_quality", "avg_gross_margin", "平均毛利率", 1,
                   0, None, 0.001, 1, 100, w_proj)        # 0.30→100分
    _add_indicator(session, rv_id, "project_quality", "price_volatility", "价格波动率", 2,
                   -1, None, 0.01, 1, 100, w_proj)        # 0.15→100分（越低越好）
    _add_indicator(session, rv_id, "project_quality", "contract_terms_score", "合同条款评分", 3,
                   0, None, 0.5, 1, 100, w_proj)          # 80→100分
    _add_indicator(session, rv_id, "project_quality", "legal_opinion_score", "法律意见采纳率", 4,
                   0, None, 0.01, 1, 100, w_proj)         # 0.85→85分
    _add_indicator(session, rv_id, "project_quality", "warehouse_control_score", "仓库管控评分", 5,
                   0, None, 0.5, 1, 100, w_proj)          # 75→100分
    _add_indicator(session, rv_id, "project_quality", "logistics_track_score", "物流追踪评分", 6,
                   0, None, 0.5, 1, 100, w_proj)          # 80→100分

    # === 履约质量（6 指标，各权重 1/6≈0.1667）===
    w_perf = round(1 / 6, 4)
    _add_indicator(session, rv_id, "performance_quality", "annual_gross_margin", "年度毛利率", 1,
                   0, None, 0.001, 1, 100, w_perf)
    _add_indicator(session, rv_id, "performance_quality", "annual_revenue", "年度营收", 2,
                   0, None, 100000, 1, 100, w_perf)
    _add_indicator(session, rv_id, "performance_quality", "immediate_overdue_rate", "即时逾期率", 3,
                   -1, None, 0.01, 1, 100, w_perf)        # 越低越好
    _add_indicator(session, rv_id, "performance_quality", "overdue_60days_rate", "60天逾期率", 4,
                   -1, None, 0.01, 1, 100, w_perf)        # 越低越好
    _add_indicator(session, rv_id, "performance_quality", "recovery_rate", "回收率", 5,
                   0, None, 0.01, 1, 100, w_perf)
    _add_indicator(session, rv_id, "performance_quality", "tax_declaration_consistency", "纳税申报一致性", 6,
                   -1, None, 1, 1, 100, w_perf)           # 越低越好

    session.commit()
    return rv_id


def seed_demo_merchants(session: Session):
    """创建演示客商及财务数据。"""
    if session.query(MerchantBasicInfo).count() > 0:
        return

    # ---- 客商 1：优质客商 ----
    m1 = MerchantBasicInfo(
        merchant_name="深圳市宏远科技有限公司",
        tax_number="91440300700123456X",
        legal_person="张伟",
        registered_address="深圳市南山区科技园南区",
        actual_controller="张伟",
        registered_capital=Decimal("5000000"),
        paid_in_capital=Decimal("4000000"),
        establish_date="2015-06-01",
        tax_completion_status=80,
        lawsuit_total=2,
        defendant_count=0,
        executed_count=0,
        executed_amount=Decimal("0"),
        avg_bank_flow=Decimal("8000000"),
        avg_approved_fund=Decimal("10000000"),
    )
    session.add(m1)
    session.flush()
    session.add(MerchantContact(
        merchant_id=m1.merchant_id, name="李明", position="采购总监",
        phone="13800138001", email="liming@hongyuan.com",
        business_role="业务对接人", is_primary=1,
    ))
    session.add(MerchantContact(
        merchant_id=m1.merchant_id, name="王芳", position="财务经理",
        phone="13800138002", email="wangfang@hongyuan.com",
        business_role="财务对接人", is_primary=0,
    ))
    # 财务数据
    session.add(FinancialRawData(
        merchant_id=m1.merchant_id, period_year=2024, period_quarter=4,
        total_asset=Decimal("20000000"), total_liability=Decimal("8000000"),
        current_asset=Decimal("10000000"), current_liability=Decimal("5000000"),
        revenue=Decimal("15000000"), net_profit=Decimal("2000000"),
        inventory=Decimal("3000000"), prepay=Decimal("500000"),
        operating_cash_flow=Decimal("3000000"), net_asset=Decimal("12000000"),
    ))
    session.add(FinancialRawData(
        merchant_id=m1.merchant_id, period_year=2023, period_quarter=4,
        total_asset=Decimal("16000000"), total_liability=Decimal("7000000"),
        current_asset=Decimal("8000000"), current_liability=Decimal("4000000"),
        revenue=Decimal("12000000"), net_profit=Decimal("1500000"),
        inventory=Decimal("2500000"), prepay=Decimal("400000"),
        operating_cash_flow=Decimal("2500000"), net_asset=Decimal("9000000"),
    ))

    # ---- 客商 2：一般客商 ----
    m2 = MerchantBasicInfo(
        merchant_name="杭州盛达贸易有限公司",
        tax_number="91330100500123457Y",
        legal_person="陈强",
        registered_address="杭州市余杭区五常街道",
        actual_controller="陈强",
        registered_capital=Decimal("1000000"),
        paid_in_capital=Decimal("500000"),
        establish_date="2020-03-15",
        tax_completion_status=60,
        lawsuit_total=5,
        defendant_count=2,
        executed_count=1,
        executed_amount=Decimal("200000"),
        avg_bank_flow=Decimal("3000000"),
        avg_approved_fund=Decimal("5000000"),
    )
    session.add(m2)
    session.flush()
    session.add(MerchantContact(
        merchant_id=m2.merchant_id, name="刘洋", position="总经理",
        phone="13900139001", email="liuyang@shengda.com",
        business_role="业务对接人", is_primary=1,
    ))
    session.add(FinancialRawData(
        merchant_id=m2.merchant_id, period_year=2024, period_quarter=4,
        total_asset=Decimal("5000000"), total_liability=Decimal("3500000"),
        current_asset=Decimal("2000000"), current_liability=Decimal("1800000"),
        revenue=Decimal("8000000"), net_profit=Decimal("400000"),
        inventory=Decimal("1200000"), prepay=Decimal("200000"),
        operating_cash_flow=Decimal("600000"), net_asset=Decimal("1500000"),
    ))

    # ---- 客商 3：新客商（无财务数据）----
    m3 = MerchantBasicInfo(
        merchant_name="北京创新未来科技股份有限公司",
        tax_number="91110108800123458Z",
        legal_person="赵敏",
        registered_address="北京市海淀区中关村软件园",
        actual_controller="赵敏",
        registered_capital=Decimal("10000000"),
        paid_in_capital=Decimal("10000000"),
        establish_date="2022-09-01",
        tax_completion_status=90,
        lawsuit_total=0,
        defendant_count=0,
        executed_count=0,
        executed_amount=Decimal("0"),
        avg_bank_flow=Decimal("5000000"),
        avg_approved_fund=Decimal("8000000"),
    )
    session.add(m3)
    session.flush()
    session.add(MerchantContact(
        merchant_id=m3.merchant_id, name="孙静", position="商务总监",
        phone="13700137001", email="sunjing@chuangxin.com",
        business_role="业务对接人", is_primary=1,
    ))

    session.commit()


def run_seed():
    """执行完整的数据库初始化与种子数据创建。"""
    init_db()
    session = SessionLocal()
    try:
        seed_rules(session)
        seed_demo_merchants(session)
    finally:
        session.close()

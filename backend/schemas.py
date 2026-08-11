"""
数据模型定义模块。

使用 Pydantic BaseModel 定义风控评分系统所需的所有数据结构，
包括原始财务数据、项目质量数据、履约数据、商户画像、评分请求及评分结果等。
"""

from typing import Optional

from pydantic import BaseModel


class RawFinancialData(BaseModel):
    """原始财务数据。"""

    period_year: int
    period_quarter: Optional[int] = None
    total_asset: float
    total_liability: float
    current_asset: float
    current_liability: float
    revenue: float
    net_profit: float
    inventory: float
    prepay: float
    operating_cash_flow: float
    net_asset: float


class ProjectQualityData(BaseModel):
    """项目质量数据。"""

    avg_gross_margin: float
    price_volatility: float
    contract_terms_score: float
    legal_opinion_adoption: float
    warehouse_control_score: float
    logistics_track_score: float


class PerformanceData(BaseModel):
    """履约数据。"""

    annual_gross_margin: float
    annual_revenue: float
    total_project_count: int
    total_batch_count: int
    immediate_overdue_project_count: int
    overdue_60days_batch_count: int
    historical_overdue_amount: float
    recovered_amount: float
    tax_declaration_diff: float


class MerchantProfile(BaseModel):
    """商户画像。"""

    merchant_id: int
    registered_capital: float
    paid_in_capital: float
    establish_date: str
    tax_completion_status: int
    lawsuit_total: int
    defendant_count: int
    executed_count: int
    executed_amount: float
    avg_bank_flow: float
    avg_approved_fund: float


class ScoringRequest(BaseModel):
    """评分请求。"""

    merchant_id: int
    is_credit_applied: bool
    raw_financial_data: Optional[RawFinancialData] = None
    project_quality_data: Optional[ProjectQualityData] = None
    performance_data: Optional[PerformanceData] = None


class DimensionScores(BaseModel):
    """各维度得分。"""

    subject_quality: float
    financial: float
    project_quality: float
    performance_quality: float


class ScoringResult(BaseModel):
    """评分结果。"""

    merchant_id: int
    assessment_batch_id: str
    scenario: str
    is_credit_applied: bool
    applied_rule_version_id: int
    financial_data_id: Optional[int] = None
    dimension_scores: DimensionScores
    total_score: float
    rating: str
    risk_coefficient: float
    qualified: bool

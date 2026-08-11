"""
指标值解析模块。

IndicatorValueResolver 根据指标编码从 ResolutionContext 中
精确提取对应的数值，是评分引擎的业务核心。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from schemas import MerchantProfile, PerformanceData, ProjectQualityData


@dataclass
class ResolutionContext:
    """评分解析上下文，承载各维度原始数据。"""

    ratios: Optional[dict] = None
    project_quality: Optional[ProjectQualityData] = None
    performance: Optional[PerformanceData] = None
    merchant_profile: Optional[MerchantProfile] = None


class IndicatorValueResolver:
    """
    指标值解析器。

    根据 indicator_code 从 ResolutionContext 中提取对应的 float 值。
    - 若对应数据源为 None 或字段缺失，返回 None。
    - 涉及除法且分母为 0 时返回 0.0。
    """

    # ------------------------------------------------------------------
    # 主体资质（subject_quality）
    # ------------------------------------------------------------------
    def _resolve_subject_quality(
        self, indicator_code: str, profile: MerchantProfile
    ) -> Optional[float]:
        if indicator_code == "registered_capital":
            return float(profile.registered_capital)

        if indicator_code == "paid_in_ratio":
            if profile.registered_capital == 0:
                return 0.0
            return float(profile.paid_in_capital) / float(profile.registered_capital)

        if indicator_code == "establish_years":
            year = self._extract_year(profile.establish_date)
            if year is None:
                return None
            return float(datetime.now().year - year)

        if indicator_code == "tax_completion":
            return float(profile.tax_completion_status)

        if indicator_code == "defendant_ratio":
            return float(profile.defendant_count) / max(float(profile.lawsuit_total), 1.0)

        if indicator_code == "executed_ratio":
            return float(profile.executed_count) / max(float(profile.lawsuit_total), 1.0)

        if indicator_code == "executed_amount_ratio":
            return float(profile.executed_amount) / max(float(profile.paid_in_capital), 1.0)

        if indicator_code == "bank_flow_ratio":
            return float(profile.avg_bank_flow) / max(float(profile.avg_approved_fund), 1.0)

        return None

    # ------------------------------------------------------------------
    # 财务（financial）
    # ------------------------------------------------------------------
    def _resolve_financial(
        self, indicator_code: str, ratios: dict
    ) -> Optional[float]:
        ratio_key_map = {
            "asset_liability_ratio": "asset_liability_ratio",
            "quick_ratio": "quick_ratio",
            "cash_flow_ratio": "cash_flow_ratio",
            "net_profit_margin": "net_profit_margin",
            "roe": "roe",
            "asset_turnover_ratio": "asset_turnover_ratio",
            "capital_operation": "capital_operation_capability",
        }
        key = ratio_key_map.get(indicator_code)
        if key is None:
            return None
        value = ratios.get(key)
        return float(value) if value is not None else None

    # ------------------------------------------------------------------
    # 立项质量（project_quality）
    # ------------------------------------------------------------------
    def _resolve_project_quality(
        self, indicator_code: str, pq: ProjectQualityData
    ) -> Optional[float]:
        field_map = {
            "avg_gross_margin": "avg_gross_margin",
            "price_volatility": "price_volatility",
            "contract_terms_score": "contract_terms_score",
            "legal_opinion_score": "legal_opinion_adoption",
            "warehouse_control_score": "warehouse_control_score",
            "logistics_track_score": "logistics_track_score",
        }
        attr_name = field_map.get(indicator_code)
        if attr_name is None:
            return None
        value = getattr(pq, attr_name, None)
        return float(value) if value is not None else None

    # ------------------------------------------------------------------
    # 履约质量（performance_quality）
    # ------------------------------------------------------------------
    def _resolve_performance_quality(
        self, indicator_code: str, perf: PerformanceData
    ) -> Optional[float]:
        if indicator_code == "annual_gross_margin":
            return float(perf.annual_gross_margin)

        if indicator_code == "annual_revenue":
            return float(perf.annual_revenue)

        if indicator_code == "immediate_overdue_rate":
            return float(perf.immediate_overdue_project_count) / max(
                float(perf.total_project_count), 1.0
            )

        if indicator_code == "overdue_60days_rate":
            return float(perf.overdue_60days_batch_count) / max(
                float(perf.total_batch_count), 1.0
            )

        if indicator_code == "recovery_rate":
            return float(perf.recovered_amount) / max(
                float(perf.historical_overdue_amount), 1.0
            )

        if indicator_code == "tax_declaration_consistency":
            return float(perf.tax_declaration_diff)

        return None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_year(date_str: Optional[str]) -> Optional[int]:
        """从日期字符串中提取年份，兼容 '2020-01-01'、'2020/01/01'、'2020' 等格式。"""
        if not date_str:
            return None
        try:
            return int(str(date_str)[:4])
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def resolve(
        self, indicator_code: str, ctx: ResolutionContext
    ) -> Optional[float]:
        """
        根据指标编码从上下文中解析对应的数值。

        Args:
            indicator_code: 指标编码（如 registered_capital、asset_liability_ratio 等）。
            ctx: 解析上下文，包含各维度原始数据。

        Returns:
            指标对应的 float 值；若数据缺失返回 None，除法分母为 0 返回 0.0。
        """
        # ---- 主体资质 ----
        subject_quality_codes = {
            "registered_capital",
            "paid_in_ratio",
            "establish_years",
            "tax_completion",
            "defendant_ratio",
            "executed_ratio",
            "executed_amount_ratio",
            "bank_flow_ratio",
        }
        if indicator_code in subject_quality_codes:
            if ctx.merchant_profile is None:
                return None
            return self._resolve_subject_quality(indicator_code, ctx.merchant_profile)

        # ---- 财务 ----
        financial_codes = {
            "asset_liability_ratio",
            "quick_ratio",
            "cash_flow_ratio",
            "net_profit_margin",
            "roe",
            "asset_turnover_ratio",
            "capital_operation",
        }
        if indicator_code in financial_codes:
            if ctx.ratios is None:
                return None
            return self._resolve_financial(indicator_code, ctx.ratios)

        # ---- 立项质量 ----
        project_quality_codes = {
            "avg_gross_margin",
            "price_volatility",
            "contract_terms_score",
            "legal_opinion_score",
            "warehouse_control_score",
            "logistics_track_score",
        }
        if indicator_code in project_quality_codes:
            if ctx.project_quality is None:
                return None
            return self._resolve_project_quality(indicator_code, ctx.project_quality)

        # ---- 履约质量 ----
        performance_codes = {
            "annual_gross_margin",
            "annual_revenue",
            "immediate_overdue_rate",
            "overdue_60days_rate",
            "recovery_rate",
            "tax_declaration_consistency",
        }
        if indicator_code in performance_codes:
            if ctx.performance is None:
                return None
            return self._resolve_performance_quality(indicator_code, ctx.performance)

        # 未知指标编码
        return None

"""财务比率计算（纯函数）。

严格按需求 2.3：财务比率由后端根据 raw_financial_data 自行计算，
严禁使用前端传入的比率。分母为 0 时返回 0 而非抛异常。
"""

from __future__ import annotations

from typing import Dict

from schemas import RawFinancialData


def _safe_div(numerator: float, denominator: float) -> float:
    """安全除法：分母为 0 返回 0。"""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_ratios(data: RawFinancialData) -> Dict[str, float]:
    """根据财务原始数据计算 7 项财务比率。

    返回 dict 的 key 即指标编码，与 rule_indicator_detail.indicator_code 对齐：
      - asset_liability_ratio        资产负债率 = 总负债 / 总资产
      - quick_ratio                  速动比率 = (流动资产 - 存货 - 预付) / 流动负债
      - cash_flow_ratio              现金流量比率 = 经营现金流 / 流动负债
      - net_profit_margin            销售净利率 = 净利润 / 营业收入
      - roe                          净资产收益率 = 净利润 / 净资产
      - asset_turnover_ratio         资产周转率 = 营业收入 / 总资产
                                    （首期无期初数，用期末总资产代替平均总资产）
      - capital_operation_capability 资本运作能力 = 净资产 / 总资产
    """
    total_asset = float(data.total_asset or 0.0)
    total_liability = float(data.total_liability or 0.0)
    current_asset = float(data.current_asset or 0.0)
    current_liability = float(data.current_liability or 0.0)
    revenue = float(data.revenue or 0.0)
    net_profit = float(data.net_profit or 0.0)
    inventory = float(data.inventory or 0.0)
    prepay = float(data.prepay or 0.0)
    operating_cash_flow = float(data.operating_cash_flow or 0.0)
    net_asset = float(data.net_asset or 0.0)

    return {
        "asset_liability_ratio": _safe_div(total_liability, total_asset),
        "quick_ratio": _safe_div(current_asset - inventory - prepay, current_liability),
        "cash_flow_ratio": _safe_div(operating_cash_flow, current_liability),
        "net_profit_margin": _safe_div(net_profit, revenue),
        "roe": _safe_div(net_profit, net_asset),
        # 首期用期末总资产代替平均总资产
        "asset_turnover_ratio": _safe_div(revenue, total_asset),
        "capital_operation_capability": _safe_div(net_asset, total_asset),
    }

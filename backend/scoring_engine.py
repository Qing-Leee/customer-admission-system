"""评分引擎编排服务。

职责：
  1. 场景自动识别（首次 / 动态）
  2. 维度组合（按场景与是否授信）
  3. 版本锚定（仅读 is_active=1 的当前生效版本）
  4. 财务比率计算（后端自行计算，禁用前端比率）
  5. 阶梯计分与维度汇总
  6. 评级与风控系数映射
  7. assessment_batch_id 生成
  8. financial_data_id 自动回填

不包含 Controller、不写库（写库由上层在拿到结果后单独处理；
不合格结果按需求 2.5 不应入库，调用方据 qualified 判断）。
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from batch_id import generate_batch_id
from dao import ScoringDao
from dimension_rules import (
    ALL_DIMENSIONS,
    active_dimensions,
)
from indicator_resolver import (
    IndicatorValueResolver,
    ResolutionContext,
)
from models import RuleIndicatorDetail
from ratio_calculator import compute_ratios
from rating_mapper import map_rating
from schemas import (
    DimensionScores,
    ScoringRequest,
    ScoringResult,
)
from segment_scorer import Segment, score_dimension


def _group_indicators(details: List[RuleIndicatorDetail]) -> Dict[str, Dict[str, List[Segment]]]:
    """将阶梯配置行分组为 {dimension: {indicator: [Segment, ...]}}。

    同一指标的各分段 weight_in_parent 应一致（取首段），便于汇总。
    """
    grouped: Dict[str, Dict[str, List[Segment]]] = defaultdict(lambda: defaultdict(list))
    for row in details:
        segment = Segment(
            indicator_code=row.indicator_code,
            lower_limit=float(row.lower_limit),
            upper_limit=float(row.upper_limit) if row.upper_limit is not None else None,
            unit_value=float(row.unit_value),
            unit_score=float(row.unit_score),
            max_score=float(row.max_score),
            weight_in_parent=float(row.weight_in_parent),
        )
        grouped[row.dimension_code][row.indicator_code].append(segment)
    return grouped


class ScoringEngine:
    """评分引擎服务。通过注入 :class:`ScoringDao` 解耦数据访问。"""

    def __init__(
        self,
        dao: ScoringDao,
        now_provider: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.dao = dao
        self._now_provider = now_provider
        self._rng = rng

    def calculate(self, request: ScoringRequest) -> ScoringResult:
        merchant_id = request.merchant_id

        # 1. 场景自动识别
        scenario = "DYNAMIC" if self.dao.has_score_snapshot(merchant_id) else "FIRST"

        # 2. 维度组合
        active_dims = active_dimensions(scenario, request.is_credit_applied)

        # 3. 版本锚定
        rule_version = self.dao.get_active_rule_version()
        if rule_version is None:
            raise RuntimeError("未找到当前生效的规则版本(is_active=1)")
        applied_rule_version_id = rule_version.id

        weight_cfg = {w["dimension_code"]: float(w["weight"]) for w in self.dao.get_weight_config(applied_rule_version_id)}
        dim_indicators = _group_indicators(self.dao.get_indicator_details(applied_rule_version_id))

        # 4. 必填数据校验（按场景维度组合）
        self._validate_required_data(scenario, request, active_dims)

        # 5. 财务比率计算（仅财务维度参与时计算；禁用前端比率）
        ratios: Optional[dict] = None
        if "financial" in active_dims:
            ratios = compute_ratios(request.raw_financial_data)

        # 6. 主体资质档案（主体资质维度恒参与，需读取）
        merchant_profile = self.dao.get_merchant_profile(merchant_id)

        ctx = ResolutionContext(
            ratios=ratios,
            project_quality=request.project_quality_data,
            performance=request.performance_data,
            merchant_profile=merchant_profile,
        )
        resolver = IndicatorValueResolver()

        # 7. 逐维度计分
        dim_scores: Dict[str, float] = {}
        for dim in ALL_DIMENSIONS:
            active = dim in active_dims
            indicator_segments = dim_indicators.get(dim, {})
            # 取该维度下各指标的原始数值
            indicator_values = {code: resolver.resolve(code, ctx) for code in indicator_segments}
            dim_scores[dim] = score_dimension(indicator_values, indicator_segments, active)

        # 维度得分四舍五入到 2 位（与出参示例一致）
        dim_scores = {dim: round(score, 2) for dim, score in dim_scores.items()}

        # 8. 总分 = 各活跃维度得分 * 维度权重 之和（不归一化，按配置原始权重汇总）
        total_score = round(
            sum(dim_scores[dim] * weight_cfg.get(dim, 0.0) for dim in active_dims),
            2,
        )

        # 9. 评级与风控系数
        rating, risk_coefficient = map_rating(total_score)
        qualified = rating != "UNQUALIFIED"

        # 10. financial_data_id 回填
        financial_data_id = self.dao.get_latest_financial_data_id(merchant_id)

        # 11. assessment_batch_id 生成
        assessment_batch_id = generate_batch_id(merchant_id, self._now_provider(), self._rng)

        return ScoringResult(
            merchant_id=merchant_id,
            assessment_batch_id=assessment_batch_id,
            scenario=scenario,
            is_credit_applied=request.is_credit_applied,
            applied_rule_version_id=applied_rule_version_id,
            financial_data_id=financial_data_id,
            dimension_scores=DimensionScores(**dim_scores),
            total_score=total_score,
            rating=rating,
            risk_coefficient=risk_coefficient,
            qualified=qualified,
        )

    @staticmethod
    def _validate_required_data(scenario: str, request: ScoringRequest, active_dims: set) -> None:
        """按场景维度组合校验必填数据。"""
        if "financial" in active_dims and request.raw_financial_data is None:
            raise ValueError("当前场景财务维度参与计算，raw_financial_data 必填")
        if "project_quality" in active_dims and request.project_quality_data is None:
            raise ValueError("首次+授信场景下 project_quality_data 必填")
        if "performance_quality" in active_dims and request.performance_data is None:
            raise ValueError("动态场景下 performance_data 必填")

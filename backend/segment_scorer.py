"""阶梯计分（纯函数）。

严格按需求 2.4：左闭右开区间匹配，命中后按单位步长累计，并截断至满分上限。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Segment:
    """单个阶梯分段。upper_limit 为 None 表示正无穷（最后一档）。"""

    indicator_code: str
    lower_limit: float
    upper_limit: Optional[float]
    unit_value: float
    unit_score: float
    max_score: float
    weight_in_parent: float


def score_indicator(value: float, segments: List[Segment]) -> Tuple[float, Optional[Segment]]:
    """对单个指标进行阶梯计分。

    返回 (得分, 命中的分段)。
    命中规则：找到满足 lower_limit <= value < upper_limit 的分段（左闭右开）。
    命中后：unit_count = (value - lower_limit) / unit_value；
           raw_score = unit_count * unit_score；
           indicator_score = min(raw_score, max_score)。
    未命中任何分段返回 (0.0, None)。
    """
    matched: Optional[Segment] = None
    for seg in segments:
        upper = seg.upper_limit if seg.upper_limit is not None else math.inf
        if seg.lower_limit <= value < upper:
            matched = seg
            break

    if matched is None:
        return 0.0, None

    if matched.unit_value in (0, None):
        # 单位步长为 0 视为无法累计，计 0 分（防御性处理）
        raw_score = 0.0
    else:
        unit_count = (value - matched.lower_limit) / matched.unit_value
        raw_score = unit_count * matched.unit_score

    indicator_score = min(raw_score, matched.max_score)
    return indicator_score, matched


def score_dimension(indicator_values: dict, indicator_segments: dict, active: bool) -> float:
    """汇总单个一级维度的得分。

    :param indicator_values:   {indicator_code: 原始数值} （未提供的指标视为缺值，计 0）
    :param indicator_segments: {indicator_code: [Segment, ...]}
    :param active:             该维度在当前场景是否参与计算；False 则直接返回 0
    """
    if not active:
        return 0.0

    total = 0.0
    for indicator_code, segments in indicator_segments.items():
        value = indicator_values.get(indicator_code)
        if value is None:
            indicator_score = 0.0
        else:
            indicator_score, _ = score_indicator(value, segments)
        # weight_in_parent 在同一指标的各分段上应一致，取首段
        weight = segments[0].weight_in_parent if segments else 0.0
        total += indicator_score * weight
    return total

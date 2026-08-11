"""维度规则模块。

定义业务评估维度的常量集合，并根据不同场景与授信状态
返回当前应激活的评估维度集合。

业务约束：
    - ``project_quality`` 与 ``performance_quality`` 永远不会同时
      出现在同一场景中。
    - ``subject_quality`` 在所有场景中均被激活。
    - ``financial`` 仅在授信场景下被激活。
"""

from typing import Set

# 全部可选维度（有序，保持业务语义优先级）
ALL_DIMENSIONS = [
    "subject_quality",
    "financial",
    "project_quality",
    "performance_quality",
]

# 场景与授信状态 -> 激活维度集合的映射表
# 通过显式映射保证业务规则的单一事实来源（single source of truth）
_DIMENSION_MAP: Set[str] = {
    ("FIRST", False): {"subject_quality"},
    ("FIRST", True): {"subject_quality", "financial", "project_quality"},
    ("DYNAMIC", False): {"subject_quality", "performance_quality"},
    ("DYNAMIC", True): {"subject_quality", "financial", "performance_quality"},
}


def active_dimensions(scenario: str, is_credit_applied: bool) -> Set[str]:
    """根据场景与授信状态返回当前激活的评估维度集合。

    维度激活规则（严格遵循）：

    +----------+-------------------+--------------------------------------------------+
    | scenario | is_credit_applied | 激活维度                                          |
    +==========+===================+==================================================+
    | FIRST    | False             | {"subject_quality"}                              |
    | FIRST    | True              | {"subject_quality", "financial", "project_quality"}       |
    | DYNAMIC  | False             | {"subject_quality", "performance_quality"}       |
    | DYNAMIC  | True              | {"subject_quality", "financial", "performance_quality"}    |
    +----------+-------------------+--------------------------------------------------+

    说明：
        - ``project_quality`` 与 ``performance_quality`` 永远不会同时出现。
        - ``subject_quality`` 为基础维度，在所有场景中恒定激活。
        - ``financial`` 仅在授信场景（``is_credit_applied=True``）下激活。

    Args:
        scenario: 业务场景标识，取值为 ``"FIRST"`` 或 ``"DYNAMIC"``。
        is_credit_applied: 是否为授信场景。``True`` 表示授信，
            ``False`` 表示非授信。

    Returns:
        当前场景下激活的维度名称集合（``Set[str]``）。返回集合为
        新构造的 ``set``，调用方可安全修改而不会影响内部映射表。

    Raises:
        ValueError: 当 ``scenario`` 不为 ``"FIRST"`` 或 ``"DYNAMIC"`` 时。

    Examples:
        >>> active_dimensions("FIRST", False)
        {'subject_quality'}
        >>> active_dimensions("FIRST", True) == {
        ...     "subject_quality", "financial", "project_quality"
        ... }
        True
        >>> active_dimensions("DYNAMIC", False) == {
        ...     "subject_quality", "performance_quality"
        ... }
        True
        >>> active_dimensions("DYNAMIC", True) == {
        ...     "subject_quality", "financial", "performance_quality"
        ... }
        True
    """
    key = (scenario, is_credit_applied)
    if key not in _DIMENSION_MAP:
        valid = {"FIRST", "DYNAMIC"}
        raise ValueError(
            f"无效的 scenario: {scenario!r}，合法取值为 {valid}；"
            f"is_credit_applied={is_credit_applied!r}。"
        )
    # 返回副本，避免外部修改污染内部映射表
    return set(_DIMENSION_MAP[key])

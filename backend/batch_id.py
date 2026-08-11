"""
批次 ID 生成模块。

生成格式为 {merchant_id}_{yyyyMMddHHmmssSSS}_{4位大写字母数字} 的批次 ID。
示例：1001_20260803142530123_A1B2
"""

import random
import string
from datetime import datetime
from typing import Optional

# 大写字母 + 数字字符集，用于生成随机后缀
_RANDOM_CHARS: str = string.ascii_uppercase + string.digits


def generate_batch_id(
    merchant_id: int,
    timestamp: float,
    rng: Optional[random.Random] = None,
) -> str:
    """
    生成批次 ID。

    格式：{merchant_id}_{yyyyMMddHHmmssSSS}_{4位大写字母数字}

    Args:
        merchant_id: 商户 ID。
        timestamp: Unix 时间戳（秒，浮点数）。
        rng: 可选的随机数生成器实例，用于生成随机后缀。
             若未传入，则内部创建 ``random.Random()`` 实例。

    Returns:
        格式化后的批次 ID 字符串，例如 ``1001_20260803142530123_A1B2``。
    """
    if rng is None:
        rng = random.Random()

    # 将时间戳转换为 yyyyMMddHHmmssSSS 格式
    dt = datetime.fromtimestamp(timestamp)
    time_str: str = dt.strftime("%Y%m%d%H%M%S") + f"{dt.microsecond // 1000:03d}"

    # 生成 4 位大写字母数字随机后缀
    random_suffix: str = "".join(rng.choices(_RANDOM_CHARS, k=4))

    return f"{merchant_id}_{time_str}_{random_suffix}"

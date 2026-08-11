"""
财务数据录入服务模块。

FinancialService 封装了财务数据录入的业务逻辑层，
支持单条录入和 Excel 批量导入。

职责：
    - 数据校验（字段必填性、范围、非负性）
    - 调用 DAO 层执行 Upsert
    - 事务异常处理与结构化响应
    - Excel 文件解析与列名映射
"""

import io
import math
from datetime import datetime
from typing import Dict, List, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from dao import ScoringDao


class FinancialService:
    """财务数据录入业务服务。"""

    # 金额数值字段列表（与 FinancialRawData 模型字段一一对应）
    _AMOUNT_FIELDS: List[str] = [
        "total_asset",
        "total_liability",
        "current_asset",
        "current_liability",
        "revenue",
        "net_profit",
        "inventory",
        "prepay",
        "operating_cash_flow",
        "net_asset",
    ]

    # Excel 列名映射：标准字段名 -> 可接受的表头别名列表（支持中英文）
    # 中文表头严格按照 B-2 模板规范，英文字段名作为兼容别名保留在后
    _COLUMN_MAP: Dict[str, List[str]] = {
        "period_year": ["period_year", "会计年度", "年度", "年"],
        "period_quarter": ["period_quarter", "季度", "期"],
        "total_asset": ["total_asset", "总资产"],
        "total_liability": ["total_liability", "总负债"],
        "current_asset": ["current_asset", "流动资产"],
        "current_liability": ["current_liability", "流动负债"],
        "revenue": ["revenue", "营业收入", "营收"],
        "net_profit": ["net_profit", "净利润"],
        "inventory": ["inventory", "存货"],
        "prepay": ["prepay", "预付账款", "预付款", "预付"],
        "operating_cash_flow": ["operating_cash_flow", "经营性现金流净额", "经营现金流", "经营活动现金流"],
        "net_asset": ["net_asset", "净资产"],
    }

    def __init__(self, session: Session):
        self.dao = ScoringDao(session)
        self.session = session

    # ------------------------------------------------------------------
    # 单条录入
    # ------------------------------------------------------------------
    def add_or_update_single(self, merchant_id: int, data: dict) -> dict:
        """
        处理单条财务数据录入。

        - 调用 _validate_record 校验 data
        - 调用 self.dao.upsert_financial_data(merchant_id, [data])
        - 返回 {"success": True, "rows_affected": 1} 或 {"success": False, "error": "..."}
        """
        valid, error = self._validate_record(data)
        if not valid:
            return {"success": False, "error": error}

        try:
            rows_affected = self.dao.upsert_financial_data(merchant_id, [data])
            return {"success": True, "rows_affected": rows_affected}
        except Exception as e:
            self.session.rollback()
            return {"success": False, "error": f"数据库写入失败: {e}"}

    # ------------------------------------------------------------------
    # Excel 批量导入
    # ------------------------------------------------------------------
    def import_from_excel(
        self, merchant_id: int, file_bytes: bytes, filename: str = ""
    ) -> dict:
        """
        解析 Excel 文件并批量导入。

        - 使用 pandas.read_excel 读取（支持 .xlsx）
        - 逐行调用 _validate_record 校验
        - 收集所有有效记录
        - 调用 self.dao.upsert_financial_data(merchant_id, valid_records)
        - 返回统计信息：
            {
                "total_rows": 10,
                "success_rows": 8,
                "failed_rows": 2,
                "failed_details": [{"row": 3, "reason": "年度不能为空"}],
                "rows_affected": 8
            }
        """
        # ---- 读取 Excel ----
        try:
            df = pd.read_excel(io.BytesIO(file_bytes))
        except Exception as e:
            return {
                "total_rows": 0,
                "success_rows": 0,
                "failed_rows": 0,
                "failed_details": [],
                "rows_affected": 0,
                "error": f"Excel 文件解析失败: {e}",
            }

        # 空文件检查
        if df.empty:
            return {
                "total_rows": 0,
                "success_rows": 0,
                "failed_rows": 0,
                "failed_details": [],
                "rows_affected": 0,
            }

        # ---- 列名映射 ----
        column_mapping = self._build_column_mapping(df.columns)

        # 必须列 period_year 是否存在
        if "period_year" not in column_mapping:
            total = len(df)
            return {
                "total_rows": total,
                "success_rows": 0,
                "failed_rows": total,
                "failed_details": [
                    {
                        "row": idx + 2,
                        "reason": "Excel 中未找到年度列（period_year / 年度 / 会计年度）",
                    }
                    for idx in range(total)
                ],
                "rows_affected": 0,
            }

        # ---- 逐行校验 ----
        total_rows = len(df)
        valid_records: List[dict] = []
        valid_row_nums: List[int] = []
        failed_details: List[dict] = []

        for idx in range(total_rows):
            row_num = idx + 2  # Excel 行号（第 1 行为表头）
            raw_row = df.iloc[idx]
            record = self._row_to_dict(raw_row, column_mapping)

            valid, error = self._validate_record(record)
            if not valid:
                failed_details.append({"row": row_num, "reason": error})
            else:
                valid_records.append(record)
                valid_row_nums.append(row_num)

        # ---- 批量写入 ----
        rows_affected = 0
        if valid_records:
            try:
                rows_affected = self.dao.upsert_financial_data(
                    merchant_id, valid_records
                )
            except Exception as e:
                self.session.rollback()
                # 数据库写入失败，所有有效记录标记为失败
                for row_num in valid_row_nums:
                    failed_details.append(
                        {"row": row_num, "reason": f"数据库写入失败: {e}"}
                    )
                valid_records = []
                rows_affected = 0

        return {
            "total_rows": total_rows,
            "success_rows": len(valid_records),
            "failed_rows": total_rows - len(valid_records),
            "failed_details": failed_details,
            "rows_affected": rows_affected,
        }

    # ------------------------------------------------------------------
    # 校验逻辑
    # ------------------------------------------------------------------
    def _validate_record(self, row: dict) -> Tuple[bool, str]:
        """
        校验单条财务数据。

        - period_year: 必须存在，且 >= 2000 且 <= 当前年份+1
        - period_quarter: 若存在，必须在 1~4 之间
        - 所有金额字段（total_asset 等）若存在，必须 >= 0
        - total_asset 允许为 0（评分引擎内部有 _safe_div 处理除零）
        - 返回 (是否通过, 错误信息)

        注意：
            - 校验通过后，会将规范化后的值写回 row 字典（类型统一为 int / float / None）。
            - 日期格式的 period_year / period_quarter 会自动提取年 / 季度。
            - 金额字段若为日期格式则拒绝。
            - row 中不存在的字段不会被添加（缺失列忽略，不覆盖已有值）。
        """
        current_year = datetime.now().year

        # ---- period_year 校验 ----
        period_year = self._clean_value(row.get("period_year"))
        if period_year is None:
            return (False, "年度不能为空")

        # 兼容 datetime、pd.Timestamp、日期字符串等多种格式
        try:
            if isinstance(period_year, (datetime, pd.Timestamp)):
                period_year = period_year.year
            elif isinstance(period_year, str):
                # 尝试解析为日期，提取年份
                try:
                    parsed = pd.to_datetime(period_year)
                    period_year = parsed.year
                except Exception:
                    # 若不是日期格式，尝试转为整数
                    period_year = int(period_year)
            else:
                period_year = int(period_year)
        except (ValueError, TypeError, AttributeError):
            return (False, "年度必须为整数或有效日期")

        if period_year < 2000:
            return (False, "年度不能小于2000")
        if period_year > current_year + 1:
            return (False, f"年度不能大于{current_year + 1}")
        row["period_year"] = period_year

        # ---- period_quarter 校验 ----
        period_quarter = self._clean_value(row.get("period_quarter"))
        if period_quarter is not None:
            # 日期格式：从月份推算季度
            if isinstance(period_quarter, datetime):
                ts = pd.Timestamp(period_quarter)
                period_quarter = (ts.month - 1) // 3 + 1
            else:
                try:
                    period_quarter = int(period_quarter)
                except (ValueError, TypeError):
                    return (False, "季度必须为整数")
            if period_quarter < 1 or period_quarter > 4:
                return (False, "季度必须在1~4之间")
            row["period_quarter"] = period_quarter
        else:
            row["period_quarter"] = None

        # ---- 金额字段校验 ----
        for field in self._AMOUNT_FIELDS:
            # 列不存在于数据源（Excel 无此列 / API 未传）：跳过，不覆盖已有值
            if field not in row:
                continue
            value = self._clean_value(row[field])
            if value is not None:
                # 日期格式不能作为金额，直接拒绝
                if isinstance(value, datetime):
                    return (False, f"{field}必须为数值，不能为日期")
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    return (False, f"{field}必须为数值")
                if value < 0:
                    return (False, f"{field}不能为负数")
                row[field] = value
            else:
                # 空单元格转为 None（不要填 0）
                row[field] = None

        # ---- total_asset 校验：若填写必须 >= 0（评分引擎内部有 _safe_div 处理除零）----
        # 金额非负性已由上方通用循环覆盖，此处无需额外限制 > 0

        return (True, "")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_value(value):
        """
        清理值：处理 pandas NaN、None、空字符串等，统一返回 None 或原值。

        用于兼容 API 请求（Python 原生类型）和 Excel 读取（pandas 类型）两种数据源。
        """
        # 1. 显式 None 直接返回
        if value is None:
            return None

        # 2. 处理空字符串
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None

        # 3. 处理 Python float 的 NaN（非 pandas 场景）
        if isinstance(value, float) and math.isnan(value):
            return None

        # 4. 处理 pandas 的 NaN / NaT / NA（如果 pandas 可用）
        try:
            import pandas as pd
            if pd.isna(value):
                return None
        except (ImportError, TypeError, ValueError):
            # pandas 不可用或 value 类型无法判断时，跳过
            pass

        return value

    def _build_column_mapping(self, columns) -> Dict[str, str]:
        """
        构建 Excel 列名到标准字段名的映射。

        支持中英文表头，按 _COLUMN_MAP 中定义的别名优先级匹配。
        匹配规则：去空格 + 转小写后精确比对。

        Returns:
            {"period_year": "年度", "total_asset": "总资产", ...}
            key 为标准字段名，value 为 Excel 中的原始列名。
        """
        mapping: Dict[str, str] = {}
        # 标准化列名（去空格 + 转小写），建立 "标准化列名 -> 原始列名" 索引
        normalized_cols: Dict[str, str] = {}
        for col in columns:
            key = str(col).strip().lower()
            normalized_cols[key] = col

        for field, aliases in self._COLUMN_MAP.items():
            for alias in aliases:
                alias_key = alias.strip().lower()
                if alias_key in normalized_cols:
                    mapping[field] = normalized_cols[alias_key]
                    break

        return mapping

    @staticmethod
    def _row_to_dict(row, column_mapping: Dict[str, str]) -> dict:
        """将 pandas Series 行转换为标准 dict，空值转为 None。"""
        record = {}
        for field, col_name in column_mapping.items():
            value = row[col_name]
            # 使用 pandas 判断空值（兼容 NaN、NaT、None 等）
            try:
                import pandas as pd
                if pd.isna(value):
                    value = None
            except (ImportError, TypeError, ValueError):
                # pandas 不可用时，仅检查 None 和空字符串
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    value = None
                # 处理 float NaN
                if isinstance(value, float) and value != value:  # NaN != NaN
                    value = None
            record[field] = value
        return record

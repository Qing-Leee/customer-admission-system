"""
客商管理业务逻辑层（Service）。

负责客商基础信息的创建/更新、分页列表、详情查询及附件上传等业务编排，
封装参数校验、文件落盘、事务提交与异常处理，对上层（路由层）返回统一格式的结果字典。
"""

import os
import re
import time
from typing import List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dao import ScoringDao  # 依赖项目已有 dao.py 中的 ScoringDao

# 允许上传的文件扩展名
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".docx"}
# 最大文件大小：20MB
MAX_FILE_SIZE = 20 * 1024 * 1024
# 统一社会信用代码：18 位，由数字与大写字母组成
_TAX_NUMBER_PATTERN = re.compile(r"^[0-9A-Z]{18}$")
# 附件根目录（相对当前工作目录）
ATTACHMENT_ROOT = "attachments"


class MerchantService:
    """客商管理业务服务。"""

    def __init__(self, session: Session):
        self.dao = ScoringDao(session)
        self.session = session

    # ------------------------------------------------------------------
    # 创建或更新客商
    # ------------------------------------------------------------------
    def create_or_update(self, data: dict, contacts: List[dict] = None) -> dict:
        """
        创建或更新客商。

        - 校验：tax_number 格式（统一社会信用代码：18位数字+字母）
        - 校验：merchant_name 不能为空
        - 校验：legal_person 不能为空
        - 校验：contacts 中 is_primary=1 恰好 1 条（若传入了 contacts）
        - 调用 dao.upsert_merchant
        - 返回 {"success": True, "merchant_id": 123} 或 {"success": False, "error": "..."}

        重要约定（对接人更新策略）：
            - contacts 采用「全量替换」模式：每次传入完整的对接人列表。
            - 若传入 contacts，DAO 会先删除该客商的所有旧联络人，再批量插入新列表。
            - 因此前端每次必须传完整列表，未传入的对接人会被删除。
            - 如需增量更新单个对接人，应走独立接口（后续可扩展）。
        """
        tax_number = data.get("tax_number")
        if not self._is_valid_tax_number(tax_number):
            return {
                "success": False,
                "error": "tax_number 格式不正确（统一社会信用代码应为18位数字+大写字母）",
            }
        if not data.get("merchant_name"):
            return {"success": False, "error": "merchant_name 不能为空"}
        if not data.get("legal_person"):
            return {"success": False, "error": "legal_person 不能为空"}

        if contacts:
            primary_count = sum(1 for c in contacts if c.get("is_primary"))
            if primary_count != 1:
                return {
                    "success": False,
                    "error": "contacts 中 is_primary=1 的记录必须恰好 1 条",
                }

        try:
            merchant_id = self.dao.upsert_merchant(data, contacts)
            self.session.commit()
            return {"success": True, "merchant_id": merchant_id}
        except ValueError as exc:
            self.session.rollback()
            return {"success": False, "error": str(exc)}
        except SQLAlchemyError as exc:
            self.session.rollback()
            return {"success": False, "error": f"数据库操作失败：{exc}"}
        except Exception as exc:  # noqa: BLE001 - 兜底，保证不向上抛出
            self.session.rollback()
            return {"success": False, "error": f"系统异常：{exc}"}

    # ------------------------------------------------------------------
    # 分页列表
    # ------------------------------------------------------------------
    def list_merchants(self, filters: dict, page: int = 1, page_size: int = 20) -> dict:
        """分页列表，直接透传 dao.get_merchant_list。"""
        try:
            return self.dao.get_merchant_list(filters, page, page_size)
        except SQLAlchemyError as exc:
            return {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "list": [],
                "error": f"数据库操作失败：{exc}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "list": [],
                "error": f"系统异常：{exc}",
            }

    # ------------------------------------------------------------------
    # 客商详情
    # ------------------------------------------------------------------
    def get_detail(self, merchant_id: int) -> dict:
        """客商详情，直接透传 dao.get_merchant_detail。"""
        try:
            return self.dao.get_merchant_detail(merchant_id)
        except SQLAlchemyError as exc:
            return {"error": f"数据库操作失败：{exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"系统异常：{exc}"}

    # ------------------------------------------------------------------
    # 上传附件
    # ------------------------------------------------------------------
    def upload_attachment(
        self,
        merchant_id: int,
        file_content: bytes,
        file_name: str,
        file_type: str,
        uploaded_by: Optional[str],
    ) -> dict:
        """
        上传附件。

        - 校验文件大小（≤20MB）
        - 校验文件类型（仅限 .pdf, .jpg, .jpeg, .png, .xlsx, .docx）
        - 生成存储路径：attachments/{merchant_id}/{timestamp}_{file_name}
        - 调用 dao.add_attachment
        - 返回 {"success": True, "attachment_id": 1, "file_path": "..."}
        """
        # 安全防护：确保 merchant_id 为正整数（路径安全的最后一道防线）
        if merchant_id <= 0:
            return {"success": False, "error": "merchant_id 必须为正整数"}

        if not file_content:
            return {"success": False, "error": "文件内容不能为空"}
        if not file_name:
            return {"success": False, "error": "文件名不能为空"}

        file_size = len(file_content)
        if file_size > MAX_FILE_SIZE:
            return {"success": False, "error": "文件大小超过 20MB 限制"}

        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return {
                "success": False,
                "error": (
                    f"不支持的文件类型：{ext}，仅支持 "
                    f"{', '.join(sorted(ALLOWED_EXTENSIONS))}"
                ),
            }

        # 生成存储路径（相对路径，存库）
        # _sanitize_filename 可能抛出 ValueError（文件名含非法字符）
        timestamp = int(time.time())
        try:
            safe_name = self._sanitize_filename(file_name)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        relative_path = f"{ATTACHMENT_ROOT}/{merchant_id}/{timestamp}_{safe_name}"

        # 落盘写入
        try:
            dir_path = os.path.join(ATTACHMENT_ROOT, str(merchant_id))
            os.makedirs(dir_path, exist_ok=True)
            full_path = os.path.join(dir_path, f"{timestamp}_{safe_name}")
            with open(full_path, "wb") as fp:
                fp.write(file_content)
        except OSError as exc:
            return {"success": False, "error": f"文件保存失败：{exc}"}

        # 写入数据库记录
        try:
            attachment_id = self.dao.add_attachment(
                merchant_id=merchant_id,
                file_name=file_name,
                file_path=relative_path,
                file_size=file_size,
                file_type=file_type,
                uploaded_by=uploaded_by,
            )
            self.session.commit()
            return {
                "success": True,
                "attachment_id": attachment_id,
                "file_path": relative_path,
            }
        except SQLAlchemyError as exc:
            self.session.rollback()
            # 回滚已落盘的文件，避免产生孤儿文件
            self._safe_remove_file(relative_path)
            return {"success": False, "error": f"附件记录保存失败：{exc}"}
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._safe_remove_file(relative_path)
            return {"success": False, "error": f"系统异常：{exc}"}

    # ==================================================================
    # 内部工具方法
    # ==================================================================
    @staticmethod
    def _is_valid_tax_number(tax_number) -> bool:
        """校验统一社会信用代码：18 位数字 + 大写字母。"""
        if not isinstance(tax_number, str):
            return False
        return bool(_TAX_NUMBER_PATTERN.match(tax_number))

    @staticmethod
    def _sanitize_filename(file_name: str) -> str:
        """清洗文件名，防止路径穿越。

        - os.path.basename 提取纯文件名，去除目录部分
        - 拒绝包含 ``..`` 或以 ``.`` 开头的文件名（防目录穿越 / 隐藏文件）
        - 替换所有路径分隔符及特殊字符为下划线
        """
        base_name = os.path.basename(file_name)
        if ".." in base_name or base_name.startswith("."):
            raise ValueError("文件名包含非法字符")
        return re.sub(r"[\/\\:*?\"<>|.]", "_", base_name)

    @staticmethod
    def _safe_remove_file(relative_path: str) -> None:
        """安全删除文件（忽略不存在等异常）。"""
        try:
            if os.path.exists(relative_path):
                os.remove(relative_path)
        except OSError:
            pass

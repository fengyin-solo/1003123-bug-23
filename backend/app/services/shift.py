"""入井管理业务规则：状态流转、字段校验与筛选口径都收在这里。

入井名单的持证校验直接读持证管理的同源判定：证书状态重算后这里跟着变。
"""
from __future__ import annotations

from typing import Any

from app.services import certificate as certificate_rules
from app.store import store

MODULE = "shift"
REQUIRED_FIELDS = ["记录编号", "入井人员", "所属班组"]
STATUS_ORDER = ["入井中", "已升井", "超时未升", "已联系"]
ACTION_RULES = {"登记入井": "入井中", "登记升井": "已升井", "超时联系": "已联系"}
NEGATIVE_ACTIONS = []
BLOCKED_CERT_STATUSES = ("已过期", "已注销")


def certificate_check(person: Any) -> str:
    """入井人员的持证校验结果：与持证管理列表同源，状态重算后这里跟着变。"""
    row = certificate_rules.find_by_person(str(person or ""))
    if row is None:
        return "未查到持证记录"
    status = row.get("status")
    if status == "即将到期":
        days = row.get("到期天数")
        return f"即将到期（剩{days}天）" if days is not None else "即将到期"
    return str(status)


def certificate_block(person: Any) -> str | None:
    """证书已过期或已注销的人员禁止登记入井；其余情况放行。"""
    row = certificate_rules.find_by_person(str(person or ""))
    if row is None or row.get("status") not in BLOCKED_CERT_STATUSES:
        return None
    name = row.get("姓名") or person
    return f"{name}的证书{row.get('status')}，禁止登记入井"


def with_cert_check(entry: dict[str, Any]) -> dict[str, Any]:
    entry["持证校验"] = certificate_check(entry.get("入井人员"))
    return entry


class ShiftService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [with_cert_check(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return with_cert_check(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        blocked = certificate_block(values.get("入井人员"))
        if blocked:
            return None, [blocked]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return with_cert_check(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入井记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于入井管理可执行范围"
        if action == "登记入井":
            blocked = certificate_block(entry.get("入井人员"))
            if blocked:
                return None, blocked
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return with_cert_check(entry), f"入井记录已{action}"

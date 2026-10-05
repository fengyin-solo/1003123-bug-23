"""入井管理业务规则：状态流转、字段校验与筛选口径都收在这里。

入井名单的持证校验直接读持证管理的实时判定，证书状态一变这里跟着变。
"""
from __future__ import annotations

from typing import Any

from app.services.certificate import certificate_status_for
from app.store import store

MODULE = "shift"
REQUIRED_FIELDS = ["记录编号", "入井人员", "所属班组"]
STATUS_ORDER = ["入井中", "已升井", "超时未升", "已联系"]
ACTION_RULES = {"登记入井": "入井中", "登记升井": "已升井", "超时联系": "已联系"}
NEGATIVE_ACTIONS = []


def _with_certificate(entry: dict[str, Any]) -> dict[str, Any]:
    """给入井记录附上持证校验结果，和持证管理读到的是同一份判定。"""
    check = certificate_status_for(str(entry.get("入井人员") or ""))
    entry["持证状态"] = check["状态"]
    return entry


def _check_certificate(person: str) -> str | None:
    """登记入井前的持证校验：证书过期或已注销就拦下，返回拦阻原因。"""
    check = certificate_status_for(person)
    if not check["可入井"]:
        return f"{person}证书状态为{check['状态']}，不允许登记入井"
    return None


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
        return [_with_certificate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return None if entry is None else _with_certificate(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        blocked = _check_certificate(str(values.get("入井人员") or ""))
        if blocked:
            return None, [blocked]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return _with_certificate(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入井记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于入井管理可执行范围"
        if action == "登记入井":
            blocked = _check_certificate(str(entry.get("入井人员") or ""))
            if blocked:
                return None, blocked
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return _with_certificate(entry), f"入井记录已{action}"

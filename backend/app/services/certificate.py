"""持证管理业务规则：状态流转、字段校验与筛选口径都收在这里。

到期判定只有这一份：列表、详情、导出、汇总和入井管理的持证校验都走
refresh_entry，谁读到的状态都一样，安全科不用再拿表格自己算。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "certificate"
REQUIRED_FIELDS = ["人员编号", "姓名", "证书类别"]
STATUS_ORDER = ["持证有效", "即将到期", "已过期", "已注销"]
ACTION_RULES = {"安排复训": "持证有效", "登记过期": "已过期", "注销证书": "已注销"}
NEGATIVE_ACTIONS = []
RETRO_ACTION = "补录复训"
TERMINAL_STATUS = "已注销"

# 剩余天数落进这个区间算即将到期，小于 0 算已过期
EXPIRING_SOON_DAYS = 30
# 证书有效期：存量证书按发证日期回填到期日期，复训后按最近一次发证重算
VALIDITY_YEARS = 3

DATE_FIELDS = ["发证日期", "到期日期", "复训记录"]
EXTRA_FIELDS = ["证书编号"]


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 2 月 29 日顺延到 2 月 28 日
        return day.replace(year=day.year + years, day=28)


def training_dates(entry: dict[str, Any]) -> list[date]:
    """复训记录里的有效日期；存量数据里的占位文本直接忽略。"""
    raw = entry.get("复训记录")
    if isinstance(raw, str):
        parts = raw.replace("，", "、").replace(",", "、").split("、")
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = []
    return [day for day in (_parse_date(item) for item in parts) if day]


def latest_issue_date(entry: dict[str, Any]) -> date | None:
    """最近一次发证日期：复训日期和发证日期冲突时，以最近一次发证为准。"""
    dates = training_dates(entry)
    issue = _parse_date(entry.get("发证日期"))
    if issue is not None:
        dates.append(issue)
    return max(dates) if dates else None


def resolve_expiry_date(entry: dict[str, Any]) -> date | None:
    """到期日期：人工填了有效到期日就尊重，缺失或不晚于最近发证的按有效期回填。"""
    base = latest_issue_date(entry)
    stored = _parse_date(entry.get("到期日期"))
    if stored is not None and (base is None or stored > base):
        return stored
    if base is not None:
        return _add_years(base, VALIDITY_YEARS)
    return stored


def days_remaining(entry: dict[str, Any], today: date | None = None) -> int | None:
    expiry = resolve_expiry_date(entry)
    if expiry is None:
        return None
    return (expiry - (today or date.today())).days


def judge_status(entry: dict[str, Any], today: date | None = None) -> str:
    """按剩余天数判定状态；已注销是人工终态，不参与自动判定。"""
    if entry.get("status") == TERMINAL_STATUS:
        return TERMINAL_STATUS
    days = days_remaining(entry, today)
    if days is None:
        status = str(entry.get("status") or "")
        return status if status in STATUS_ORDER else STATUS_ORDER[0]
    if days < 0:
        return "已过期"
    if days <= EXPIRING_SOON_DAYS:
        return "即将到期"
    return "持证有效"


def refresh_entry(entry: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """把到期判定写回记录：回填到期日期、剩余天数与状态，所有入口共用。"""
    today = today or date.today()
    expiry = resolve_expiry_date(entry)
    if expiry is not None:
        text = expiry.isoformat()
        if entry.get("到期日期") != text:
            entry["到期日期"] = text  # 存量证书按发证日期回填
    entry["剩余天数"] = None if expiry is None else (expiry - today).days
    entry["status"] = judge_status(entry, today)
    entry["证书状态"] = entry["status"]
    entry["pending"] = entry["status"] != TERMINAL_STATUS
    entry["abnormal"] = entry["status"] in ("即将到期", "已过期")
    return entry


def certificate_status_for(name: str, today: date | None = None) -> dict[str, Any]:
    """给入井管理等外部模块用的持证校验：按姓名找证书，返回实时判定结果。"""
    name = name.strip()
    matched = [
        refresh_entry(row, today)
        for row in store.rows(MODULE)
        if name and str(row.get("姓名", "")).strip() == name
    ]
    if not matched:
        return {"状态": "未登记", "可入井": True, "说明": f"{name or '该人员'}未登记证书"}
    statuses = sorted({row["status"] for row in matched}, key=STATUS_ORDER.index)
    allowed = any(row["status"] in ("持证有效", "即将到期") for row in matched)
    return {"状态": "、".join(statuses), "可入井": allowed, "说明": "、".join(statuses)}


class CertificateService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        cert_category: str | None = None,
        expiry_month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [refresh_entry(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("人员编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if cert_category:
            rows = [row for row in rows if cert_category in str(row.get("证书类别", ""))]
        if expiry_month:
            rows = [row for row in rows if str(row.get("到期日期", "")).startswith(expiry_month)]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def summary(self) -> dict[str, Any]:
        """各状态实时计数：统计卡片和列表筛选用的是同一份判定。"""
        rows = [refresh_entry(row) for row in store.rows(MODULE)]
        counts = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            counts[row["status"]] += 1
        return {"total": len(rows), "counts": counts, "expiring_soon_days": EXPIRING_SOON_DAYS}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return None if entry is None else refresh_entry(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in DATE_FIELDS + EXTRA_FIELDS:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry.setdefault("复训记录", "")
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        return refresh_entry(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"持证人员 {entry_id} 不存在或已归档"
        if action == RETRO_ACTION:
            return self._record_retraining(entry, values or {})
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于持证管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        # 人工动作之后仍按到期日期重算：除已注销外，状态以日期判定为准
        refresh_entry(entry)
        return entry, f"持证人员已{action}，当前状态{entry['status']}"

    def _record_retraining(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """补录复训记录：重复提交只生效一次，录完按最近一次发证重算状态。"""
        trained = _parse_date(values.get("复训日期"))
        if trained is None:
            return None, "补录复训需要合法的复训日期（YYYY-MM-DD）"
        existing = training_dates(entry)
        if trained in existing:
            refresh_entry(entry)
            return entry, f"复训记录 {trained.isoformat()} 已存在，重复提交不再生效"
        recorded = sorted(existing + [trained])
        entry["复训记录"] = "、".join(day.isoformat() for day in recorded)
        # 复训相当于重新发证：到期日期按最近一次发证重算
        base = latest_issue_date(entry)
        if base is not None:
            entry["到期日期"] = _add_years(base, VALIDITY_YEARS).isoformat()
        refresh_entry(entry)
        return entry, f"复训记录已补录，证书状态重算为{entry['status']}"

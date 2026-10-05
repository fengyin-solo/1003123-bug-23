"""持证管理业务规则：状态流转、字段校验与筛选口径都收在这里。

到期判定只有这一份：剩余天数 = 到期日期 - 今天；
剩余天数 < 0 落已过期，落进即将到期区间（EXPIRING_SOON_DAYS 天）算即将到期，
其余为持证有效；已注销是人工终态，不参与自动推导。
列表、详情、导出、入井校验都走同一套 derive/sync，读到的状态才一致。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "certificate"
REQUIRED_FIELDS = ["人员编号", "姓名", "证书类别"]
OPTIONAL_FIELDS = ["证书编号", "发证日期", "到期日期", "复训记录"]
STATUS_ORDER = ["持证有效", "即将到期", "已过期", "已注销"]
CANCELLED_STATUS = "已注销"

EXPIRING_SOON_DAYS = 30  # 剩余天数落进 [0, 30] 自动算即将到期
VALIDITY_YEARS = 3  # 证书有效期：发证日期 + 3 年，存量证书按此回填到期日期

ACTIONS = ["安排复训", "补录复训记录", "注销证书"]


def parse_date(value: Any) -> date | None:
    """把字符串/日期统一解析成 date，解析不了返回 None。"""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 2 月 29 日顺延到 28 日
        return day.replace(year=day.year + years, day=28)


def expiry_for_issue(issue: date) -> date:
    """发证日期 + 有效期 = 到期日期。"""
    return add_years(issue, VALIDITY_YEARS)


def remaining_days(entry: dict[str, Any], today: date | None = None) -> int | None:
    """按到期日期算剩余天数；没有可解析的到期日期时返回 None。"""
    expiry = parse_date(entry.get("到期日期"))
    if expiry is None:
        return None
    return (expiry - (today or date.today())).days


def derive_status(entry: dict[str, Any], today: date | None = None) -> str:
    """唯一的判定标准：已注销不动，其余按剩余天数自动落档。"""
    if entry.get("status") == CANCELLED_STATUS:
        return CANCELLED_STATUS
    days = remaining_days(entry, today)
    if days is None:
        status = str(entry.get("status") or "")
        return status if status in STATUS_ORDER else STATUS_ORDER[0]
    if days < 0:
        return "已过期"
    if days <= EXPIRING_SOON_DAYS:
        return "即将到期"
    return "持证有效"


def sync_entry(entry: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """把判定结果写回记录：状态、到期天数、展示字段同源，哪个入口读都一致。

    存量证书缺到期日期时按发证日期 + 有效期回填。
    """
    issue = parse_date(entry.get("发证日期"))
    if parse_date(entry.get("到期日期")) is None and issue is not None:
        entry["到期日期"] = expiry_for_issue(issue).isoformat()
    status = derive_status(entry, today)
    entry["status"] = status
    entry["证书状态"] = status
    entry["到期天数"] = remaining_days(entry, today)
    entry["pending"] = status in ("即将到期", "已过期")
    entry["abnormal"] = status == "已过期"
    return entry


def expiry_month_of(entry: dict[str, Any]) -> str | None:
    """到期月份（YYYY-MM），供组合查询比对。"""
    expiry = parse_date(entry.get("到期日期"))
    return expiry.isoformat()[:7] if expiry else None


def find_by_person(identifier: str, today: date | None = None) -> dict[str, Any] | None:
    """按姓名或人员编号查持证记录并同步状态；入井校验等外部入口统一走这里。"""
    key = str(identifier or "").strip()
    if not key:
        return None
    for row in store.rows(MODULE):
        if key in (str(row.get("姓名", "")), str(row.get("人员编号", ""))):
            return sync_entry(row, today)
    return None


class CertificateService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        category: str | None = None,
        expiry_month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [sync_entry(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("人员编号", ""))]
        if category:
            rows = [row for row in rows if category in str(row.get("证书类别", ""))]
        if expiry_month:
            month = expiry_month.strip()[:7]
            rows = [row for row in rows if expiry_month_of(row) == month]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return sync_entry(entry) if entry is not None else None

    def status_counts(self) -> dict[str, int]:
        """按状态统计持证人数，给看板卡片用；口径与列表一致。"""
        counts = {status: 0 for status in STATUS_ORDER}
        for row in store.rows(MODULE):
            counts[sync_entry(row)["status"]] += 1
        return counts

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS + OPTIONAL_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        return sync_entry(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"持证人员 {entry_id} 不存在或已归档"
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于持证管理可执行范围"
        if action == "注销证书":
            entry["status"] = CANCELLED_STATUS
            return sync_entry(entry), "持证人员已注销证书"
        if action == "安排复训":
            entry["复训安排"] = "已安排复训"
            return sync_entry(entry), "持证人员已安排复训"
        return self._record_retraining(entry, values or {})

    def _record_retraining(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """补录复训记录：复训合格即最近一次发证，按复训日期重算发证与到期。

        同一张证书的同一复训日期只生效一次，重复提交直接告知已登记；
        复训日期早于当前发证日期时以最近一次发证为准，不回退日期。
        """
        retrained_on = parse_date(values.get("复训日期"))
        if retrained_on is None:
            return None, "补录复训记录需要提供复训日期（格式 YYYY-MM-DD）"
        records: list[str] = entry.setdefault("复训日期列表", [])
        if retrained_on.isoformat() in records:
            return sync_entry(entry), f"{retrained_on.isoformat()} 的复训记录已登记过，重复提交不再生效"
        records.append(retrained_on.isoformat())
        entry["复训记录"] = "；".join(records)
        entry.pop("复训安排", None)
        issue = parse_date(entry.get("发证日期"))
        if issue is None or retrained_on > issue:
            entry["发证日期"] = retrained_on.isoformat()
            entry["到期日期"] = expiry_for_issue(retrained_on).isoformat()
            message = f"复训记录已补录，按最近一次发证 {retrained_on.isoformat()} 重算到期日期"
        else:
            message = "复训记录已补录，早于最近一次发证日期，发证与到期日期维持不变"
        return sync_entry(entry), message

    def backfill_existing(self) -> int:
        """存量证书初始化：缺到期日期的按发证日期回填，并同步全部状态。"""
        rows = store.rows(MODULE)
        for row in rows:
            sync_entry(row)
        return len(rows)

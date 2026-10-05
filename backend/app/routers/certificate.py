"""持证管理接口：维护持证人员，覆盖安排复训、补录复训记录、注销证书等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.certificate import CertificateService

router = APIRouter(prefix="/api/certificate", tags=["持证管理"])

service = CertificateService()

LIST_FIELDS = ["人员编号", "姓名", "证书类别", "证书编号", "发证日期", "到期日期", "到期天数", "复训记录", "证书状态"]
STATUSES = ["持证有效", "即将到期", "已过期", "已注销"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按人员编号检索"),
    status: str | None = Query(default=None, description="持证有效、即将到期、已过期、已注销"),
    category: str | None = Query(default=None, description="按证书类别检索"),
    expiry_month: str | None = Query(default=None, description="按到期月份检索，格式 YYYY-MM"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按人员编号、状态、证书类别与到期月份组合过滤；命中总数与实际条数一致。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, category=category, expiry_month=expiry_month,
        page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats() -> dict[str, int]:
    """按状态统计持证人数：与列表同一套判定口径。"""
    return service.status_counts()


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按人员编号检索"),
    status: str | None = Query(default=None, description="持证有效、即将到期、已过期、已注销"),
    category: str | None = Query(default=None, description="按证书类别检索"),
    expiry_month: str | None = Query(default=None, description="按到期月份检索，格式 YYYY-MM"),
) -> dict[str, Any]:
    """导出持证管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(
        keyword=keyword, status=status, category=category, expiry_month=expiry_month,
        page=1, size=10000,
    )
    return {"module": "certificate", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条持证人员明细；到期天数与状态和列表同源，不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"持证人员 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条持证人员，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="持证人员已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条持证人员执行安排复训、补录复训记录、注销证书；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

"""可借资格链 + 一笔 active 互斥 + 逾期判定。

可借栏、借出确认、顶细条可借数必须共用 shows_as_available 这一条口径，
任何收紧（例如无主脏数据不再放行）都在这一个函数里改，三处同时生效；
测例只能引用本函数的结论，不得另立一套期望。
"""

def shows_as_available(item: dict) -> bool:
    """物品是否进入可借集合（可借栏/借出确认/顶细条同源）。

    当前已落地口径：只看 status，无主脏数据（owner 为空、data_quality='dirty'）
    仍随 status='available' 放行；但任何写入口都不得借此把它洗白。
    """
    return item.get("status") == "available"

def can_lend(item_status_or_row, active_loans: int) -> dict:
    row = item_status_or_row if isinstance(item_status_or_row, dict) else {"status": item_status_or_row}
    if not shows_as_available(row):
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}

def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    if loan_status != "active":
        return False
    return bool(due_date) and due_date < today

def classify_loans(loans: list[dict], today: str) -> dict:
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == "returned":
            returned.append(L)
        elif is_overdue(L.get("due_date"), today, st):
            overdue.append({**L, "overdue": True})
        elif st == "active":
            active.append({**L, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}

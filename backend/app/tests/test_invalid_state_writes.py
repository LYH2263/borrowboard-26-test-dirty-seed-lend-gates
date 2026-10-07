"""脏种与非法态的跨入口拒写测例。

不止四条 HTTP：每次拒写都同时核对
  回包 / loans 分类 / 库内行 / 可借栏 / 物主栏 / 顶细条计数，
所有期望只引用资格链 shows_as_available 的落地结论，测例不另立一套。

失败信息一律带 API 路径名（见 fail() / expect_*()）。
"""
import sqlite3
import pytest

from app.db import db_path
from app.engines.borrow_rules import shows_as_available
from app.modules import deposit

P_ITEMS = "/api/items"
P_BOARD = "/api/board"
P_LOANS = "/api/loans"
def p_lend(iid): return f"/api/items/{iid}/lend"
def p_return(lid): return f"/api/loans/{lid}/return"


# ---------- 工具 ----------

def fail(path, msg):
    raise AssertionError(f"[{path}] {msg}")

def expect_status(path, resp, code):
    if resp.status_code != code:
        fail(path, f"期望 {code}，实得 {resp.status_code}：{resp.text}")

def expect_failed(path, resp):
    if resp.status_code < 400:
        fail(path, f"非法态写入应失败，却返回 {resp.status_code}：{resp.text}")

def db():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    return c

def count_items():
    c = db(); n = c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]; c.close(); return n

def item_by_title(title):
    c = db(); row = c.execute("SELECT * FROM items WHERE title=?", (title,)).fetchone()
    c.close(); return dict(row) if row else None

def active_loans_of(iid):
    c = db()
    n = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
    c.close(); return n

def ids(rows): return [r["id"] for r in rows]

def assert_strip_matches_panes(path, b):
    """顶细条与分栏同一结论：计数必须等于分栏实际行数。"""
    n_av, n_ac, n_od = b["counts"]["available"], b["counts"]["active"], b["counts"]["overdue"]
    if n_av != len(b["available"]):
        fail(path, f"顶细条可借 {n_av} ≠ 可借栏 {len(b['available'])}")
    if n_ac != len(b["active"]):
        fail(path, f"顶细条在借 {n_ac} ≠ 在借栏 {len(b['active'])}")
    if n_od != len(b["overdue"]):
        fail(path, f"顶细条逾期 {n_od} ≠ 逾期栏 {len(b['overdue'])}")
    if len(ids(b["available"])) != len(set(ids(b["available"]))):
        fail(path, "可借栏出现重复物品行")


# ---------- 种子 ----------

def test_seed_dirty_and_onloan_samples_visible(client):
    """种子脏数据-无主 与 已外借样例 必须保持可见（库内事实源 + 可查询入口）。"""
    # 从库内事实源核对，避免分栏随资格链藏行时误判种子被清。
    orphan = item_by_title("脏数据-无主")
    onloan = item_by_title("已外借样例")
    if orphan is None:
        fail(P_ITEMS, "种子样例「脏数据-无主」在库内丢失")
    if onloan is None:
        fail(P_ITEMS, "种子样例「已外借样例」在库内丢失")
    if orphan["owner"] or orphan["data_quality"] != "dirty" or orphan["status"] != "available":
        fail(P_ITEMS, f"无主脏种属性被改动：{orphan}")
    if onloan["status"] != "on_loan":
        fail(P_ITEMS, "已外借样例必须仍是 on_loan")

    # 已外借样例任何口径下都应在物主栏可见；无主物按资格链口径另测。
    rows = client.get(P_ITEMS).json()
    if "已外借样例" not in [r["title"] for r in rows]:
        fail(P_ITEMS, "已外借样例在物主栏不可见")


# ---------- 已外借再借：五个面同时核对 ----------

def test_relend_on_loan_item_rejected_everywhere(client):
    iid = 4  # 种子「已外借样例」
    before = client.get(P_BOARD).json()
    assert_strip_matches_panes(P_BOARD, before)
    if iid in ids(before["available"]):
        fail(p_lend(iid), "前置：已外借物本不该出现在可借栏")

    r = client.post(p_lend(iid), json={"borrower": "邻居乙", "due_date": "2026-12-31"})
    expect_failed(p_lend(iid), r)  # 1) 回包失败

    loans = client.get(P_LOANS).json()
    item_active = [l for l in loans["active"] + loans["overdue"] if l["item_id"] == iid]
    if len(item_active) != 1:
        fail(p_lend(iid), f"loans 不得新增第二笔 active，实有 {len(item_active)} 笔：{item_active}")  # 2)
    if active_loans_of(iid) != 1:
        fail(p_lend(iid), f"库内必须仍仅一笔 active，实有 {active_loans_of(iid)} 笔")  # 5)

    after = client.get(P_BOARD).json()
    assert_strip_matches_panes(P_BOARD, after)
    if iid in ids(after["available"]):
        fail(P_BOARD, "拒借后该物仍不得进入可借栏")  # 3)
    if after["counts"]["active"] != before["counts"]["active"]:
        fail(P_BOARD, f"顶细条在借数被改变：{before['counts']['active']} → {after['counts']['active']}")  # 4)
    if after["counts"]["overdue"] != before["counts"]["overdue"]:
        fail(P_BOARD, f"顶细条逾期数被改变：{before['counts']['overdue']} → {after['counts']['overdue']}")

    c = db()
    st = c.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()["status"]
    c.close()
    if st != "on_loan":
        fail(p_lend(iid), f"物品状态被翻动：{st}")


# ---------- 空 title / 缺 owner 上架：三个面同时核对 ----------

@pytest.mark.parametrize("body", [
    {"title": "", "owner": "老周"},
    {"title": "   ", "owner": "老周"},
    {"title": "梯子", "owner": ""},
    {"title": "梯子", "owner": "   "},
    {"owner": "老周"},               # 缺 title
    {"title": "梯子"},               # 缺 owner
])
def test_create_without_title_or_owner_rejected(client, body):
    n0 = count_items()
    b0 = client.get(P_BOARD).json()
    owners0 = client.get(P_ITEMS).json()

    r = client.post(P_ITEMS, json=body)
    expect_status(P_ITEMS, r, 422)  # 1) 回包失败

    if count_items() != n0:
        fail(P_ITEMS, "items 行数增加，非法上架落库了")  # 2)
    owners1 = client.get(P_ITEMS).json()
    if len(owners1) != len(owners0):
        fail(P_ITEMS, "物主栏出现幽灵行（行数变化）")  # 3)
    ghost_title = body.get("title", "").strip()
    if ghost_title and any(x["title"] == ghost_title for x in owners1):
        fail(P_ITEMS, f"物主栏出现被拒物品的幽灵行：{ghost_title}")
    b1 = client.get(P_BOARD).json()
    if b1["counts"]["available"] != b0["counts"]["available"]:
        fail(P_BOARD, "顶细条可借数被非法上架改变")


# ---------- 已 returned 再还：三个面同时核对 ----------

def test_return_already_returned_does_not_readd(client):
    lid = client.post(p_lend(1), json={"borrower": "邻居丙", "due_date": "2026-12-31"}).json()["loan_id"]
    expect_status(p_return(lid), client.post(p_return(lid)), 200)

    b_ok = client.get(P_BOARD).json()
    if 1 not in ids(b_ok["available"]):
        fail(P_BOARD, "正常归还后应回到可借栏")

    r = client.post(p_return(lid))
    expect_status(p_return(lid), r, 400)  # 1) 回包失败

    loans = client.get(P_LOANS).json()
    returned_hits = [l for l in loans["returned"] if l["id"] == lid]
    if len(returned_hits) != 1:
        fail(p_return(lid), f"该笔必须且只能有一笔 returned，实得 {len(returned_hits)}")
    c = db()
    st = c.execute("SELECT status FROM loans WHERE id=?", (lid,)).fetchone()["status"]
    ist = c.execute("SELECT status FROM items WHERE id=1").fetchone()["status"]
    c.close()
    if st != "returned":
        fail(p_return(lid), f"loan status 被翻动：{st}")  # 2)
    if ist != "available":
        fail(p_return(lid), f"item status 被翻动：{ist}")

    b = client.get(P_BOARD).json()
    assert_strip_matches_panes(P_BOARD, b)
    if ids(b["available"]).count(1) != 1:
        fail(P_BOARD, "失败的再还把物品重复加回可借栏")  # 3)
    if b["counts"]["available"] != b_ok["counts"]["available"]:
        fail(P_BOARD, "失败的再仍改动了顶细条可借数")


# ---------- 无主物：完全按资格链已落地的那套写期望，三处同结论 ----------

def test_ownerless_item_follows_landed_chain_everywhere(client):
    orphan = item_by_title("脏数据-无主")  # 库内事实源，不走会藏行的分栏
    iid = orphan["id"]
    # 期望直接取自资格链，测例不自行决定放行/拒绝。
    admitted = shows_as_available(orphan)

    b = client.get(P_BOARD).json()
    assert_strip_matches_panes(P_BOARD, b)
    in_board = iid in ids(b["available"])
    if in_board != admitted:
        fail(P_BOARD, f"可借栏结论 {in_board} 与资格链 {admitted} 不一致")
    # 顶细条把它算进去的方式：放行则计数含它，拒绝则不含（基于库内全集过链）。
    c = db(); all_rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close()
    chain_av = [i for i in all_rows if shows_as_available(i)]
    if b["counts"]["available"] != len(chain_av):
        fail(P_BOARD, f"顶细条可借数 {b['counts']['available']} 与资格链集合 {len(chain_av)} 不一致")

    # 物主栏与资格链同一结论。
    owners = client.get(P_ITEMS).json()
    in_owners = any(r["id"] == iid for r in owners)
    if in_owners != admitted:
        fail(P_ITEMS, f"物主栏结论 {in_owners} 与资格链 {admitted} 不一致")

    # 借出入口同样引用资格链：放行则可借成，拒绝则确认失败；无论哪种都不得洗白。
    r = client.post(p_lend(iid), json={"borrower": "邻居丁", "due_date": "2026-12-31"})
    if admitted:
        expect_status(p_lend(iid), r, 200)
        row = next(x for x in client.get(P_ITEMS).json() if x["id"] == iid)
        if row["owner"] != "" or row["data_quality"] != "dirty" or row["status"] != "on_loan":
            fail(p_lend(iid), f"借出确认洗白了无主脏数据：{row}")
        if active_loans_of(iid) != 1:
            fail(p_lend(iid), "放行借出后库内应恰有一笔 active")
        b2 = client.get(P_BOARD).json()
        if iid in ids(b2["available"]) or b2["counts"]["active"] != b["counts"]["active"] + 1:
            fail(P_BOARD, "借出后应离开可借栏、顶细条在借数 +1")
    else:
        expect_failed(p_lend(iid), r)
        c = db()
        row = dict(c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())
        n_active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
        c.close()
        if row["status"] != "available" or row["owner"] != "" or row["data_quality"] != "dirty":
            fail(p_lend(iid), f"拒借不得翻动/洗白无主物：{row}")
        if n_active != 0:
            fail(p_lend(iid), "拒借不得产生 active 借阅")


# ---------- 占用档空桩：锁住无主物，且不与直接借出双活 ----------

def test_deposit_stub_locks_ownerless_and_no_double_active(client):
    orphan = item_by_title("脏数据-无主")
    clean = item_by_title("电钻")

    if deposit.OCCUPANCY_UNAVAILABLE is not True:
        fail("app.modules.deposit", "占用档已不再是空桩，本桩位锁测例必须改写为真实占用互斥核对")
    if deposit.occupiable(orphan):
        fail("app.modules.deposit", "空桩阶段无主物不得进入可占用集合")
    if deposit.occupiable(clean):
        fail("app.modules.deposit", "空桩阶段档口未开，任何物品都不应可占用")

    # 无占用表、无占用入口：占用与直接借出不可能双活；活口只有 loans 一个。
    c = db()
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    c.close()
    if tables != {"items", "loans", "settings"}:
        fail("app.modules.deposit", f"出现桩外新表，占用双活锁需补写：{tables}")
    paths = {getattr(rt, "path", "") for rt in client.app.routes}
    if any("occup" in p or "deposit" in p or "hold" in p for p in paths):
        fail("app.modules.deposit", f"占用入口已出现，空桩锁必须升级：{sorted(paths)}")

    # 即使占用档以后挂上，借出确认也不得把无主脏数据洗成 clean（当前先把这道锁坐实）。
    if shows_as_available(orphan):
        client.post(p_lend(orphan["id"]), json={"borrower": "邻居戊", "due_date": "2026-12-31"})
        c = db()
        row = dict(c.execute("SELECT owner, data_quality, status FROM items WHERE id=?",
                             (orphan["id"],)).fetchone())
        c.close()
        if row["owner"] != "" or row["data_quality"] != "dirty":
            fail(p_lend(orphan["id"]), f"借出确认把无主脏数据洗白：{row}")


# ---------- 互斥/逾期既有核对保持可运行（端到端再兜一层） ----------

def test_overdue_seed_still_classified(client):
    b = client.get(P_BOARD).json()
    od = [l for l in b["overdue"] if l["item_id"] == 4]
    if len(od) != 1 or not od[0]["overdue"]:
        fail(P_BOARD, "种子逾期借阅必须仍被归入逾期栏")
    loans = client.get(P_LOANS).json()
    if any(l["item_id"] == 4 for l in loans["active"]):
        fail(P_LOANS, "逾期笔不得同时出现在在借栏")

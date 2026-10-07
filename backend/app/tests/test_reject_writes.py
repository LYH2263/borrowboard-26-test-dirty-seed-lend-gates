"""脏种与非法态的跨入口拒写核对。

每个拒写测例都同时走 HTTP 入口与库内行级入口, 不只打四条 HTTP 就过;
断言失败一律打印路径名。上架 / 借出 / 归还 / 顶细条 / 分栏须同一结论。
"""
import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.engines.borrow_rules import can_lend, can_list
from app.main import app

DIRTY_TITLE = "脏数据-无主"  # 种子脏数据: 无主
ONLOAN_TITLE = "已外借样例"  # 种子样例: 已外借
OCCUPANCY_HINTS = ("occup", "hold", "reserve")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每测例独立一库, 种子(含脏数据)照常落地。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def ok(resp, path, code=200):
    assert resp.status_code == code, f"{path} -> {resp.status_code}: {resp.text}"
    return resp.json()


def rejected(resp, path, code):
    assert resp.status_code == code, f"{path} 应拒写({code}), 实得 {resp.status_code}: {resp.text}"


def row(sql, args=()):
    c = connect(); r = c.execute(sql, args).fetchone(); c.close()
    return r


def rows(sql, args=()):
    c = connect(); r = [dict(x) for x in c.execute(sql, args)]; c.close()
    return r


def bar_matches_columns(board, path="/api/board"):
    """顶细条与分栏同一结论。"""
    for k in ("available", "active", "overdue"):
        assert board["counts"][k] == len(board[k]), \
            f"{path} 顶细条 {k}={board['counts'][k]} 与分栏 {len(board[k])} 条不符"


def test_seed_dirty_samples_stay_visible(client):
    path = "/api/items"
    items = {r["title"]: r for r in ok(client.get(path), "GET " + path)}
    assert DIRTY_TITLE in items, f"GET {path} 种子脏数据 {DIRTY_TITLE} 须保持可见"
    assert ONLOAN_TITLE in items, f"GET {path} 种子样例 {ONLOAN_TITLE} 须保持可见"
    assert items[DIRTY_TITLE]["owner"] == "", f"GET {path} {DIRTY_TITLE} 应保持无主"
    assert items[DIRTY_TITLE]["data_quality"] == "dirty", f"GET {path} {DIRTY_TITLE} 应保持 dirty"
    assert items[ONLOAN_TITLE]["status"] == "on_loan", f"GET {path} {ONLOAN_TITLE} 应保持 on_loan"
    board = ok(client.get("/api/board"), "GET /api/board")
    assert any(l["title"] == ONLOAN_TITLE for l in board["overdue"]), \
        f"GET /api/board 逾期栏须见 {ONLOAN_TITLE}"
    bar_matches_columns(board)


def test_relend_on_loan_rejected_everywhere(client):
    item = row("SELECT * FROM items WHERE title=?", (ONLOAN_TITLE,))
    board0 = ok(client.get("/api/board"), "GET /api/board")
    path = f"/api/items/{item['id']}/lend"
    assert not can_lend(item["status"], 1)["ok"], "资格链引擎入口须拒已外借再借"
    r = client.post(path, json={"borrower": "邻居乙", "due_date": "2027-01-01"})
    rejected(r, "POST " + path, 409)
    # 库内仍一笔 active
    n = row("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item["id"],))["c"]
    assert n == 1, f"POST {path} 被拒后库内 active 借款须仍为 1 笔, 实为 {n}"
    # loans 不增第二笔 active(在借+逾期合算)
    loans = ok(client.get("/api/loans"), "GET /api/loans")
    live = [l for l in loans["active"] + loans["overdue"] if l["item_id"] == item["id"]]
    assert len(live) == 1, f"GET /api/loans 该物在借+逾期须仍为 1 笔, 实为 {len(live)}"
    # 可借栏仍无该物 + 顶细条在借数不变
    board1 = ok(client.get("/api/board"), "GET /api/board")
    assert all(i["id"] != item["id"] for i in board1["available"]), \
        f"GET /api/board 可借栏仍不得出现 {ONLOAN_TITLE}"
    assert board1["counts"] == board0["counts"], \
        f"GET /api/board 顶细条计数不得漂移: {board0['counts']} -> {board1['counts']}"
    bar_matches_columns(board1)


LIST_CASES = [
    ({"title": "", "owner": "新物主"}, "空title"),
    ({"title": "   ", "owner": "新物主"}, "空白title"),
    ({"title": "幽灵物"}, "缺owner字段"),
    ({"title": "幽灵物", "owner": ""}, "空owner"),
    ({"title": "幽灵物", "owner": "   "}, "空白owner"),
]


def test_list_blank_title_or_owner_rejected_everywhere(client):
    path = "/api/items"
    n0 = row("SELECT COUNT(*) c FROM items")["c"]
    for body, label in LIST_CASES:
        assert not can_list(body.get("title"), body.get("owner"))["ok"], f"资格链引擎入口须拒 {label}"
        r = client.post(path, json=body)
        rejected(r, f"POST {path} ({label})", 422)
    # items 行数不增(库内)
    n1 = row("SELECT COUNT(*) c FROM items")["c"]
    assert n1 == n0, f"POST {path} 被拒后 items 行数不得变: {n0} -> {n1}"
    # 物主栏也不出现幽灵行(HTTP 入口复核)
    items = ok(client.get(path), "GET " + path)
    assert len(items) == n0, f"GET {path} 物主栏行数不得变: {n0} -> {len(items)}"
    assert all(r["title"] != "幽灵物" for r in items), f"GET {path} 物主栏不得出现幽灵行"
    assert all(r["owner"] != "新物主" for r in items), f"GET {path} 物主栏不得出现幽灵物主"


def test_return_already_returned_rejected_everywhere(client):
    iid = ok(client.post("/api/items", json={"title": "冲击钻", "owner": "阿珍"}),
             "POST /api/items")["id"]
    lid = ok(client.post(f"/api/items/{iid}/lend", json={"borrower": "邻居", "due_date": "2027-01-01"}),
             f"POST /api/items/{iid}/lend")["loan_id"]
    ok(client.post(f"/api/loans/{lid}/return"), f"POST /api/loans/{lid}/return")
    path = f"/api/loans/{lid}/return"
    r = client.post(path)
    rejected(r, "POST " + path, 400)
    # status 仍 returned(库内)
    st = row("SELECT status FROM loans WHERE id=?", (lid,))["status"]
    assert st == "returned", f"POST {path} 被拒后 loan 状态须仍 returned, 实为 {st}"
    # 可借栏不因为这次失败重复加回
    board = ok(client.get("/api/board"), "GET /api/board")
    hits = [i for i in board["available"] if i["id"] == iid]
    assert len(hits) == 1, f"GET /api/board 可借栏该物须恰好 1 条, 实为 {len(hits)}"
    bar_matches_columns(board)


def test_ownerless_follows_landed_chain(client):
    """无主物进不进可借栏: 期望一律取自已落地的资格链, 测例不另定一套。"""
    item = row("SELECT * FROM items WHERE title=?", (DIRTY_TITLE,))
    active = row("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item["id"],))["c"]
    expect = can_lend(item["status"], active)["ok"]
    board = ok(client.get("/api/board"), "GET /api/board")
    items = ok(client.get("/api/items"), "GET /api/items")
    in_avail = any(i["id"] == item["id"] for i in board["available"])
    in_owners = any(r["id"] == item["id"] for r in items)
    assert in_avail == expect, f"GET /api/board 可借栏对无主物的结论须跟资格链走(期望 {expect})"
    assert in_owners == expect, f"GET /api/items 物主栏对无主物的结论须跟资格链走(期望 {expect})"
    bar_matches_columns(board)  # 顶细条与分栏同一结论, 可借数把不把它算进去随分栏


def _occupancy_surface():
    paths = [r.path for r in app.routes if any(h in r.path.lower() for h in OCCUPANCY_HINTS)]
    tables = [t["name"] for t in rows("SELECT name FROM sqlite_master WHERE type='table'")
              if any(h in t["name"].lower() for h in OCCUPANCY_HINTS)]
    return paths, tables


def test_occupancy_never_launders_ownerless(client):
    """占用档已挂上: 无主物不进可占用集合 / 占用确认不洗白 / 不与直接借出双活;
    仍是空桩: 锁住借出确认不得把无主物洗白。"""
    item = row("SELECT * FROM items WHERE title=?", (DIRTY_TITLE,))
    active = row("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item["id"],))["c"]
    if can_lend(item["status"], active)["ok"]:
        path = f"/api/items/{item['id']}/lend"
        ok(client.post(path, json={"borrower": "邻居丙", "due_date": "2027-01-01"}), "POST " + path)
    item = row("SELECT * FROM items WHERE id=?", (item["id"],))
    assert item["data_quality"] == "dirty", \
        f"借出/占用确认不得把无主脏数据洗成 clean, 实为 {item['data_quality']}"
    paths, tables = _occupancy_surface()
    if not paths and not tables:
        # 空桩: 无占用面可漏, 该物在役形态至多一笔 active 借款
        n = row("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item["id"],))["c"]
        assert n <= 1, f"空桩期该物 active 借款不得超 1 笔, 实为 {n}"
        return
    # 已挂上: 无主物不得进入可占用集合, 占用与直接借出不得双活
    for t in tables:
        for occ in rows(f"SELECT * FROM {t}"):
            assert occ.get("item_id") != item["id"], f"无主物不得进入可占用集合 {t}"
            iid = occ.get("item_id")
            if iid is not None and str(occ.get("status", "")).lower() in ("active", "confirmed", "occupied"):
                n = row("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,))["c"]
                assert n == 0, f"占用与直接借出不得双活: {t} {occ} vs {n} 笔 active 借款"


def test_list_lend_return_views_agree(client):
    """上架→借出→归还: 顶细条与分栏、物主栏、库内同一结论。"""
    iid = ok(client.post("/api/items", json={"title": "露营灯", "owner": "老吴"}),
             "POST /api/items")["id"]
    board = ok(client.get("/api/board"), "GET /api/board")
    assert any(i["id"] == iid for i in board["available"]), "GET /api/board 上架后须进可借栏"
    assert any(r["id"] == iid for r in ok(client.get("/api/items"), "GET /api/items")), \
        "GET /api/items 上架后须进物主栏"
    bar_matches_columns(board)
    lid = ok(client.post(f"/api/items/{iid}/lend", json={"borrower": "邻居", "due_date": "2027-06-01"}),
             f"POST /api/items/{iid}/lend")["loan_id"]
    board = ok(client.get("/api/board"), "GET /api/board")
    assert all(i["id"] != iid for i in board["available"]), "GET /api/board 借出后须出可借栏"
    assert any(l["item_id"] == iid for l in board["active"]), "GET /api/board 借出后须进在借栏"
    loans = ok(client.get("/api/loans"), "GET /api/loans")
    assert any(l["id"] == lid for l in loans["active"]), "GET /api/loans 借出后须见 active"
    assert row("SELECT status FROM items WHERE id=?", (iid,))["status"] == "on_loan", \
        "库内借出后物品须 on_loan"
    bar_matches_columns(board)
    ok(client.post(f"/api/loans/{lid}/return"), f"POST /api/loans/{lid}/return")
    board = ok(client.get("/api/board"), "GET /api/board")
    hits = [i for i in board["available"] if i["id"] == iid]
    assert len(hits) == 1, "GET /api/board 归还后须回可借栏且仅 1 条"
    loans = ok(client.get("/api/loans"), "GET /api/loans")
    assert any(l["id"] == lid for l in loans["returned"]), "GET /api/loans 归还后须见 returned"
    assert row("SELECT status FROM items WHERE id=?", (iid,))["status"] == "available", \
        "库内归还后物品须 available"
    assert row("SELECT status FROM loans WHERE id=?", (lid,))["status"] == "returned", \
        "库内归还后借款须 returned"
    bar_matches_columns(board)

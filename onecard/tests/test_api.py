"""The live demo path: the hero card waits, a human answers, the draft is approved, the card posts, the run continues."""
import json

from fastapi.testclient import TestClient

import serve
from conftest import SEED, finished
from run import hero_card


def test_hero_card_live(world, tmp_path):
    lines = json.loads((world / "truth" / "truth.json").read_text())["lines"]
    line_id, line = next((k, v) for k, v in lines.items() if v.get("hero"))
    hero, day = "C-" + line_id[2:], line["date"]
    assert (hero, day) == hero_card(SEED)
    db = tmp_path / "demo.db"
    finished(world, db, "C", until=day, hold=[hero]).store.db.close()
    client = TestClient(serve.build(SEED, "demo", db=db))

    queue = client.get("/api/queue").json()
    assert [q["card_id"] for q in queue] == [hero]
    item = queue[0]["item"]
    assert "$12.40" in item["question"] and 2 <= len(item["options"]) <= 4
    for o in item["options"]:  # every entry preview balances
        assert sum(ln["debit"] for ln in o["entry"]) == sum(ln["credit"] for ln in o["entry"])
    assert client.post(f"/api/cards/{hero}/resolve", json={"option_id": "a", "reason": " "}).status_code == 422

    write_off = next(o for o in item["options"] if o["treatment"] == "write_off_discount")
    out = client.post(f"/api/cards/{hero}/resolve", json={"option_id": write_off["id"], "reason": "Under $25 is not worth chasing."}).json()
    draft = out["drafts"][0]
    assert draft["code"] == "SHORTPAY-01" and draft["scope"]["customers"] == ["CUST-001"] and draft["backtest"]["conflicts"] == 0
    assert out["card"]["card"]["status"] == "escalated"  # nothing moves until the rule is approved

    done = client.post("/api/policies/approve", json={"decision_id": out["decision"]["decision_id"]}).json()
    assert done["card"]["card"]["status"] == "posted" and [p["code"] for p in done["live"]] == ["SHORTPAY-01"]
    net = {}
    for ln in done["card"]["entries"][0]["lines"]:
        net[ln["account"]] = net.get(ln["account"], 0) + ln["debit"] - ln["credit"]
    assert net == {"cash": 742760, "ar": -747500, "bank_fees": 3500, "sales_discounts": 1240}
    assert client.post(f"/api/cards/{hero}/resolve", json={"option_id": "a", "reason": "x"}).status_code == 409

    months = client.post("/api/continue", json={}).json()["months"]
    assert [m["wrong_postings"] for m in months.values()] == [0, 0, 0] and all(m["locked"] for m in months.values())
    for path in ("/api/run", "/api/cards?period=2026-02", f"/api/cards/{hero}", "/api/policies", "/api/close", "/api/findings",
                 "/api/accounts/ar?period=2026-01", "/", "/app.js"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/accounts/not_an_account").status_code == 404

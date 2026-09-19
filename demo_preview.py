"""Launch an isolated, explicitly illustrative policy-preview demo. No models.

    uv run python demo_preview.py --port 8791 --data-dir work/policy-demo

The two clients use hand-authored demo policies. This is a UI rehearsal, not an
induction result or evaluation. Existing demo data is kept so approval and undo
remain visible across restarts. Use a new directory for a fresh rehearsal.
"""
import argparse
from pathlib import Path

import uvicorn
from shadow import db, playbook


def seed(root):
    db.DATA, db.RUNS = root / "data", root / "runs"
    for client, name, role in [("A", "Lucky Quarter Holdings", "owner"), ("B", "Meridian AI", "controller")]:
        if db.db_path(client).exists():
            continue
        con = db.connect(client)
        con.execute("INSERT INTO client VALUES (?,?,?,?,?)", (client, name, "Illustrative policy-preview rehearsal", '{"6990":"Small differences","cash":"Cash"}', 3))
        con.execute("INSERT INTO user VALUES (?,?,?,1)", ("senior", "Demo reviewer", role))
        con.execute("INSERT INTO user VALUES ('junior','Demo clerk','bookkeeper',0)")
        for i, delta in enumerate((8, 12.4, 25)):
            ref = f"INV-{i}"
            con.execute("INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)", (f"{client}-BL-{i}", "2026-03", "2026-03-10", 4800-delta, "Customer receipt " + ref, "Demo customer", ref))
            con.execute("INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)", (f"{client}-LE-{i}", "2026-03", "2026-03-09", "2026-03-09", "cash", 4800, "Expected customer receipt", "Demo customer", ref, ref))
        for i, amount in enumerate((-900, -1100)):
            con.execute("INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)", (f"{client}-BL-V{i}", "2026-03", f"2026-03-{11+i}", amount, "Vendor payment", "Demo vendor", f"V{i}"))
            con.execute("INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)", (f"{client}-LE-V{i}", "2026-03", f"2026-03-{11+i}", f"2026-03-{11+i}", "cash", amount, "Vendor payment", "Demo vendor", f"V{i}", ""))
        con.execute("INSERT INTO document VALUES (?,?,?,?,?,?,?)", (f"{client}-DOC-1", "email", "2026-03-08", "billing@vendor.example", "New bank details", "Our bank account has changed. Please update the payment details.", '{"party":"Demo vendor"}'))
        con.commit()
        con.close()
        r = {"id": f"{client}-R-001", "status": "approved", "executable": True,
             "text": "Demo policy: customer shortfalls up to $10.00 go to small differences.",
             "when": {"direction": "in", "candidate": {"by": "ref"}, "diff_abs_max": 10},
             "then": {"action": "match_adjust", "account": "6990"}, "open_question": None,
             "backtest": {"support": 0, "conflicts": 0}, "precedent_ids": [],
             "bands": {"diff_abs_max": {"lo": 10, "hi": 20, "side": "upper", "source": "demo", "n_known": 3,
                                          "lo_precedent": None, "hi_precedent": None}},
             "origin": "hand-authored illustrative fixture"}
        if client == "B":
            r.update(text="Demo policy: every customer shortfall goes to the controller.", bands={},
                     when={"direction": "in", "candidate": {"by": "ref"}, "diff_min": 0.01},
                     then={"action": "escalate", "escalate_to": role})
        carry = {"id": f"{client}-R-002", "status": "approved", "executable": True,
                 "text": "Demo policy: leave unmatched ledger entries outstanding.", "when": {"item_kind": "ledger"},
                 "then": {"action": "carry_forward"}, "open_question": None}
        playbook.save(client, "preview_demo", {"trained_before": "2026-03", "rules": [r, carry], "synthetic_demo": True},
                      {"type": "induction", "note": "Hand-authored UI fixture. Not an induction or benchmark result."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("work/policy-demo"))
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    seed(args.data_dir.resolve())
    print(f"Illustrative demo: http://127.0.0.1:{args.port}/playbook.html?track=preview_demo&period=2026-03", flush=True)
    uvicorn.run("shadow.server:app", host="127.0.0.1", port=args.port)

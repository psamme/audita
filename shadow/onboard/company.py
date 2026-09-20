"""Create the company the agent will work for.

The simulator creates its two clients through `sim.world.World`, whose constructor deletes any
existing database and needs module constants from the client modules. Neither is usable here, and
agent code may not import `sim` at all, so this writes the same two tables directly.

One company per install. The id is stored in data/company.json so the server knows which client
to register on start-up.
"""
import json
import re

from shadow import db

CONFIG = db.DATA / "company.json"
ID_RE = re.compile(r"^[A-Za-z0-9_]{1,8}$")
DEFAULT_CLOSE_DAYS = 5


def valid_id(client: str) -> bool:
    """`playbook.pb_dir` enforces this, but `db.db_path` does not and is reached first, so an
    unchecked id would be a write outside data/."""
    return bool(client and ID_RE.match(client))


def configured() -> str | None:
    if not CONFIG.exists():
        return None
    try:
        return json.loads(CONFIG.read_text()).get("client")
    except json.JSONDecodeError:
        return None


def exists(client: str) -> bool:
    return valid_id(client) and db.db_path(client).exists()


def create(client: str, name: str, blurb: str, chart: dict, users: list[dict],
           close_days: int = DEFAULT_CLOSE_DAYS, reconciler: str | None = None) -> dict:
    """Materialise data/<client>/client.db and write the client row and the roster.

    users: [{id, name, role, senior}]. At least one senior is required: the senior roles are the
    entire escalation vocabulary, and with none of them the investigator rejects every escalation
    it tries to make.
    """
    if client in {"A", "B"}:
        raise ValueError("That workspace ID is reserved for the sample companies.")
    if not valid_id(client):
        raise ValueError("company id must be 1 to 8 characters of letters, digits or underscore")
    if not name.strip():
        raise ValueError("company name is required")
    if not chart:
        raise ValueError("a chart of accounts is required: rules name accounts, and the induction "
                         "prompt is given the chart verbatim")
    if not any(u.get("senior") for u in users):
        raise ValueError("at least one person must be marked senior: escalations are addressed to "
                         "a senior role, and with none the agent cannot escalate anything")
    seen = set()
    for u in users:
        if not u.get("id") or not u.get("role"):
            raise ValueError("every person needs an id and a role")
        if u["id"] in seen:
            raise ValueError(f"duplicate person id {u['id']}")
        seen.add(u["id"])
    if reconciler and reconciler not in seen:
        raise ValueError(f"default reconciler {reconciler} is not in the roster")

    con = db.connect(client, readonly=False)      # creates the directory and runs the schema
    try:
        con.execute("INSERT OR REPLACE INTO client VALUES (?,?,?,?,?)",
                    (client, name.strip(), blurb.strip(), json.dumps(chart), int(close_days)))
        for u in users:
            con.execute("INSERT OR REPLACE INTO user VALUES (?,?,?,?)",
                        (u["id"], u.get("name") or u["id"], u["role"], 1 if u.get("senior") else 0))
        con.commit()
    finally:
        con.close()

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() and configured() == client else {}
    CONFIG.write_text(json.dumps(cfg | {"client": client, "reconciler": reconciler}, indent=1))
    register()
    return summary(client)


def actors(con) -> set[str]:
    """Everyone the imported history says did something. Seniority is read off the user row a
    trail entry points at, so dropping one of these quietly changes what the agent can learn."""
    seen: set[str] = set()
    for sql, col in (("SELECT DISTINCT reconciled_by c FROM reconcile_link", "c"),
                     ("SELECT DISTINCT posted_by c FROM journal_entry", "c"),
                     ("SELECT DISTINCT approver c FROM approval", "c")):
        seen |= {r[col] for r in db.q(con, sql) if r[col]}
    return seen


def set_users(client: str, users: list[dict], reconciler: str | None = None,
              force: bool = False) -> dict:
    """Replace the roster. Kept separate so people can be added after the first import."""
    if not exists(client):
        raise ValueError("no such company")
    if not any(u.get("senior") for u in users):
        raise ValueError("at least one person must be marked senior")
    keeping = {u["id"] for u in users}
    con = db.connect(client, readonly=True)
    try:
        dropped = actors(con) - keeping
    finally:
        con.close()
    if dropped and not force:
        raise ValueError(
            f"{', '.join(sorted(dropped))} appear in your imported history but are not on this "
            "list. Removing them loses the record of who did that work, and with it any seniority "
            "the agent learned from it. Keep them, or save again to remove them anyway.")
    con = db.connect(client, readonly=False)
    try:
        con.execute("DELETE FROM user")
        for u in users:
            con.execute("INSERT OR REPLACE INTO user VALUES (?,?,?,?)",
                        (u["id"], u.get("name") or u["id"], u["role"], 1 if u.get("senior") else 0))
        con.commit()
    finally:
        con.close()
    if reconciler is not None:
        cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {"client": client}
        cfg["reconciler"] = reconciler
        CONFIG.write_text(json.dumps(cfg, indent=1))
    return summary(client)


COUNTED = ("bank_line", "ledger_entry", "reconcile_link", "journal_entry",
           "approval", "invoice", "document")


def summary(client: str) -> dict:
    """Everything the setup screen needs to show what is configured and what has been imported."""
    if not exists(client):
        return {"client": client, "created": False}
    con = db.connect(client, readonly=True)
    try:
        rows = db.q(con, "SELECT * FROM client")
        info = rows[0] if rows else {}
        users = db.q(con, "SELECT * FROM user ORDER BY senior DESC, id")
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTED}
        periods = [r["period"] for r in db.q(
            con, "SELECT DISTINCT period FROM bank_line ORDER BY 1")]
    finally:
        con.close()
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    from shadow import playbook as pbmod
    versions = pbmod.versions(client, "main") if (db.DATA / client / "playbook" / "main").exists() else []
    from shadow.onboard import boundary
    before = boundary.cutoff(client)
    return {"training_before": before, "incoming_periods": [p for p in periods if before and p >= before], "client": client, "created": True, "name": info.get("name"),
            "blurb": info.get("blurb"), "chart": info.get("chart") or {},
            "close_days": info.get("close_days"), "reconciler": cfg.get("reconciler"),
            "users": users, "counts": counts, "periods": periods,
            "playbook_versions": versions, "has_playbook": bool(versions),
            "seniors": [u["role"] for u in users if u["senior"]]}


def register(app=None) -> str | None:
    """Teach the existing server about this company.

    `server.clients()` and `server._con()` both read the module-level CLIENTS tuple at call time,
    so rebinding it here is enough for the queue, correction, playbook, band, conflict and record
    routes to serve a real company without touching that file.
    """
    from shadow import server
    client = configured()
    if client and exists(client):
        server.CLIENTS = tuple(dict.fromkeys(tuple(server.CLIENTS) + (client,)))
    return client


def parse_chart(text: str) -> dict:
    """Accept a pasted chart of accounts as `code,name` or `code<tab>name` per line."""
    chart: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in ("\t", ",", "  ", " "):
            if sep in line:
                code, name = line.split(sep, 1)
                code, name = code.strip().strip('"'), name.strip().strip('"')
                if code and code.lower() not in ("account", "code", "account_code"):
                    chart[code] = name
                break
    return chart

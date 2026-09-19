"""Stable record ids across re-imports.

Ids are not cosmetic. `precedent_ids`, `supported_by`, `conflict_ids`, every band's
`lo_precedent`, the correction log, each run's resolutions and every evidence fingerprint all
refer to records by id, as a value. If a second upload of the same file renumbers a bank line,
the playbook that cited it is orphaned.

So each record gets a natural key derived from its content (or from the source system's own id
when the file has one), and that key maps to an id for good. The map lives beside the client
because the database schema is not ours to extend.
"""
import json
import hashlib
import re
import threading

from shadow import db

KINDS = {"bank_line": "BL", "ledger_entry": "LE", "reconcile_link": "LNK",
         "journal_entry": "JE", "approval": "APR", "invoice": "INV", "document": "DOC"}
_LOCK = threading.Lock()
_WS = re.compile(r"\s+")


def keys_path(client: str):
    p = db.DATA / client / "import"
    p.mkdir(parents=True, exist_ok=True)
    return p / "keys.json"


def load(client: str) -> dict:
    path = keys_path(client)
    if not path.exists():
        return {"seq": {}, "by_key": {}}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"seq": {}, "by_key": {}}


def save(client: str, state: dict) -> None:
    keys_path(client).write_text(json.dumps(state, indent=1))


def norm(s) -> str:
    return _WS.sub(" ", str(s or "").strip().upper())


def natural_key(kind: str, fields: dict, source_id: str | None = None) -> str:
    """The source system's own id when we have one, else a hash of the identifying content."""
    if source_id:
        return f"src:{norm(source_id)}"
    parts = [str(fields.get(k, "")) for k in ("date", "amount", "description", "memo", "ref",
                                              "account", "bank_ref", "ledger_ref")]
    return "h:" + hashlib.sha1("|".join(map(norm, parts)).encode()).hexdigest()[:16]


class Allocator:
    """Hands out ids for one import, remembering what it saw so duplicates inside the same file
    get distinct ids rather than colliding."""

    def __init__(self, client: str):
        self.client = client
        self.state = load(client)
        self.state.setdefault("seq", {})
        self.state.setdefault("by_key", {})
        self._used_this_run: dict[str, int] = {}
        self.reused = 0
        self.fresh = 0

    def allocate(self, kind: str, fields: dict, source_id: str | None = None,
                 prefix: str | None = None) -> tuple[str, str]:
        """Returns (record_id, natural_key). Same key in a later upload returns the same id."""
        code = prefix or KINDS[kind]
        key = natural_key(kind, fields, source_id)
        n = self._used_this_run.get(key, 0)
        self._used_this_run[key] = n + 1
        if n:                                  # genuine duplicate rows within one file
            key = f"{key}|#{n + 1}"
        table = self.state["by_key"].setdefault(code, {})
        if key in table:
            self.reused += 1
            return table[key], key
        nxt = self.state["seq"].get(code, 0) + 1
        self.state["seq"][code] = nxt
        rid = f"{self.client}-{code}-{nxt:05d}"
        table[key] = rid
        self.fresh += 1
        return rid, key

    def lookup(self, kind: str, ref: str) -> str | None:
        """Resolve a reference from another file: our own id, the source id, or a natural key."""
        code = KINDS[kind]
        table = self.state["by_key"].get(code, {})
        if not ref:
            return None
        if str(ref).startswith(f"{self.client}-{code}-"):
            return str(ref)
        return table.get(f"src:{norm(ref)}") or table.get(str(ref))

    def commit(self) -> None:
        with _LOCK:
            current = load(self.client)
            current.setdefault("seq", {})
            current.setdefault("by_key", {})
            for code, seq in self.state["seq"].items():
                current["seq"][code] = max(current["seq"].get(code, 0), seq)
            for code, table in self.state["by_key"].items():
                current["by_key"].setdefault(code, {}).update(table)
            save(self.client, current)

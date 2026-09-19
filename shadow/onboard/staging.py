"""Hold an uploaded file, work out how to read it, and describe its columns.

Finance exports are messy in boring ways: byte-order marks, cp1252 smart quotes, semicolon
delimiters from European Excel, a title row above the header. Everything here is deterministic;
the model is only asked to map columns, and only after we can already read the file.
"""
import csv
import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timezone

from shadow import db
from shadow.onboard import contract

MAX_BYTES = 64 * 1024 * 1024
SAMPLE_ROWS = 20


def upload_dir(client: str):
    p = db.DATA / client / "import" / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def decode(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8 (with replacements)"


def sniff_delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: head.count(d) for d in ",;\t|"}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def find_header(rows: list[list[str]]) -> int:
    """Skip a title or export-stamp row above the real header: the header is the first row whose
    cells are mostly non-empty, distinct and non-numeric."""
    for i, row in enumerate(rows[:10]):
        cells = [c.strip() for c in row]
        filled = [c for c in cells if c]
        if len(filled) < 2 or len(set(filled)) != len(filled):
            continue
        numeric = sum(bool(re.fullmatch(r"-?[\d.,$()]+", c)) for c in filled)
        if numeric <= len(filled) // 3:
            return i
    return 0


def read(raw: bytes) -> dict:
    text, encoding = decode(raw)
    delim = sniff_delimiter(text)
    all_rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    all_rows = [r for r in all_rows if any(c.strip() for c in r)]
    if not all_rows:
        raise ValueError("the file has no rows")
    h = find_header(all_rows)
    header = [c.strip() for c in all_rows[h]]
    seen: dict[str, int] = {}
    for i, name in enumerate(header):                 # duplicate headers are common
        if not name:
            header[i] = f"column_{i + 1}"
        elif name in seen:
            seen[name] += 1
            header[i] = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
    rows = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in all_rows[h + 1:]]
    return {"header": header, "rows": rows, "encoding": encoding, "delimiter": delim,
            "header_row": h, "skipped_preamble": h}


def profile(header: list[str], rows: list[dict]) -> list[dict]:
    """Per column: a few real values and what they look like. This is what the model is shown."""
    out = []
    for col in header:
        vals = [r.get(col, "") for r in rows]
        filled = [v for v in vals if str(v).strip()]
        samples = []
        for v in filled:
            if v not in samples:
                samples.append(v)
            if len(samples) >= 5:
                break
        out.append({"name": col, "samples": samples,
                    "nulls": len(vals) - len(filled), "distinct": len(set(map(str, filled))),
                    "inferred": _infer(filled)})
    return out


def _infer(values: list) -> str:
    if not values:
        return "empty"
    probe = values[:40]
    ok = lambda fn: sum(_try(fn, v) for v in probe) >= max(1, int(len(probe) * 0.8))
    if ok(lambda v: contract.to_date(v, dayfirst=False)):
        return "date"
    if ok(contract.to_amount):
        return "amount"
    if all(str(v).strip().upper() in ("DR", "CR", "D", "C", "DEBIT", "CREDIT") for v in probe):
        return "dr_cr_flag"
    return "text"


def _try(fn, v) -> bool:
    try:
        fn(v)
        return True
    except Exception:
        return False


def store(client: str, role: str, filename: str, raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if role not in contract.SPECS:
        raise ValueError(f"unknown upload kind {role}")
    sha = hashlib.sha256(raw).hexdigest()
    parsed = read(raw)
    prior = _find_by_sha(client, role, sha)
    upload_id = prior["upload_id"] if prior else f"up_{uuid.uuid4().hex[:10]}"
    rec = {"upload_id": upload_id, "client": client, "role": role, "filename": filename,
           "sha256": sha, "rows": len(parsed["rows"]), "encoding": parsed["encoding"],
           "delimiter": parsed["delimiter"], "skipped_preamble": parsed["skipped_preamble"],
           "header": parsed["header"], "columns": profile(parsed["header"], parsed["rows"]),
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "duplicate_of": prior["upload_id"] if prior else None}
    d = upload_dir(client)
    (d / f"{upload_id}.json").write_text(json.dumps(rec, indent=1))
    (d / f"{upload_id}.csv").write_bytes(raw)
    return rec


def _find_by_sha(client: str, role: str, sha: str) -> dict | None:
    for path in upload_dir(client).glob("up_*.json"):
        try:
            rec = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if rec.get("sha256") == sha and rec.get("role") == role:
            return rec
    return None


def load(client: str, upload_id: str) -> dict:
    path = upload_dir(client) / f"{upload_id}.json"
    if not path.exists():
        raise ValueError("no such upload")
    rec = json.loads(path.read_text())
    rec["parsed"] = read((upload_dir(client) / f"{upload_id}.csv").read_bytes())
    return rec

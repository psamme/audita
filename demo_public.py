"""Public HackMIT API gateway over isolated copies of the prepared sample workspace.

uv run uvicorn demo_public:app --host 127.0.0.1 --port 8796
Only synthetic demo data is reachable. Each visitor owns a cookie-scoped sandbox;
local company uploads, API-key setup, induction and arbitrary model jobs are blocked.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from demo_stage import prepare
from shadow import db, preview, jev

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'work' / 'public-live'
SEED = BASE / 'seed'
SESSIONS = BASE / 'sessions'
SESSIONS.mkdir(parents=True, exist_ok=True)
prepare(SEED, 'stage_learned')
# The existing key is used server-side only, and is never part of a session or deployment bundle.
key_file = ROOT / 'work' / 'judging-final' / '.typesafe-key'
if key_file.exists() and not os.environ.get('TYPESAFE_API_KEY'):
    os.environ['TYPESAFE_API_KEY'] = key_file.read_text().strip()
from shadow.server import app as backend

app = FastAPI(title='Audita live judging API', docs_url=None, redoc_url=None, openapi_url=None)
LOCK = asyncio.Lock()
PREVIEWS = {}
VISITS = {}
TRIAGE_CALLS = {}
COOKIE = 'audita_demo'
READ = re.compile(r'^/api/(stage|clients|runs(?:/[a-zA-Z0-9_-]+(?:/queue|/stale)?)?|record/[AB]/[a-zA-Z0-9_.-]+|playbook/[AB](?:/diff|/questions)?|corrections/[AB]|reopened/[AB]|real/(?:results|playbook|examples)|close/[AB]|audit/[AB](?:/download|/file/[a-zA-Z0-9_-]+)?|metrics|curve|experiment(?:/bank-change)?|jev/status)$')
WRITE = {'/api/playbook/preview-policy', '/api/playbook/preview-band', '/api/playbook/apply-preview', '/api/playbook/answer-band', '/api/retract', '/api/jev/triage'}


def fail(message, code=400):
    return JSONResponse({'detail': message}, status_code=code, headers={'Cache-Control':'no-store'})


@app.middleware('http')
async def sandbox(request: Request, call_next):
    path = request.url.path
    if not ((request.method == 'GET' and READ.fullmatch(path)) or (request.method == 'POST' and path in WRITE)):
        return fail('This public demo supports the sample review queue, policy approval, undo and benchmark. Company uploads and model training are available in the local app.', 404)
    if request.query_params.get('grades'):
        return fail('Grades are not exposed by the public demo.', 403)
    if request.query_params.get('track', 'stage') != 'stage':
        return fail('Use the sample demo workspace.', 400)
    body = None
    if request.method == 'POST':
        raw = await request.body()
        if len(raw) > 32768:
            return fail('Request is too large.', 413)
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            return fail('Expected a JSON request.')
        if not isinstance(body, dict) or body.get('client', 'A') not in ('A', 'B') or body.get('track', 'stage') != 'stage':
            return fail('Use one of the sample companies in this demo.')
        if body.get('period', '2026-05') != '2026-05':
            return fail('The live sample period is May 2026.')
    sid = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[a-f0-9]{48}', sid) or not (SESSIONS / sid / 'stage.json').exists():
        sid = secrets.token_hex(24)
    async with LOCK:
        now = time.time()
        times = [t for t in VISITS.get(sid, []) if now-t < 60]
        if len(times) >= 90:
            return fail('Please wait a moment before making more requests.', 429)
        VISITS[sid] = times + [now]
        root = SESSIONS / sid
        if not root.exists():
            if sum(1 for _ in SESSIONS.iterdir()) >= 300:
                return fail('The demo is at capacity. Please use the recorded fallback.', 503)
            shutil.copytree(SEED, root)
        old_data, old_runs, old_previews = db.DATA, db.RUNS, preview.PREVIEWS
        db.DATA, db.RUNS = root / 'data', root / 'runs'
        preview.PREVIEWS = PREVIEWS.setdefault(sid, {})
        try:
            if path == '/api/jev/triage':
                if TRIAGE_CALLS.get(sid,0) >= 3:
                    response = fail('The public demo allows three Jev suggestions per visitor. Policy approval and undo remain available.',429)
                else:
                    TRIAGE_CALLS[sid] = TRIAGE_CALLS.get(sid,0) + 1
                    try:
                        from shadow.server import JevReview
                        inp = JevReview(**body)
                        result = await asyncio.to_thread(jev.triage, inp.client, inp.item_ids, inp.note)
                        response = JSONResponse(result)
                    except Exception as exc:
                        response = fail(str(exc) if isinstance(exc,(ValueError,jev.Unavailable)) else 'The reviewer suggestion is unavailable. Approval and undo still work.',503)
            elif path == '/api/jev/status':
                response = JSONResponse(jev.status() | {'public_demo':True})
            else:
                response = await call_next(request)
            response.set_cookie(COOKIE, sid, httponly=True, secure=True, samesite='lax', max_age=21600)
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['CDN-Cache-Control'] = 'no-store'
            return response
        finally:
            db.DATA, db.RUNS, preview.PREVIEWS = old_data, old_runs, old_previews

app.mount('/', backend)

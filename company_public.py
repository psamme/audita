"""One isolated process per public company workspace, including background jobs."""
import asyncio
import fcntl
import os
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse
from shadow import db, llm

root = Path(os.environ['AUDITA_COMPANY_ROOT']).resolve()
db.DATA, db.RUNS = root / 'data', root / 'runs'
db.DATA.mkdir(parents=True, exist_ok=True)
db.RUNS.mkdir(parents=True, exist_ok=True)
from shadow.app import app

# Shared across public company workers, including retries. Never touches local runs.
_original = llm._openai_call

def budgeted_call(*args, **kwargs):
    llm._load_env()
    llm._openai()  # A missing key must not consume the demo budget.
    with (root.parent / 'model-call-count').open('a+') as counter:
        fcntl.flock(counter, fcntl.LOCK_EX)
        counter.seek(0)
        used = int(counter.read() or '0')
        if used >= 20:
            raise RuntimeError('The shared public demo AI budget is used up. The prepared review queue still works.')
        counter.seek(0); counter.truncate(); counter.write(str(used + 1)); counter.flush()
    return _original(*args, **kwargs)

llm._openai_call = budgeted_call
_jev_calls = 0

@app.middleware('http')
async def public_company_only(request: Request, call_next):
    global _jev_calls
    path = request.url.path
    if path == '/api/jev/status' and request.method == 'GET':
        from shadow import jev
        return JSONResponse(jev.status() | {'public_demo': True})
    if path == '/api/jev/triage' and request.method == 'POST':
        from shadow import jev
        from shadow.server import JevReview
        if _jev_calls >= 3:
            return JSONResponse({'detail': 'This workspace has used its three Jev suggestions.'}, status_code=429)
        _jev_calls += 1
        try:
            inp = JevReview(**await request.json())
            result = await asyncio.to_thread(jev.triage, inp.client, inp.item_ids, inp.note)
            return JSONResponse(result)
        except Exception:
            return JSONResponse({'detail': 'Jev could not suggest a reviewer. You can still review this case manually.'}, status_code=503)
    read_ok = path.startswith(('/api/onboarding/', '/api/jobs/', '/api/runs/', '/api/playbook/', '/api/record/', '/api/corrections/', '/api/reopened/')) or path in {'/api/clients','/api/runs','/api/jobs'}
    write_ok = path.startswith(('/api/onboarding/', '/api/jobs/', '/api/conflicts/')) or path in {'/api/corrections','/api/retract','/api/playbook/answer','/api/playbook/preview-policy','/api/playbook/preview-band','/api/playbook/apply-preview','/api/playbook/answer-band'}
    if request.query_params.get('grades') or not ((request.method=='GET' and read_ok) or (request.method=='POST' and write_ok)):
        return JSONResponse({'detail':'This endpoint is not part of company onboarding.'},status_code=404)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'private, no-store'
    return response

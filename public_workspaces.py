"""Proxy public company requests to isolated workers, never to the presenter's data."""
import asyncio
import atexit
import os
import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path
import httpx
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'work' / 'public-companies'
WORKERS = {}
LOCK = asyncio.Lock()
COOKIE = 'audita_company'


def stop_workers():
    for proc, _ in WORKERS.values():
        if proc.poll() is None:
            proc.terminate()
atexit.register(stop_workers)


async def proxy(request):
    sid = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[a-f0-9]{48}', sid):
        sid = secrets.token_hex(24)
    raw = await request.body()
    if len(raw) > 8 * 1024 * 1024:
        return JSONResponse({'detail':'Please use a CSV smaller than 8 MB for the public demo.'},status_code=413)
    async with LOCK:
        existing = WORKERS.get(sid)
        if not existing or existing[0].poll() is not None:
            active = sum(proc.poll() is None for proc, _ in WORKERS.values())
            if active >= 8:
                return JSONResponse({'detail':'All public company workspaces are in use. The sample review queue remains available.'},status_code=503)
            workspace = BASE / sid
            workspace.mkdir(parents=True, exist_ok=True)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = os.environ | {'AUDITA_COMPANY_ROOT':str(workspace), 'SHADOW_BACKEND':'openai','SHADOW_MODEL':'gpt-5.2'}
            with (workspace/'server.log').open('ab') as log:
                proc = subprocess.Popen([sys.executable,'-m','uvicorn','company_public:app','--host','127.0.0.1','--port',str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            WORKERS[sid] = (proc, port)
            async with httpx.AsyncClient() as client:
                for _ in range(80):
                    try:
                        ready = await client.get(f'http://127.0.0.1:{port}/api/onboarding/health')
                        if ready.status_code == 200: break
                    except httpx.TransportError:
                        pass
                    if proc.poll() is not None:
                        return JSONResponse({'detail':'Workspace startup failed. Please refresh.'},status_code=503)
                    await asyncio.sleep(.1)
                else:
                    proc.terminate()
                    return JSONResponse({'detail':'Workspace startup timed out. Please try again.'},status_code=503)
        proc, port = WORKERS[sid]
    path = request.url.path.replace('/company-api/', '/api/', 1)
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            upstream = await client.request(request.method, f'http://127.0.0.1:{port}{path}', params=request.query_params,
                headers={'content-type':request.headers.get('content-type','application/json')},content=raw)
        except httpx.TransportError:
            return JSONResponse({'detail':'Company workspace disconnected. Please refresh.'},status_code=503)
    response = Response(upstream.content, status_code=upstream.status_code,
        headers={'Content-Type':upstream.headers.get('content-type','application/json'),'Cache-Control':'private, no-store','CDN-Cache-Control':'no-store'})
    if 'content-disposition' in upstream.headers:
        response.headers['Content-Disposition'] = upstream.headers['content-disposition']
    response.set_cookie(COOKIE,sid,httponly=True,secure=True,samesite='lax',max_age=21600)
    return response

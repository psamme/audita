"""Entry point that serves the demo and the company product from one app.

    uv run uvicorn shadow.app:app --port 8787

`shadow/server.py` is unchanged and still works on its own; this composes it.

Two things have to happen in the right order:

1. The existing app ends by mounting `ui/` at "/". A mount on the root path matches everything,
   and Starlette tries routes in the order they were added, so any router appended afterwards is
   shadowed and every one of its paths comes back as a 404 from the static files. The mounts are
   therefore lifted off, the new routers added, and the mounts put back last.

2. The company is registered with the existing server, which keeps its list of known clients in a
   module-level name that both `/api/clients` and the client lookup read at call time. Rebinding
   it means the review queue, corrections, band answers, conflicts, retraction and record lookups
   all serve a real company without that file changing.
"""
from starlette.routing import Mount

from shadow import server
from shadow.onboard import company, routes as onboard

app = server.app

registered = company.register(app)

_mounts = [r for r in app.router.routes if isinstance(r, Mount)]
for _m in _mounts:
    app.router.routes.remove(_m)

app.include_router(onboard.router)
app.include_router(onboard.jobs_router)

from fastapi.responses import RedirectResponse

@app.get('/')
def company_home():
    return RedirectResponse('/company/index.html?track=main')

app.router.routes.extend(_mounts)          # the catch-all static mount stays last

# Anything registered after this point would be shadowed by that mount. New routes belong on a
# router in shadow/onboard/routes.py, not here.

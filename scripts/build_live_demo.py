"""Package the actual judging UI for Vercel; proxy its API to the isolated live gateway."""
import json
import shutil
from pathlib import Path
root=Path(__file__).resolve().parent.parent
out=root/'work'/'live-vercel'
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(root/'ui', out, dirs_exist_ok=True)
# The API serves synthetic session workspaces, never the general local server.
origin='https://macbook-air.tail88c359.ts.net:8443'
config={'framework':None,
 'redirects':[{'source':'/','destination':'/queue.html?track=stage&present=1','permanent':False}],
 'rewrites':[{'source':'/api/:path*','destination':origin+'/api/:path*'}],
 'headers':[{'source':'/api/:path*','headers':[{'key':'Cache-Control','value':'private, no-store'},{'key':'x-vercel-enable-rewrite-caching','value':'0'}]}]}
(out/'vercel.json').write_text(json.dumps(config,indent=2))
print(out)

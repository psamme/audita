"""Package the actual judging UI for Vercel; proxy its API to the isolated live gateway."""
import json
import shutil
from pathlib import Path
root=Path(__file__).resolve().parent.parent
out=root/'work'/'live-vercel'
out.mkdir(parents=True, exist_ok=True)
shutil.copytree(root/'ui', out, dirs_exist_ok=True)
# Public company API requests use a separate isolated worker.
api=out/'js/api.js'
api.write_text('window.AUDITA_PUBLIC = true;\n'+api.read_text())
# The API serves synthetic session workspaces, never the general local server.
origin='https://macbook-air.tail88c359.ts.net:8443'
config={'framework':None,
 'redirects':[{'source':'/','destination':'/start.html','permanent':False}],
 'rewrites':[{'source':'/company-api/:path*','destination':origin+'/company-api/:path*'},{'source':'/api/:path*','destination':origin+'/api/:path*'}],
 'headers':[{'source':'/api/:path*','headers':[{'key':'Cache-Control','value':'private, no-store'},{'key':'x-vercel-enable-rewrite-caching','value':'0'}]}]}
(out/'vercel.json').write_text(json.dumps(config,indent=2))
print(out)

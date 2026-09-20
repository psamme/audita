"""Package only the recorded demonstration and aggregate benchmark for static hosting.

python3 scripts/build_public_demo.py
# Archived recording package only. Use build_live_demo.py for the production website.

The recording is checked in. Regenerate it with export_stage_replay.py only when
changing the demo sequence. No company uploads, credentials or backend are copied.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / 'work' / 'public-demo'
out.mkdir(parents=True, exist_ok=True)
for source, target in [('ui/recorded-demo.html', 'index.html'),
                       ('ui/recorded-demo.html', 'recorded-demo.html'),
                       ('ui/recorded-demo.json', 'validation.json'),
                       ('benchrec/report_summary.json', 'benchmark-summary.json')]:
    shutil.copyfile(ROOT / source, out / target)
(out / 'vercel.json').write_text(json.dumps({'framework': None, 'headers': [
    {'source': '/(.*)', 'headers': [
        {'key': 'X-Content-Type-Options', 'value': 'nosniff'},
        {'key': 'Referrer-Policy', 'value': 'strict-origin-when-cross-origin'}]}]}, indent=2))
print(out)

"""Inline tokens, components and marble into one publishable page.

    python3 ui/design/build_panel.py <out.html> [--standalone]
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
out = Path(sys.argv[1])
body = (HERE / "panel.body.html").read_text()
for mark, name in (("/*TOKENS*/", "tokens.css"), ("/*COMPONENTS*/", "components.css"), ("/*MARBLE*/", "marble.js")):
    body = body.replace(mark, (HERE / name).read_text())
if "--standalone" in sys.argv:
    body = ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>' + body + "</body></html>")
out.write_text(body)
print(out, len(body))

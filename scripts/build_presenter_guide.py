"""Render the short Markdown presenter guide without external dependencies."""
from pathlib import Path
from html import escape
root=Path(__file__).resolve().parent.parent
blocks=[]
for b in (root/'docs/PRESENTATION.md').read_text().split('\n\n'):
    if b.startswith('### '): blocks.append('<h3>'+escape(b[4:])+'</h3>')
    elif b.startswith('## '): blocks.append('<h2>'+escape(b[3:])+'</h2>')
    elif b.startswith('# '): blocks.append('<h1>'+escape(b[2:])+'</h1>')
    elif b.startswith('- '): blocks.append('<ul>'+''.join('<li>'+escape(x[2:])+'</li>' for x in b.splitlines())+'</ul>')
    else: blocks.append('<p>'+escape(b).replace('\n','<br>')+'</p>')
(root/'ui/presenter.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Audita · Presenter guide</title><style>body{font:17px/1.65 system-ui,sans-serif;margin:32px auto;padding:0 22px;max-width:900px;color:#222;background:#faf9f6}h1{font-size:38px;line-height:1.2}h2{border-top:1px solid #ccc;padding-top:26px;margin-top:38px}h3{margin-top:30px}li{margin:12px 0}a{color:inherit}nav{display:flex;gap:25px;flex-wrap:wrap;position:sticky;top:0;background:#faf9f6;padding:16px 0;border-bottom:1px solid #ccc}@media print{nav{display:none}h2,h3{break-after:avoid}}</style><nav><a href="https://audita-hackmit.vercel.app/">Open live queue</a><a href="recorded-demo.html">Recorded fallback</a><a href="https://audita-hackmit.vercel.app">Public demo</a></nav><main>'''+''.join(blocks)+'</main></html>')

"""The one chart: touchless rate by month, one line per configuration, wrong postings printed under each point.
A dependency-free SVG, so the scoreboard needs nothing installed. Palette slots 1 to 3 of the validated categorical
order (all-pairs CVD and normal-vision checks pass in light and dark); identity is also carried by direct labels and the
table rows, never by colour alone.
"""
PERIODS = [("2026-01", "January"), ("2026-02", "February"), ("2026-03", "March")]
SERIES = [("A", "A  Independent desks", "s1"), ("B", "B  Cards, no memory", "s2"), ("C", "C  Full", "s3")]
STYLE = """
  .bg{fill:#fcfcfb}.ink{fill:#0b0b0b}.ink2{fill:#52514e}.grid{stroke:#e6e5e0;stroke-width:1}
  .s1{stroke:#2a78d6;fill:#2a78d6}.s2{stroke:#eb6834;fill:#eb6834}.s3{stroke:#1baf7a;fill:#1baf7a}
  .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.ring{stroke:#fcfcfb;stroke-width:2}
  text{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:12px}
  .title{font-size:15px;font-weight:600}.num{font-variant-numeric:tabular-nums}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19}.ink{fill:#ffffff}.ink2{fill:#c3c2b7}.grid{stroke:#31312f}.ring{stroke:#1a1a19}
    .s1{stroke:#3987e5;fill:#3987e5}.s2{stroke:#d95926;fill:#d95926}.s3{stroke:#199e70;fill:#199e70}}
"""


def touchless_svg(board: dict, key: str = "touchless_rate") -> str:
    runs = {c: board["configs"][c]["months"] for c, _, _ in SERIES if c in board["configs"]}
    rates = [m[key] for months in runs.values() for m in months.values()]
    lo = min(0.8, int(min(rates, default=0.8) * 20) / 20)  # floor to a 5% step; a rate line need not start at zero
    W, H, L, R, T, B = 920, 470, 200, 240, 64, 300
    x = lambda i: L + 70 + (W - L - R - 110) * i / 2
    y = lambda v: B - (B - T) * (v - lo) / (1 - lo)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="Touchless rate by month for three configurations, with wrong postings under each point">',
           f"<style>{STYLE}</style>", f'<rect class="bg" width="{W}" height="{H}"/>',
           f'<text class="ink title" x="24" y="28">Touchless rate by month</text>',
           f'<text class="ink2" x="24" y="46">Cards closed with no human touch, of all cards. Seed {board["seed"]}, '
           f'model {next(iter(board["configs"].values()))["meta"].get("model", "off")}. Synthetic company, scripted controller.</text>']
    steps = int(round((1 - lo) / 0.05))
    for k in range(steps + 1):
        v = lo + 0.05 * k
        out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        out.append(f'<text class="ink2 num" x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{100 * v:.0f}%</text>')
    for i, (_, name) in enumerate(PERIODS):
        out.append(f'<text class="ink" x="{x(i):.1f}" y="{B + 22}" text-anchor="middle">{name}</text>')
    ends = []
    for c, label, cls in SERIES:
        if c not in runs:
            continue
        pts = [(x(i), y(runs[c][p][key]), runs[c][p]) for i, (p, _) in enumerate(PERIODS) if p in runs[c]]
        out.append(f'<polyline class="line {cls}" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b, _ in pts)}"/>')
        for a, b, m in pts:
            out.append(f'<circle class="{cls} ring" cx="{a:.1f}" cy="{b:.1f}" r="5"><title>{label}: {100 * m[key]:.1f}% touchless, '
                       f'{m["wrong_postings"]} wrong postings, {m["human_decisions"]} human decisions</title></circle>')
        ends.append([pts[-1][1], label, cls, pts[-1][2][key]])
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):  # keep end labels apart; a leader ties each back to its line
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 16)
    for ly, label, cls, v in ends:
        out.append(f'<circle class="{cls}" cx="{W - R + 16}" cy="{ly:.1f}" r="4"/>')
        out.append(f'<text class="ink" x="{W - R + 26}" y="{ly + 4:.1f}"><tspan class="num">{100 * v:.1f}%</tspan>  {label}</text>')
    # the table under the axis, columns under the points: wrong postings, contradictions at close, humans asked
    top = B + 56
    out.append(f'<line class="grid" x1="24" x2="{W - 24}" y1="{top - 18}" y2="{top - 18}"/>')
    for r, (c, label, cls) in enumerate(s for s in SERIES if s[0] in runs):
        yy = top + r * 36
        out.append(f'<circle class="{cls}" cx="30" cy="{yy - 4}" r="4"/><text class="ink" x="42" y="{yy}">{label}</text>')
        for i, (p, _) in enumerate(PERIODS):
            m = runs[c].get(p)
            if m:
                out.append(f'<text class="ink num" x="{x(i):.1f}" y="{yy}" text-anchor="middle">{m["wrong_postings"]} wrong posting{"" if m["wrong_postings"] == 1 else "s"}</text>')
                out.append(f'<text class="ink2 num" x="{x(i):.1f}" y="{yy + 14}" text-anchor="middle">{m["contradictions"]} contradictions, '
                           f'{m["human_decisions"]} asked</text>')
    out.append("</svg>")
    return "\n".join(out)

"""Shadow Onboarding deck, simple version. Big type, marble behind everything, nothing under 32px."""
import json, datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE / "project"
(ROOT / "slides").mkdir(parents=True, exist_ok=True)

G, INK, INK2 = "#F3F2EF", "#0D0D0D", "#46474B"
DK, DKINK, DKINK2 = "#0B0B0C", "#F4F3F0", "#B4B5B9"
POS, NEG = "#1F7A4D", "#B3261E"
SANS = "font-family:'Hanken Grotesk', Arial, sans-serif"
MONO = "font-family:'IBM Plex Mono', 'Courier New', monospace"


def marble(dark=False):
    base, soft, mid, fine = (DK, "#2A2A2D", "#8A8A90", "#D8D8DC") if dark else (G, "#CFCDC8", "#A3A19C", "#77767200"[:7])
    o = 0.8 if dark else 1
    return f"""<svg aria-label="Marble" width="1920" height="1080" viewBox="0 0 1920 1080" style="position:absolute; left:0px; top:0px; width:1920px; height:1080px">
<defs><filter id="a"><feGaussianBlur stdDeviation="46"/></filter><filter id="b"><feGaussianBlur stdDeviation="12"/></filter><filter id="c"><feGaussianBlur stdDeviation="3"/></filter></defs>
<rect width="1920" height="1080" fill="{base}"/>
<g fill="none" stroke-linecap="round" opacity="{o}">
<path d="M-60 170 C 340 60, 640 300, 1040 170 S 1560 -20, 1990 120" stroke="{soft}" stroke-width="130" filter="url(#a)" opacity="0.55"/>
<path d="M-60 1010 C 420 880, 760 1100, 1180 960 S 1640 820, 1990 930" stroke="{soft}" stroke-width="150" filter="url(#a)" opacity="0.55"/>
<path d="M1500 -40 C 1640 260, 1840 420, 1800 700 S 1900 980, 1860 1120" stroke="{soft}" stroke-width="110" filter="url(#a)" opacity="0.5"/>
<path d="M-60 150 C 330 50, 650 290, 1050 160 S 1570 -30, 1990 100" stroke="{mid}" stroke-width="6" filter="url(#b)" opacity="0.5"/>
<path d="M1520 -40 C 1650 250, 1850 430, 1810 690 S 1905 985, 1870 1120" stroke="{mid}" stroke-width="5" filter="url(#b)" opacity="0.5"/>
<path d="M-60 1000 C 430 875, 770 1090, 1190 950 S 1650 815, 1990 915" stroke="{mid}" stroke-width="6" filter="url(#b)" opacity="0.45"/>
<path d="M-60 158 C 335 55, 645 295, 1045 165 S 1565 -25, 1990 110" stroke="{fine}" stroke-width="1.5" filter="url(#c)" opacity="0.55"/>
<path d="M1050 160 C 1240 200, 1420 120, 1540 40" stroke="{fine}" stroke-width="1.2" filter="url(#c)" opacity="0.45"/>
<path d="M1810 690 C 1700 800, 1500 860, 1190 950" stroke="{fine}" stroke-width="1.2" filter="url(#c)" opacity="0.4"/>
</g></svg>"""


slides = []


def sec(id_, inner, notes, dark=False, center=False, gap=56, pad="128px"):
    bg, fg = (DK, DKINK) if dark else (G, INK)
    jc = "center" if center else "flex-start"
    slides.append((id_, f'<section id="{id_}" data-transition="fade" style="background:{bg}; color:{fg}; {SANS}; padding:{pad}; display:flex; flex-direction:column; justify-content:{jc}; gap:{gap}px">{marble(dark)}{inner}<aside>{notes}</aside></section>'))


def eye(t, dark=False):
    return f'<p style="{MONO}; font-size:32px; font-weight:500; letter-spacing:4px; text-transform:uppercase; color:{DKINK2 if dark else INK2}">{t}</p>'


def H(t, size=88, width=1600):
    return f'<h2 style="font-size:{size}px; font-weight:600; line-height:1.06; letter-spacing:-2px; width:{width}px">{t}</h2>'


def col(num, title, body, accent=INK, tsize=56):
    return (f'<div style="flex:1; display:flex; flex-direction:column; gap:20px; border-top:5px solid {accent}; padding:28px 0 0">'
            + (f'<p style="{MONO}; font-size:56px; font-weight:500; color:{accent}">{num}</p>' if num else "")
            + f'<h3 style="font-size:{tsize}px; font-weight:600; line-height:1.1; letter-spacing:-1px">{title}</h3>'
            f'<p style="font-size:36px; line-height:1.35; color:{INK2}">{body}</p></div>')


def big(num, label, color=INK, size=120):
    return (f'<div style="flex:1; display:flex; flex-direction:column; gap:16px; border-top:5px solid {color}; padding:28px 0 0">'
            f'<p style="{MONO}; font-size:{size}px; font-weight:500; line-height:1; letter-spacing:-3px; color:{color}">{num}</p>'
            f'<p style="font-size:38px; line-height:1.3; color:{INK2}">{label}</p></div>')


# 1
sec("cover",
    eye("HackMIT 2026 · Maximor · Office of the CFO") +
    '<h1 style="font-size:176px; font-weight:600; line-height:0.98; letter-spacing:-6px">Shadow<br>Onboarding</h1>'
    f'<p style="font-size:48px; line-height:1.3; color:{INK2}; width:1300px">A reconciliation agent that learns each client&#39;s rules from their own books.</p>',
    "One sentence, then go to the product.", center=True, gap=48)

# 2
sec("problem",
    eye("The problem") + H("Every finance team resolves exceptions differently. Nobody writes it down.", 104, 1560),
    "Maximor told us their pain is that every client is different. The difference is policy and convention, not schema. It lives in how past exceptions were resolved.")

# 3
sec("split",
    eye("Same transaction, two clients") + H("A payment arrives $12.40 short", 88) +
    '<div style="display:flex; gap:64px">'
    f'<div style="flex:1; display:flex; flex-direction:column; gap:16px; border-top:5px solid {INK}; padding:28px 0 0"><p style="font-size:38px; color:{INK2}">Vending roll-up, lenient</p><p style="font-size:112px; font-weight:600; line-height:1; letter-spacing:-3px">Write it off</p></div>'
    f'<div style="flex:1; display:flex; flex-direction:column; gap:16px; border-top:5px solid {INK}; padding:28px 0 0"><p style="font-size:38px; color:{INK2}">AI lab, strict</p><p style="font-size:112px; font-weight:600; line-height:1; letter-spacing:-3px">Investigate</p></div>'
    '</div>'
    f'<p style="font-size:40px; line-height:1.3; color:{INK2}">Same agent, no configuration. Each answer cites that client&#39;s own history.</p>',
    "This is the split screen, the first demo beat. Both clients get the identical ambiguous transaction.")

# 4
sec("idea",
    eye("The idea", True) +
    '<h2 style="font-size:132px; font-weight:600; line-height:1.03; letter-spacing:-4px">It asks before it acts.<br>Then it is right.</h2>'
    f'<p style="font-size:44px; line-height:1.35; color:{DKINK2}; width:1400px">It reads how the finance team resolved past exceptions and writes that client&#39;s playbook. A controller signs the playbook once instead of reviewing every transaction.</p>',
    "The playbook is human readable. Every rule cites the precedents behind it.", dark=True, center=True, gap=48)

# 5
sec("how",
    eye("How it works") + H("Cheapest first, and every step may refuse", 80) +
    '<div style="display:flex; gap:48px">' +
    col("1", "Match", "Plain code. Matches only when the answer is unique. Free.") +
    col("2", "Playbook", "The client&#39;s learned rules, compiled. Free.") +
    col("3", "Investigate", "An agent with tools reads invoices, emails, the ledger.") +
    col("4", "Ask", "A person answers one question. It becomes a rule.") + '</div>',
    "After sign-off the two free steps clear 96% of lines at client A and 88% at client B. The model only sees what is left. Guardrails sit in front of everything: a payment to changed bank details is held however exactly it ties.")

# 6 bands
sec("bands",
    eye("Memory") + H("It knows what it has never seen", 88) +
    '<div style="display:flex; height:180px">'
    f'<div style="flex:3; background:{INK}; display:flex; align-items:center; justify-content:center"><p style="font-size:48px; font-weight:600; color:{DKINK}">Applies</p></div>'
    f'<div style="flex:2; background:#C4C2BD; display:flex; align-items:center; justify-content:center"><p style="font-size:48px; font-weight:600; color:{INK}">Asks a person</p></div>'
    f'<div style="flex:4; background:#FDFCFA; border:3px solid {INK}; display:flex; align-items:center; justify-content:center"><p style="font-size:48px; font-weight:600; color:{INK}">Does not apply</p></div>'
    '</div>'
    f'<p style="font-size:40px; line-height:1.35; color:{INK2}; width:1500px">Every threshold is a measured band. Each end cites the real precedent that set it. In the middle the client has never been seen to act, so the agent does not act either.</p>',
    "Rules are induced from the ERP trail: reconcile links, terse journal memos, approvals, some email. No table of decisions or reasons. Each rule carries a back-tested agreement rate.")

# 7 learning
sec("learning",
    eye("Self-improvement") + H("An answer has to agree with history before it runs", 80) +
    '<div style="display:flex; gap:48px">' +
    col("", "Answer once", "One question, not a review queue. Open items re-run and clear at $0.00.") +
    col("", "Checked first", "Replayed over the client&#39;s history. Under 60% agreement, it does not execute.") +
    col("", "Undo anything", "Retract an answer and see exactly which past resolutions re-open. 0.07 seconds.") + '</div>',
    "The floor exists because of a real bug: a mistranslated controller answer mis-booked every card payout, 33 silent errors on the dev holdout. Juniors cannot widen a limit. A declared policy change is dated and shown as a diff.")

d = json.load(open(HERE / "agent_results.json")) if (HERE / "agent_results.json").exists() else {}

# 8 team
if "team" in d:
    t = d["team"]
    sec("team",
        eye("A finance team, not one agent") + H(t["title"], 80) +
        '<div style="display:flex; gap:48px">' +
        col("", "Preparer", t["preparer"]) + col("", "Approver", t["approver"]) + col("", "Auditor", t["auditor"], accent=POS) + '</div>',
        t["notes"])
    if t.get("stats"):
        sec("audit",
            eye("The auditor&#39;s report") + H(t["stats_title"], 80) +
            '<div style="display:flex; gap:48px">' + "".join(big(n, l, POS if c == "POS" else NEG if c == "NEG" else INK, 96) for n, l, c in t["stats"]) + '</div>',
            t["stats_notes"])

# 9 real
if "real" in d:
    r = d["real"]
    sec("real",
        eye("Real data, labelled by a bank&#39;s own analysts") + H(r["title"], 80) +
        '<div style="display:flex; gap:48px">' + "".join(big(n, l, POS if c == "POS" else INK, r.get("size", 120)) for n, l, c in r["stats"]) + '</div>' +
        (f'<p style="font-size:38px; line-height:1.35; color:{INK2}; width:1500px">{r["line"]}</p>' if r.get("line") else ""),
        r["notes"])

# 10 results
def row(label, a0, a1, b0, b1, last=False):
    cell = lambda v, strong: f'<p style="flex:1; {MONO}; font-size:48px; font-weight:{600 if strong else 400}; color:{INK if strong else INK2}">{v}</p>'
    return (f'<div style="display:flex; gap:32px; align-items:baseline; padding:22px 0; border-top:3px solid {INK}">'
            f'<p style="flex:1.3; font-size:40px">{label}</p>{cell(a0, False)}{cell(a1, True)}{cell(b0, False)}{cell(b1, True)}</div>')

hd = lambda v: f'<p style="flex:1; font-size:32px; font-weight:600; letter-spacing:2px; text-transform:uppercase; color:{INK2}">{v}</p>'
sec("results",
    eye("Our measure of better") + H("Fewer silent errors, a quarter of the cost", 80) +
    '<div style="display:flex; flex-direction:column">'
    f'<div style="display:flex; gap:32px; padding:0 0 16px"><p style="flex:1.3; font-size:32px"></p>{hd("A, model alone")}{hd("A, signed")}{hd("B, model alone")}{hd("B, signed")}</div>' +
    row("Accuracy", "85.7%", "93.6%", "93.4%", "98.9%") +
    row("Silent wrong", "1.4%", "0.7%", "2.2%", "0%") +
    row("Model cost", "$11.99", "$2.67", "$5.72", "$3.03") + '</div>' +
    f'<p style="font-size:36px; line-height:1.35; color:{INK2}; width:1560px">Before sign-off the playbook is not more accurate than a frontier model. It is safer and cheaper. Accuracy arrives after about 15 answers.</p>',
    "Interim April set, 231 items, single runs, agent code cannot open the key. Silent wrong means resolved wrongly with no escalation. As induced: A 87.1% with 0% silent wrong at $8.57; B 87.9%, 1.1%, $4.30, which is 5 points under zero-shot because unsigned rules refuse to execute. Say that out loud.",
    gap=40, pad="112px 128px")

# 11 curve
sec("curve",
    eye("Questions to trust") +
    f'<p style="{MONO}; font-size:220px; font-weight:500; line-height:1; letter-spacing:-8px">31% → 90%</p>'
    f'<p style="font-size:48px; line-height:1.3; width:1500px">of the strict client&#39;s exceptions handled right at zero model cost, after four answers from the controller.</p>'
    f'<p style="font-size:48px; line-height:1.3; font-weight:600; color:{POS}">Zero silent wrong resolutions at all 37 points on the curve.</p>',
    "March development holdout, simulated controller holding the client's written policy. 72% after 3 answers, 93% after 10. Client A is flat at 89 to 91% because the execution floor refused two rules the patcher wrote: the safety worked and cost us the gain.",
    center=True, gap=44)

# 12 ran
sec("ran",
    eye("What is true of this build") + H("Everything here ran, this weekend, on this laptop", 88) +
    '<div style="display:flex; gap:48px">' +
    big("860", "model calls across eight scored runs. Nothing stubbed or recorded.") +
    big("20", "playbook versions per client, every change a visible diff.") +
    big("0", "lines of per-client configuration.") + '</div>',
    "Only if a judge compares entries. Never name another team. The investigator is a real tool loop with many turns per case. Agent code cannot read the simulator truth or the answer key, and a test enforces it.")

# 13 honest
li = lambda t: f'<p style="font-size:44px; line-height:1.3; border-top:3px solid {INK}; padding:24px 0 0">{t}</p>'
sec("honest",
    eye("Disclosures") + H("What is simulated", 88) +
    '<div style="display:flex; flex-direction:column; gap:24px">' +
    li("The two demo clients are synthetic. The real ledger is not.") +
    li("In the experiments, a model plays the controller. On stage, a person does.") +
    li("Single runs. A difference of one to three items is noise.") + '</div>',
    "Volunteer these. Costs are an upper bound: the CLI backend writes a fresh prompt cache per call.")

# 14 close
sec("close",
    '<h2 style="font-size:132px; font-weight:600; line-height:1.03; letter-spacing:-4px">It asks before it acts.<br>Then it is right.</h2>'
    f'<p style="font-size:44px; line-height:1.35; color:{DKINK2}; width:1400px">No per-client configuration. A playbook the controller can read. An auditor that checks the work. Every answer can be undone.</p>',
    "Demo beats: split screen, answer one band question, undo with blast radius, bank change held, close checklist, audit file, real ledger.", dark=True, center=True, gap=48)

ORDER = ["cover", "problem", "split", "idea", "how", "bands", "learning", "team", "audit", "real", "results", "curve", "ran", "honest", "close"]
by = dict(slides)
order = [i for i in ORDER if i in by]
keep = set(order)
removed = []
for f in (ROOT / "slides").glob("*.html"):
    if f.stem not in keep:
        removed.append(f.stem)
    f.unlink()
for i in order:
    (ROOT / "slides" / f"{i}.html").write_text(by[i])
idx = ROOT / "deck.json"
created = json.load(open(idx))["createdOnFiles"]
json.dump({"v": 4, "createdOnFiles": created, "title": "Shadow Onboarding Deck", "order": order, "cover": "cover",
           "sections": {"s1": {"description": "The problem: every client resolves exceptions differently", "start": "cover"},
                        "s2": {"description": "How the steps, the playbook and safe learning work", "start": "how"},
                        "s3": {"description": "What we measured", "start": "real" if "real" in keep else "results"},
                        "s4": {"description": "Disclosures and the close", "start": "honest"}},
           "faces": {"hanken-grotesk": {"family": "Hanken Grotesk", "href": "https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400..700&display=swap"},
                     "ibm-plex-mono": {"family": "IBM Plex Mono", "href": "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap"}},
           "designSystems": []}, open(idx, "w"), indent=1)
files = {f"project/slides/{i}.html": f"project/slides/{i}.html" for i in order}
for r in removed:
    files[f"project/slides/{r}.html"] = None
print(json.dumps(files))

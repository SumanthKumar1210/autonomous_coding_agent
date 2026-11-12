"""Generates docs/system-overview.svg. Run: python docs/gen_system_overview.py docs/system-overview.svg"""
import sys
from html import escape

W, H = 1440, 800
out: list[str] = []
add = out.append

C = {
    "blue":   ("#ddf4ff", "#0969da"),
    "green":  ("#d4f7dc", "#1a7f37"),
    "yellow": ("#fff4cc", "#bf8700"),
    "grey":   ("#f3f4f6", "#8c959f"),
    "purple": ("#fbefff", "#8250df"),
    "white":  ("#ffffff", "#0969da"),
}


def box(x, y, w, h, title, lines=(), color="green", num=None, tag=None, dashed=False, rx=12, tsize=16):
    fill, stroke = C[color]
    d = ' stroke-dasharray="6 4"' if dashed else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2"{d}/>')
    top = y + 34
    if num is not None:
        add(f'<circle cx="{x + 22}" cy="{y + 22}" r="13" fill="{stroke}"/>')
        add(f'<text x="{x + 22}" y="{y + 27}" font-size="13" font-weight="700" fill="#fff" text-anchor="middle">{num}</text>')
        top = y + 62
    add(f'<text x="{x + w/2}" y="{top}" font-size="{tsize}" font-weight="700" fill="#1f2328" text-anchor="middle">{escape(title)}</text>')
    for i, line in enumerate(lines):
        add(f'<text x="{x + w/2}" y="{top + 20 * (i + 1)}" font-size="12.5" fill="#424a53" text-anchor="middle">{escape(line)}</text>')
    if tag:
        tf, ts = C["yellow"] if tag == "NEXT" else C["grey"]
        tw = 8 * len(tag) + 14
        add(f'<rect x="{x + w - tw - 6}" y="{y + 6}" width="{tw}" height="18" rx="9" fill="{tf}" stroke="{ts}"/>')
        add(f'<text x="{x + w - tw/2 - 6}" y="{y + 19}" font-size="10" font-weight="700" fill="#57606a" text-anchor="middle">{tag}</text>')


def arrow(d, label=None, lx=0, ly=0, dashed=False, anchor="middle"):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    add(f'<path d="{d}" fill="none" stroke="#424a53" stroke-width="2"{dash} marker-end="url(#a)"/>')
    if label:
        add(f'<text x="{lx}" y="{ly}" font-size="12" font-style="italic" fill="#57606a" text-anchor="{anchor}">{escape(label)}</text>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    'font-family="Segoe UI, Helvetica, Arial, sans-serif">')
add('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#424a53"/></marker></defs>')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
add('<text x="40" y="44" font-size="24" font-weight="700" fill="#1f2328">AutoForge — System Overview</text>')
add('<text x="40" y="68" font-size="14" fill="#57606a">From ticket to reviewed pull request: AI writes the code, '
    'a human always makes the final call.</text>')

# ---- 1. engineer writes ticket ----
box(40, 150, 200, 130, "Engineer", ["writes a ticket", "GitHub Issue + label"], "purple", num=1)

# ---- AutoForge container ----
add('<rect x="290" y="104" width="760" height="300" rx="18" fill="#f6faff" stroke="#0969da" stroke-width="2.5"/>')
add('<text x="310" y="134" font-size="18" font-weight="800" fill="#0969da">AutoForge</text>')
add('<text x="412" y="134" font-size="13" fill="#57606a">agentic coding pipeline</text>')

steps = [
    ("Understand", ["reads the ticket", "+ repository code"], "green", None, False),
    ("Write code", ["Coder agent", "(LLM)"], "green", None, False),
    ("Safety checks", ["blocks unsafe or", "out-of-scope edits"], "green", None, False),
    ("Review", ["Reviewer agent", "sends fixes back"], "green", None, False),
    ("Verify", ["lint & tests", "in a sandbox"], "grey", "PLANNED", True),
]
xs = [310, 456, 602, 748, 894]
for i, ((title, lines, color, tag, dashed), x) in enumerate(zip(steps, xs)):
    box(x, 172, 132, 132, title, lines, color, num=i + 2, tag=tag, dashed=dashed, tsize=15)
    if i < len(xs) - 1:
        arrow(f"M{x + 132},238 H{xs[i + 1] - 2}")
# review -> write code feedback loop
arrow("M814,172 V152 H522 V170", "fixes (bounded loop)", 668, 147)
# trace banner
add('<rect x="640" y="322" width="386" height="66" rx="10" fill="#ffffff" stroke="#d0d7de"/>')
add('<text x="833" y="343" font-size="13" font-weight="600" fill="#1f2328" text-anchor="middle">'
    'Every run is traceable</text>')
add('<text x="833" y="362" font-size="12" fill="#424a53" text-anchor="middle">'
    'run ID in logs, PR and ticket · token cost per run</text>')
add('<text x="833" y="379" font-size="12" fill="#424a53" text-anchor="middle">'
    'failures reported back on the ticket</text>')

# ---- LLM ----
box(402, 452, 240, 76, "OpenAI LLM", ["structured output (JSON)"], "blue")
arrow("M500,304 V450", "prompt", 492, 430, anchor="end")
arrow("M544,452 V306", "code change", 552, 430, anchor="start")

# ---- 7. pull request, 8. human merge ----
box(1100, 150, 140, 130, "Pull request", ["branch + commit", "+ review outcome"], "green", num=7, tsize=15)
box(1280, 150, 130, 130, "Engineer", ["reviews &", "merges"], "purple", num=8, tsize=15)
add('<rect x="1262" y="296" width="166" height="46" rx="8" fill="#fff8f0" stroke="#d1242f" stroke-width="1.4"/>')
add('<text x="1345" y="315" font-size="12" font-weight="700" fill="#d1242f" text-anchor="middle">Human-in-the-loop</text>')
add('<text x="1345" y="332" font-size="11.5" fill="#57606a" text-anchor="middle">AutoForge can never merge</text>')

# main flow arrows
arrow("M240,215 H288", "label", 264, 207)
arrow("M1050,215 H1098")
arrow("M1240,215 H1278")
# status back to ticket
arrow("M1170,280 V570 H140 V282", "comments the PR link and status label back on the ticket", 655, 562)

# ---- roadmap strip ----
add('<rect x="40" y="606" width="1370" height="160" rx="16" fill="#f6f8fa" stroke="#d0d7de"/>')
add('<text x="60" y="636" font-size="15" font-weight="700" fill="#1f2328">Roadmap — one platform across the ASPICE V-model</text>')
phases = [
    ("Phase 1 · Coding agent", "Software construction (SWE.3)", "In progress", "green"),
    ("Phase 2 · Unit tests & MISRA C", "Unit verification (SWE.4)", "Planned", "grey"),
    ("Phase 3 · Requirements & Polarion", "Requirements + traceability (SYS.2/SWE.1)", "Planned", "grey"),
    ("Phase 4 · HIL/SIL testing", "Integration & system tests (SWE.5-6, SYS.4)", "Planned", "grey"),
]
pw, gap, px0, py, ph = 315, 20, 60, 656, 92
for i, (title, sub, status, color) in enumerate(phases):
    x = px0 + i * (pw + gap)
    fill, stroke = C[color]
    tip = 22
    pts = (f"{x},{py} {x + pw - tip},{py} {x + pw},{py + ph/2} {x + pw - tip},{py + ph} {x},{py + ph}"
           + ("" if i == 0 else f" {x + tip},{py + ph/2}"))
    d = "" if color == "green" else ' stroke-dasharray="6 4"'
    add(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2"{d}/>')
    cx = x + pw / 2 + (0 if i == 0 else 6)
    add(f'<text x="{cx}" y="{py + 32}" font-size="14.5" font-weight="700" fill="#1f2328" text-anchor="middle">{escape(title)}</text>')
    add(f'<text x="{cx}" y="{py + 52}" font-size="12" fill="#424a53" text-anchor="middle">{escape(sub)}</text>')
    add(f'<text x="{cx}" y="{py + 72}" font-size="12" font-weight="700" fill="{stroke}" text-anchor="middle">{status}</text>')

add("</svg>")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote", sys.argv[1])

"""Generates docs/system-design.svg. Run: python docs/gen_system_design.py docs/system-design.svg"""
import sys
from html import escape

W, H = 1540, 1350
out: list[str] = []
add = out.append

STYLE = {
    "built":   ("#d4f7dc", "#1a7f37", ""),
    "next":    ("#fff4cc", "#bf8700", ""),
    "planned": ("#f3f4f6", "#8c959f", "6 4"),
    "ext":     ("#ddf4ff", "#0969da", ""),
    "actor":   ("#fbefff", "#8250df", ""),
}


def block(x, y, w, h, title, lines=(), kind="built", rx=8):
    fill, stroke, dash = STYLE[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{d}/>')
    n = 1 + len(lines)
    ty = y + h / 2 - (n - 1) * 8 + 5
    add(f'<text x="{x + w / 2}" y="{ty}" class="t">{escape(title)}</text>')
    for i, line in enumerate(lines):
        add(f'<text x="{x + w / 2}" y="{ty + 16 * (i + 1)}" class="s">{escape(line)}</text>')


def port(x, y, w, h, title, sub, kind="built"):
    fill, stroke, dash = STYLE[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    pts = f"{x+16},{y} {x+w-16},{y} {x+w},{y+h/2} {x+w-16},{y+h} {x+16},{y+h} {x},{y+h/2}"
    add(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{d}/>')
    add(f'<text x="{x + w/2}" y="{y + h/2 - 2}" class="t">{escape(title)}</text>')
    add(f'<text x="{x + w/2}" y="{y + h/2 + 14}" class="s">{escape(sub)}</text>')


def band(x, y, w, h, title, tx=None, anchor="start", ty=None):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" class="band"/>')
    add(f'<text x="{tx if tx is not None else x + 14}" y="{ty if ty is not None else y + 22}" class="bt" text-anchor="{anchor}">{escape(title)}</text>')


def path(d, kind="solid", label=None, lx=0, ly=0, anchor="middle", marker="arrow"):
    cls = {"solid": "ln", "dashed": "ln dash", "impl": "ln impl"}[kind]
    m = f' marker-end="url(#{marker})"' if marker else ""
    add(f'<path d="{d}" class="{cls}"{m}/>')
    if label:
        add(f'<text x="{lx}" y="{ly}" class="lbl" text-anchor="{anchor}">{escape(label)}</text>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    'font-family="Segoe UI, Helvetica, Arial, sans-serif">')
add("""<defs>
<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
  <path d="M0,0 L10,5 L0,10 z" fill="#424a53"/></marker>
<marker id="tri" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="11" markerHeight="11" orient="auto">
  <path d="M1,1 L11,6 L1,11 z" fill="#ffffff" stroke="#6e7781" stroke-width="1.3"/></marker>
</defs>
<style>
 .t{font-size:14px;font-weight:600;fill:#1f2328;text-anchor:middle}
 .s{font-size:11.5px;fill:#424a53;text-anchor:middle}
 .bt{font-size:13px;font-weight:700;fill:#57606a;letter-spacing:.3px}
 .band{fill:#f6f8fa;stroke:#d0d7de;stroke-width:1.2}
 .ln{fill:none;stroke:#424a53;stroke-width:1.5}
 .dash{stroke-dasharray:6 4}
 .impl{stroke:#6e7781;stroke-dasharray:2 3}
 .lbl{font-size:11px;fill:#57606a;font-style:italic}
 .h1{font-size:22px;font-weight:700;fill:#1f2328}
 .h2{font-size:13px;fill:#57606a}
</style>""")
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
add('<text x="40" y="42" class="h1">AutoForge — System Design (Phase 1, ASPICE SWE.3)</text>')
add('<text x="40" y="64" class="h2">Hexagonal architecture · deterministic tool execution · PR-only (human merges)</text>')

# ---------------- external column ----------------
EX, EW = 1220, 260
block(EX, 76, EW, 52, "Engineer", ["writes ticket · reviews & merges PR"], "actor", rx=26)
add(f'<text x="{EX}" y="172" class="bt">EXTERNAL SYSTEMS</text>')
block(EX, 190, EW, 64, "GitHub Webhooks", ["issues.labeled events"], "ext")
block(EX, 790, EW, 64, "GitHub REST API", ["Issues · Git Data · Pulls"], "ext")
block(EX, 875, EW, 50, "OpenAI Responses API", ["structured outputs"], "ext")
block(EX, 935, EW, 46, "Jira Cloud API", [], "ext")
block(EX, 991, EW, 46, "Container runtime", [], "ext")

# ---------------- band A: app ----------------
band(40, 150, 1120, 130, "app/  —  entry points & composition root")
block(60, 190, 195, 64, "config.py", ["Settings · SecretStr"])
block(280, 190, 195, 64, "wiring.py", ["build_dependencies()"])
block(500, 190, 195, 64, "cli.py", ["autoforge run <issue>"])
block(720, 190, 195, 64, "Run queue / worker", ["dedupe by delivery ID"], "planned")
block(940, 190, 195, 64, "webhook.py", ["HMAC · actor permission check"], "planned")
path("M255,222 H278")
path("M940,222 H917", "dashed")
path("M817,254 V268 H377 V256", "dashed")
path("M597,254 V268", "solid", marker=None)
path(f"M{EX},222 H1137", "dashed")

# ---------------- band B: workflow ----------------
band(40, 310, 1120, 130, "workflow/  —  orchestration")
block(60, 350, 400, 64, "pipeline.py", ["run_ticket() · idempotency · ticket feedback", "uses agents/context.py · domain/policy.py"])
block(500, 350, 400, 64, "graph.py  (LangGraph)", ["context → code ⇄ review → verify → publish", "HITL interrupt"], "next")
block(940, 350, 195, 64, "Checkpoint store", ["graph state · resume"], "next")
path("M320,254 V348", label="Dependencies", lx=328, ly=300, anchor="start")
path("M460,382 H498", "dashed")
add('<text x="480" y="372" class="lbl" text-anchor="middle">1.2</text>')
path("M900,382 H938", "dashed")

# bus to ports
path("M260,414 V460", marker=None)
path("M700,414 V460", "dashed", marker=None)
path("M150,460 H1060", marker=None)
for cx in (150, 380, 700, 880, 1060):
    path(f"M{cx},460 V513")
add('<text x="540" y="454" class="lbl" text-anchor="middle">workflow calls ports only — never concrete integrations</text>')

# ---------------- band C: ports ----------------
band(40, 480, 1120, 120, "ports.py", tx=540, anchor="middle", ty=598 - 8)
port(70, 515, 160, 60, "Coder", "generate()")
port(300, 515, 160, 60, "Reviewer", "review()", "next")
port(620, 515, 160, 60, "TicketSource", "get · comment · status")
port(800, 515, 160, 60, "RepoHost", "read · publish · PR")
port(980, 515, 160, 60, "Verifier", "verify()", "planned")

# ---------------- band D: agents | integrations ----------------
band(40, 630, 540, 250, "agents/  —  LLM roles", ty=870)
band(600, 630, 560, 250, "integrations/  —  adapters", tx=1146, anchor="end", ty=870)
block(60, 670, 180, 64, "coder.py", ["OpenAICoder"])
block(300, 670, 160, 64, "reviewer.py", ["verdict + feedback"], "next")
block(60, 785, 160, 60, "context.py", ["snapshot via RepoHost"])
block(235, 785, 160, 60, "prompts.py", ["versioned prompts"])
block(410, 785, 160, 60, "languages/", ["Python · C rules"], "planned")

block(620, 670, 160, 64, "github/issues.py", ["GitHubIssuesSource"])
block(800, 670, 160, 64, "github/repo.py", ["GitHubRepoHost"])
block(980, 670, 160, 64, "sandbox/", ["ContainerVerifier"], "planned")
block(620, 785, 160, 60, "jira/", ["implements TicketSource"], "planned")
block(800, 785, 160, 60, "github/client.py", ["retries · no merge"])

# implements (adapter -> port)
for cx, kind in ((150, "built"), (380, "next"), (700, "built"), (880, "built"), (1060, "planned")):
    path(f"M{cx},668 V578", "impl", marker="tri")
add('<text x="160" y="628" class="lbl" text-anchor="start">implements</text>')

# internal calls
path("M150,734 V760 H300 V783")
path("M380,734 V760 H330 V783", "dashed")
path("M410,815 H397", "dashed")
path("M700,734 V760 H860 V783")
path("M880,734 V783")

# to external systems
path(f"M228,734 V900 H{EX - 2}", label="responses.parse", lx=620, ly=894)
path("M402,734 V900", "dashed", marker=None)
path(f"M960,815 H{EX - 2}", label="HTTPS", lx=1090, ly=809)
path(f"M700,845 V958 H{EX - 2}", "dashed")
path(f"M1140,702 H1190 V1014 H{EX - 2}", "dashed")

# engineer flows
path(f"M{EX},102 H597 V188", label="dev / re-run", lx=900, ly=96)
path(f"M{EX + EW},102 H1505 V822 H{EX + EW + 2}")
add('<text x="1520" y="460" class="lbl" text-anchor="middle" transform="rotate(90 1520 460)">labels issue · reviews &amp; merges PR</text>')
path(f"M1350,790 V256", label="issues.labeled", lx=1358, ly=520, anchor="start")

# ---------------- domain ----------------
band(40, 1050, 1120, 110, "domain/  —  pure core (no I/O)")
block(60, 1082, 360, 60, "models.py", ["Ticket · RepoSnapshot · CodeChange · PullRequest"])
block(440, 1082, 360, 60, "policy.py", ["validate_change · assert_writable_branch"])
block(820, 1082, 320, 60, "errors.py", ["TicketError · SafetyError · AgentError"])
path("M24,215 V1048", label="", marker="arrow")
add('<text x="18" y="640" class="lbl" text-anchor="middle" transform="rotate(-90 18 640)">every layer depends on domain/ (never the reverse)</text>')
add('<text x="1146" y="1068" class="lbl" text-anchor="end">← github/repo.py enforces policy before every write</text>')

# ---------------- cross-cutting ----------------
band(40, 1180, 1120, 92, "cross-cutting")
block(60, 1206, 530, 54, "observability.py", ["JSON logs · run_id context · token usage — used by all layers"])
block(610, 1206, 530, 54, "evals/", ["sample tickets · quality scoring of agents"], "planned")

# ---------------- legend ----------------
ly = 1300
items = [("built", "Built (Phase 1.1)"), ("next", "Next (1.2 LangGraph + reviewer)"),
         ("planned", "Planned (1.3 / 1.4 / later)"), ("ext", "External system"), ("actor", "Actor")]
x = 40
for kind, text in items:
    fill, stroke, dash = STYLE[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{ly}" width="26" height="16" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{d}/>')
    add(f'<text x="{x + 34}" y="{ly + 13}" font-size="12" fill="#1f2328">{escape(text)}</text>')
    x += 34 + len(text) * 6.6 + 28
add(f'<path d="M{x},{ly+8} H{x+36}" class="ln" marker-end="url(#arrow)"/>')
add(f'<text x="{x+44}" y="{ly+13}" font-size="12" fill="#1f2328">call / data flow</text>')
x += 150
add(f'<path d="M{x},{ly+8} H{x+36}" class="ln dash" marker-end="url(#arrow)"/>')
add(f'<text x="{x+44}" y="{ly+13}" font-size="12" fill="#1f2328">planned flow</text>')
x += 130
add(f'<path d="M{x},{ly+8} H{x+36}" class="ln impl" marker-end="url(#tri)"/>')
add(f'<text x="{x+44}" y="{ly+13}" font-size="12" fill="#1f2328">implements port</text>')

add("</svg>")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote", sys.argv[1])

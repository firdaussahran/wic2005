#!/usr/bin/env python3
"""Week 6 assignment: generate your overlay's page from overlay.yaml.

Run it in the course container, from your netlab folder:
    python3 make_page6.py overlay.yaml

It checks overlay.yaml with the same checks as build_overlay.py (including your check_mtu()),
so a design that would black-hole big packets never gets published, then writes
docs/week6/index.html with your design, the VTEP commands your tenant_commands() produces for
every hypervisor, your evidence (evidence6/) and your answers (page6.yaml).
GitHub Pages publishes it at https://<you>.github.io/<repository>/week6/

There's nothing for you to write in this file.
"""
import datetime
import html
import json
import os
import sys

import yaml

from build_overlay import all_commands, check_mtu, check_spec, load, plan

EVIDENCE = [
    ("check", "evidence6/check.txt", "check_mtu() refusing the black-hole design"),
    ("flood", "evidence6/flood.txt", "Flood and learn: the VTEP table before, the flooded broadcast, the table after"),
    ("isolation", "evidence6/isolation.txt", "Two tenants, one address: who answers"),
    ("blackhole", "evidence6/blackhole.txt", "The black hole: tenant MTU 1500 on a 1500-byte underlay"),
    ("fixes", "evidence6/fixes.txt", "Two fixes, measured"),
]


def esc(text):
    return html.escape(str(text))


def hypervisors_section(spec, p):
    rows = ""
    for hv, ip in p["hypervisors"].items():
        vms = [f"{vm} ({v['tenant']})" for vm, v in p["vms"].items() if v["hv"] == hv]
        rows += f"<tr><td>{esc(hv)}</td><td><code>{esc(ip)}</code></td><td>{esc(', '.join(vms) or '—')}</td></tr>"
    u = spec["underlay"]
    return (
        f"<p>Underlay <code>{esc(u['network'])}</code>, MTU {esc(u['mtu'])}.</p>"
        f"<table><tr><th>Hypervisor</th><th>Underlay address</th><th>VMs</th></tr>{rows}</table>"
    )


def tenants_section(spec, p):
    parts = []
    for t in spec["tenants"]:
        rows = "".join(
            f"<tr><td>{esc(vm)}</td><td>{esc(hv)}</td><td><code>{esc(p['vms'][vm]['ip'])}</code></td>"
            f"<td><code>{esc(p['vms'][vm]['mac'])}</code></td></tr>"
            for vm, hv in t["vms"]
        )
        parts.append(
            f"<h3>{esc(t['name'])}: VNI {esc(t['vni'])}, <code>{esc(t['network'])}</code>, MTU {esc(t['mtu'])}</h3>"
            f"<table><tr><th>VM</th><th>On</th><th>Address</th><th>MAC</th></tr>{rows}</table>"
        )
    return "\n".join(parts)


def commands_section(spec, commands):
    parts = []
    for hv, cmds in commands.items():
        body = esc("\n".join(cmds)) if cmds else "TODO: tenant_commands() returned nothing for this hypervisor."
        parts.append(f'<h3>{esc(hv)}</h3>\n<pre id="commands-{esc(hv)}">{body}</pre>')
    return "\n".join(parts)


def mermaid_diagram(spec, p):
    lines = ["graph TB", "  s1[[underlay switch s1]]"]
    for hv, ip in p["hypervisors"].items():
        lines.append(f'  {hv}["{hv}<br>{ip}"] --- s1')
    for vm, v in p["vms"].items():
        lines.append(f'  {vm}("{vm}<br>{v["ip"]}<br>{v["tenant"]} · VNI {v["vni"]}") --- {v["hv"]}')
    return "\n".join(lines)


def evidence_section(ev_id, path, title):
    if os.path.exists(path) and open(path).read().strip():
        body = f'<pre id="evidence-{ev_id}">{esc(open(path).read().rstrip())}</pre>'
    else:
        print(f"WARNING: {path} is missing or empty")
        body = f'<pre id="evidence-{ev_id}" class="missing">Missing: {esc(path)}</pre>'
    return f'<h3>{esc(title)}</h3>\n<p class="file">{esc(path)}</p>\n{body}'


def answers_section(page):
    parts = []
    for key, qa in page.get("answers", {}).items():
        answer = str(qa.get("answer") or "").strip()
        if not answer:
            print(f"WARNING: page6.yaml has no answer for '{key}'")
            answer = "(Not answered yet.)"
        paragraphs = "".join(f"<p>{esc(p.strip())}</p>" for p in answer.split("\n\n") if p.strip())
        parts.append(f'<section id="answer-{esc(key)}"><h3>{esc(qa.get("question", key))}</h3>{paragraphs}</section>')
    return "\n".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overlay {last4}: my data centre, from data</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.55; color: #1f2937; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }} h2 {{ margin-top: 2.2rem; border-bottom: 1px solid #e5e7eb; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .92rem; margin-bottom: 1rem; }} th, td {{ border-bottom: 1px solid #e5e7eb; padding: .35rem .5rem; text-align: left; }}
  pre {{ background: #111827; color: #f3f4f6; padding: .8rem; border-radius: .4rem; overflow-x: auto; font-size: .82rem; }}
  pre.mermaid {{ background: none; color: inherit; }} pre.missing {{ background: #fef2f2; color: #991b1b; }}
  .meta, .file {{ color: #6b7280; font-size: .85rem; }} code {{ font-size: .88em; }}
</style>
</head>
<body>
<p class="meta"><a href="../">← Week 2: my network, built from data</a> · <a href="../week3/">Week 3: routed, from data</a></p>
<h1>My data centre overlay, from data</h1>
<p class="meta">Student ID ending {last4} · generated from <code>{source}</code> by <code>make_page6.py</code> on {today}</p>
<p id="summary">{n_hv} hypervisors and {n_tenants} tenants ({n_vms} VMs) on <code>{v4_block}</code>, joined by VXLAN.
Every tenant's MTU was checked against the underlay before anything was built.</p>
<p>Repository: <a id="repository" href="{repo}">{repo}</a></p>

<h2>The design</h2>
<h3>Hypervisors and the underlay</h3>
<section id="hypervisors">{hypervisors}</section>
<section id="tenants">{tenants}</section>
<h3>Diagram</h3>
<pre class="mermaid" id="diagram">{diagram}</pre>

<h2>VTEP commands, as built</h2>
<p>What <code>tenant_commands()</code> ran on each hypervisor.</p>
<section id="commands">{commands}</section>

<h2>Evidence from the running overlay</h2>
<section id="evidence">{evidence}</section>

<h2>What I learned</h2>
<section id="answers">{answers}</section>

<script type="application/json" id="overlay-data">{data}</script>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true }});
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 make_page6.py <overlay.yaml>")
    source = sys.argv[1]
    spec = load(source)
    problems = check_spec(spec)
    if not problems:
        problems = check_mtu(spec)
    if problems:
        for pr in problems:
            print("PROBLEM:", pr)
        sys.exit("Fix the design first: a design with problems doesn't get published.")

    commands = all_commands(spec)
    if not any(commands.values()):
        print("WARNING: tenant_commands() returned no commands: the page will say TODO.")
    with open("page6.yaml") as f:
        page = yaml.safe_load(f)
    p = plan(spec)
    last4 = str(spec["student_id_last4"])
    os.makedirs("docs/week6", exist_ok=True)
    with open("docs/week6/index.html", "w") as f:
        f.write(
            PAGE.format(
                last4=esc(last4),
                source=esc(source),
                today=datetime.date.today().isoformat(),
                n_hv=len(spec["hypervisors"]),
                n_tenants=len(spec["tenants"]),
                n_vms=len(p["vms"]),
                v4_block=f"10.{int(last4[2:])}.0.0/16",
                repo=esc(page.get("repository", "")),
                hypervisors=hypervisors_section(spec, p),
                tenants=tenants_section(spec, p),
                diagram=esc(mermaid_diagram(spec, p)),
                commands=commands_section(spec, commands),
                evidence="\n".join(evidence_section(*e) for e in EVIDENCE),
                answers=answers_section(page),
                data=json.dumps({"design": spec, "commands": commands}, indent=2).replace("</", "<\\/"),
            )
        )
    print("Wrote docs/week6/index.html")


if __name__ == "__main__":
    main()

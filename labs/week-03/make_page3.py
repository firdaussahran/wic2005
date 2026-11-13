#!/usr/bin/env python3
"""Week 3 assignment: generate your routed network's page from network.yaml.

Run it inside the lab container, from your netlab folder:
    python3 make_page3.py network.yaml

It checks network.yaml with the same checks as build_routed.py (including your check_paths()),
so a network with a loop or a missing route never gets published, then writes
docs/week3/index.html with your network, your evidence (evidence3/) and your answers
(page3.yaml). GitHub Pages publishes it at https://<you>.github.io/<repository>/week3/

One part is yours to write: routing_tables_section(). Look for TODO.
"""
import datetime
import html
import ipaddress  # for routing_tables_section()
import json
import os
import sys

import yaml

from build_routed import check_paths, check_spec, hosts, load, plan

EVIDENCE = [
    ("routes", "evidence3/routes.txt", "Every router's routing table: ip route"),
    ("traces", "evidence3/traces.txt", "From one host to every other: traceroute"),
    ("summary", "evidence3/summary.txt", "Your summary route at work: ip route get"),
    ("failure", "evidence3/failure.txt", "A link goes down: what static routes do"),
    ("check", "evidence3/check.txt", "check_paths() stopping a loop and a missing route"),
]


def esc(text):
    return html.escape(str(text))


def lans_section(spec):
    rows = "".join(
        f"<tr><td>{esc(l['name'])}</td><td><code>{esc(l['network'])}</code></td><td>{esc(l['router'])}</td>"
        f"<td>{esc(l['host'])} <code>{esc(hosts(spec)[l['host']][0])}</code></td></tr>"
        for l in spec["lans"]
    )
    return f"<table><thead><tr><th>LAN</th><th>Network</th><th>Router (.1)</th><th>Host (.10)</th></tr></thead><tbody>{rows}</tbody></table>"


def links_section(spec):
    rows = "".join(
        f"<tr><td>{esc(a)} — {esc(b)}</td><td><code>{esc(n)}</code></td></tr>" for a, b, n in spec["links"]
    )
    return f"<table><thead><tr><th>Link</th><th>Network</th></tr></thead><tbody>{rows}</tbody></table>"


def routing_tables_section(spec):
    """TODO (assignment): return HTML showing each router's routing table, as the router sees it.

    For each router in spec["routers"], list:
      - its directly connected networks: plan(spec)[router] gives its interfaces;
        each one's .network is a connected network
      - its static routes: spec["routes"].get(router), each with "to" and "via"
    sorted longest prefix first (the order in which longest-prefix match prefers them).
    Show each entry's destination and where it goes ("directly connected", or the next hop).
    For example, one table per router:
        <h3>r1</h3><table><tr><th>Destination</th><th>Next hop</th></tr>
        <tr><td>10.7.12.0/30</td><td>directly connected</td></tr> ...</table>
    Use esc() on every value.
    """
    return "<p>TODO: show each router's routing table.</p>"


def mermaid_diagram(spec):
    lines = ["graph LR"]
    for host, (address, router) in hosts(spec).items():
        lines.append(f'  {host}["{host}<br>{address}"] --- {router}(({router}))')
    for a, b, network in spec["links"]:
        lines.append(f"  {a}(({a})) ---|{network}| {b}(({b}))")
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
            print(f"WARNING: page3.yaml has no answer for '{key}'")
            answer = "(Not answered yet.)"
        paragraphs = "".join(f"<p>{esc(p.strip())}</p>" for p in answer.split("\n\n") if p.strip())
        parts.append(f'<section id="answer-{esc(key)}"><h3>{esc(qa.get("question", key))}</h3>{paragraphs}</section>')
    return "\n".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network {last4}: routed, from data</title>
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
<p class="meta"><a href="../">← Week 2: my network, built from data</a></p>
<h1>My routed network, from data</h1>
<p class="meta">Student ID ending {last4} · generated from <code>{source}</code> by <code>make_page3.py</code> on {today}</p>
<p id="summary">{n_routers} routers, {n_lans} LANs and {n_links} router links, with {n_routes} static routes, on <code>{v4_block}</code>.
Every route was checked for loops and missing routes before it reached a router.</p>
<p>Repository: <a id="repository" href="{repo}">{repo}</a></p>

<h2>The network</h2>
<h3>LANs</h3>
<section id="lans">{lans}</section>
<h3>Router links</h3>
<section id="links">{links}</section>
<h3>Diagram</h3>
<pre class="mermaid" id="diagram">{diagram}</pre>

<h2>Routing tables</h2>
<section id="routing-tables">{tables}</section>

<h2>Evidence from the running network</h2>
<section id="evidence">{evidence}</section>

<h2>What I learned</h2>
<section id="answers">{answers}</section>

<script type="application/json" id="network-data">{data}</script>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true }});
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 make_page3.py <network.yaml>")
    source = sys.argv[1]
    spec = load(source)
    problems = check_spec(spec)
    if not problems:
        problems = check_paths(spec)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        sys.exit("Fix the description first: a network with problems doesn't get published.")

    with open("page3.yaml") as f:
        page = yaml.safe_load(f)
    last4 = str(spec["student_id_last4"])
    os.makedirs("docs/week3", exist_ok=True)
    with open("docs/week3/index.html", "w") as f:
        f.write(
            PAGE.format(
                last4=esc(last4),
                source=esc(source),
                today=datetime.date.today().isoformat(),
                n_routers=len(spec["routers"]),
                n_lans=len(spec["lans"]),
                n_links=len(spec["links"]),
                n_routes=sum(len(v or []) for v in (spec.get("routes") or {}).values()),
                v4_block=f"10.{int(last4[2:])}.0.0/16",
                repo=esc(page.get("repository", "")),
                lans=lans_section(spec),
                links=links_section(spec),
                diagram=esc(mermaid_diagram(spec)),
                tables=routing_tables_section(spec),
                evidence="\n".join(evidence_section(*e) for e in EVIDENCE),
                answers=answers_section(page),
                data=json.dumps(spec, indent=2).replace("</", "<\\/"),
            )
        )
    print("Wrote docs/week3/index.html")


if __name__ == "__main__":
    main()

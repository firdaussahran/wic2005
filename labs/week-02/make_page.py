#!/usr/bin/env python3
"""Week 2 assignment: generate your network's web page from the same data that builds it.

Run it inside the lab container, from your netlab folder:
    python3 make_page.py topology.yaml

It reads your topology file, checks it with the same checks build_topo.py uses (so a broken
network never gets published), and writes docs/index.html. The page includes:
  - your network, from topology.yaml
  - your evidence, from the files in evidence/
  - your answers, from page.yaml
GitHub Pages then publishes the docs folder (Settings -> Pages -> main, /docs).

Two parts are yours to write: links_section() and mermaid_diagram(). Look for TODO.
"""
import datetime
import html
import json
import os
import sys

import yaml

# The same functions build_topo.py uses: one data model, one set of checks, two outputs.
from build_topo import check, check_mine, load

# (id, file, title) for each piece of evidence. Save each file from your running network.
EVIDENCE = [
    ("pingall", "evidence/pingall.txt", "pingall: every host reaches every other"),
    ("neigh", "evidence/neigh.txt", "h1's neighbour table: ARP and Neighbour Discovery"),
    ("macs", "evidence/macs.txt", "What a switch's data plane learned: brctl showmacs"),
    ("dad", "evidence/dad.txt", "A duplicate address: IPv6 notices, IPv4 doesn't"),
    ("check", "evidence/check.txt", "check() stopping bad data before it's built"),
]


def esc(text):
    return html.escape(str(text))


def hosts_section(spec):
    """A table of hosts: name, addresses, and the switch each one is plugged into. (Worked example.)"""
    plugged_into = {}
    for a, b in spec["links"]:
        if a in spec["switches"]:
            plugged_into[b] = a
        elif b in spec["switches"]:
            plugged_into[a] = b
    rows = "".join(
        f"<tr><td>{esc(h['name'])}</td><td><code>{esc(h['ip'])}</code></td>"
        f"<td><code>{esc(h.get('ipv6', ''))}</code></td><td>{esc(plugged_into.get(h['name'], '—'))}</td></tr>"
        for h in spec["hosts"]
    )
    return (
        "<table><thead><tr><th>Host</th><th>IPv4</th><th>IPv6</th><th>Switch</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def links_section(spec):
    """TODO (assignment): return HTML listing every link in spec["links"].

    For example, for links [h1, s1] and [s1, s2] return:
        <ul><li>h1 — s1</li><li>s1 — s2</li></ul>
    Use esc() on every name, as hosts_section() does.
    """
    return "<p>TODO: list the links.</p>"


def mermaid_diagram(spec):
    """TODO (assignment): return Mermaid text that draws the network, built from spec["links"].

    Mermaid turns text like this into a diagram in the browser:
        graph LR
          h1 --- s1
          s1 --- s2
    Start with "graph LR", then add one line per link. For extra credit, show each host's
    IPv4 address in its box, e.g.  h1["h1<br>10.NN.0.1"] --- s1
    """
    return "graph LR\n  todo[Diagram not generated yet]"


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
            print(f"WARNING: page.yaml has no answer for '{key}'")
            answer = "(Not answered yet.)"
        paragraphs = "".join(f"<p>{esc(p.strip())}</p>" for p in answer.split("\n\n") if p.strip())
        parts.append(f'<section id="answer-{esc(key)}"><h3>{esc(qa.get("question", key))}</h3>{paragraphs}</section>')
    return "\n".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network {last4}: built from data</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.55; color: #1f2937; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }} h2 {{ margin-top: 2.2rem; border-bottom: 1px solid #e5e7eb; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }} th, td {{ border-bottom: 1px solid #e5e7eb; padding: .35rem .5rem; text-align: left; }}
  pre {{ background: #111827; color: #f3f4f6; padding: .8rem; border-radius: .4rem; overflow-x: auto; font-size: .82rem; }}
  pre.mermaid {{ background: none; color: inherit; }} pre.missing {{ background: #fef2f2; color: #991b1b; }}
  .meta, .file {{ color: #6b7280; font-size: .85rem; }} code {{ font-size: .88em; }}
</style>
</head>
<body>
<h1>My network, built from data</h1>
<p class="meta">Student ID ending {last4} · generated from <code>{source}</code> by <code>make_page.py</code> on {today}</p>
<p id="summary">{n_switches} switches, {n_hosts} hosts and {n_links} links, on IPv4 <code>{v4_block}</code> and IPv6 <code>{v6_block}</code>.
The same file builds this network in Mininet and generates this page.</p>
<p>Repository: <a id="repository" href="{repo}">{repo}</a></p>

<h2>The network</h2>
<section id="hosts">{hosts}</section>
<h3>Links</h3>
<section id="links">{links}</section>
<h3>Diagram</h3>
<pre class="mermaid" id="diagram">{diagram}</pre>

<h2>Evidence from the running network</h2>
<section id="evidence">{evidence}</section>

<h2>What I learned</h2>
<section id="answers">{answers}</section>

<script type="application/json" id="topology-data">{data}</script>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true }});
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 make_page.py <topology.yaml|topology.json>")
    source = sys.argv[1]
    spec = load(source)

    problems = check_mine(spec) + check(spec)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        sys.exit("Fix the description first: a broken network doesn't get published.")

    with open("page.yaml") as f:
        page = yaml.safe_load(f)
    last4 = str(spec["student_id_last4"])

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w") as f:
        f.write(
            PAGE.format(
                last4=esc(last4),
                source=esc(source),
                today=datetime.date.today().isoformat(),
                n_switches=len(spec["switches"]),
                n_hosts=len(spec["hosts"]),
                n_links=len(spec["links"]),
                v4_block=f"10.{int(last4[2:])}.0.0/16",
                v6_block=f"fd00:{last4}::/32",
                repo=esc(page.get("repository", "")),
                hosts=hosts_section(spec),
                links=links_section(spec),
                diagram=esc(mermaid_diagram(spec)),
                evidence="\n".join(evidence_section(*e) for e in EVIDENCE),
                answers=answers_section(page),
                # The page carries its own data model; "</" is escaped so it can't end the script tag.
                data=json.dumps(spec, indent=2).replace("</", "<\\/"),
            )
        )
    print("Wrote docs/index.html")


if __name__ == "__main__":
    main()

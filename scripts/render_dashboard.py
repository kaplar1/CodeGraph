#!/usr/bin/env python3
"""
render_dashboard.py — Embed a knowledge-graph.json into a single, self-contained
HTML file with an interactive D3 force-directed view. No server required;
teammates can just open the file, or you can host it anywhere static.

Usage:
  python3 render_dashboard.py path/to/knowledge-graph.json --out dashboard.html
"""
import argparse
import json

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CodeGraph — Codebase Knowledge Graph</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --text:#e6e8ee; --muted:#8b93a7; --accent:#6ea8fe; --border:#262b36; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
  #app { display:flex; height:100vh; }
  #sidebar { width:320px; padding:16px; border-right:1px solid var(--border); overflow-y:auto; background:var(--panel); }
  #sidebar h1 { font-size:16px; margin:0 0 4px; }
  #sidebar .stats { color:var(--muted); font-size:12px; margin-bottom:12px; }
  #search { width:100%; padding:8px; border-radius:6px; border:1px solid var(--border); background:#0f1115; color:var(--text); margin-bottom:12px; }
  #detail { font-size:13px; line-height:1.5; }
  #detail .path { font-weight:600; word-break:break-all; }
  #detail .row { color:var(--muted); margin-top:6px; }
  #detail ul { margin:6px 0; padding-left:18px; }
  .legend { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; font-size:12px; }
  .legend span { display:flex; align-items:center; gap:4px; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  #graph { flex:1; }
  svg { width:100%; height:100%; }
  .link { stroke:#3a4152; stroke-opacity:0.6; }
  .node text { fill:var(--text); font-size:9px; pointer-events:none; }
  .node circle { stroke:#0f1115; stroke-width:1.2px; cursor:pointer; }
  .node.dim circle, .node.dim text { opacity:0.15; }
  .link.dim { opacity:0.05; }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1>CodeGraph</h1>
    <div style="color:var(--muted);font-size:11px;margin:-2px 0 10px;">Codebase Knowledge Graph</div>
    <div class="stats" id="stats"></div>
    <input id="search" placeholder="Search files / functions...">
    <div class="legend" id="legend"></div>
    <div id="detail">Click a node to inspect it.</div>
  </div>
  <div id="graph"><svg></svg></div>
</div>
<script>
const GRAPH = __GRAPH_JSON__;

const langColor = d3.scaleOrdinal(d3.schemeTableau10);
const stats = GRAPH.stats;
document.getElementById('stats').innerHTML =
  `${stats.file_count} files &middot; ${stats.edge_count} edges &middot; generated ${new Date(GRAPH.generated_at).toLocaleString()}`;

const langs = [...new Set(GRAPH.nodes.map(n => n.language).filter(Boolean))];
document.getElementById('legend').innerHTML = langs.map(l =>
  `<span><span class="dot" style="background:${langColor(l)}"></span>${l}</span>`).join('');

const nodesById = new Map(GRAPH.nodes.map(n => [n.id, n]));
const links = GRAPH.edges
  .filter(e => nodesById.has(e.source) && nodesById.has(e.target))
  .map(e => Object.assign({}, e));
const nodes = GRAPH.nodes.map(n => Object.assign({}, n));

const svg = d3.select('svg');
const g = svg.append('g');
svg.call(d3.zoom().scaleExtent([0.1, 6]).on('zoom', (ev) => g.attr('transform', ev.transform)));

function size() {
  const el = document.getElementById('graph');
  return [el.clientWidth, el.clientHeight];
}
let [W, H] = size();

const sim = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(links).id(d => d.id).distance(50).strength(0.25))
  .force('charge', d3.forceManyBody().strength(-90))
  .force('center', d3.forceCenter(W/2, H/2))
  .force('collide', d3.forceCollide(d => 6 + (d.criticality||0)));

const link = g.append('g').selectAll('line').data(links).join('line').attr('class', 'link');

const node = g.append('g').selectAll('g').data(nodes).join('g')
  .attr('class', 'node')
  .call(d3.drag()
    .on('start', (ev,d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
    .on('drag', (ev,d) => { d.fx=ev.x; d.fy=ev.y; })
    .on('end', (ev,d) => { if (!ev.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }));

node.append('circle')
  .attr('r', d => d.type === 'file' ? 4 + Math.min(10, (d.criticality||0)) : 3)
  .attr('fill', d => langColor(d.language || 'unknown'));

node.append('text').attr('dx', 8).attr('dy', 3)
  .text(d => d.id.split('/').pop());

node.on('click', (ev, d) => showDetail(d));

sim.on('tick', () => {
  link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
      .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
  node.attr('transform', d => `translate(${d.x},${d.y})`);
});

function showDetail(d) {
  const importers = links.filter(l => l.target.id === d.id).map(l => l.source.id);
  const imports = links.filter(l => l.source.id === d.id).map(l => l.target.id);
  let html = `<div class="path">${d.id}</div>`;
  html += `<div class="row">type: ${d.type} &middot; lang: ${d.language||'—'}${d.loc?` &middot; ${d.loc} lines`:''}</div>`;
  if (d.criticality !== undefined) html += `<div class="row">depended on by ${d.criticality} file(s)</div>`;
  if (d.defines && d.defines.length) {
    html += `<div class="row">defines:</div><ul>${d.defines.slice(0,40).map(x=>`<li>${x.kind} ${x.name}</li>`).join('')}</ul>`;
  }
  if (importers.length) html += `<div class="row">imported by:</div><ul>${importers.slice(0,20).map(x=>`<li>${x}</li>`).join('')}</ul>`;
  if (imports.length) html += `<div class="row">imports:</div><ul>${imports.slice(0,20).map(x=>`<li>${x}</li>`).join('')}</ul>`;
  document.getElementById('detail').innerHTML = html;

  const connected = new Set([d.id, ...importers, ...imports]);
  node.classed('dim', n => !connected.has(n.id));
  link.classed('dim', l => !(connected.has(l.source.id) && connected.has(l.target.id)));
}

document.getElementById('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) { node.classed('dim', false); link.classed('dim', false); return; }
  const matched = new Set(nodes.filter(n => n.id.toLowerCase().includes(q)).map(n => n.id));
  node.classed('dim', n => !matched.has(n.id));
  link.classed('dim', l => !(matched.has(l.source.id) || matched.has(l.target.id)));
});

window.addEventListener('resize', () => {
  [W, H] = size();
  sim.force('center', d3.forceCenter(W/2, H/2));
  sim.alpha(0.3).restart();
});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("graph_json")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()

    with open(args.graph_json) as f:
        graph = json.load(f)

    html = TEMPLATE.replace("__GRAPH_JSON__", json.dumps(graph))
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out} ({len(html)/1024:.0f} KB) — open it directly in a browser, no server needed.")


if __name__ == "__main__":
    main()

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
  #detail .fns { color:var(--muted); font-size:11px; }
  .legend { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; font-size:12px; }
  .legend span { display:flex; align-items:center; gap:4px; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .edge-legend { font-size:11px; color:var(--muted); margin-bottom:14px; }
  .edge-legend div { display:flex; align-items:center; gap:6px; margin-top:4px; }
  .edge-legend .ln { width:22px; height:0; border-top-width:2px; }
  #focus { margin:10px 0 14px; display:none; }
  #focus .label { font-size:11px; color:var(--muted); margin-bottom:5px; }
  #focus button { font-size:11px; padding:3px 8px; margin-right:4px; border-radius:5px; border:1px solid var(--border); background:#0f1115; color:var(--text); cursor:pointer; }
  #focus button.active { background:var(--accent); color:#0f1115; border-color:var(--accent); font-weight:600; }
  #graph { flex:1; }
  svg { width:100%; height:100%; }
  .link-imports { stroke:#3a4152; stroke-opacity:0.6; }
  .link-defines { stroke:#4a5164; stroke-opacity:0.35; stroke-dasharray:1,3; }
  .link-calls { stroke:#e8a33d; stroke-opacity:0.55; stroke-dasharray:4,3; }
  .node text { fill:var(--text); font-size:9px; pointer-events:none; }
  .node circle { stroke:#0f1115; stroke-width:1.2px; cursor:pointer; }
  .node.external circle { stroke-dasharray:2,2; }
  .node.dim circle, .node.dim text { opacity:0.15; }
  .link.dim { opacity:0.04; }
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
    <div class="edge-legend">
      <div><span class="ln" style="border-top:2px solid #3a4152;"></span>imports</div>
      <div><span class="ln" style="border-top:2px dashed #4a5164;"></span>defines</div>
      <div><span class="ln" style="border-top:2px dashed #e8a33d;"></span>calls</div>
    </div>
    <div id="focus">
      <div class="label">Focus neighborhood (from selected node)</div>
      <button data-depth="1">1 hop</button><button data-depth="2">2 hops</button><button data-depth="3">3 hops</button><button data-depth="0">All</button>
    </div>
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

const link = g.append('g').selectAll('line').data(links).join('line')
  .attr('class', d => `link link-${d.type}`);

const node = g.append('g').selectAll('g').data(nodes).join('g')
  .attr('class', d => `node${d.type === 'external' ? ' external' : ''}`)
  .call(d3.drag()
    .on('start', (ev,d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
    .on('drag', (ev,d) => { d.fx=ev.x; d.fy=ev.y; })
    .on('end', (ev,d) => { if (!ev.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }));

node.append('circle')
  .attr('r', d => d.type === 'file' ? 4 + Math.min(10, (d.criticality||0)) : (d.type === 'external' ? 3 : 4))
  .attr('fill', d => d.type === 'external' ? '#4a5164' : langColor(d.language || 'unknown'));

node.append('text').attr('dx', 8).attr('dy', 3)
  .text(d => d.type === 'external' ? d.name : d.id.split('/').pop());

node.on('click', (ev, d) => { selectedNodeId = d.id; showDetail(d); });

sim.on('tick', () => {
  link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
      .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
  node.attr('transform', d => `translate(${d.x},${d.y})`);
});

let selectedNodeId = null;
let focusDepth = 1;

function neighborsOf(nodeId) {
  const out = [], into = [];
  for (const l of links) {
    if (l.source.id === nodeId) out.push(l);
    if (l.target.id === nodeId) into.push(l);
  }
  return { out, into };
}

// BFS over all edge types, undirected, up to `depth` hops from `startId`.
// depth === 0 means "no limit" (whole connected component).
function neighborhood(startId, depth) {
  const adjacency = new Map();
  for (const l of links) {
    if (!adjacency.has(l.source.id)) adjacency.set(l.source.id, []);
    if (!adjacency.has(l.target.id)) adjacency.set(l.target.id, []);
    adjacency.get(l.source.id).push(l.target.id);
    adjacency.get(l.target.id).push(l.source.id);
  }
  const visited = new Set([startId]);
  let frontier = [startId];
  let hops = 0;
  while (frontier.length && (depth === 0 || hops < depth)) {
    const next = [];
    for (const id of frontier) {
      for (const nb of (adjacency.get(id) || [])) {
        if (!visited.has(nb)) { visited.add(nb); next.push(nb); }
      }
    }
    frontier = next;
    hops++;
    if (depth === 0 && hops > 200) break; // safety valve against pathological graphs
  }
  return visited;
}

function applyFocus(nodeId, depth) {
  const connected = neighborhood(nodeId, depth);
  node.classed('dim', n => !connected.has(n.id));
  link.classed('dim', l => !(connected.has(l.source.id) && connected.has(l.target.id)));
}

document.querySelectorAll('#focus button').forEach(btn => {
  btn.addEventListener('click', () => {
    focusDepth = +btn.dataset.depth;
    document.querySelectorAll('#focus button').forEach(b => b.classList.toggle('active', b === btn));
    if (selectedNodeId) applyFocus(selectedNodeId, focusDepth);
  });
});

function listItem(id) {
  const n = nodesById.get(id);
  const label = n && n.type === 'external' ? n.name + ' (external)' : id;
  return `<li>${label}</li>`;
}

// A "calls" edge into a library node (e.g. external::stdio) can represent
// several distinct function names collapsed onto one edge (see `functions`
// on the edge) - show that breakdown instead of just the library name.
function callEdgeItem(l) {
  const n = nodesById.get(l.target.id);
  const label = n && n.type === 'external' ? n.name + ' (external)' : l.target.id;
  const countSuffix = l.count ? ` &times;${l.count}` : '';
  const fns = l.functions && l.functions.length
    ? `<div class="fns">${l.functions.map(f => f.count > 1 ? `${f.name} &times;${f.count}` : f.name).join(', ')}</div>`
    : '';
  return `<li>${label}${countSuffix}${fns}</li>`;
}

function showDetail(d) {
  const { out, into } = neighborsOf(d.id);
  const importedBy = into.filter(l => l.type === 'imports').map(l => l.source.id);
  const importsList = out.filter(l => l.type === 'imports').map(l => l.target.id);
  const calledBy = into.filter(l => l.type === 'calls').map(l => l.source.id);
  const callEdges = out.filter(l => l.type === 'calls');
  const memberOf = into.filter(l => l.type === 'defines').map(l => l.source.id);

  let html = `<div class="path">${d.type === 'external' ? d.name : d.id}</div>`;
  if (d.type === 'external') {
    html += `<div class="row">external symbol — not defined in the scanned code (library/OS call)</div>`;
  } else {
    html += `<div class="row">type: ${d.type} &middot; lang: ${d.language||'—'}${d.loc?` &middot; ${d.loc} lines`:''}</div>`;
  }
  if (d.criticality !== undefined) html += `<div class="row">depended on by ${d.criticality} file(s)</div>`;
  if (memberOf.length) html += `<div class="row">member of:</div><ul>${memberOf.map(listItem).join('')}</ul>`;
  if (d.defines && d.defines.length) {
    html += `<div class="row">defines:</div><ul>${d.defines.slice(0,40).map(x=>`<li>${x.kind} ${x.name}</li>`).join('')}</ul>`;
  }
  if (callEdges.length) html += `<div class="row">calls:</div><ul>${callEdges.slice(0,30).map(callEdgeItem).join('')}</ul>`;
  if (calledBy.length) html += `<div class="row">called by:</div><ul>${calledBy.slice(0,30).map(listItem).join('')}</ul>`;
  if (importedBy.length) html += `<div class="row">imported by:</div><ul>${importedBy.slice(0,20).map(listItem).join('')}</ul>`;
  if (importsList.length) html += `<div class="row">imports:</div><ul>${importsList.slice(0,20).map(listItem).join('')}</ul>`;
  document.getElementById('detail').innerHTML = html;

  document.getElementById('focus').style.display = 'block';
  document.querySelectorAll('#focus button').forEach(b => b.classList.toggle('active', +b.dataset.depth === focusDepth));
  applyFocus(d.id, focusDepth);
}

document.getElementById('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  selectedNodeId = null;
  document.getElementById('focus').style.display = 'none';
  if (!q) { node.classed('dim', false); link.classed('dim', false); return; }
  const matched = new Set(nodes.filter(n => (n.type === 'external' ? n.name : n.id).toLowerCase().includes(q)).map(n => n.id));
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

    with open(args.graph_json, encoding="utf-8") as f:
        graph = json.load(f)

    html = TEMPLATE.replace("__GRAPH_JSON__", json.dumps(graph))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {args.out} ({len(html)/1024:.0f} KB) — open it directly in a browser, no server needed.")


if __name__ == "__main__":
    main()

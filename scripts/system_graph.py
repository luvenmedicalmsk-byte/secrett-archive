#!/usr/bin/env python3
"""
System Graph Engine v2.3

Строит взвешенный направленный граф зависимостей.
Node types: country | vector | domain | infrastructure | actor
Edge types: cascade | dependency | amplification | feedback | exposure

Алгоритмы:
  - PageRank-style centrality (iterative, pure Python)
  - Betweenness approximation (greedy BFS paths)
  - Synchronization clustering (Jaccard similarity на event sets)
  - Contagion path enumeration (DFS с ограничением глубины)
  - Critical node identification (degree + betweenness × edge_weight)
"""

from typing import Optional
from collections import defaultdict, deque


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

DOMAINS       = ("geopolitics", "climate", "economy", "technology", "social")
INFRA_VECTORS = ("infrastructure", "energy", "shipping", "internet", "financial")

# Статические structural dependencies (domain → domains it inherently affects)
STRUCTURAL_DEPS: dict[str, dict[str, float]] = {
    "geopolitics": {"economy": 0.7, "social": 0.5, "technology": 0.3, "climate": 0.2},
    "climate":     {"social": 0.6, "economy": 0.5, "geopolitics": 0.3},
    "economy":     {"social": 0.65, "geopolitics": 0.45, "technology": 0.3},
    "technology":  {"economy": 0.55, "infrastructure": 0.5, "geopolitics": 0.3},
    "social":      {"geopolitics": 0.4, "economy": 0.35},
}

# Векторные зависимости: вектор → домены которые он нагружает
VECTOR_DOMAIN_LOAD: dict[str, list[str]] = {
    "kinetic":        ["geopolitics", "social", "economy"],
    "cyber":          ["technology", "economy", "infrastructure"],
    "economic":       ["economy", "social", "geopolitics"],
    "environmental":  ["climate", "social", "economy"],
    "political":      ["geopolitics", "economy", "social"],
    "infrastructure": ["economy", "technology", "social"],
    "social":         ["social", "geopolitics"],
    "informational":  ["geopolitics", "social"],
}


class DirectedGraph:
    """
    Взвешенный направленный граф.
    Edges: {from_id → {to_id → {weight, edge_type, count}}}
    Nodes: {id → {type, label, score, active}}
    """
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict[str, dict]] = defaultdict(dict)

    def add_node(self, nid: str, ntype: str, label: str,
                 score: float = 0.0, active: bool = True):
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "type": ntype, "label": label,
                               "score": score, "active": active}
        else:
            self.nodes[nid]["score"] = max(self.nodes[nid]["score"], score)

    def add_edge(self, src: str, dst: str, weight: float,
                 edge_type: str = "cascade", count: int = 1):
        if dst not in self.edges[src]:
            self.edges[src][dst] = {"weight": weight, "type": edge_type, "count": count}
        else:
            e = self.edges[src][dst]
            e["weight"] = min(1.0, e["weight"] + weight * 0.3)
            e["count"] += count

    def neighbors(self, nid: str) -> list[tuple[str, float]]:
        return [(dst, e["weight"]) for dst, e in self.edges.get(nid, {}).items()]

    def in_degree(self, nid: str) -> int:
        return sum(1 for edges in self.edges.values() if nid in edges)

    def out_degree(self, nid: str) -> int:
        return len(self.edges.get(nid, {}))

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes.values()),
            "edges": [
                {"from": src, "to": dst, **attrs}
                for src, dsts in self.edges.items()
                for dst, attrs in dsts.items()
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════

def _build_graph(events: list[dict], convergence: dict) -> DirectedGraph:
    g = DirectedGraph()

    # Domain nodes
    domain_stats: dict[str, list] = {d: [] for d in DOMAINS}
    for ev in events:
        d = ev.get("domain", "")
        if d in domain_stats:
            domain_stats[d].append(ev.get("escalation_score", 0))

    for d in DOMAINS:
        scores = domain_stats[d]
        avg = sum(scores) / len(scores) if scores else 0
        g.add_node(f"dom:{d}", "domain", d, score=avg / 100, active=avg > 0)

    # Статические structural edges между доменами
    for src_d, targets in STRUCTURAL_DEPS.items():
        for dst_d, base_w in targets.items():
            src_avg = sum(domain_stats.get(src_d, [0])) / max(1, len(domain_stats.get(src_d, [1])))
            # Вес усиливается если src домен активен
            w = base_w * (0.5 + src_avg / 200)
            g.add_edge(f"dom:{src_d}", f"dom:{dst_d}", round(w, 3), "dependency")

    # Динамические edges из cascade полей событий
    for ev in events:
        if ev.get("escalation_level") not in ("critical", "high"):
            continue
        src_d = ev.get("domain", "")
        if not src_d:
            continue
        esc = ev.get("escalation_score", 0) / 100
        for dst_d in ev.get("cascade", []):
            w = min(1.0, esc * 0.8)
            g.add_edge(f"dom:{src_d}", f"dom:{dst_d}", w, "cascade")

        # Vector nodes → domain edges
        for vec in ev.get("vectors", []):
            g.add_node(f"vec:{vec}", "vector", vec, score=esc)
            g.add_edge(f"vec:{vec}", f"dom:{src_d}", esc * 0.6, "amplification")
            for load_d in VECTOR_DOMAIN_LOAD.get(vec, []):
                if load_d != src_d:
                    g.add_edge(f"vec:{vec}", f"dom:{load_d}", esc * 0.3, "exposure")

    # Country nodes (из country_profiles или region поля событий)
    country_events: dict[str, list] = defaultdict(list)
    for ev in events:
        r = ev.get("region", "")
        if r:
            country_events[r].append(ev)

    for region, evs in country_events.items():
        scores = [e.get("escalation_score", 0) for e in evs if e.get("escalation_score")]
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        nid = f"ctry:{region[:20]}"
        g.add_node(nid, "country", region[:30], score=avg / 100)
        # Country → domain edges
        for ev in evs:
            d = ev.get("domain", "")
            if d:
                w = ev.get("escalation_score", 0) / 100 * 0.7
                g.add_edge(nid, f"dom:{d}", w, "exposure")

    # Feedback edges: если два домена взаимно в rising state
    rising_domains = set(convergence.get("rising_domains", []))
    for d1 in rising_domains:
        for d2 in rising_domains:
            if d1 < d2:  # once per pair
                if d2 in STRUCTURAL_DEPS.get(d1, {}):
                    g.add_edge(f"dom:{d2}", f"dom:{d1}", 0.4, "feedback")

    return g


# ══════════════════════════════════════════════════════════════════════════════
# CENTRALITY ALGORITHMS
# ══════════════════════════════════════════════════════════════════════════════

def _pagerank(g: DirectedGraph, damping: float = 0.85, iterations: int = 20) -> dict[str, float]:
    """
    PageRank-style centrality (iterative power method).
    Вес рёбер учитывается как transition probability.
    """
    nodes = list(g.nodes.keys())
    n = len(nodes)
    if n == 0:
        return {}

    rank = {nid: 1.0 / n for nid in nodes}

    for _ in range(iterations):
        new_rank: dict[str, float] = {nid: (1 - damping) / n for nid in nodes}
        for src in nodes:
            out = g.edges.get(src, {})
            if not out:
                # Dangling node: distribute evenly
                delta = damping * rank[src] / n
                for nid in nodes:
                    new_rank[nid] += delta
            else:
                total_w = sum(e["weight"] for e in out.values())
                for dst, edge in out.items():
                    if dst in new_rank:
                        new_rank[dst] += damping * rank[src] * (edge["weight"] / max(total_w, 1e-9))
        rank = new_rank

    return {k: round(v, 5) for k, v in rank.items()}


def _betweenness_approx(g: DirectedGraph, max_nodes: int = 30) -> dict[str, float]:
    """
    Приближённый betweenness centrality через BFS от каждого node.
    O(V × E) — приемлемо для графов до 100 узлов.
    """
    nodes = list(g.nodes.keys())[:max_nodes]
    bc: dict[str, float] = {nid: 0.0 for nid in nodes}

    for src in nodes:
        # BFS shortest paths
        dist = {src: 0}
        sigma = {src: 1}
        pred: dict[str, list] = defaultdict(list)
        queue = deque([src])
        order = []

        while queue:
            v = queue.popleft()
            order.append(v)
            for w, _ in g.neighbors(v):
                if w not in dist:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist.get(w) == dist[v] + 1:
                    sigma[w] = sigma.get(w, 0) + sigma[v]
                    pred[w].append(v)

        # Back-propagation
        delta = defaultdict(float)
        while order:
            w = order.pop()
            for v in pred.get(w, []):
                if sigma.get(w, 0) > 0:
                    delta[v] += (sigma.get(v, 0) / sigma[w]) * (1 + delta[w])
            if w != src:
                bc[w] = bc.get(w, 0) + delta[w]

    # Normalise
    n = len(nodes)
    norm = (n - 1) * (n - 2) if n > 2 else 1
    return {k: round(v / norm, 5) for k, v in bc.items()}


# ══════════════════════════════════════════════════════════════════════════════
# CONTAGION PATHS
# ══════════════════════════════════════════════════════════════════════════════

def _find_contagion_paths(
    g: DirectedGraph,
    start: str,
    max_depth: int = 4,
    min_weight: float = 0.25,
) -> list[list[str]]:
    """
    DFS enumeration всех contagion paths из start node.
    Ограничение: max_depth, min_weight на каждое ребро.
    """
    paths = []

    def dfs(current: str, path: list, visited: set):
        for dst, w in g.neighbors(current):
            if w < min_weight or dst in visited:
                continue
            new_path = path + [dst]
            if len(new_path) <= max_depth:
                paths.append(new_path)
                dfs(dst, new_path, visited | {dst})

    if start in g.nodes:
        dfs(start, [start], {start})

    return sorted(paths, key=len, reverse=True)[:15]


def _synchronization_clusters(events: list[dict]) -> list[dict]:
    """
    Кластеры доменов с высокой синхронизацией (Jaccard на fingerprint наборах).
    Два домена синхронизированы если ≥30% их событий имеют общие cascade targets.
    """
    domains = list(DOMAINS)
    # Fingerprint set per domain
    fp_sets: dict[str, set] = defaultdict(set)
    cascade_targets: dict[str, set] = defaultdict(set)

    for ev in events:
        d = ev.get("domain", "")
        fp = ev.get("fingerprint", "")
        if d and fp:
            fp_sets[d].add(fp)
        for c in ev.get("cascade", []):
            if d and c:
                cascade_targets[d].add(c)

    clusters = []
    checked = set()
    for i, d1 in enumerate(domains):
        for d2 in domains[i+1:]:
            pair = (d1, d2)
            if pair in checked:
                continue
            checked.add(pair)

            ct1 = cascade_targets.get(d1, set())
            ct2 = cascade_targets.get(d2, set())
            inter = len(ct1 & ct2)
            union = len(ct1 | ct2)
            jaccard = inter / union if union > 0 else 0.0

            if jaccard >= 0.3:
                clusters.append({
                    "domains":  [d1, d2],
                    "sync":     round(jaccard, 3),
                    "shared_targets": list(ct1 & ct2),
                })

    return sorted(clusters, key=lambda x: x["sync"], reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# CRITICAL NODE IDENTIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def _critical_nodes(
    g: DirectedGraph,
    pagerank: dict[str, float],
    betweenness: dict[str, float],
    top_n: int = 10,
) -> list[dict]:
    """
    Critical nodes = высокий PageRank + высокий betweenness + высокий score.
    Это узлы, удаление которых максимально нарушает систему.
    """
    scored = []
    for nid, node in g.nodes.items():
        pr = pagerank.get(nid, 0)
        bt = betweenness.get(nid, 0)
        sc = node.get("score", 0)
        in_d  = g.in_degree(nid)
        out_d = g.out_degree(nid)
        # Composite criticality: PageRank (системная важность) + betweenness (мостовость) + score
        criticality = (pr * 40 + bt * 30 + sc * 20 + (in_d + out_d) / 20 * 10)
        scored.append({
            "id":          nid,
            "type":        node["type"],
            "label":       node["label"],
            "score":       round(sc, 3),
            "pagerank":    pr,
            "betweenness": bt,
            "in_degree":   in_d,
            "out_degree":  out_d,
            "criticality": round(criticality, 4),
        })

    return sorted(scored, key=lambda x: x["criticality"], reverse=True)[:top_n]


def _systemic_bridges(g: DirectedGraph, betweenness: dict[str, float]) -> list[dict]:
    """
    Ключевые мосты: узлы с высоким betweenness и низким PageRank.
    Это узлы через которые проходят contagion paths, но сами не кричат.
    """
    bridges = []
    all_pr = list(betweenness.values())
    bt_mean = sum(all_pr) / len(all_pr) if all_pr else 0
    for nid, bt in betweenness.items():
        if bt > bt_mean * 1.5 and g.nodes.get(nid, {}).get("score", 0) < 0.5:
            n = g.nodes[nid]
            bridges.append({
                "id":    nid,
                "type":  n.get("type", ""),
                "label": n.get("label", ""),
                "betweenness": bt,
                "in_degree":  g.in_degree(nid),
                "out_degree": g.out_degree(nid),
            })
    return sorted(bridges, key=lambda x: x["betweenness"], reverse=True)[:5]


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def build_system_graph(events: list[dict], convergence: dict) -> dict:
    """
    Строит полный system graph и возвращает intelligence output.
    """
    g = _build_graph(events, convergence)

    pr  = _pagerank(g)
    bt  = _betweenness_approx(g)

    critical   = _critical_nodes(g, pr, bt)
    bridges    = _systemic_bridges(g, bt)
    sync_clust = _synchronization_clusters(events)

    # Contagion paths из топ-2 critical domain nodes
    domain_critical = [n for n in critical if n["type"] == "domain"][:2]
    contagion_paths = []
    for nc in domain_critical:
        paths = _find_contagion_paths(g, nc["id"])
        for p in paths[:3]:
            contagion_paths.append({
                "start":  nc["id"],
                "path":   p,
                "length": len(p),
            })

    # Dependency clusters (connected components по сильным рёбрам > 0.5)
    dep_clusters: dict[str, list] = defaultdict(list)
    for src, dsts in g.edges.items():
        for dst, edge in dsts.items():
            if edge["weight"] >= 0.5:
                dep_clusters[src].append(dst)

    return {
        "nodes_count":          len(g.nodes),
        "edges_count":          sum(len(e) for e in g.edges.values()),
        "critical_nodes":       critical,
        "systemic_bridges":     bridges,
        "contagion_paths":      contagion_paths,
        "synchronization_clusters": sync_clust,
        "dependency_clusters":  dict(dep_clusters),
        "graph":                g.to_dict(),
    }

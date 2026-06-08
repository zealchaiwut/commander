"""Build a topological DAG from ticket file-overlap data."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Union


class CyclicDependencyError(Exception):
    pass


@dataclass
class DAGResult:
    layers: list[list[str]] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class CycleError:
    """Structured error returned (not raised) when cycle(s) exist in the dependency graph."""
    cycles: list[list[str]]

    def to_payload(self) -> dict:
        return {
            "error": "cycle_detected",
            "cycles": self.cycles,
        }


def _find_all_cycles(adj: dict[str, list[str]], ids: list[str]) -> list[list[str]]:
    """DFS with coloring to find all simple cycles in a directed graph.

    Returns a list of cycles; each cycle is a list of node IDs.
    Cycles are normalized: rotated to start at the lexicographically smallest node,
    ensuring identical cycles discovered via different traversal paths are deduplicated.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in ids}
    path: list[str] = []
    path_set: set[str] = set()
    cycles: list[list[str]] = []
    seen_keys: set[tuple[str, ...]] = set()

    def _dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        path_set.add(node)
        for nb in adj.get(node, []):
            if color[nb] == GRAY and nb in path_set:
                idx = path.index(nb)
                cycle = path[idx:]
                min_pos = cycle.index(min(cycle))
                key = tuple(cycle[min_pos:] + cycle[:min_pos])
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(list(key))
            elif color[nb] == WHITE:
                _dfs(nb)
        path.pop()
        path_set.discard(node)
        color[node] = BLACK

    for nid in ids:
        if color[nid] == WHITE:
            _dfs(nid)

    return cycles


def build_dag(tickets: list[dict[str, Any]]) -> Union[DAGResult, CycleError]:
    """Return topological layers and edges derived from file-overlap between tickets.

    Each ticket must have 'id' (str) and 'files_touched' (list/set of paths).
    For each file, the first ticket (by input order) to touch it becomes the
    owner; directed edges run from that owner to every later ticket sharing
    the same file.  This gives a star topology per file rather than a chain,
    preserving parallel-safe batches (e.g. diamond patterns).

    Returns CycleError (structured payload, not exception) if cycles are detected.
    """
    if not tickets:
        return DAGResult()

    ids = [t["id"] for t in tickets]
    file_sets: dict[str, set[str]] = {
        t["id"]: set(t.get("files_touched") or []) for t in tickets
    }

    adj: dict[str, list[str]] = {tid: [] for tid in ids}
    in_degree: dict[str, int] = {tid: 0 for tid in ids}
    edges_seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    file_first: dict[str, str] = {}

    for tid in ids:
        for f in file_sets[tid]:
            if f not in file_first:
                file_first[f] = tid
            else:
                owner = file_first[f]
                edge = (owner, tid)
                if edge not in edges_seen:
                    edges_seen.add(edge)
                    edges.append(edge)
                    adj[owner].append(tid)
                    in_degree[tid] += 1

    # DFS cycle detection before emitting a run plan.
    found_cycles = _find_all_cycles(adj, ids)
    if found_cycles:
        return CycleError(cycles=found_cycles)

    # Kahn's algorithm for topological sort with layer tracking.
    queue: deque[str] = deque(tid for tid in ids if in_degree[tid] == 0)
    layers: list[list[str]] = []
    visited = 0

    while queue:
        layer = list(queue)
        layers.append(layer)
        queue.clear()
        for node in layer:
            visited += 1
            for neighbour in adj[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

    return DAGResult(layers=layers, edges=edges)

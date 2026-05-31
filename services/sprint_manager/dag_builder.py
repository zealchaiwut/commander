"""Build a topological DAG from ticket file-overlap data."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


class CyclicDependencyError(Exception):
    pass


@dataclass
class DAGResult:
    layers: list[list[str]] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


def build_dag(tickets: list[dict[str, Any]]) -> DAGResult:
    """Return topological layers and edges derived from file-overlap between tickets.

    Each ticket must have 'id' (str) and 'files_touched' (list/set of paths).
    For each file, the first ticket (by input order) to touch it becomes the
    owner; directed edges run from that owner to every later ticket sharing
    the same file.  This gives a star topology per file rather than a chain,
    preserving parallel-safe batches (e.g. diamond patterns).
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

    if visited != len(ids):
        raise CyclicDependencyError("Cycle detected in ticket file-overlap graph")

    return DAGResult(layers=layers, edges=edges)

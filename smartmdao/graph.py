from collections import defaultdict
from typing import List, Dict, Set

from .models import Step


def tarjan_scc(steps: List[Step], adj_list: Dict[Step, List[Step]]) -> List[List[Step]]:
    """Groups steps into strongly connected components (Tarjan's algorithm)."""
    index = 0
    indices = {}
    lowlinks = {}
    stack = []
    on_stack = set()
    sccs = []

    def strongconnect(v):
        nonlocal index
        indices[v] = index
        lowlinks[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)

        for w in adj_list[v]:
            if w not in indices:
                strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif w in on_stack:
                lowlinks[v] = min(lowlinks[v], indices[w])

        if lowlinks[v] == indices[v]:
            new_scc = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                new_scc.append(w)
                if w == v:
                    break
            sccs.append(new_scc)

    for step in steps:
        if step not in indices:
            strongconnect(step)

    return sccs


def map_producers(steps: List[Step]) -> Dict[str, Step]:
    """Maps each variable name to the step that produces it."""
    mapping = {}
    for step in steps:
        for out in step.resolve_output_names():
            mapping[out] = step
    return mapping


def build_dependency_graph(steps: List[Step], input_keys: Set[str], producers_map: Dict[str, Step]):
    """Builds a producer -> consumer adjacency list and an indegree map."""
    adj_list = defaultdict(list)
    indegree = defaultdict(int)

    for s in steps:
        indegree[s] = 0

    for consumer in steps:
        # Use .get_signature() to see through decorators (e.g. @cached)
        sig = consumer.get_signature()
        for param in sig.parameters:

            # PRIORITY: Check if it's an internal producer FIRST.
            if param in producers_map:
                producer = producers_map[param]
                adj_list[producer].append(consumer)
                indegree[consumer] += 1

            # Only if it's NOT produced internally do we check if it's satisfied by inputs.
            elif param in input_keys:
                continue

    return adj_list, indegree

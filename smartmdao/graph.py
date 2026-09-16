from collections import defaultdict, deque
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple

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


def weakly_connected_components(steps: List[Step]) -> List[Tuple[Step, ...]]:
    """
    Groups steps that share a variable, directly or transitively.

    Direction is ignored - two steps are in the same group if a value flows
    between them either way. A well-formed pipeline is a single group: every
    discipline is wired, however indirectly, to every other.

    More than one group means a discipline is connected to nothing else, which
    in a model almost always means it was never wired in. The symptom is a
    pipeline that converges, whose arithmetic is right, and whose answer does
    not depend on half its inputs.

    Groups are returned largest first, each ordered by registration.
    """
    parent: Dict[object, object] = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] is not item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root is not right_root:
            parent[left_root] = right_root

    for step in steps:
        find(step)
        touched = list(step.get_signature().parameters) + list(
            step.resolve_output_names()
        )
        for name in touched:
            # Variables are keyed separately from steps so a shared name joins
            # its producer and every consumer into one group.
            union(step, ("variable", name))

    grouped: Dict[object, List[Step]] = {}
    for step in steps:
        grouped.setdefault(find(step), []).append(step)

    return sorted(
        (tuple(group) for group in grouped.values()),
        key=lambda group: (-len(group), group[0].name),
    )


@dataclass(frozen=True)
class ExecutionBlock:
    """
    One unit of work in a decomposed pipeline: either a single step that runs
    once, or a strongly connected group that has to be iterated to convergence.

    `steps` is already in execution order. For a cyclic block that order is
    alphabetical by step name, which is what keeps a solve deterministic - and
    is also why which variable needs an initial guess depends on what the steps
    are called.
    """
    steps: Tuple[Step, ...]
    is_cyclic: bool


def build_execution_plan(steps: List[Step], input_keys: Set[str]) -> List[ExecutionBlock]:
    """
    Decomposes a pipeline into the blocks a solver would run, in order.

    This is pure structural analysis - nothing is executed. `HybridSolver` uses
    it to drive a solve; `smartmdao.analysis` uses it to describe what a solve
    *would* do without running one. Sharing the function is deliberate: a
    second implementation would drift, and the analysis would start lying.
    """
    producers_map = map_producers(steps)
    adj_list, _ = build_dependency_graph(steps, input_keys, producers_map)

    sccs = tarjan_scc(steps, adj_list)

    # Condensation graph: a DAG whose nodes are the SCCs.
    scc_map = {step: i for i, cluster in enumerate(sccs) for step in cluster}
    scc_adj = defaultdict(set)
    scc_indegree = defaultdict(int)

    for producer in steps:
        producer_scc = scc_map[producer]
        for consumer in adj_list[producer]:
            consumer_scc = scc_map[consumer]
            if producer_scc != consumer_scc and consumer_scc not in scc_adj[producer_scc]:
                scc_adj[producer_scc].add(consumer_scc)
                scc_indegree[consumer_scc] += 1

    for index in range(len(sccs)):
        if index not in scc_indegree:
            scc_indegree[index] = 0

    queue = deque([i for i, degree in scc_indegree.items() if degree == 0])
    plan = []

    while queue:
        current = queue.popleft()
        group = sccs[current]

        # A lone step that doesn't depend on itself runs once; anything else
        # is a feedback loop and has to be iterated.
        if len(group) == 1 and group[0] not in adj_list[group[0]]:
            plan.append(ExecutionBlock(steps=(group[0],), is_cyclic=False))
        else:
            plan.append(
                ExecutionBlock(
                    steps=tuple(sorted(group, key=lambda step: step.name)),
                    is_cyclic=True,
                )
            )

        for neighbor in scc_adj[current]:
            scc_indegree[neighbor] -= 1
            if scc_indegree[neighbor] == 0:
                queue.append(neighbor)

    return plan

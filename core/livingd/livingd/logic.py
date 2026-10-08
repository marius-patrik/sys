"""Pure, deterministic execution contracts. No database or model state lives here."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


CAPABILITIES = {
    "text.echo": {"in": {"value": "text"}, "out": {"value": "text"}},
    "text.upper": {"in": {"value": "text"}, "out": {"value": "text"}},
    "text.prefix": {"in": {"value": "text", "prefix": "text"}, "out": {"value": "text"}},
    "view.text": {"in": {"value": "text"}, "out": {"view": "view"}},
    "memory.search": {"in": {"query": "text"}, "out": {"value": "text"}},
    "memory.remember": {"in": {"value": "text"}, "out": {"value": "text"}},
    "model.answer": {"in": {"question": "text", "context": "text"}, "out": {"value": "text"}},
    "input.dispatch": {"in": {"value": "text"}, "out": {"view": "view"}},
    "graph.compose": {"in": {"value": "text"}, "out": {"value": "text"}},
    "program.python": {"in": {"code": "text"}, "out": {"value": "text"}},
    "memory.attend": {"in": {"value": "text"}, "out": {"view": "view"}},
    "memory.approve": {"in": {"value": "text"}, "out": {"view": "view"}},
}


class GraphValidationError(ValueError):
    pass


def validate_graph(graph: dict[str, Any]) -> list[str]:
    """Validate a closed, typed, acyclic bootstrap graph; return topo order."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise GraphValidationError("nodes must be a list")
    nodes = graph["nodes"]
    if not nodes or len(nodes) > 64:
        raise GraphValidationError("graph must contain 1–64 nodes")
    by_id: dict[str, dict] = {}
    for node in nodes:
        key = node.get("id")
        if not isinstance(key, str) or not key or key in by_id:
            raise GraphValidationError("duplicate/invalid node id")
        if node.get("capability") not in CAPABILITIES:
            raise GraphValidationError(f"unknown capability for node {key}")
        if not isinstance(node.get("inputs"), dict):
            raise GraphValidationError(f"missing input bindings: {key}")
        by_id[key] = node
    input_types = graph.get("inputs", {})
    if not isinstance(input_types, dict):
        raise GraphValidationError("graph inputs must be object")
    adjacency = {k: set() for k in by_id}
    indegree = dict.fromkeys(by_id, 0)
    for key, node in by_id.items():
        required = CAPABILITIES[node["capability"]]["in"]
        if set(node["inputs"]) != set(required):
            raise GraphValidationError(f"input ports mismatch for {key}")
        for port, expected in required.items():
            binding = node["inputs"][port]
            if not isinstance(binding, dict):
                raise GraphValidationError(f"invalid binding at {key}.{port}")
            if "literal" in binding and len(binding) == 1:
                actual = "text" if isinstance(binding["literal"], str) else None
            elif "input" in binding and len(binding) == 1:
                actual = input_types.get(binding["input"])
            elif "node" in binding and "port" in binding and len(binding) == 2:
                origin = binding["node"]
                if origin not in by_id:
                    raise GraphValidationError(f"unknown input node {origin}")
                actual = CAPABILITIES[by_id[origin]["capability"]]["out"].get(binding["port"])
                if key not in adjacency[origin]:
                    adjacency[origin].add(key)
                    indegree[key] += 1
            else:
                raise GraphValidationError(f"invalid binding at {key}.{port}")
            if actual != expected:
                raise GraphValidationError(f"type mismatch at {key}.{port}: {actual} != {expected}")
    order = []
    ready = sorted(k for k, d in indegree.items() if d == 0)
    while ready:
        node = ready.pop(0)
        order.append(node)
        for successor in sorted(adjacency[node]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(order) != len(by_id):
        raise GraphValidationError("graph has an unbounded cycle")
    for key, binding in graph.get("outputs", {}).items():
        if not isinstance(binding, dict) or binding.get("node") not in by_id:
            raise GraphValidationError(f"invalid output binding {key}")
        ports = CAPABILITIES[by_id[binding["node"]]["capability"]]["out"]
        if binding.get("port") not in ports:
            raise GraphValidationError(f"unknown output port {key}")
    return order


def dependencies(graph: dict, node_id: str) -> set[str]:
    n = next(n for n in graph["nodes"] if n["id"] == node_id)
    return {b["node"] for b in n["inputs"].values() if "node" in b}


def node_inputs(graph: dict, node_id: str, inputs: dict, outputs: dict[str, dict]) -> dict:
    n = next(n for n in graph["nodes"] if n["id"] == node_id)
    result = {}
    for port, b in n["inputs"].items():
        if "literal" in b:
            result[port] = b["literal"]
        elif "input" in b:
            result[port] = inputs[b["input"]]
        else:
            result[port] = outputs[b["node"]][b["port"]]
    return result


def execute_pure(capability: str, inputs: dict) -> dict:
    if capability == "text.echo":
        return {"value": inputs["value"]}
    if capability == "text.upper":
        return {"value": inputs["value"].upper()}
    if capability == "text.prefix":
        return {"value": inputs["prefix"] + inputs["value"]}
    if capability == "view.text":
        return {"view": {"type": "text", "value": inputs["value"]}}
    raise GraphValidationError(f"refusing capability: {capability}")


def selected_outputs(graph: dict, outputs: dict) -> dict:
    return {name: outputs[b["node"]][b["port"]] for name, b in graph["outputs"].items()}


def due_nodes(graph: dict, completed: set[str], running: set[str]) -> list[str]:
    return [n for n in validate_graph(graph) if n not in completed and n not in running and dependencies(graph, n) <= completed]

"""Small dependency graph with Hamilton-compatible ownership semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeatureNode:
    name: str
    dependencies: tuple[str, ...]
    compute: Callable[[Mapping[str, object]], object]
    version: str


class FeatureGraph:
    def __init__(self, nodes: tuple[FeatureNode, ...]) -> None:
        normalized_nodes = tuple(
            FeatureNode(
                name=node.name.strip(),
                dependencies=tuple(dependency.strip() for dependency in node.dependencies),
                compute=node.compute,
                version=node.version.strip(),
            )
            for node in nodes
        )
        self.nodes = {node.name: node for node in normalized_nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError("feature names must be unique")
        if any(
            not node.name.strip()
            or not node.version.strip()
            or any(not dependency.strip() for dependency in node.dependencies)
            for node in normalized_nodes
        ):
            raise ValueError("feature nodes require names, versions, and dependencies")

    def compute(
        self, requested: tuple[str, ...], inputs: Mapping[str, object]
    ) -> dict[str, object]:
        normalized_requested = tuple(name.strip() for name in requested)
        if not normalized_requested or any(not name for name in normalized_requested):
            raise ValueError("feature requests must contain named features")
        if len(normalized_requested) != len(set(normalized_requested)):
            raise ValueError("feature requests must be unique")
        values = dict(inputs)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ValueError("feature dependency cycle")
            try:
                node = self.nodes[name]
            except KeyError as exc:
                raise ValueError(f"unknown feature dependency {name!r}") from exc
            visiting.add(name)
            for dependency in node.dependencies:
                if dependency not in values:
                    visit(dependency)
            values[name] = node.compute(values)
            visiting.remove(name)
            visited.add(name)

        for name in normalized_requested:
            visit(name)
        return {name: values[name] for name in normalized_requested}

"""Validate and print the local service-boundary manifest."""

from __future__ import annotations

from .boundaries import ServiceRegistry


def main() -> int:
    registry = ServiceRegistry()
    for descriptor in registry.startup_order():
        print(f"{descriptor.kind.value}\t{descriptor.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

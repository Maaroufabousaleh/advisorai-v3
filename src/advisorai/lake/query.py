"""Local DuckDB/Polars query boundary over immutable Parquet artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import polars as pl


class LakeQuery:
    """Query client only; it cannot mutate the lake or ledger authority."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def sql(self, query: str) -> list[tuple[object, ...]]:
        normalized = query.strip().lower()
        if ";" in normalized:
            raise PermissionError("LakeQuery accepts one read-only statement at a time")
        if not re.match(r"^(select|with)\b", normalized):
            raise PermissionError("LakeQuery is read-only and accepts SELECT/WITH queries only")
        if re.search(
            r"\b(insert|update|delete|create|drop|alter|attach|copy|export|install|load|pragma|vacuum|truncate)\b",
            normalized,
        ):
            raise PermissionError("LakeQuery is read-only")
        # DuckDB table functions can otherwise read arbitrary local files or
        # remote URLs. Only paths beneath the configured local lake are valid.
        path_functions = (
            r"\b(read_parquet|parquet_scan|read_csv|read_json|read_ndjson|glob)"
            r"\s*\(\s*['\"]([^'\"]+)"
        )
        for _function, raw_path in re.findall(
            path_functions,
            query,
            flags=re.IGNORECASE,
        ):
            if "://" in raw_path:
                raise PermissionError("LakeQuery cannot read remote URLs")
            candidate = Path(raw_path)
            resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
            root = self.root.resolve()
            if resolved != root and root not in resolved.parents:
                raise PermissionError("LakeQuery cannot read outside the local lake root")
        # DuckDB also accepts a bare file path in FROM/JOIN clauses.  Apply
        # the same local-root check to that shorthand so it cannot bypass the
        # table-function guard above.
        for raw_path in re.findall(
            r"\b(?:from|join)\s*['\"]([^'\"]+)['\"]", query, flags=re.IGNORECASE
        ):
            if "://" in raw_path:
                raise PermissionError("LakeQuery cannot read remote URLs")
            candidate = Path(raw_path)
            resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
            root = self.root.resolve()
            if resolved != root and root not in resolved.parents:
                raise PermissionError("LakeQuery cannot read outside the local lake root")
        with duckdb.connect(database=":memory:") as connection:
            return [tuple(row) for row in connection.execute(query).fetchall()]

    def scan(self, parquet_glob: str) -> pl.DataFrame:
        root = self.root.resolve()
        path = (root / parquet_glob).resolve()
        if path != root and root not in path.parents:
            raise PermissionError("LakeQuery cannot scan outside the local lake root")
        return pl.scan_parquet(path).collect()

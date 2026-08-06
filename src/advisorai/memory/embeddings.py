"""Deterministic CPU retrieval candidate kept below authoritative memory."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class HashingEmbedder:
    """Small dependency-free signed-hash embedder for optional recall.

    This is a retrieval candidate, not a semantic authority.  It stores no
    source text or model state and is deterministic across processes for a
    pinned version and dimension.  FTS5 remains the default memory index.
    """

    dimension: int = 128
    version: str = "hashing-embed-v1"

    def __post_init__(self) -> None:
        if self.dimension < 8:
            raise ValueError("hashing embedder dimension must be at least eight")
        if not self.version.strip():
            raise ValueError("hashing embedder version cannot be blank")

    def embed(self, text: str) -> tuple[float, ...]:
        tokens = _TOKEN.findall(text.lower())
        if not tokens:
            raise ValueError("embedding text cannot be blank")
        values = [0.0] * self.dimension
        for token in tokens:
            digest = hashlib.sha256(f"{self.version}:{token}".encode()).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0 or not math.isfinite(norm):
            raise ValueError("embedding norm is invalid")
        return tuple(value / norm for value in values)

    def similarity(self, left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != self.dimension or len(right) != self.dimension:
            raise ValueError("embedding dimensions do not match")
        if any(not math.isfinite(value) for value in (*left, *right)):
            raise ValueError("embedding values must be finite")
        return sum(a * b for a, b in zip(left, right, strict=True))


__all__ = ["HashingEmbedder"]

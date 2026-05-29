"""Indexer service: build embeddings from library, persist."""
from .build import build_index, IndexerOptions

__all__ = ["build_index", "IndexerOptions"]

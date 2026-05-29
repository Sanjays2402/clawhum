"""CLAP audio embeddings with batching and device autoselect."""
from .clap import ClapEmbedder, select_device
from .base import Embedder
from .fallback import HashEmbedder

__all__ = ["ClapEmbedder", "Embedder", "HashEmbedder", "select_device"]

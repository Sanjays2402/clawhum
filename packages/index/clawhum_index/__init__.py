"""Vector index: FAISS HNSW with NumPy fallback. Persistence + incremental add."""
from .base import VectorIndex, IndexedItem
from .faiss_index import FaissHNSW
from .numpy_index import NumpyIndex
from .factory import make_index

__all__ = ["VectorIndex", "IndexedItem", "FaissHNSW", "NumpyIndex", "make_index"]

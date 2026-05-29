from __future__ import annotations
import hashlib
import time
from collections import OrderedDict
from typing import Any


class LRUCache:
    def __init__(self, capacity: int = 256, ttl_s: float = 600.0):
        self.capacity = capacity
        self.ttl = ttl_s
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    @staticmethod
    def key_for(payload: bytes) -> str:
        return hashlib.sha1(payload).hexdigest()

    def get(self, key: str):
        v = self._d.get(key)
        if v is None:
            return None
        ts, val = v
        if time.time() - ts > self.ttl:
            self._d.pop(key, None)
            return None
        self._d.move_to_end(key)
        return val

    def put(self, key: str, value: Any) -> None:
        self._d[key] = (time.time(), value)
        self._d.move_to_end(key)
        while len(self._d) > self.capacity:
            self._d.popitem(last=False)

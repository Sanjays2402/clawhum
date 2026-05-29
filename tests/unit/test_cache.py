from clawhum_match.cache import LRUCache


def test_lru_basic():
    c = LRUCache(capacity=2)
    c.put("a", 1); c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)  # evicts b (a was just used)
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


def test_key_for_deterministic():
    assert LRUCache.key_for(b"x") == LRUCache.key_for(b"x")

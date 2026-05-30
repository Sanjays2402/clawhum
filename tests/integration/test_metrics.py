from fastapi.testclient import TestClient


def _boot(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    return create_app()


def test_metrics_exposition_is_valid_prometheus_text(monkeypatch, tmp_path):
    """The /metrics endpoint returns valid Prometheus text exposition."""
    app = _boot(monkeypatch, tmp_path)
    with TestClient(app) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        # Core domain gauges with HELP/TYPE lines from prometheus_client.
        assert "# HELP clawhum_uptime_seconds" in body
        assert "# TYPE clawhum_uptime_seconds gauge" in body
        assert "clawhum_index_vectors" in body
        assert "clawhum_index_tracks" in body
        # Histogram is registered eagerly so its HELP line is present
        # even before any traffic has been observed.
        assert "# TYPE clawhum_http_request_duration_seconds histogram" in body
        assert "# TYPE clawhum_http_requests_total counter" in body


def test_request_metrics_recorded_with_route_label(monkeypatch, tmp_path):
    """Hitting a real route records a labelled counter sample."""
    app = _boot(monkeypatch, tmp_path)
    with TestClient(app) as c:
        # /health is excluded from rate limiting and audit and is a
        # stable matched route, so it is the simplest signal source.
        for _ in range(3):
            assert c.get("/health").status_code == 200
        body = c.get("/metrics").text

    # Counter line with labels for method, route, and status. The exact
    # quoting follows the Prometheus exposition format, where labels
    # are listed alphabetically by name.
    assert 'clawhum_http_requests_total{method="GET",route="/health",status="200"}' in body
    # Histogram bucket and count series are emitted for the same route.
    assert 'clawhum_http_request_duration_seconds_count{method="GET",route="/health"}' in body
    assert 'clawhum_http_request_duration_seconds_bucket{le="+Inf",method="GET",route="/health"}' in body


def test_metrics_endpoint_is_not_counted_in_request_metrics(monkeypatch, tmp_path):
    """Scraping /metrics must not pollute its own series."""
    app = _boot(monkeypatch, tmp_path)
    with TestClient(app) as c:
        # Two scrapes.
        c.get("/metrics")
        body = c.get("/metrics").text
    # No counter line for the metrics route itself.
    assert 'route="/metrics"' not in body

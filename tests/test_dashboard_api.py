"""Smoke tests for the read-only dashboard API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from controlplane.dashboard.app import app

client = TestClient(app)


def test_index_is_html_and_uncached():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ControlPlane" in r.text
    assert r.headers.get("cache-control") == "no-store"


def test_static_assets_present():
    for path in (
        "/static/app.css",
        "/static/app.js",
        "/static/charts.js",
        "/static/favicon.svg",
        "/static/og.jpg",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.content) > 100, path


def test_policy_endpoint_returns_tiers_and_workloads():
    r = client.get("/api/policy")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tiers"]) == 3
    assert all(t.get("description") for t in body["tiers"])
    assert {w["id"] for w in body["workloads"]} >= {"A", "B", "C"}


def test_experiment_includes_lag_strata():
    r = client.get("/api/experiment")
    assert r.status_code == 200
    loc = r.json().get("localization_vs_baselines") or {}
    assert loc.get("featured_baseline") == "previous_step"
    assert "by_lag" in loc
    assert loc["by_lag"]["caught_late"]["n"] > 0
    assert loc["ours"]["exact_step_pct"] == 100.0


def test_incidents_feed_has_scored_rows():
    r = client.get("/api/incidents")
    assert r.status_code == 200
    feed = r.json()
    assert len(feed) > 0
    scored = [i for i in feed if i["localization_error"] is not None]
    assert len(scored) >= 20
    assert any((i.get("delta_detect") or 0) >= 40 for i in scored)


def test_guards_attest_sabotage():
    r = client.get("/api/guards")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == 20
    assert body["sabotage_validated"] >= 18

"""The composed app: new routes reachable, demo routes untouched.

The failure this file mostly exists to catch is route ordering. `shadow/server.py` finishes by
mounting `ui/` at "/", which matches every path, and Starlette tries routes in the order they were
added. A router appended after that mount is shadowed completely, and every one of its endpoints
returns the index page with a 200, which reads like a front-end bug rather than a wiring mistake.
"""
import shutil

import pytest
from fastapi.testclient import TestClient

from shadow import db
from shadow.onboard import company

pytestmark = pytest.mark.skipif(not db.db_path("A").exists(),
                                reason="run `uv run python -m sim.build` first")


@pytest.fixture(scope="module")
def client():
    from shadow.app import app
    return TestClient(app)


def test_new_routes_are_not_shadowed_by_the_static_mount(client):
    res = client.get("/api/onboarding/health")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json"), \
        "the static mount is matching first; the routers must be added before it"
    assert res.json()["ok"] is True


def test_static_files_are_still_served(client):
    for path in ("/", "/index.html", "/queue.html", "/playbook.html", "/results.html"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert b"<!doctype html>" in res.content[:40].lower()


def test_the_company_screens_are_served(client):
    for path in ("/company/setup.html", "/company/import.html",
                 "/company/playbook.html", "/company/reconcile.html"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert b"Shadow Onboarding" in res.content


def test_the_demo_api_is_unchanged(client):
    ids = {c["id"] for c in client.get("/api/clients").json()}
    assert {"A", "B"} <= ids, "the demo clients must still be listed"
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/playbook/A").status_code == 200


def test_a_real_company_is_served_by_the_existing_client_routes(client):
    """The point of rebinding the server's client list: the queue, corrections and playbook
    routes start working for a real company without that file changing."""
    co = "ROUTECO"
    shutil.rmtree(db.DATA / co, ignore_errors=True)
    res = client.post("/api/onboarding/company", json={
        "id": co, "name": "Route Co", "blurb": "Testing.",
        "chart": {"1010": "Cash"},
        "users": [{"id": "sam", "name": "Sam", "role": "controller", "senior": True}],
        "reconciler": "sam"})
    assert res.status_code == 200, res.text

    from shadow import server
    company.register(None)
    assert co in server.CLIENTS

    assert client.get(f"/api/record/{co}/nope").status_code in (200, 404)
    assert client.get(f"/api/corrections/{co}").status_code == 200
    assert co in {c["id"] for c in client.get("/api/clients").json()}


def test_setup_rejects_a_company_with_no_senior(client):
    res = client.post("/api/onboarding/company", json={
        "id": "NOSEN", "name": "No Seniors", "chart": {"1010": "Cash"},
        "users": [{"id": "ana", "name": "Ana", "role": "clerk", "senior": False}]})
    assert res.status_code == 400
    assert "senior" in res.json()["detail"]


def test_upload_needs_a_body(client):
    res = client.post("/api/onboarding/upload?role=bank_lines&filename=x.csv", content=b"")
    assert res.status_code == 400


def test_unknown_job_is_a_404(client):
    assert client.get("/api/jobs/nope_123").status_code == 404

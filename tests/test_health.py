"""Checks that the app starts and the health endpoint answers."""


def test_health_returns_ok(client):
    # /health needs no login and never calls AWS.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_static_css_is_served(client):
    # The stylesheet must be reachable, or every page will look unstyled.
    response = client.get("/static/style.css")
    assert response.status_code == 200

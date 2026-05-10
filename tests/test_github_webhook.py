import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import app as app_module


def signed_body(payload, secret="test-secret"):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return body, signature


def webhook_payload(action="opened"):
    return {
        "action": action,
        "repository": {
            "name": "demo-repo",
            "owner": {"login": "octocat"}
        },
        "pull_request": {
            "number": 42,
            "state": "open"
        }
    }


class FakeResponse:
    def __init__(self, text="", json_data=None):
        self.text = text
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        return None


class FakeAsyncClient:
    instances = []

    def __init__(self, *args, comments=None, **kwargs):
        self.comments = comments if comments is not None else []
        self.calls = []
        FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers=None):
        self.calls.append(("GET", url, headers, None))
        if "/pulls/" in url:
            return FakeResponse(text="diff --git a/app.py b/app.py")
        return FakeResponse(json_data=self.comments)

    async def patch(self, url, headers=None, json=None):
        self.calls.append(("PATCH", url, headers, json))
        return FakeResponse(json_data={"id": 100})

    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, headers, json))
        return FakeResponse(json_data={"id": 101})


def install_fake_client(monkeypatch, comments=None):
    FakeAsyncClient.instances = []

    class ClientFactory(FakeAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, comments=comments, **kwargs)

    monkeypatch.setattr(app_module.httpx, "AsyncClient", ClientFactory)


def post_webhook(client, payload, signature, event="pull_request"):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/github/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature
        }
    )


def test_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")

    client = TestClient(app_module.app)
    response = post_webhook(client, webhook_payload(), "sha256=bad")

    assert response.status_code == 401


def test_review_endpoint_returns_structured_diagnostics(monkeypatch):
    class FakeGraph:
        def invoke(self, state):
            return {
                "initial_analysis": "analysis",
                "issues": ["use a clearer name"],
                "final_report": "## Summary\nNeeds a naming pass.",
                "diagnostics": [{
                    "line": 2,
                    "column": 5,
                    "severity": "warning",
                    "message": "Use a clearer name."
                }]
            }

    class FakeAgent:
        graph = FakeGraph()

    monkeypatch.setattr(app_module, "agent", FakeAgent())

    client = TestClient(app_module.app)
    response = client.post("/review", json={"code": "x = 1\nprint(x)"})

    assert response.status_code == 200
    assert response.json() == {
        "analysis": "analysis",
        "issues": ["use a clearer name"],
        "report": "## Summary\nNeeds a naming pass.",
        "diagnostics": [{
            "line": 2,
            "column": 5,
            "severity": "warning",
            "message": "Use a clearer name."
        }]
    }


def test_review_endpoint_drops_invalid_diagnostics(monkeypatch):
    class FakeGraph:
        def invoke(self, state):
            return {
                "initial_analysis": "analysis",
                "issues": [],
                "final_report": "No line-specific issues.",
                "diagnostics": [{
                    "line": "not-a-line",
                    "severity": "critical",
                    "message": "bad"
                }]
            }

    class FakeAgent:
        graph = FakeGraph()

    monkeypatch.setattr(app_module, "agent", FakeAgent())

    client = TestClient(app_module.app)
    response = client.post("/review", json={"code": "print(1)"})

    assert response.status_code == 200
    assert response.json()["diagnostics"] == []


def test_webhook_ignores_unsupported_event(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")

    payload = webhook_payload()
    body, signature = signed_body(payload)
    client = TestClient(app_module.app)

    response = client.post(
        "/github/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature
        }
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "unsupported event"}


def test_webhook_fetches_diff_and_updates_existing_comment(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(
        app_module,
        "run_review",
        lambda diff: {"analysis": "ok", "issues": [], "report": f"reviewed {diff}"}
    )
    install_fake_client(
        monkeypatch,
        comments=[{
            "id": 100,
            "url": "https://api.github.com/repos/octocat/demo-repo/issues/comments/100",
            "body": f"{app_module.REVIEW_COMMENT_MARKER}\nold"
        }]
    )

    payload = webhook_payload()
    _, signature = signed_body(payload)
    client = TestClient(app_module.app)

    response = post_webhook(client, payload, signature)

    assert response.status_code == 200
    assert response.json()["comment"] == {"action": "updated", "comment_id": 100}

    fake_client = FakeAsyncClient.instances[0]
    diff_call = fake_client.calls[0]
    update_call = fake_client.calls[-1]

    assert diff_call[0] == "GET"
    assert diff_call[1].endswith("/repos/octocat/demo-repo/pulls/42")
    assert diff_call[2]["Accept"] == "application/vnd.github.diff"
    assert update_call[0] == "PATCH"
    assert app_module.REVIEW_COMMENT_MARKER in update_call[3]["body"]
    assert "reviewed diff --git" in update_call[3]["body"]


def test_webhook_creates_comment_when_marker_is_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(
        app_module,
        "run_review",
        lambda diff: {"analysis": "ok", "issues": [], "report": "new report"}
    )
    install_fake_client(monkeypatch, comments=[])

    payload = webhook_payload("synchronize")
    _, signature = signed_body(payload)
    client = TestClient(app_module.app)

    response = post_webhook(client, payload, signature)

    assert response.status_code == 200
    assert response.json()["comment"] == {"action": "created", "comment_id": 101}
    assert FakeAsyncClient.instances[0].calls[-1][0] == "POST"

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from stravalib.exc import ActivityUploadFailed

from wattdisco.export.strava import Strava, StravaError, StravaKeys, duplicate_activity_id, load_keys, relay_post

KEYS = StravaKeys(12345, "shh")
SHARED = StravaKeys(999, relay_url="https://relay.example")


class FakeStravaApi:
    """Stands in for stravalib.Client: records calls, never touches the network."""

    def __init__(self):
        self.calls = []
        self.upload_error = None
        self.now = 1_000_000

    def factory(self, access_token=None):
        api = self

        class Client:
            def authorization_url(self, **kw):
                api.calls.append(("authorize", kw))
                return f"https://www.strava.com/oauth/authorize?client_id={kw['client_id']}&state={kw['state']}"

            def exchange_code_for_token(self, **kw):
                api.calls.append(("exchange", kw))
                access = {"access_token": "a1", "refresh_token": "r1", "expires_at": api.now + 21600}
                return access, SimpleNamespace(id=7, firstname="Test", lastname="Rider")

            def refresh_access_token(self, **kw):
                api.calls.append(("refresh", kw))
                return {"access_token": "a2", "refresh_token": "r2", "expires_at": api.now + 21600}

            def upload_activity(self, f, **kw):
                api.calls.append(("upload", access_token, len(f.read()), kw))
                uploader = SimpleNamespace(activity_id=None, polls=0)

                def poll():
                    uploader.polls += 1
                    if api.upload_error:
                        raise api.upload_error
                    if uploader.polls >= 2:  # Strava needs a moment to process the file
                        uploader.activity_id = 99

                def wait(**_):
                    raise AssertionError("wait() fetches the activity, which needs read access")

                uploader.poll, uploader.wait = poll, wait
                return uploader

            def get_activity(self, activity_id):
                raise AssertionError("fetching the activity needs read access the app does not have")

            def update_activity(self, activity_id, **kw):
                api.calls.append(("update", activity_id, kw))

            def deauthorize(self):
                api.calls.append(("deauthorize", access_token))

        return Client()


@pytest.fixture
def api():
    return FakeStravaApi()


@pytest.fixture
def strava(tmp_path, api):
    return Strava(KEYS, tmp_path, client_factory=api.factory, clock=lambda: api.now, sleep=lambda s: None)


def sign_in(strava):
    url = strava.authorize_url("http://127.0.0.1:8765/api/strava/callback", "/upload.html?ride=1")
    state = url.split("state=")[1]
    return strava.finish_sign_in("code123", state, "read,activity:write")


def test_load_keys(tmp_path, monkeypatch):
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_RELAY_URL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    none = {"shared_id": None, "shared_relay": None}  # a build without the shared app
    assert load_keys(env, **none) is None
    env.write_text("STRAVA_CLIENT_ID=\nSTRAVA_CLIENT_SECRET=\n")
    assert load_keys(env, **none) is None
    env.write_text("STRAVA_CLIENT_ID=abc\nSTRAVA_CLIENT_SECRET=x\n")
    assert load_keys(env, **none) is None
    env.write_text("STRAVA_CLIENT_ID=12345\nSTRAVA_CLIENT_SECRET=shh\n")
    assert load_keys(env, **none) == KEYS


def test_the_app_uses_the_shared_watt_disco_app_and_relay(tmp_path, monkeypatch):
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_RELAY_URL"):
        monkeypatch.delenv(name, raising=False)
    keys = load_keys(tmp_path / ".env")
    assert keys.mode == "shared" and keys.client_secret is None
    assert keys.relay_url.startswith("https://") and keys.relay_url.endswith(".workers.dev")


def test_load_keys_shared_app(tmp_path, monkeypatch):
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_RELAY_URL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    shared = {"shared_id": 999, "shared_relay": "https://relay.example/"}
    assert load_keys(env, **shared) == SHARED and SHARED.mode == "shared"
    env.write_text("STRAVA_CLIENT_ID=12345\nSTRAVA_CLIENT_SECRET=shh\n")
    assert load_keys(env, **shared) == KEYS and KEYS.mode == "own"  # own keys win
    env.write_text("STRAVA_CLIENT_ID=4321\nSTRAVA_RELAY_URL=https://other.example\n")
    assert load_keys(env, **shared) == StravaKeys(4321, relay_url="https://other.example")
    env.write_text("STRAVA_RELAY_URL=http://relay.example\n")  # tokens never over plain http
    assert load_keys(env, **shared) is None
    env.write_text("")
    assert load_keys(env, shared_id=None, shared_relay="https://relay.example") is None


def test_not_configured(tmp_path):
    s = Strava(None, tmp_path)
    assert s.status() == {"configured": False, "connected": False, "athlete": None, "mode": None}
    with pytest.raises(StravaError):
        s.authorize_url("http://x/cb")


def test_sign_in_stores_tokens_privately(strava, api, tmp_path):
    assert sign_in(strava) == "/upload.html?ride=1"
    assert strava.status() == {"configured": True, "connected": True, "athlete": "Test Rider", "mode": "own"}
    authorize = next(kw for name, kw in api.calls if name == "authorize")
    assert authorize["scope"] == ["read", "activity:write"]
    if os.name != "nt":  # Windows has no such mode; the folder itself belongs to the user there
        mode = stat.S_IMODE(os.stat(tmp_path / "strava_tokens.json").st_mode)
        assert mode == 0o600


def test_sign_in_rejects_unknown_state_and_missing_scope(strava):
    with pytest.raises(StravaError):
        strava.finish_sign_in("code", "made-up-state", "read,activity:write")
    url = strava.authorize_url("http://x/cb")
    with pytest.raises(StravaError, match="upload"):
        strava.finish_sign_in("code", url.split("state=")[1], "read")
    assert not strava.connected


def test_return_page_must_be_local(strava):
    url = strava.authorize_url("http://x/cb", "https://evil.example/")
    assert strava.finish_sign_in("c", url.split("state=")[1], "activity:write") == "/"


def test_upload_and_settings_on_the_activity(strava, api, tmp_path):
    sign_in(strava)
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x" * 100)
    result = strava.upload(fit, "20261102-165013", "Sweet spot", "desc", sport_type="VirtualRide", private=True)
    assert result == {"state": "done", "activity_id": 99, "url": "https://www.strava.com/activities/99", "message": "Uploaded"}
    upload = next(c for c in api.calls if c[0] == "upload")
    assert upload[1] == "a1" and upload[2] == 100
    assert upload[3]["external_id"] == "wattdisco-20261102-165013" and upload[3]["trainer"] is True
    assert ("update", 99, {"sport_type": "VirtualRide", "private": True, "trainer": True}) in api.calls


def test_expired_token_is_refreshed_before_upload(strava, api, tmp_path):
    sign_in(strava)
    api.now += 21600  # six hours later
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x")
    strava.upload(fit, "20261102-165013", "Ride")
    assert any(c[0] == "refresh" for c in api.calls)
    assert next(c for c in api.calls if c[0] == "upload")[1] == "a2"


def test_duplicate_upload(strava, api, tmp_path):
    sign_in(strava)
    api.upload_error = ActivityUploadFailed("file.fit duplicate of activity 4242")
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x")
    result = strava.upload(fit, "20261102-165013", "Ride")
    assert result["state"] == "duplicate" and result["activity_id"] == 4242


def test_failed_upload_is_a_plain_error(strava, api, tmp_path):
    sign_in(strava)
    api.upload_error = ActivityUploadFailed("Improperly formatted data")
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x")
    with pytest.raises(StravaError, match="could not process"):
        strava.upload(fit, "20261102-165013", "Ride")


def test_disconnect(strava, api):
    sign_in(strava)
    strava.disconnect()
    assert not strava.connected
    assert ("deauthorize", "a1") in api.calls


def test_duplicate_id_parsing():
    assert duplicate_activity_id("x duplicate of <a href='/activities/123'>ride</a>") == 123
    assert duplicate_activity_id("Improperly formatted") is None


def test_update_failure_still_counts_as_uploaded(strava, api, tmp_path):
    sign_in(strava)
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x")
    original = api.factory

    def factory(access_token=None):
        client = original(access_token)
        if access_token:
            def broken(*a, **k):
                raise RuntimeError("403")
            client.update_activity = broken
        return client

    strava._client_factory = factory
    result = strava.upload(fit, "20261102-165013", "Ride")
    assert result["state"] == "done" and result["activity_id"] == 99
    assert "visibility could not be set" in result["message"]


# ---------- shared app through the relay ----------

class FakeRelay:
    def __init__(self, api):
        self.api, self.calls = api, []

    def post(self, url, payload):
        self.calls.append((url, payload))
        n = len(self.calls)
        out = {"access_token": f"s{n}", "refresh_token": f"sr{n}", "expires_at": self.api.now + 21600}
        if url.endswith("/token"):
            out["athlete"] = {"id": 7, "firstname": "Test", "lastname": "Rider", "city": "not kept"}
        return out


@pytest.fixture
def shared(tmp_path, api):
    relay = FakeRelay(api)
    s = Strava(SHARED, tmp_path, client_factory=api.factory, clock=lambda: api.now, sleep=lambda s: None, post=relay.post)
    return s, relay


def test_shared_sign_in_goes_through_the_relay(shared, api, tmp_path):
    strava, relay = shared
    assert sign_in(strava) == "/upload.html?ride=1"
    assert relay.calls == [("https://relay.example/token", {"code": "code123"})]
    assert not any(c[0] == "exchange" for c in api.calls)  # no secret here to exchange with
    authorize = next(kw for name, kw in api.calls if name == "authorize")
    assert authorize["client_id"] == 999
    assert strava.status() == {"configured": True, "connected": True, "athlete": "Test Rider", "mode": "shared"}
    stored = json.loads((tmp_path / "strava_tokens.json").read_text())
    assert stored["athlete"] == {"id": 7, "firstname": "Test", "lastname": "Rider"}


def test_shared_refresh_and_upload(shared, api, tmp_path):
    strava, relay = shared
    sign_in(strava)
    api.now += 21600
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"x")
    strava.upload(fit, "20261102-165013", "Ride")
    assert relay.calls[-1] == ("https://relay.example/refresh", {"refresh_token": "sr1"})
    assert not any(c[0] == "refresh" for c in api.calls)
    assert next(c for c in api.calls if c[0] == "upload")[1] == "s2"  # uploads go straight to Strava


def test_shared_disconnect_revokes_at_strava(shared, api):
    strava, _ = shared
    sign_in(strava)
    strava.disconnect()
    assert not strava.connected and ("deauthorize", "s1") in api.calls


def test_incomplete_relay_answer_is_a_plain_error(tmp_path, api):
    s = Strava(SHARED, tmp_path, client_factory=api.factory, post=lambda url, payload: {"error": "nope"})
    url = s.authorize_url("http://x/cb")
    with pytest.raises(StravaError, match="incomplete"):
        s.finish_sign_in("c", url.split("state=")[1], "activity:write")
    assert not s.connected


@pytest.fixture
def local_relay():
    """A real HTTP server standing in for the Worker."""
    answers = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, out = answers[self.path]
            out = out(body) if callable(out) else out
            data = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", answers
    server.shutdown()


def test_relay_post(local_relay):
    base, answers = local_relay
    answers["/token"] = (200, lambda body: {"echo": body["code"]})
    assert relay_post(f"{base}/token", {"code": "abc"}) == {"echo": "abc"}
    answers["/token"] = (400, {"error": "Bad Request"})
    with pytest.raises(StravaError, match="Bad Request"):
        relay_post(f"{base}/token", {"code": "abc"})
    answers["/token"] = (403, {"error": "Forbidden", "detail": "limit of connected athletes exceeded"})
    with pytest.raises(StravaError, match="full for now"):
        relay_post(f"{base}/token", {"code": "abc"})
    answers["/token"] = (400, {"error": "Forbidden"})  # the first relay: no detail, 403 sent as 400
    with pytest.raises(StravaError, match="full for now"):
        relay_post(f"{base}/token", {"code": "abc"})
    answers["/token"] = (429, {"error": "Rate Limit Exceeded"})
    with pytest.raises(StravaError, match="busy"):
        relay_post(f"{base}/token", {"code": "abc"})


def test_relay_trusts_the_bundled_certificates(monkeypatch):
    import ssl

    from wattdisco.export import strava as module

    seen = {}
    monkeypatch.setenv("SSL_CERT_FILE", "/no/such/cert.pem")  # like a built app on a rider's Mac
    monkeypatch.setenv("SSL_CERT_DIR", "/no/such/certs")

    def fake_urlopen(request, timeout, context):
        seen["context"] = context
        raise OSError("stop here")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(StravaError):
        relay_post("https://relay.example/token", {"code": "abc"})
    assert isinstance(seen["context"], ssl.SSLContext) and seen["context"].cert_store_stats()["x509_ca"] > 50


def test_relay_unreachable():
    with pytest.raises(StravaError, match="internet"):
        relay_post("http://127.0.0.1:9/token", {"code": "abc"})

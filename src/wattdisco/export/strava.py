"""Strava: sign in once (OAuth), then upload rides as .fit files.

Two ways to sign in:
- shared (default): Watt Disco's own Strava app. Its client secret can't ship in a
  download, so the sign-in code and the refreshes go through a small relay that holds
  it (relay/strava-relay.js). Riders set up nothing.
- own (advanced): the rider's own Strava API app, keys in .env. The secret never
  leaves this process.
Tokens from the sign-in are stored in data/strava_tokens.json, readable only by
the owner, and refreshed automatically before they expire. Uploads always go
straight to Strava.

All calls here block (stravalib uses requests): run them in a thread.
"""

import json
import ssl
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# stravalib warns when a client has no refresh details; this module refreshes tokens itself.
os.environ.setdefault("SILENCE_TOKEN_WARNINGS", "true")

from stravalib import Client  # noqa: E402
from stravalib.exc import ActivityUploadFailed  # noqa: E402

TOKEN_FILE = "strava_tokens.json"
log = logging.getLogger(__name__)
SCOPES = ["read", "activity:write"]
REFRESH_MARGIN = 300  # refresh tokens that expire within 5 minutes
UPLOAD_TIMEOUT = 180  # seconds to wait for Strava to process an upload
POLL_INTERVAL = 2.0
SPORT_TYPES = ("VirtualRide", "Ride")
RELAY_TIMEOUT = 20

# Watt Disco's shared Strava app and its relay (see relay/README.md). Both are public;
# only the client secret is private, and it lives in the relay (Cloudflare, see relay/README.md).
SHARED_CLIENT_ID: int | None = 293374
SHARED_RELAY_URL: str | None = "https://wattdisco-strava-relay.wattdisco.workers.dev"


ATHLETES_FULL = ("Watt Disco's Strava connection is full for now: Strava lets it connect only a few riders until it "
                 "approves more. Save the .fit file and upload it at strava.com/upload in the meantime.")


class StravaError(Exception):
    """Something the user should see, in plain words."""


@dataclass(frozen=True)
class StravaKeys:
    client_id: int
    client_secret: str | None = None  # own app: the secret stays in this process
    relay_url: str | None = None  # shared app: the relay holds the secret

    @property
    def mode(self) -> str:
        return "own" if self.client_secret else "shared"


def _relay_ok(url: str) -> bool:
    return url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))


def load_keys(env_file: Path, shared_id: int | None = SHARED_CLIENT_ID,
              shared_relay: str | None = SHARED_RELAY_URL) -> StravaKeys | None:
    """Own keys from .env (or the environment) when both are there, else the shared app.

    STRAVA_RELAY_URL (and STRAVA_CLIENT_ID without a secret) can point at another relay,
    e.g. to try a new one. None when neither way is available.
    """
    values = {**dotenv_values(env_file), **{k: v for k, v in os.environ.items() if k.startswith("STRAVA_")}}
    client_id = (values.get("STRAVA_CLIENT_ID") or "").strip()
    secret = (values.get("STRAVA_CLIENT_SECRET") or "").strip()
    if client_id.isdigit() and secret:
        return StravaKeys(int(client_id), secret)
    relay = (values.get("STRAVA_RELAY_URL") or shared_relay or "").strip().rstrip("/")
    cid = int(client_id) if client_id.isdigit() else shared_id
    if cid and relay and _relay_ok(relay):
        return StravaKeys(int(cid), relay_url=relay)
    return None


def _tls() -> ssl.SSLContext:
    """Certificates from certifi, bundled with the app: a built app's own OpenSSL may look for them in a folder
    that doesn't exist on the rider's computer (the cause of "could not reach" in 1.1.0)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def relay_post(url: str, payload: dict) -> dict:
    """POST JSON to the relay, return its JSON. Raises StravaError in plain words."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "wattdisco"})
    try:
        with urllib.request.urlopen(request, timeout=RELAY_TIMEOUT, context=_tls()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read())
            message, detail = answer.get("error"), str(answer.get("detail") or "")
        except (ValueError, AttributeError):
            message, detail = None, ""
        if e.code == 429:
            raise StravaError("Strava is busy right now, please try again in a few minutes") from e
        refused = e.code == 403 or (message or "").strip().lower() == "forbidden"  # older relays send 400 "Forbidden"
        if "limit" in detail.lower() or (refused and url.endswith("/token")):
            raise StravaError(ATHLETES_FULL) from e
        raise StravaError(f"Strava sign-in failed: {message or e.reason}") from e
    except (OSError, ValueError) as e:
        raise StravaError("could not reach the Watt Disco sign-in service, check the internet connection") from e


def duplicate_activity_id(message: str) -> int | None:
    """Strava says e.g. 'duplicate of <a href='/activities/123'>…' or 'duplicate of activity 123'."""
    if "duplicate" not in message.lower():
        return None
    match = re.search(r"activit(?:y|ies)\D{0,4}(\d+)", message)
    return int(match.group(1)) if match else None


def activity_url(activity_id: int) -> str:
    return f"https://www.strava.com/activities/{activity_id}"


class Strava:
    def __init__(
        self,
        keys: StravaKeys | None,
        data_dir: Path,
        client_factory: Callable[..., Client] = Client,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        post: Callable[[str, dict], dict] = relay_post,
    ) -> None:
        self.keys = keys
        self._post = post
        self.token_path = data_dir / TOKEN_FILE
        self._client_factory = client_factory
        self._clock = clock
        self._sleep = sleep
        self._states: dict[str, str] = {}  # sign-in state -> page to return to

    # ---------- status ----------

    @property
    def configured(self) -> bool:
        return self.keys is not None

    @property
    def connected(self) -> bool:
        return self.configured and self.token_path.exists()

    def status(self) -> dict:
        athlete = self._tokens().get("athlete", {}) if self.connected else {}
        name = " ".join(x for x in (athlete.get("firstname"), athlete.get("lastname")) if x)
        return {"configured": self.configured, "connected": self.connected, "athlete": name or None,
                "mode": self.keys.mode if self.keys else None}

    # ---------- sign-in ----------

    def authorize_url(self, redirect_uri: str, return_to: str = "/upload.html") -> str:
        if not self.keys:
            raise StravaError("Strava sign-in is not available in this version")
        state = secrets.token_urlsafe(24)
        self._states[state] = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
        return self._client_factory().authorization_url(
            client_id=self.keys.client_id, redirect_uri=redirect_uri, approval_prompt="auto", scope=SCOPES, state=state
        )

    def finish_sign_in(self, code: str, state: str, scope: str) -> str:
        """Exchange the code from Strava's redirect for tokens. Returns the page to go back to."""
        if state not in self._states:
            raise StravaError("sign-in expired or did not start here, please try again")
        return_to = self._states.pop(state)
        if "activity:write" not in (scope or "").split(","):
            raise StravaError("Watt Disco needs permission to upload activities")
        if self.keys.mode == "shared":
            answer = self._post(f"{self.keys.relay_url}/token", {"code": code})
            tokens = self._token_dict(answer)
            athlete = answer.get("athlete") or {}
            if athlete:
                tokens["athlete"] = {k: athlete.get(k) for k in ("id", "firstname", "lastname")}
        else:
            access, athlete = self._client_factory().exchange_code_for_token(
                client_id=self.keys.client_id, client_secret=self.keys.client_secret, code=code, return_athlete=True
            )
            tokens = self._token_dict(access)
            if athlete is not None:
                tokens["athlete"] = {"id": athlete.id, "firstname": athlete.firstname, "lastname": athlete.lastname}
        self._save(tokens)
        return return_to

    def disconnect(self) -> None:
        """Revoke access on Strava (best effort) and forget the tokens."""
        if self.connected:
            try:
                self._client().deauthorize()
            except Exception:
                pass  # offline or already revoked: forgetting locally is what matters
        self.token_path.unlink(missing_ok=True)

    # ---------- upload ----------

    def upload(
        self,
        fit_path: Path,
        ride_id: str,
        name: str,
        description: str = "",
        sport_type: str = "VirtualRide",
        private: bool = True,
    ) -> dict:
        if sport_type not in SPORT_TYPES:
            raise StravaError(f"activity type must be one of {list(SPORT_TYPES)}")
        client = self._client()
        try:
            with fit_path.open("rb") as f:
                uploader = client.upload_activity(
                    f, data_type="fit", name=name, description=description or None,
                    external_id=f"wattdisco-{ride_id}", trainer=True,
                )
            # Poll the upload ourselves. stravalib's wait() also fetches the new activity, which
            # needs read access (read_all for "only me" rides) that this app does not ask for.
            deadline = self._clock() + UPLOAD_TIMEOUT
            while uploader.activity_id is None:
                if self._clock() > deadline:
                    raise StravaError("Strava is still processing the file; check your Strava feed in a minute")
                self._sleep(POLL_INTERVAL)
                uploader.poll()  # raises ActivityUploadFailed when Strava rejects the file
            activity_id = uploader.activity_id
        except ActivityUploadFailed as e:
            existing = duplicate_activity_id(str(e))
            if existing is not None:
                return {"state": "duplicate", "activity_id": existing, "url": activity_url(existing),
                        "message": "This ride is already on Strava"}
            raise StravaError(f"Strava could not process the file: {e}") from e
        # The file already sets the type (see export/fit.py). Setting it again here is a second
        # chance; visibility can only be set this way. Strava can refuse this for "only me" rides.
        try:
            client.update_activity(activity_id, sport_type=sport_type, private=private, trainer=True)
        except Exception as e:  # the ride is on Strava either way, with the type from the file
            log.warning("Strava activity update failed: %s: %s", e.__class__.__name__, e)
            return {"state": "done", "activity_id": activity_id, "url": activity_url(activity_id),
                    "message": "Uploaded (visibility could not be set; it follows your Strava default)"}
        return {"state": "done", "activity_id": activity_id, "url": activity_url(activity_id), "message": "Uploaded"}

    # ---------- tokens ----------

    def _client(self) -> Client:
        if not self.connected:
            raise StravaError("not connected to Strava")
        tokens = self._tokens()
        if tokens["expires_at"] - self._clock() < REFRESH_MARGIN:
            if self.keys.mode == "shared":
                access = self._post(f"{self.keys.relay_url}/refresh", {"refresh_token": tokens["refresh_token"]})
            else:
                access = self._client_factory().refresh_access_token(
                    client_id=self.keys.client_id, client_secret=self.keys.client_secret,
                    refresh_token=tokens["refresh_token"],
                )
            tokens = {**tokens, **self._token_dict(access)}
            self._save(tokens)
        return self._client_factory(access_token=tokens["access_token"])

    @staticmethod
    def _token_dict(access) -> dict:
        get = access.get if isinstance(access, dict) else lambda k: getattr(access, k)
        try:
            tokens = {"access_token": get("access_token"), "refresh_token": get("refresh_token"),
                      "expires_at": int(get("expires_at"))}
        except (TypeError, ValueError, AttributeError) as e:
            raise StravaError("Strava sent an incomplete sign-in, please try again") from e
        if not (isinstance(tokens["access_token"], str) and isinstance(tokens["refresh_token"], str)):
            raise StravaError("Strava sent an incomplete sign-in, please try again")
        return tokens

    def _tokens(self) -> dict:
        try:
            return json.loads(self.token_path.read_text())
        except (OSError, ValueError) as e:
            raise StravaError("stored Strava sign-in is unreadable, please connect again") from e

    def _save(self, tokens: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f)
        tmp.replace(self.token_path)

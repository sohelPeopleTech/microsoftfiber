"""Sign-in for the Capacity Intelligence app.

Deliberately stdlib-only, for the same reason the rest of the project is: a
Fabric Spark session should not need a package install to serve this, and an
auth library is a poor thing to discover missing at startup.

**This is a demo gate, not an identity system.** It proves who is sitting in
front of the dashboard well enough for a pilot, and no further. The whole of it
is one interface -- `authenticate()` and `current_user()` -- so replacing it
with Entra ID means rewriting this file and nothing else.

    session cookie = base64(payload).base64(hmac-sha256(payload, SECRET))

Passwords are never stored, compared, or logged in the clear: each user carries
a PBKDF2-SHA256 hash with a per-user salt, and verification is constant-time.

Configuration, in order of precedence:

    APP_USERS        "alice:<salt>:<hash>,bob:<salt>:<hash>"  -- production shape
    APP_SECRET_KEY   cookie signing key; generated per-process if unset, which
                     logs everyone out on restart. Set it in Key Vault for a
                     deployment that should survive a pipeline run.

With neither set the module falls back to a built-in demo account and says so
loudly at startup, so nobody ships that by accident.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

log = logging.getLogger(__name__)

#: How long a session lasts. A capacity review runs long; a working day is the
#: shortest window that does not interrupt one.
SESSION_MAX_AGE = 12 * 60 * 60

COOKIE_NAME = "fci_session"
PBKDF2_ROUNDS = 240_000

#: Only used when APP_USERS is unset. Documented in the startup warning.
DEMO_USERNAME = "capacity"
DEMO_PASSWORD = "fabric2026"

#: Display metadata. Real deployments get this from the directory; here it just
#: fills the sidebar so the shell has something true to render.
PROFILES = {
    "capacity": {"name": "Capacity Operations", "scope": "All regions"},
}


# --------------------------------------------------------------------------
# password hashing
# --------------------------------------------------------------------------


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (salt, hash), both hex. Use this to mint APP_USERS entries."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS
    )
    return salt, digest.hex()


def _verify_password(password: str, salt: str, expected: str) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def _load_users() -> dict[str, tuple[str, str]]:
    """username -> (salt, hash). Falls back to the demo account, noisily."""
    raw = os.environ.get("APP_USERS", "").strip()
    if raw:
        users = {}
        for entry in raw.split(","):
            parts = entry.strip().split(":")
            if len(parts) != 3:
                log.warning("APP_USERS entry ignored (want user:salt:hash): %r", entry)
                continue
            users[parts[0]] = (parts[1], parts[2])
        if users:
            return users
        log.warning("APP_USERS was set but no entry parsed; falling back to demo login.")

    log.warning(
        "No APP_USERS configured -- serving the built-in demo account %r. "
        "Set APP_USERS before this is reachable by anyone you did not invite.",
        DEMO_USERNAME,
    )
    return {DEMO_USERNAME: hash_password(DEMO_PASSWORD)}


def _load_secret() -> bytes:
    key = os.environ.get("APP_SECRET_KEY", "").strip()
    if key:
        return key.encode()
    log.warning(
        "APP_SECRET_KEY unset -- generating a per-process key. Sessions will not "
        "survive a restart. Set it from Key Vault for a real deployment."
    )
    return secrets.token_bytes(32)


USERS = _load_users()
SECRET = _load_secret()


# --------------------------------------------------------------------------
# session cookie
# --------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_session(username: str) -> str:
    payload = _b64(json.dumps({"u": username, "t": int(time.time())}).encode())
    signature = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_session(cookie: str | None) -> str | None:
    """Username if the cookie is well-formed, correctly signed and unexpired."""
    if not cookie or "." not in cookie:
        return None
    payload, _, signature = cookie.partition(".")

    expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        return None

    # A valid signature only proves we minted it. It could still be old, or --
    # if SECRET was reused across versions -- shaped differently than expected.
    try:
        data = json.loads(_unb64(payload))
        issued = int(data["t"])
        username = str(data["u"])
    except (ValueError, KeyError, TypeError):
        return None

    if time.time() - issued > SESSION_MAX_AGE:
        return None
    return username if username in USERS else None


# --------------------------------------------------------------------------
# the two calls the app makes
# --------------------------------------------------------------------------


def authenticate(username: str, password: str) -> bool:
    """Constant-time even for an unknown user, so timing cannot enumerate them."""
    record = USERS.get(username)
    if record is None:
        hash_password(password, secrets.token_hex(16))  # burn the same work
        return False
    salt, expected = record
    return _verify_password(password, salt, expected)


def profile(username: str) -> dict:
    meta = PROFILES.get(username, {})
    return {
        "username": username,
        "name": meta.get("name", username.title()),
        "scope": meta.get("scope", "Capacity Operations"),
        "initials": "".join(w[0] for w in meta.get("name", username).split()[:2]).upper(),
    }

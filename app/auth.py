"""Authentication & access control (Scalekit Full Stack Auth).

Scalekit handles identity (login, org membership, email invitations). App-level
roles (admin vs member) are stored LOCALLY in `memberships` — the org creator is
admin, invitees are members — so gating doesn't depend on extra Scalekit role
config.

Session model: a server-side `sessions` row keyed by a random `sid` in an
HttpOnly cookie. On each request we resolve the caller to a "principal":
{user_id, scalekit_user_id, email, name, org_id, role}.
"""
from fastapi import Depends, HTTPException, Request

from . import config, db

_client = None


def _install_jwt_leeway(seconds: int = 120) -> None:
    """Tolerate small clock skew when validating Scalekit tokens.

    The SDK validates id/access tokens with PyJWT, which rejects a token whose
    `iat`/`nbf` sits even a second in the future ("The token is not yet valid
    (iat)") — exactly what happens when this machine's clock runs behind
    Scalekit's servers, breaking every login at /auth/callback. The SDK exposes
    no leeway knob, so we widen PyJWT's default leeway once, process-wide. This
    is standard practice and does not meaningfully weaken validation; the real
    cure is an accurate system clock (NTP time sync)."""
    import jwt
    if getattr(jwt.decode, "_leeway_patched", False):
        return
    _orig_decode = jwt.decode

    def decode(*args, **kwargs):
        kwargs.setdefault("leeway", seconds)
        return _orig_decode(*args, **kwargs)

    decode._leeway_patched = True
    jwt.decode = decode


def get_client():
    """Lazily build the Scalekit client (only needed when auth is enabled)."""
    global _client
    if _client is None:
        if not (config.SCALEKIT_ENVIRONMENT_URL and config.SCALEKIT_CLIENT_ID
                and config.SCALEKIT_CLIENT_SECRET):
            raise RuntimeError(
                "Scalekit credentials missing — set SCALEKIT_ENVIRONMENT_URL / "
                "SCALEKIT_CLIENT_ID / SCALEKIT_CLIENT_SECRET, or AUTH_ENABLED=false."
            )
        _install_jwt_leeway()
        from scalekit import ScalekitClient
        _client = ScalekitClient(
            config.SCALEKIT_ENVIRONMENT_URL,
            config.SCALEKIT_CLIENT_ID,
            config.SCALEKIT_CLIENT_SECRET,
        )
    return _client


# ─── Scalekit flow helpers ────────────────────────────────────
def authorization_url(prompt: str | None = None, *, organization_id: str | None = None,
                      connection_id: str | None = None, login_hint: str | None = None,
                      state: str | None = None) -> str:
    from scalekit import AuthorizationUrlOptions
    opts = AuthorizationUrlOptions()
    opts.scopes = config.AUTH_SCOPES
    # `prompt=login` forces the IdP to re-authenticate instead of silently
    # reusing an existing SSO session — needed to sign in as a *different* user.
    if prompt:
        opts.prompt = prompt
    # For invite-accept / IdP-initiated logins we forward the org + connection so
    # Scalekit resumes the *same* login (right org, prefilled email) instead of
    # starting a blank one.
    if organization_id:
        opts.organization_id = organization_id
    if connection_id:
        opts.connection_id = connection_id
    if login_hint:
        opts.login_hint = login_hint
    if state:
        opts.state = state
    return get_client().get_authorization_url(config.AUTH_REDIRECT_URI, opts)


def idp_initiated_claims(token: str) -> dict:
    """Decode the `idp_initiated_login` JWT Scalekit sends to the Initiate Login
    URI when a user starts a flow outside the app (e.g. clicking Accept invite).
    Returns a plain dict of claims (connection_id, organization_id, login_hint,
    relay_state)."""
    claims = get_client().get_idp_initiated_login_claims(token)
    if isinstance(claims, dict):
        return claims
    return {k: getattr(claims, k) for k in
            ("connection_id", "organization_id", "login_hint", "relay_state")
            if hasattr(claims, k)}


def exchange_code(code: str) -> dict:
    from scalekit import CodeAuthenticationOptions
    return get_client().authenticate_with_code(
        code, config.AUTH_REDIRECT_URI, CodeAuthenticationOptions()
    )


def unwrap(resp):
    """Scalekit management calls use gRPC `.with_call`, which returns a
    (response, call) tuple. Unwrap to the response message."""
    return resp[0] if isinstance(resp, tuple) else resp


def logout_url(id_token: str | None) -> str:
    from scalekit.common.scalekit import LogoutUrlOptions
    # Only send post_logout_redirect_uri when configured. If it's not registered
    # in the Scalekit dashboard, Scalekit rejects the whole request with
    # `post_logout_redirect_uri invalid`; the SDK omits the param when it's None,
    # so an empty config value degrades to Scalekit's default signed-out page
    # instead of an error dead-end.
    return get_client().get_logout_url(
        LogoutUrlOptions(
            id_token_hint=id_token,
            post_logout_redirect_uri=config.AUTH_POST_LOGOUT_URI or None,
        )
    )


# ─── Session lifecycle ────────────────────────────────────────
def establish_session(auth_result: dict) -> str:
    """Turn a Scalekit auth result into a local user + server session.
    Activates any pending memberships (invited user accepting). Returns the sid."""
    info = auth_result.get("user", {}) or {}
    scalekit_user_id = info.get("id") or info.get("sub")
    email = info.get("email")
    name = (info.get("name")
            or " ".join(filter(None, [info.get("given_name"), info.get("family_name")]))
            or email)
    user = db.upsert_user(scalekit_user_id, email, name)

    for m in db.memberships_for_user(user["id"]):
        if m["status"] == "pending":
            db.activate_membership(user["id"], m["org_id"])

    active_org = next(
        (m["org_id"] for m in db.memberships_for_user(user["id"]) if m["status"] == "active"),
        None,
    )
    return db.create_session(
        user["id"], auth_result.get("access_token"), auth_result.get("refresh_token"),
        auth_result.get("id_token"), active_org, config.SESSION_TTL_SECONDS,
    )


def _principal(session: dict) -> dict | None:
    user = db.get_user(session["user_id"])
    if not user:
        return None
    org_id = session.get("active_org_id")
    role = None
    if org_id:
        m = db.get_membership(user["id"], org_id)
        role = m["role"] if (m and m["status"] == "active") else None
    if not role:  # fall back to first active membership
        org_id = None
        for m in db.memberships_for_user(user["id"]):
            if m["status"] == "active":
                org_id, role = m["org_id"], m["role"]
                break
    return {
        "user_id": user["id"],
        "scalekit_user_id": user["scalekit_user_id"],
        "email": user["email"],
        "name": user["name"],
        "org_id": org_id,
        "role": role,
        "sid": session["sid"],
        "id_token": session.get("id_token"),
    }


# ─── FastAPI dependencies ─────────────────────────────────────
_DEV_PRINCIPAL = {
    "user_id": "local", "scalekit_user_id": None, "email": "local@dev",
    "name": "Local Dev", "org_id": config.DEFAULT_TENANT_ID, "role": "admin",
    "sid": None, "id_token": None,
}


async def current_user(request: Request) -> dict:
    if not config.AUTH_ENABLED:
        return dict(_DEV_PRINCIPAL)
    sid = request.cookies.get(config.SESSION_COOKIE)
    session = db.get_session(sid) if sid else None
    if not session:
        raise HTTPException(status_code=401, detail="not authenticated")
    principal = _principal(session)
    if not principal:
        raise HTTPException(status_code=401, detail="invalid session")
    return principal


async def require_org(user: dict = Depends(current_user)) -> dict:
    if not user.get("org_id"):
        raise HTTPException(status_code=403, detail="no active organization")
    return user


async def require_admin(user: dict = Depends(require_org)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from gym_tracker.config import get_settings
from gym_tracker.database import SessionLocal
from gym_tracker import models  # expects a models.User (see migration notes below)

router = APIRouter()
settings = get_settings()

# ---- DB dependency (mirrors your repo pattern) ----
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---- Authlib OAuth client (Google OIDC) ----
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ---- Routes ----
@router.get("/login")
async def login(request: Request):
    """
    Kick off the Google OIDC Authorization Code (+PKCE) flow.
    """
    # This must exactly match one of your Authorized redirect URIs in Google Cloud.
    return await oauth.google.authorize_redirect(request, settings.OAUTH_REDIRECT_URI)


@router.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    """
    Google redirects here with a one-time code.
    We exchange it for tokens, verify the ID token, and upsert the user.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        # Common pitfall: redirect_uri mismatch or incorrect client secret
        raise HTTPException(status_code=401, detail=f"OAuth exchange failed: {e}")

    # Prefer userinfo; Authlib also exposes ID token 'claims'
    userinfo = token.get("userinfo") or {}
    claims = {**token.get("claims", {}), **userinfo}

    google_sub: Optional[str] = claims.get("sub")
    email: str = (claims.get("email") or "").lower()
    email_verified: bool = bool(claims.get("email_verified"))
    full_name: str = claims.get("name") or ""
    avatar_url: str = claims.get("picture") or ""

    if not google_sub:
        raise HTTPException(status_code=401, detail="Missing Google subject (sub)")

    # Optional allowlist (useful while testing)
    allowed = settings.allowed_emails_set
    if allowed and email not in allowed:
        raise HTTPException(status_code=403, detail="Email not allowed")

    # Upsert user by google_sub
    now = datetime.utcnow()
    user = db.query(models.User).filter(models.User.google_sub == google_sub).one_or_none()
    if user:
        # Keep existing values if Google omits them
        user.email = email or user.email
        user.email_verified = email_verified
        user.full_name = full_name or user.full_name
        user.avatar_url = avatar_url or user.avatar_url
        user.last_login_at = now
    else:
        user = models.User(
            google_sub=google_sub,
            email=email,
            email_verified=email_verified,
            full_name=full_name,
            avatar_url=avatar_url,
            role="client",       # placeholder for future RBAC
            is_active=True,
            created_at=now,
            last_login_at=now,
        )
        db.add(user)

    db.commit()
    db.refresh(user)

    # Auto-link any purchases where this user's email was set as partner
    if email:
        unlinked = db.query(models.Purchase).filter(
            models.Purchase.partner_email == email,
            models.Purchase.partner_user_id.is_(None),
        ).all()
        for purchase in unlinked:
            purchase.partner_user_id = user.id
        if unlinked:
            db.commit()

    # Store only user_id in the signed session cookie (set via SessionMiddleware in main.py)
    request.session["user_id"] = user.id

    # Head home (or wherever you want post-login)
    return RedirectResponse(url=settings.BASE_URL)


@router.get("/logout")
async def logout(request: Request):
    """
    Clear the app session. No need to call Google.
    """
    request.session.clear()
    return RedirectResponse(url=settings.BASE_URL)


@router.get("/me")
async def me(request: Request, db: Session = Depends(get_db)):
    """
    Tiny helper for debugging: who am I?
    Returns 200 with user info if logged in, else 401.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = db.query(models.User).get(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "last_login_at": user.last_login_at,
        "role": user.role,
    }

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from gym_tracker.config import get_settings
from gym_tracker.database import SessionLocal
from gym_tracker import models, invite_crud  # expects a models.User (see migration notes below)

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
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"OAuth exchange failed: {e}")

    userinfo = token.get("userinfo") or {}
    claims = {**token.get("claims", {}), **userinfo}

    google_sub: Optional[str] = claims.get("sub")
    email: str = (claims.get("email") or "").lower()
    email_verified: bool = bool(claims.get("email_verified"))
    full_name: str = claims.get("name") or ""
    avatar_url: str = claims.get("picture") or ""

    if not google_sub:
        raise HTTPException(status_code=401, detail="Missing Google subject (sub)")

    # Invite-only: reject anyone not in user_invites
    invite = invite_crud.get_invite_by_email(db, email) if email else None
    if not invite:
        raise HTTPException(status_code=403, detail="Access denied: you have not been invited.")

    # Upsert user by google_sub
    now = datetime.utcnow()
    user = db.query(models.User).filter(models.User.google_sub == google_sub).one_or_none()
    if user:
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
            role=invite.role,   # role comes from invite
            is_active=True,
            created_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.flush()  # get user.id before linking

    # Accept invite on first login
    if invite.accepted_at is None:
        invite_crud.accept_invite(db, invite)

    # Auto-link trainer record: invite.trainer_id takes priority
    trainer = None
    if invite.trainer_id:
        trainer = db.get(models.Trainer, invite.trainer_id)
    elif email:
        trainer = (
            db.query(models.Trainer)
            .filter(models.Trainer.email == email, models.Trainer.user_id.is_(None))
            .first()
        )
    if trainer and not trainer.user_id:
        trainer.user_id = user.id

    db.commit()
    db.refresh(user)

    # Auto-link partner purchases (existing behavior)
    if email:
        unlinked = db.query(models.Purchase).filter(
            models.Purchase.partner_email == email,
            models.Purchase.partner_user_id.is_(None),
        ).all()
        for purchase in unlinked:
            purchase.partner_user_id = user.id
        if unlinked:
            db.commit()

    request.session["user_id"] = user.id
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

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from gym_tracker import activities as activities_mod
from gym_tracker import crud, models, progress, progress_entries, schemas
from gym_tracker.auth import router as auth_router
from gym_tracker.config import get_settings
from gym_tracker.database import SessionLocal, engine
from gym_tracker.email import EmailSendError, send_invite_email
from gym_tracker.invites import generate_token, hash_token

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# App setup
# -------------------------------------------------------------
settings = get_settings()
app = FastAPI()

# --- Login-required middleware (add FIRST so SessionMiddleware wraps outside) ---
PUBLIC_PATHS = {
    "/login",
    "/auth/callback",
    "/logout",
    "/healthz",
    "/static",
    "/favicon.ico",
    "/me",  # keep public if you use it for debugging
    "/privacy",
    "/terms",
    "/invite/confirm",  # public: consume an invite token (no session required)
    "/dev/login",  # dev-only login bypass (gated by DEV_LOGIN env var; see route)
}

class LoginRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths and anything under /static
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Let API clients that ask for JSON continue (you can add JSON 401 deps if desired)
        accepts = request.headers.get("accept", "")
        if accepts.startswith("application/json"):
            return await call_next(request)

        # Enforce login for browser traffic
        if not request.session.get("user_id"):
            return RedirectResponse("/login")

        return await call_next(request)

# Add login middleware first (inner)
app.add_middleware(LoginRequiredMiddleware)

# Sessions middleware second (outer) so request.session is available above
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    session_cookie=settings.SESSION_COOKIE_NAME,
    same_site="lax",
    https_only=False,  # set True in prod behind HTTPS
)

# Routers
app.include_router(auth_router)

# Dev-only: create tables if they don't exist (use Alembic in prod)
# models.Base.metadata.create_all(bind=engine)

# -------------------------------------------------------------
# DB dependency
# -------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def get_current_user(request: Request, db: Session) -> models.User | None:
    user_id = request.session.get("user_id")
    return db.get(models.User, user_id) if user_id else None

def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency that requires admin role."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# -------------------------------------------------------------
# Templates
# -------------------------------------------------------------
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# -------------------------------------------------------------
# Pydantic response models for reports
# -------------------------------------------------------------
class CostByDuration(BaseModel):
    duration: int
    cost: float

class MinutesByDuration(BaseModel):
    duration: int
    minutes: int

class MinutesByPartner(BaseModel):
    partner: str
    minutes: int

class ReportsData(BaseModel):
    training: List[dict]  # trainer → minutes
    total_cost: float
    cost_by_duration: List[CostByDuration]
    total_minutes_by_duration: List[MinutesByDuration]
    minutes_by_partner: List[MinutesByPartner]
    sessions_remaining: List[dict] = []

    model_config = {"from_attributes": True}


class ProgressSummaryRow(BaseModel):
    activity: str
    category: str
    times: int
    best: str | None = None
    total: str | None = None
    latest: str | None = None
    primary_field: str | None = None

class ProgressData(BaseModel):
    summary: List[ProgressSummaryRow]
    series: dict  # {activity: {field_key: [{date, value}]}}

# -------------------------------------------------------------
# Health
# -------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}

# -------------------------------------------------------------
# Dev-only login bypass — DISABLED unless DEV_LOGIN env var is truthy.
# Lets you click through the UI locally without Google OAuth.
# Upserts a dev admin user and sets the session. NEVER enable in prod.
# -------------------------------------------------------------
@app.get("/dev/login")
def dev_login(request: Request, db: Session = Depends(get_db)):
    if os.getenv("DEV_LOGIN", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not found")
    email = os.getenv("DEV_LOGIN_EMAIL", "dev@example.com").strip().lower()
    user, ambiguous = crud.find_user_by_email_ci(db, email)
    if ambiguous:
        logger.error("dev_login refused: %r matches multiple users rows", email)
        raise HTTPException(status_code=409, detail="DEV_LOGIN_EMAIL matches multiple users")
    if not user:
        user = models.User(
            google_sub=f"dev-{email}",
            email=email,
            email_verified=True,
            full_name="Dev User",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = user.id
    return RedirectResponse("/")

# -------------------------------------------------------------
# Root landing page (renders templates/index.html)
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "index.html", {"current_user": user})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "privacy.html", {
        "current_user": user,
        "last_updated": "September 29, 2025"
    })

@app.get("/terms", response_class=HTMLResponse)
def terms_of_service(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "terms.html", {
        "current_user": user,
        "last_updated": "September 29, 2025"
    })


# -------------------------------------------------------------
# Public invite confirmation (spec 5.6) — no auth dependency
# -------------------------------------------------------------
@app.get("/invite/confirm", response_class=HTMLResponse)
def invite_confirm(request: Request, db: Session = Depends(get_db)):
    """Consume an invite token: flip the matching pending row to active.

    Idempotent from the user's point of view — an unknown, already-used, or
    no-longer-pending token renders the 'invalid or already used' page. The
    page is returned with HTTP 200 (it is a page, not an API).
    """
    token = request.query_params.get("token", "")
    row = None
    if token:
        row = (
            db.query(models.User)
            .filter(
                models.User.invite_token_hash == hash_token(token),
                models.User.status == "pending",
            )
            .one_or_none()
        )
    if row is None:
        return templates.TemplateResponse(
            request, "invite_invalid.html", {"current_user": None}
        )

    row.status = "active"
    row.confirmed_at = datetime.utcnow()
    row.invite_token_hash = None
    row.is_active = True
    db.commit()
    return templates.TemplateResponse(
        request, "invite_confirmed.html", {"current_user": None}
    )

# -------------------------------------------------------------
# Summary endpoint (scoped)
# -------------------------------------------------------------
@app.get("/summary")
@app.get("/summary/")
def summary(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    return crud.get_summary(db, user_id=user_id)

# -------------------------------------------------------------
# Log a new session (writes user id)
# -------------------------------------------------------------
@app.post("/sessions/", response_model=schemas.Session)
def create_session(
    request: Request,
    session_in: schemas.SessionCreate,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    try:
        return crud.create_session(
            db,
            session_in.duration_minutes,
            session_in.trainer,
            created_by_user_id=user_id,
            partner_email=session_in.partner_email,
            num_people=session_in.num_people,
            activities=session_in.activities,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# -------------------------------------------------------------
# Buy a new purchase pack (writes user id)
# -------------------------------------------------------------
@app.post("/purchases/", response_model=schemas.Purchase)
def create_purchase(
    request: Request,
    data: schemas.PurchaseCreate,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")  # who logs it
    return crud.create_purchase(
        db,
        data,
        logged_by_user_id=user_id,  # pass through
    )

# -------------------------------------------------------------
# History data endpoints (JSON, scoped)
# -------------------------------------------------------------
@app.get("/history/sessions/", response_model=List[schemas.Session])
def history_sessions(
    request: Request,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    return crud.get_sessions(db, start, end, user_id=user_id)

@app.get("/history/purchases/", response_model=List[schemas.Purchase])
def history_purchases(
    request: Request,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    return crud.get_purchases_history(db, start, end, user_id=user_id)

# -------------------------------------------------------------
# History page (server-rendered, scoped)
# -------------------------------------------------------------
@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    sessions = crud.get_sessions(db, user_id=request.session.get("user_id"))
    purchases = crud.get_purchases(db, user_id=request.session.get("user_id"))
    return templates.TemplateResponse(
        request,
        "history.html",
        {"sessions": sessions, "purchases": purchases, "current_user": user},
    )

# -------------------------------------------------------------
# AJAX API endpoints for editing and deleting (unchanged semantics)
# -------------------------------------------------------------
@app.post("/history/api/edit/session/{session_id}")
async def api_edit_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    data = await request.json()

    s = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")

    s_purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
    if not crud.user_can_edit_session(s, s_purchase, user_id):
        raise HTTPException(403, "Not allowed")

    owner_id = s_purchase.logged_by_user_id if s_purchase else user_id

    old_duration = s.duration_minutes
    new_duration = data["duration_minutes"]

    if new_duration != old_duration:
        if owner_id is None:
            raise HTTPException(400, "Cannot reallocate pack: session has no pack owner")

        # Refund to the ORIGINAL purchase that this session used (not "first pack").
        if s_purchase:
            s_purchase.sessions_remaining += 1
            db.add(s_purchase)

        # Deduct from a NEW pack owned by the pack owner, with the new duration
        new_pack = (
            db.query(models.Purchase)
            .filter(
                models.Purchase.duration_minutes == new_duration,
                models.Purchase.sessions_remaining > 0,
                models.Purchase.logged_by_user_id == owner_id,   # scope to pack owner
            )
            .order_by(models.Purchase.purchase_date)
            .first()
        )
        if not new_pack:
            raise HTTPException(400, f"No {new_duration}-min package available to reallocate")

        new_pack.sessions_remaining -= 1
        db.add(new_pack)

        # Repoint the session to the new purchase and duration
        s.purchase_id = new_pack.id
        s.duration_minutes = new_duration

    # Update editable fields
    s.session_date = datetime.fromisoformat(data["session_date"])
    s.trainer = data["trainer"]

    # Reconcile activities if provided (full desired set)
    if "activities" in data and data["activities"] is not None:
        try:
            items = [schemas.SessionActivityInput(**row) for row in data["activities"]]
            activities_mod.reconcile_session_activities(db, s, items, created_by_user_id=user_id)
        except (ValueError, TypeError) as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return {"success": True}


@app.post("/history/api/edit/purchase/{purchase_id}")
async def api_edit_purchase(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    data = await request.json()

    pur = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not pur:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Ownership check
    if pur.logged_by_user_id != user_id:
        raise HTTPException(403, "Not allowed")

    pur.purchase_date = datetime.fromisoformat(data["purchase_date"])
    pur.total_sessions = data.get("total_sessions")
    pur.sessions_remaining = data.get("sessions_remaining")

    db.commit()
    return {"success": True}

@app.post("/history/api/delete/session/{session_id}")
def api_delete_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")

    s = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
    if not crud.user_can_edit_session(s, purchase, user_id):
        raise HTTPException(403, "Not allowed")

    # Refund the session to the purchase that funded it (regardless of who deletes).
    if purchase:
        purchase.sessions_remaining += 1
        db.add(purchase)

    db.delete(s)
    db.commit()
    return {"success": True}


@app.post("/history/api/delete/purchase/{purchase_id}")
def api_delete_purchase(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")

    pur = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not pur:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Ownership check
    if pur.logged_by_user_id != user_id:
        raise HTTPException(403, "Not allowed")

    db.delete(pur)
    db.commit()
    return {"success": True}


# -------------------------------------------------------------
# Reports (scoped)
# -------------------------------------------------------------
@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "reports.html", {"current_user": user})

@app.get("/reports/data", response_model=ReportsData)
def reports_data(
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    training = crud.get_training_by_trainer(db, start, end, user_id=user_id)
    total_cost = crud.get_total_cost(db, start, end, user_id=user_id)

    cost_results = (
        db.query(models.Purchase.duration_minutes, func.sum(models.Purchase.cost))
        .filter(
            models.Purchase.purchase_date >= start,
            models.Purchase.purchase_date <= end,
        )
        .filter(models.Purchase.logged_by_user_id == user_id)  # scoped
        .group_by(models.Purchase.duration_minutes)
        .all()
    )

    minutes_results = crud.get_total_minutes_by_duration(db, start, end, user_id=user_id)
    partner_results = crud.get_minutes_by_partner(db, start, end, user_id=user_id)

    return {
        "training": [{"trainer": t, "minutes": int(m)} for t, m in training],
        "total_cost": float(total_cost),
        "cost_by_duration": [{"duration": d, "cost": float(c)} for d, c in cost_results],
        "total_minutes_by_duration": [{"duration": d, "minutes": int(m)} for d, m in minutes_results],
        "minutes_by_partner": partner_results,
        # sessions_remaining is a CURRENT snapshot — intentionally not date-filtered (unlike the rest of the report)
        "sessions_remaining": crud.get_summary(db, user_id=user_id),
    }


@app.get("/reports/progress/data", response_model=ProgressData)
def reports_progress_data(
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    rows = crud.user_activity_rows(db, user_id=user_id, start=start, end=end)
    cat_ids = {r["category_id"] for r in rows}
    fields_by_cat = {}
    for cid in cat_ids:
        fields_by_cat[cid] = (
            db.query(models.CategoryField)
            .filter(models.CategoryField.category_id == cid)
            .order_by(models.CategoryField.sort_order)
            .all()
        )
    return progress.summarize(rows, fields_by_cat)


# -------------------------------------------------------------
# Trainer Management API endpoints
# -------------------------------------------------------------

@app.get("/api/trainers/", response_model=List[schemas.Trainer])
def list_trainers(db: Session = Depends(get_db)):
    """Get list of active trainers."""
    return crud.get_trainers(db, active_only=True)

@app.post("/api/trainers/", response_model=schemas.Trainer)
def create_trainer(
    trainer_in: schemas.TrainerCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new trainer (admin only)."""
    return crud.create_trainer(db, trainer_in)

@app.put("/api/trainers/{trainer_id}", response_model=schemas.Trainer)
def update_trainer(
    trainer_id: int,
    trainer_update: schemas.TrainerUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a trainer (admin only)."""
    trainer = crud.update_trainer(db, trainer_id, trainer_update)
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")
    return trainer

@app.delete("/api/trainers/{trainer_id}", response_model=schemas.Trainer)
def delete_trainer(
    trainer_id: int,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Soft delete a trainer (admin only)."""
    trainer = crud.delete_trainer(db, trainer_id)
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")
    return trainer


# -------------------------------------------------------------
# Activity Tracking API endpoints
# -------------------------------------------------------------

@app.get("/api/categories", response_model=List[schemas.ActivityCategoryRead])
def list_categories(db: Session = Depends(get_db)):
    """Active categories with their active fields (drives the log form)."""
    cats = activities_mod.list_categories(db)
    # Hide inactive fields from the form payload
    for c in cats:
        c.fields = [f for f in c.fields if f.is_active]
    return cats


@app.get("/api/activities", response_model=List[schemas.ActivityRead])
def list_activities(category_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Active activities, optionally filtered by category."""
    return activities_mod.list_activities(db, category_id=category_id)


@app.post("/api/activities", response_model=schemas.ActivityRead)
def create_activity(
    request: Request,
    activity_in: schemas.ActivityCreate,
    db: Session = Depends(get_db),
):
    """Create a global activity (any authenticated user). Dedups by name."""
    user_id = request.session.get("user_id")
    try:
        return activities_mod.create_activity(db, activity_in, created_by_user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -------------------------------------------------------------
# Standalone Progress Entry API endpoints
# -------------------------------------------------------------

def _require_user(request: Request, db: Session) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _run_entry_op(fn):
    """Map progress_entries exceptions to HTTP errors."""
    try:
        return fn()
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/progress-entries", response_model=schemas.ProgressEntryRead)
def create_progress_entry(
    entry_in: schemas.ProgressEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    return _run_entry_op(lambda: progress_entries.create_entry(db, actor=user, data=entry_in))


@app.get("/api/progress-entries", response_model=List[schemas.ProgressEntryRead])
def list_progress_entries(
    request: Request,
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    target = user_id if user_id is not None else user.id
    return _run_entry_op(lambda: progress_entries.list_entries(db, actor=user, user_id=target))


@app.put("/api/progress-entries/{entry_id}", response_model=schemas.ProgressEntryRead)
def update_progress_entry(
    entry_id: int,
    entry_update: schemas.ProgressEntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    return _run_entry_op(lambda: progress_entries.update_entry(
        db, actor=user, entry_id=entry_id, data=entry_update))


@app.delete("/api/progress-entries/{entry_id}")
def delete_progress_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    _run_entry_op(lambda: progress_entries.delete_entry(db, actor=user, entry_id=entry_id))
    return {"ok": True}


@app.get("/api/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    """Minimal user list for the admin/trainer 'log for someone' selector."""
    user = _require_user(request, db)
    if user.role not in progress_entries.PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="Admin or trainer access required")
    users = (
        db.query(models.User)
        .filter(models.User.is_active == True)
        .order_by(models.User.full_name, models.User.email)
        .all()
    )
    return [{"id": u.id, "name": u.full_name or u.email, "email": u.email} for u in users]


# -------------------------------------------------------------
# Admin Console
# -------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_console(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin console main dashboard."""
    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {"current_user": admin_user}
    )

@app.get("/admin/trainers", response_class=HTMLResponse)
def admin_trainers(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin trainer management page."""
    trainers = crud.get_trainers(db, active_only=False)
    return templates.TemplateResponse(
        request,
        "admin/trainers.html",
        {"current_user": admin_user, "trainers": trainers}
    )


# -------------------------------------------------------------
# Package Management API endpoints
# -------------------------------------------------------------

@app.get("/api/packages/", response_model=List[schemas.Package])
def list_packages(db: Session = Depends(get_db)):
    """Get list of active packages."""
    return crud.get_packages(db, active_only=True)

@app.post("/api/packages/", response_model=schemas.Package)
def create_package(
    package_in: schemas.PackageCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new package (admin only)."""
    return crud.create_package(db, package_in)

@app.put("/api/packages/{package_id}", response_model=schemas.Package)
def update_package(
    package_id: int,
    package_update: schemas.PackageUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a package (admin only)."""
    package = crud.update_package(db, package_id, package_update)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package

@app.delete("/api/packages/{package_id}", response_model=schemas.Package)
def delete_package(
    package_id: int,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Soft delete a package (admin only)."""
    package = crud.delete_package(db, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package


@app.get("/admin/packages", response_class=HTMLResponse)
def admin_packages(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin package management page."""
    packages = crud.get_packages(db, active_only=False)
    return templates.TemplateResponse(
        request,
        "admin/packages.html",
        {"current_user": admin_user, "packages": packages}
    )


# -------------------------------------------------------------
# Activity admin API + page
# -------------------------------------------------------------

@app.post("/api/admin/categories", response_model=schemas.ActivityCategoryRead)
def admin_create_category(
    category_in: schemas.CategoryCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return activities_mod.create_category(db, category_in)


@app.patch("/api/admin/categories/{category_id}", response_model=schemas.ActivityCategoryRead)
def admin_update_category(
    category_id: int,
    update: schemas.CategoryUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cat = activities_mod.update_category(db, category_id, update)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@app.post("/api/admin/categories/{category_id}/fields", response_model=schemas.CategoryFieldRead)
def admin_create_field(
    category_id: int,
    field_in: schemas.CategoryFieldCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        return activities_mod.create_field(db, category_id, field_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/admin/categories/{category_id}/fields/{field_id}", response_model=schemas.CategoryFieldRead)
def admin_update_field(
    category_id: int,
    field_id: int,
    update: schemas.CategoryFieldUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = activities_mod.update_field(db, field_id, update)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    return field


@app.delete("/api/admin/categories/{category_id}/fields/{field_id}", response_model=schemas.CategoryFieldRead)
def admin_delete_field(
    category_id: int,
    field_id: int,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = activities_mod.soft_delete_field(db, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    return field


@app.patch("/api/admin/activities/{activity_id}", response_model=schemas.ActivityRead)
def admin_update_activity(
    activity_id: int,
    update: schemas.ActivityUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    activity = activities_mod.update_activity(db, activity_id, update)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@app.get("/admin/activities", response_class=HTMLResponse)
def admin_activities(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin activity/category/field management page."""
    categories = activities_mod.list_categories(db)
    return templates.TemplateResponse(
        request,
        "admin/activities.html",
        {"current_user": admin_user, "categories": categories},
    )


# -------------------------------------------------------------
# Client Management (admin console) — page + API (spec 5.4 / 5.5)
# -------------------------------------------------------------

class ClientCreate(BaseModel):
    email: str
    name: Optional[str] = None


def build_confirm_url(request: Request, raw_token: str) -> str:
    """Absolute URL for the emailed confirmation link.

    Prefer an explicit BASE_URL in production; otherwise derive from the
    incoming request. Strip a trailing slash from whichever base is chosen so a
    configured "https://host/" does not yield "https://host//invite/confirm".
    """
    base = (settings.BASE_URL or str(request.base_url)).rstrip("/")
    return f"{base}/invite/confirm?token={raw_token}"


def _clients_ordered(db: Session):
    """Query for role='client' rows, newest invite first.

    Ordering is expressed portably: `invited_at IS NULL` (a boolean, sorts
    False < True so non-NULL rows come first) then `invited_at DESC` then
    `id DESC`. Avoids `.nullslast()`, which compiles to `DESC NULLS LAST` and
    is rejected by MySQL.
    """
    return (
        db.query(models.User)
        .filter(models.User.role == "client")
        .order_by(
            models.User.invited_at.is_(None),
            models.User.invited_at.desc(),
            models.User.id.desc(),
        )
    )


def _get_client_row(db: Session, client_id: int) -> models.User:
    row = db.get(models.User, client_id)
    if row is None or row.role != "client":
        raise HTTPException(status_code=404, detail="Client not found")
    return row


_DUP_EMAIL_DETAIL = "A user with that email already exists"


def _is_email_uniqueness_error(exc: IntegrityError) -> bool:
    """True when an IntegrityError is the case-insensitive email uniqueness
    violation (MySQL constraint or the SQLite functional index), regardless of
    dialect wording."""
    return "uq_users_email_ci" in str(getattr(exc, "orig", exc)).lower()


def _persist_new_client(db: Session, row: models.User, admin_id: int) -> str:
    """Commit a new pending client row with a fresh invite token; return the raw
    token.

    Concurrency: the CI pre-check in the caller is not atomic, so two requests
    can both reach here. `uq_users_email_ci` then makes the losing commit raise
    IntegrityError. Handle it: on the email-uniqueness violation (or if a row now
    exists) -> 409, matching the normal duplicate path; on any other
    IntegrityError (e.g. an invite_token_hash collision) regenerate the token and
    retry once, then re-raise so nothing is swallowed silently.
    """
    for attempt in (1, 2):
        raw = generate_token()
        row.invite_token_hash = hash_token(raw)
        row.invited_at = datetime.utcnow()
        row.invited_by_id = admin_id
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
            return raw
        except IntegrityError as exc:
            db.rollback()
            existing, ambiguous = crud.find_user_by_email_ci(db, row.email)
            if existing or ambiguous or _is_email_uniqueness_error(exc):
                logger.warning("Client create lost the race for %r: %s", row.email, exc)
                raise HTTPException(status_code=409, detail=_DUP_EMAIL_DETAIL)
            if attempt == 2:
                raise
            logger.warning("Client create IntegrityError (retrying with new token): %s", exc)
    raise RuntimeError("unreachable")  # pragma: no cover


def _issue_invite(db: Session, row: models.User, request: Request, admin_id: int) -> dict:
    """Rotate the invite token on `row`, COMMIT, then send the email.

    Store-hash-then-send: the row and its new token are persisted before any
    outbound call, so a send failure (or a crash mid-send) can never leave a
    live confirm link with no matching row. A send failure is non-fatal — the
    committed row keeps its token and the response carries a `warning` field so
    the UI can offer 'Resend'.
    """
    raw = generate_token()
    row.invite_token_hash = hash_token(raw)
    row.invited_at = datetime.utcnow()
    row.invited_by_id = admin_id
    db.commit()
    db.refresh(row)

    body = {"id": row.id, "status": row.status}
    try:
        send_invite_email(row.email, build_confirm_url(request, raw), to_name=row.full_name)
    except EmailSendError as exc:
        body["warning"] = str(exc)
    return body


@app.post("/api/admin/clients", status_code=201)
def admin_create_client(
    payload: ClientCreate,
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=400, detail="A valid email address is required")
    exists, ambiguous = crud.find_user_by_email_ci(db, email)
    if ambiguous:
        logger.error("Client create blocked: %r matches multiple users rows", email)
        raise HTTPException(
            status_code=409,
            detail="Multiple accounts already use that email; contact an administrator",
        )
    if exists:
        raise HTTPException(status_code=409, detail=_DUP_EMAIL_DETAIL)

    row = models.User(
        email=email,
        google_sub=None,
        role="client",
        status="pending",
        full_name=(payload.name or None),
        email_verified=False,
        is_active=False,
        invited_by_id=admin_user.id,
        invited_at=datetime.utcnow(),
    )
    # store-hash-then-send: persist the row + token (race-safe) before emailing.
    raw = _persist_new_client(db, row, admin_user.id)
    body = {"id": row.id, "status": row.status}
    try:
        send_invite_email(row.email, build_confirm_url(request, raw), to_name=row.full_name)
    except EmailSendError as exc:
        body["warning"] = str(exc)
    return JSONResponse(status_code=201, content=body)


@app.post("/api/admin/clients/{client_id}/resend")
def admin_resend_client(
    client_id: int,
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_client_row(db, client_id)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending invitation can be resent")
    return _issue_invite(db, row, request, admin_user.id)  # commits, then sends


@app.post("/api/admin/clients/{client_id}/disable")
def admin_disable_client(
    client_id: int,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_client_row(db, client_id)
    # Soft-disable only. Historical data (purchases/sessions/progress) is retained.
    row.status = "disabled"
    row.is_active = False
    db.commit()
    return {"id": row.id, "status": row.status}


@app.post("/api/admin/clients/{client_id}/reinvite")
def admin_reinvite_client(
    client_id: int,
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_client_row(db, client_id)
    if row.status != "disabled":
        raise HTTPException(status_code=409, detail="Only a disabled client can be re-invited")
    row.status = "pending"
    row.confirmed_at = None
    row.is_active = False
    return _issue_invite(db, row, request, admin_user.id)  # commits status + token, then sends


@app.get("/admin/clients", response_class=HTMLResponse)
def admin_clients(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin client management page — lists role='client' rows only."""
    clients = _clients_ordered(db).all()
    return templates.TemplateResponse(
        request,
        "admin/clients.html",
        {"current_user": admin_user, "clients": clients},
    )

# main.py
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from gym_tracker import crud, models, schemas
from gym_tracker.auth import router as auth_router
from gym_tracker.config import get_settings
from gym_tracker.database import SessionLocal, engine

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
    finally:
        db.close()

def get_current_user(request: Request, db: Session) -> models.User | None:
    user_id = request.session.get("user_id")
    return db.get(models.User, user_id) if user_id else None

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

class ReportsData(BaseModel):
    training: List[dict]  # trainer → minutes
    total_cost: float
    cost_by_duration: List[CostByDuration]
    total_minutes_by_duration: List[MinutesByDuration]

    model_config = {"from_attributes": True}

# -------------------------------------------------------------
# Health
# -------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}

# -------------------------------------------------------------
# Root landing page (renders templates/index.html)
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("index.html", {"request": request, "current_user": user})

# -------------------------------------------------------------
# Summary endpoint (scoped)
# -------------------------------------------------------------
@app.get("/summary", response_model=dict)
@app.get("/summary/", response_model=dict)
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
    user_id = request.session.get("user_id")  # who is creating it
    try:
        return crud.create_session(
            db,
            session_in.duration_minutes,
            session_in.trainer,
            created_by_user_id=user_id,  # pass through
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
        "history.html",
        {"request": request, "sessions": sessions, "purchases": purchases, "current_user": user},
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

    # Ownership check
    if s.created_by_user_id != user_id:
        raise HTTPException(403, "Not allowed")

    old_duration = s.duration_minutes
    new_duration = data["duration_minutes"]

    if new_duration != old_duration:
        # Refund to the ORIGINAL purchase that this session used (not “first pack”).
        original_purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
        if original_purchase:
            # (Optional) verify the purchase also belongs to this user
            if original_purchase.logged_by_user_id != user_id:
                raise HTTPException(403, "Not allowed to modify packs you don't own")
            original_purchase.sessions_remaining += 1
            db.add(original_purchase)

        # Deduct from a NEW pack owned by the same user, with the new duration
        new_pack = (
            db.query(models.Purchase)
            .filter(
                models.Purchase.duration_minutes == new_duration,
                models.Purchase.sessions_remaining > 0,
                models.Purchase.logged_by_user_id == user_id,   # scope to owner
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

    # Ownership check
    if s.created_by_user_id != user_id:
        raise HTTPException(403, "Not allowed")

    # Refund the session to the purchase that funded it (if it belongs to the same user)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
    if purchase and purchase.logged_by_user_id == user_id:
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
    return templates.TemplateResponse("reports.html", {"request": request, "current_user": user})

@app.get("/reports/data", response_model=ReportsData)
def reports_data(
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
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

    minutes_results = (
        db.query(models.Session.duration_minutes, func.sum(models.Session.duration_minutes))
        .filter(
            models.Session.session_date >= start,
            models.Session.session_date <= end,
        )
        .filter(models.Session.created_by_user_id == user_id)  # scoped
        .group_by(models.Session.duration_minutes)
        .all()
    )

    return {
        "training": [{"trainer": t, "minutes": int(m)} for t, m in training],
        "total_cost": float(total_cost),
        "cost_by_duration": [{"duration": d, "cost": float(c)} for d, c in cost_results],
        "total_minutes_by_duration": [{"duration": d, "minutes": int(m)} for d, m in minutes_results],
    }

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from gym_tracker import crud, models, schemas
from gym_tracker.database import SessionLocal, engine

# ------------------------------------------------------------------
# Database initialization
# ------------------------------------------------------------------
models.Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------------------------------------------------
# FastAPI app and templates
# ------------------------------------------------------------------
app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ------------------------------------------------------------------
# Root landing page (renders templates/index.html)
# ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("index.html", {"request": request})

# ------------------------------------------------------------------
# Summary endpoint
# ------------------------------------------------------------------
@app.get("/summary/")
def summary(db: Session = Depends(get_db)):
    # Return the raw dict from crud.get_summary:
    return crud.get_summary(db)

# ------------------------------------------------------------------
# Log a new session
# ------------------------------------------------------------------
@app.post("/sessions/", response_model=schemas.Session)
def create_session(session_in: schemas.SessionCreate, db: Session = Depends(get_db)):
    try:
        # Pass duration and trainer separately
        return crud.create_session(db, session_in.duration_minutes, session_in.trainer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ------------------------------------------------------------------
# Buy a new purchase pack
# ------------------------------------------------------------------
@app.post("/purchases/", response_model=schemas.Purchase)
def create_purchase(data: schemas.PurchaseCreate, db: Session = Depends(get_db)):
    return crud.create_purchase(db, data)

# ------------------------------------------------------------------
# History data endpoints (JSON)
# ------------------------------------------------------------------
@app.get("/history/sessions/", response_model=List[schemas.Session])
def history_sessions(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    return crud.get_sessions(db, start, end)

@app.get("/history/purchases/", response_model=List[schemas.Purchase])
def history_purchases(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    return crud.get_purchases_history(db, start, end)

# ------------------------------------------------------------------
# History page (renders templates/history.html)
# ------------------------------------------------------------------
@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    sessions  = crud.get_sessions(db)
    purchases = crud.get_purchases(db)
    return templates.TemplateResponse(
      "history.html",
      {"request": request, "sessions": sessions, "purchases": purchases}
    )

# ------------------------------------------------------------------
# AJAX API endpoints for editing and deleting
# ------------------------------------------------------------------
@app.post("/history/api/edit/session/{session_id}")
async def api_edit_session(session_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    session_obj = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session_obj:
        raise HTTPException(404, "Session not found")

    # 1) If the duration changed, refund one to the old purchase
    old_duration = session_obj.duration_minutes
    new_duration = data["duration_minutes"]
    if new_duration != old_duration:
        old_pack = db.query(models.Purchase)\
                     .filter(models.Purchase.duration_minutes == old_duration)\
                     .order_by(models.Purchase.purchase_date)\
                     .first()
        if old_pack:
            old_pack.sessions_remaining += 1
            db.add(old_pack)

        # 2) Deduct one from a new pack of the new duration
        new_pack = db.query(models.Purchase)\
                     .filter(models.Purchase.duration_minutes == new_duration,
                             models.Purchase.sessions_remaining > 0)\
                     .order_by(models.Purchase.purchase_date)\
                     .first()
        if not new_pack:
            raise HTTPException(400, f"No {new_duration}-min package available to reallocate")
        new_pack.sessions_remaining -= 1
        db.add(new_pack)

        session_obj.duration_minutes = new_duration

    # 3) Update the other fields
    session_obj.session_date = datetime.fromisoformat(data["session_date"])
    session_obj.trainer      = data["trainer"]

    db.commit()
    return {"success": True}


@app.post("/history/api/edit/purchase/{purchase_id}")
async def api_edit_purchase(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    pur = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not pur:
        raise HTTPException(status_code=404, detail="Purchase not found")
    pur.purchase_date      = datetime.fromisoformat(data["purchase_date"])
    pur.total_sessions     = data.get("total_sessions")
    pur.sessions_remaining = data.get("sessions_remaining")
    db.commit()
    return {"success": True}

@app.post("/history/api/delete/session/{session_id}")
def api_delete_session(session_id: int, db: Session = Depends(get_db)):
    # 1) Load the session
    session_obj = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    # 2) Increment the purchase's remaining count
    purchase = db.query(models.Purchase)\
                 .filter(models.Purchase.id == session_obj.purchase_id)\
                 .first()
    if purchase:
        purchase.sessions_remaining += 1
        db.add(purchase)

    # 3) Delete the session record
    db.delete(session_obj)

    # 4) Commit both changes
    db.commit()
    return {"success": True}


@app.post("/history/api/delete/purchase/{purchase_id}")
def api_delete_purchase(purchase_id: int, db: Session = Depends(get_db)):
    pur = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not pur:
        raise HTTPException(status_code=404, detail="Purchase not found")
    db.delete(pur)
    db.commit()
    return {"success": True}

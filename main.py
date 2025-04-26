from typing import List
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from datetime import datetime
from gym_tracker import crud, schemas, models
from gym_tracker.database import engine, SessionLocal

models.Base.metadata.create_all(bind=engine)
app = FastAPI(title="Gym Session Tracker")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    return HTMLResponse(r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gym Session Tracker</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-4">
    <h1 class="mb-4">Gym Session Tracker</h1>
    <div id="summary" class="mb-4">Loading summary...</div>
    <div class="mb-4">
      <div class="d-flex mb-2">
        <button id="log30" class="btn btn-primary me-2">Log 30-min Session</button>
        <button id="log45" class="btn btn-secondary">Log 45-min Session</button>
      </div>
      <div class="d-flex mb-2">
        <button id="buy30" class="btn btn-success me-2">Buy 30-min Package</button>
        <button id="buy45" class="btn btn-success">Buy 45-min Package</button>
      </div>
      <div class="mb-2">
        <a href="/history" class="btn btn-info">View History</a>
      </div>
    </div>
    <div id="notification"></div>
  </div>

  <div class="modal fade" id="trainerModal" tabindex="-1" aria-labelledby="trainerModalLabel" aria-hidden="true">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Select Trainer</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <select id="modalTrainerSelect" class="form-select">
            <option>Rachel</option>
            <option>Lindsay</option>
          </select>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button type="button" id="confirmTrainer" class="btn btn-primary">Log Session</button>
        </div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    let pendingDuration = null;
    const trainerModal = new bootstrap.Modal(document.getElementById('trainerModal'));

    async function refreshSummary() {
      const res = await fetch('/summary/');
      const data = await res.json();
      document.getElementById('summary').innerHTML = Object.entries(data)
        .map(([d, r]) =>
          `<div class="alert ${r === 0 ? 'alert-danger' : 'alert-success'}">
             Duration ${d} min: ${r} left
           </div>`
        ).join('');
    }

    function promptTrainer(d) {
      pendingDuration = d;
      trainerModal.show();
    }

    document.getElementById('confirmTrainer').addEventListener('click', async () => {
      const trainer = document.getElementById('modalTrainerSelect').value;
      trainerModal.hide();
      const res = await fetch('/sessions/', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({duration_minutes: pendingDuration, trainer})
      });
      const notif = document.getElementById('notification');
      if (res.ok) {
        const s = await res.json();
        notif.innerHTML = `<div class="alert alert-info">
          Logged ${s.duration_minutes}-min with ${s.trainer}.${s.purchase_exhausted ? ' Purchase exhausted!' : ''}
        </div>`;
      } else {
        const e = await res.json();
        notif.innerHTML = `<div class="alert alert-warning">${e.detail}</div>`;
      }
      refreshSummary();
    });

    document.addEventListener('DOMContentLoaded', () => {
      document.getElementById('log30').addEventListener('click', () => promptTrainer(30));
      document.getElementById('log45').addEventListener('click', () => promptTrainer(45));
      document.getElementById('buy30').addEventListener('click', () => buyPackage(30));
      document.getElementById('buy45').addEventListener('click', () => buyPackage(45));
      refreshSummary();
    });

    async function buyPackage(d) {
      const res = await fetch('/purchases/', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({duration_minutes: d})
      });
      const notif = document.getElementById('notification');
      if (res.ok) {
        const p = await res.json();
        notif.innerHTML = `<div class="alert alert-success">
          Purchased ${p.duration_minutes}-min package.
        </div>`;
      } else {
        const e = await res.json();
        notif.innerHTML = `<div class="alert alert-danger">Error: ${e.detail}</div>`;
      }
      refreshSummary();
    }
  </script>
</body>
</html>
""")

@app.get("/summary/", response_model=dict)
def read_summary(db: Session = Depends(get_db)):
    return crud.get_summary(db)

@app.post("/purchases/", response_model=schemas.Purchase)
def create_purchase(purchase: schemas.PurchaseCreate, db: Session = Depends(get_db)):
    return crud.create_purchase(db, purchase)

@app.post("/sessions/", response_model=schemas.Session)
def log_session(session_in: schemas.SessionCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_session(db, session_in.duration_minutes, session_in.trainer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/sessions/", response_model=List[schemas.Session])
def read_sessions(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud.get_sessions(db, skip, limit)

@app.get("/history", response_class=HTMLResponse)
def serve_history():
    return HTMLResponse(r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Gym Tracker History</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-4">
    <h1 class="mb-4">Gym History</h1>
    <div class="btn-group mb-4">
      <button class="btn btn-outline-primary" data-range="current_month">Current Month</button>
      <button class="btn btn-outline-primary" data-range="last_6_months">Last 6 Months</button>
      <button class="btn btn-outline-primary" data-range="last_12_months">Last 12 Months</button>
      <button class="btn btn-outline-primary" data-range="current_year">Current Year</button>
    </div>
    <div id="history-content"></div>
    <a href="/" class="btn btn-secondary mt-4">Back</a>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    function getDates(r) {
      const n = new Date(), s = new Date();
      switch(r) {
        case 'current_month':    s.setDate(1); break;
        case 'last_6_months':    s.setMonth(n.getMonth() - 6); break;
        case 'last_12_months':   s.setFullYear(n.getFullYear() - 1); break;
        case 'current_year':     s.setMonth(0); s.setDate(1); break;
      }
      return { start: s.toISOString(), end: new Date().toISOString() };
    }
    function parseUTC(str) {
      return (/Z|[+\-]\d{2}:\d{2}$/.test(str) ? new Date(str) : new Date(str + 'Z'));
    }
    async function loadHistory(r) {
      const { start, end } = getDates(r);
      const sr = await fetch(`/history/sessions/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
      const pr = await fetch(`/history/purchases/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
      const ss = await sr.json(), ps = await pr.json();
      let html = '<h3>Sessions</h3><ul>' +
        ss.map(x => `<li>${parseUTC(x.session_date).toLocaleString()}: ${x.duration_minutes} min with ${x.trainer}</li>`).join('') +
        '</ul><h3>Purchases</h3><ul>' +
        ps.map(x => `<li>${parseUTC(x.purchase_date).toLocaleString()}: ${x.duration_minutes} min package, ${x.sessions_remaining} left</li>`).join('') +
        '</ul>';
      document.getElementById('history-content').innerHTML = html;
    }
    document.addEventListener('click', e => {
      if (e.target.dataset.range) loadHistory(e.target.dataset.range);
    });
    window.addEventListener('DOMContentLoaded', () => loadHistory('current_month'));
  </script>
</body>
</html>
""")

@app.get("/history/sessions/", response_model=List[schemas.Session])
def history_sessions(start: datetime = Query(None), end: datetime = Query(None), db: Session = Depends(get_db)):
    return crud.get_sessions(db, start, end)

@app.get("/history/purchases/", response_model=List[schemas.Purchase])
def history_purchases(start: datetime = Query(None), end: datetime = Query(None), db: Session = Depends(get_db)):
    return crud.get_purchases_history(db, start, end)

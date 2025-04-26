# File: main.py

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import List, Dict
from datetime import datetime
from gym_tracker import crud, schemas, database

app = FastAPI()

# create tables
database.Base.metadata.create_all(bind=database.engine)

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Gym Tracker</title>
  <link
    href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    rel="stylesheet">
</head>
<body class="p-3">
  <div class="container">
    <h1 class="mb-4">Gym Tracker</h1>

    <div class="row mb-3">
      <div class="col">
        <button id="log30" class="btn btn-primary w-100">Log 30-min</button>
      </div>
      <div class="col">
        <button id="log45" class="btn btn-primary w-100">Log 45-min</button>
      </div>
    </div>

    <div class="row mb-3">
      <div class="col">
        <button id="buy30" class="btn btn-success w-100">Buy 30-min</button>
      </div>
      <div class="col">
        <button id="buy45" class="btn btn-success w-100">Buy 45-min</button>
      </div>
    </div>

    <div class="row mb-3">
      <div class="col">
        <button id="viewHistory" class="btn btn-info w-100">View History</button>
      </div>
    </div>

    <div id="alertArea"></div>
    <div id="historyArea"></div>
  </div>

  <!-- Trainer Modal -->
  <div class="modal fade" id="trainerModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Select Trainer</h5>
        </div>
        <div class="modal-body">
          <select id="trainerSelect" class="form-select">
            <option>Rachel</option>
            <option>Lindsay</option>
          </select>
        </div>
        <div class="modal-footer">
          <button id="confirmLog" class="btn btn-primary">Log Session</button>
        </div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    let pendingDuration = null;
    const modal = new bootstrap.Modal(document.getElementById('trainerModal'));

    function showAlert(msg, type='info') {
      document.getElementById('alertArea').innerHTML = 
        `<div class="alert alert-${type}">${msg}</div>`;
    }

    document.getElementById('log30').onclick = () => {
      pendingDuration = 30;
      modal.show();
    };
    document.getElementById('log45').onclick = () => {
      pendingDuration = 45;
      modal.show();
    };

    document.getElementById('confirmLog').onclick = async () => {
      modal.hide();
      const trainer = document.getElementById('trainerSelect').value;
      try {
        const res = await fetch('/sessions/', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({duration_minutes: pendingDuration, trainer})
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Error');
        showAlert(`Logged ${data.duration_minutes}min with ${data.trainer}`, 'success');
      } catch (err) {
        showAlert(err.message, 'danger');
      }
    };

    document.getElementById('buy30').onclick = async () => {
      await fetch('/purchases/', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({duration_minutes:30})
      }).then(r => r.json())
        .then(d => showAlert(`Bought 30-min package`, 'success'))
        .catch(e => showAlert(e.message,'danger'));
    };
    document.getElementById('buy45').onclick = async () => {
      await fetch('/purchases/', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({duration_minutes:45})
      }).then(r => r.json())
        .then(d => showAlert(`Bought 45-min package`, 'success'))
        .catch(e => showAlert(e.message,'danger'));
    };

    document.getElementById('viewHistory').onclick = () => {
      window.location.href = '/history';
    };
  </script>
</body>
</html>
"""

@app.get("/summary/", response_model=Dict[int, int])
def get_summary(db: Session = Depends(get_db)):
    return crud.get_summary(db)

@app.post("/purchases/", response_model=schemas.Purchase)
def create_purchase(purchase: schemas.PurchaseCreate, db: Session = Depends(get_db)):
    return crud.create_purchase(db, purchase.duration_minutes)

@app.post("/sessions/", response_model=schemas.Session)
def create_session(session: schemas.SessionCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_session(db, session.duration_minutes, session.trainer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/history", response_class=HTMLResponse)
def serve_history():
    return """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>History</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-3">
  <div class="container">
    <h1>History</h1>
    <div class="btn-group mb-3">
      <button class="btn btn-outline-primary" data-range="current_month">Current Month</button>
      <button class="btn btn-outline-primary" data-range="last_6_months">Last 6 Months</button>
      <button class="btn btn-outline-primary" data-range="last_12_months">Last 12 Months</button>
      <button class="btn btn-outline-primary" data-range="current_year">Current Year</button>
    </div>
    <div id="history-content"></div>
  </div>
<script>
  function getDates(range) {
    const now = new Date();
    let start;
    switch(range) {
      case 'current_month':
        start = new Date(now.getFullYear(), now.getMonth(), 1);
        break;
      case 'last_6_months':
        start = new Date(now.getFullYear(), now.getMonth() - 5, 1);
        break;
      case 'last_12_months':
        start = new Date(now.getFullYear(), now.getMonth() - 11, 1);
        break;
      case 'current_year':
        start = new Date(now.getFullYear(), 0, 1);
        break;
    }
    return { start: start.toISOString(), end: now.toISOString() };
  }

  function parseUTC(str) {
    if (/Z|[+\\-]\\d{2}:\\d{2}$/.test(str)) return new Date(str);
    return new Date(str + 'Z');
  }

  async function loadHistory(range) {
    const { start, end } = getDates(range);
    const sr = await fetch(`/history/sessions/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    const pr = await fetch(`/history/purchases/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    const sessions = await sr.json();
    const purchases = await pr.json();

    let html = '<h3>Sessions</h3><ul>';
    html += sessions.map(s =>
      `<li>${parseUTC(s.session_date).toLocaleString()}: ${s.duration_minutes} min with ${s.trainer}</li>`
    ).join('');
    html += '</ul><h3>Purchases</h3><ul>';
    html += purchases.map(p =>
      `<li>${parseUTC(p.purchase_date).toLocaleString()}: ${p.duration_minutes} min package, ${p.sessions_remaining} left</li>`
    ).join('');
    html += '</ul>';
    document.getElementById('history-content').innerHTML = html;
  }

  // wire up buttons
  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', () => loadHistory(btn.dataset.range));
  });

  // auto-load current month on page open
  window.addEventListener('DOMContentLoaded', () => loadHistory('current_month'));
</script>
</body>
</html>
"""


@app.get("/history/sessions/", response_model=List[schemas.Session])
def history_sessions(start: str, end: str, db: Session = Depends(get_db)):
    return crud.get_sessions(db, start, end)

@app.get("/history/purchases/", response_model=List[schemas.Purchase])
def history_purchases(start: str, end: str, db: Session = Depends(get_db)):
    return crud.get_purchases_history(db, start, end)

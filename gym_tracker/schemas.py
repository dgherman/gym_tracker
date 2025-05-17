from datetime import datetime
from pydantic import BaseModel

# --------------------
# Session Schemas
# --------------------

class SessionBase(BaseModel):
    session_date: datetime
    duration_minutes: int
    trainer: str

class SessionCreate(BaseModel):
    duration_minutes: int
    trainer: str

class Session(SessionBase):
    id: int
    purchase_id: int
    purchase_exhausted: bool = False
    model_config = {
        "from_attributes": True
    }


# --------------------
# Purchase Schemas
# --------------------

class PurchaseBase(BaseModel):
    duration_minutes: int
    cost: float

class PurchaseCreate(PurchaseBase):
    pass

class Purchase(PurchaseBase):
    id: int
    total_sessions: int
    sessions_remaining: int
    purchase_date: datetime

    model_config = {
        "from_attributes": True
    }

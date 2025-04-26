from datetime import datetime
from pydantic import BaseModel, ConfigDict

class PurchaseBase(BaseModel):
    duration_minutes: int

class PurchaseCreate(PurchaseBase):
    pass

class Purchase(PurchaseBase):
    id: int
    total_sessions: int
    sessions_remaining: int
    purchase_date: datetime

    model_config = ConfigDict(from_attributes=True)

class SessionBase(BaseModel):
    duration_minutes: int
    trainer: str

class SessionCreate(SessionBase):
    pass

class Session(SessionBase):
    id: int
    purchase_id: int
    session_date: datetime
    purchase_exhausted: bool

    model_config = ConfigDict(from_attributes=True)
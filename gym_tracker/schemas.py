from datetime import datetime
from pydantic import BaseModel, field_validator

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

    @field_validator('trainer')
    @classmethod
    def validate_trainer(cls, v):
        if not v or not v.strip():
            raise ValueError('Trainer name is required and cannot be empty')
        return v.strip()

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
    cost: float = 0.0

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


# --------------------
# Trainer Schemas
# --------------------

class TrainerBase(BaseModel):
    name: str

class TrainerCreate(TrainerBase):
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Trainer name is required and cannot be empty')
        return v.strip()

class TrainerUpdate(BaseModel):
    name: str = None
    is_active: bool = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValueError('Trainer name cannot be empty')
        return v.strip() if v else v

class Trainer(TrainerBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

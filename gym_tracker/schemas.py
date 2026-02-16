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
    num_people: int = 1
    partner_email: str | None = None

    @field_validator('trainer')
    @classmethod
    def validate_trainer(cls, v):
        if not v or not v.strip():
            raise ValueError('Trainer name is required and cannot be empty')
        return v.strip()

    @field_validator('partner_email')
    @classmethod
    def validate_partner_email(cls, v):
        if v is not None:
            v = v.strip().lower()
            if not v:
                return None
        return v

class Session(SessionBase):
    id: int
    purchase_id: int
    purchase_exhausted: bool = False
    partner_email: str | None = None
    partner_name: str | None = None
    num_people: int = 1
    is_owner: bool = True
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
    num_people: int = 1
    partner_email: str | None = None

    @field_validator('partner_email')
    @classmethod
    def validate_partner_email(cls, v):
        if v is not None:
            v = v.strip().lower()
            if not v:
                return None
        return v

class Purchase(PurchaseBase):
    id: int
    total_sessions: int
    sessions_remaining: int
    purchase_date: datetime
    num_people: int = 1
    partner_email: str | None = None
    partner_name: str | None = None
    is_owner: bool = True

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


# --------------------
# Package Schemas
# --------------------

class PackageBase(BaseModel):
    name: str
    duration_minutes: int
    num_people: int
    total_sessions: int
    price_per_session: float

class PackageCreate(PackageBase):
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Package name is required and cannot be empty')
        return v.strip()

    @field_validator('duration_minutes')
    @classmethod
    def validate_duration(cls, v):
        if v <= 0:
            raise ValueError('Duration must be positive')
        return v

    @field_validator('num_people')
    @classmethod
    def validate_num_people(cls, v):
        if v <= 0:
            raise ValueError('Number of people must be positive')
        return v

    @field_validator('total_sessions')
    @classmethod
    def validate_total_sessions(cls, v):
        if v <= 0:
            raise ValueError('Total sessions must be positive')
        return v

    @field_validator('price_per_session')
    @classmethod
    def validate_price(cls, v):
        if v < 0:
            raise ValueError('Price cannot be negative')
        return v

class PackageUpdate(BaseModel):
    name: str = None
    duration_minutes: int = None
    num_people: int = None
    total_sessions: int = None
    price_per_session: float = None
    is_active: bool = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValueError('Package name cannot be empty')
        return v.strip() if v else v

    @field_validator('duration_minutes')
    @classmethod
    def validate_duration(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Duration must be positive')
        return v

    @field_validator('num_people')
    @classmethod
    def validate_num_people(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Number of people must be positive')
        return v

    @field_validator('total_sessions')
    @classmethod
    def validate_total_sessions(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Total sessions must be positive')
        return v

    @field_validator('price_per_session')
    @classmethod
    def validate_price(cls, v):
        if v is not None and v < 0:
            raise ValueError('Price cannot be negative')
        return v

class Package(PackageBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

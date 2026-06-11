from datetime import datetime
from pydantic import AliasChoices, BaseModel, Field, field_validator

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
    activities: list["SessionActivityInput"] = []

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
    can_edit: bool = True
    activities: list["SessionActivityRead"] = []
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


# --------------------
# Activity Tracking Schemas
# --------------------

ALLOWED_FIELD_TYPES = {"integer", "decimal", "duration", "text"}


class CategoryFieldRead(BaseModel):
    id: int
    key: str
    label: str
    field_type: str
    unit: str | None = None
    is_required: bool
    sort_order: int
    model_config = {"from_attributes": True}


class ActivityCategoryRead(BaseModel):
    id: int
    name: str
    slug: str
    sort_order: int
    fields: list[CategoryFieldRead] = []
    model_config = {"from_attributes": True}


class ActivityRead(BaseModel):
    id: int
    category_id: int
    name: str
    is_active: bool
    model_config = {"from_attributes": True}


class ActivityCreate(BaseModel):
    category_id: int
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Activity name is required and cannot be empty")
        return v.strip()


class SessionActivityInput(BaseModel):
    # id present => update existing row; absent => insert
    id: int | None = None
    activity_id: int
    values: dict = {}
    notes: str | None = None
    person_slot: int | None = None  # None=shared, 1=owner, 2=partner


class SessionActivityRead(BaseModel):
    id: int
    activity_id: int
    activity_name: str
    category_id: int
    category_name: str
    values: dict = {}
    notes: str | None = None
    # Wire slot is viewer-relative (1=me, 2=the other person); crud annotation
    # sets person_slot_for_viewer, falling back to the stored absolute column.
    person_slot: int | None = Field(
        None,
        validation_alias=AliasChoices("person_slot_for_viewer", "person_slot"),
    )
    person_name: str | None = None
    sort_order: int
    model_config = {"from_attributes": True, "populate_by_name": True}


# ---- Admin management schemas ----

class CategoryCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Category name is required")
        return v.strip()


class CategoryUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class CategoryFieldCreate(BaseModel):
    key: str
    label: str
    field_type: str
    unit: str | None = None
    is_required: bool = False
    sort_order: int = 0

    @field_validator("key")
    @classmethod
    def validate_key(cls, v):
        v = (v or "").strip().lower()
        if not v:
            raise ValueError("Field key is required")
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Field key must be alphanumeric/underscore")
        return v

    @field_validator("field_type")
    @classmethod
    def validate_type(cls, v):
        if v not in ALLOWED_FIELD_TYPES:
            raise ValueError(f"field_type must be one of {sorted(ALLOWED_FIELD_TYPES)}")
        return v


class CategoryFieldUpdate(BaseModel):
    label: str | None = None
    unit: str | None = None
    is_required: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ActivityUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValueError("Activity name cannot be empty")
        return v.strip() if v else v


SessionCreate.model_rebuild()
Session.model_rebuild()

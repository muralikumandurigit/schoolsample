# app/schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import date

# ---------- Students ----------
class StudentBase(BaseModel):
    name: str
    email: EmailStr
    phone: str
    grade: int
    dob: Optional[date] = None
    address: Optional[str] = None
    parent_name: Optional[str] = None
    fee_total: Optional[float] = 0.0
    fee_paid: Optional[float] = 0.0
    active: Optional[bool] = True

class StudentCreate(StudentBase):
    pass

class StudentUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    grade: Optional[int] = None
    dob: Optional[date] = None
    address: Optional[str] = None
    parent_name: Optional[str] = None
    fee_total: Optional[float] = None
    fee_paid: Optional[float] = None
    active: Optional[bool] = None

class StudentOut(StudentBase):
    id: int
    fee_due: float

    # pydantic v2: allow reading attributes from ORM objects
    model_config = {"from_attributes": True}


# ---------- Teachers ----------
class TeacherBase(BaseModel):
    name: str
    email: EmailStr
    phone: str
    subject: Optional[str] = None
    salary: Optional[float] = 0.0

class TeacherCreate(TeacherBase):
    # allow specifying grades at creation time (list of grade numbers)
    grades: Optional[List[int]] = None

class TeacherUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    subject: Optional[str] = None
    salary: Optional[float] = None
    grades: Optional[List[int]] = None

class TeacherOut(TeacherBase):
    id: int
    # we'll return a list of grade integers in endpoints
    grades: List[int] = []

    model_config = {"from_attributes": True}

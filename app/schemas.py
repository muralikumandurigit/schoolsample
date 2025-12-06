# app/schemas.py
from pydantic import BaseModel, EmailStr
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

    model_config = {"from_attributes": True}


# ---------- Teachers ----------
class TeacherBase(BaseModel):
    name: str
    email: EmailStr
    phone: str
    subject: Optional[str] = None
    salary: Optional[float] = 0.0

class TeacherCreate(TeacherBase):
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
    grades: List[int] = []

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, obj):
        """
        Convert ORM Teacher object to TeacherOut.
        Ensures 'grades' is a list of integers.
        """
        return cls(
            id=obj.id,
            name=obj.name,
            email=obj.email,
            phone=obj.phone,
            subject=obj.subject,
            salary=obj.salary,
            grades=[g.grade if hasattr(g, 'grade') else g for g in getattr(obj, "grades", [])]
        )

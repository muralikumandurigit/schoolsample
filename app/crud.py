# app/crud.py
from sqlalchemy.orm import Session
from typing import List, Optional
from app import models, schemas
from sqlalchemy import select

# ---------- STUDENT CRUD ----------
def create_student(db: Session, student: schemas.StudentCreate) -> models.Student:
    db_student = models.Student(**student.model_dump())
    db.add(db_student)
    db.commit()
    db.refresh(db_student)
    return db_student

def get_student(db: Session, student_id: int) -> Optional[models.Student]:
    return db.query(models.Student).filter(models.Student.id == student_id).first()

def update_student(db: Session, student_id: int, updates: dict) -> Optional[models.Student]:
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        return None
    for k, v in updates.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s

def delete_student(db: Session, student_id: int) -> bool:
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        return False
    db.delete(s)
    db.commit()
    return True

# list / query helpers
def list_students(db: Session, skip: int = 0, limit: int = 100) -> List[models.Student]:
    return db.query(models.Student).offset(skip).limit(limit).all()

def students_with_fee_due(db: Session, min_due: float = 0.0) -> List[models.Student]:
    return db.query(models.Student).filter((models.Student.fee_total - models.Student.fee_paid) >= min_due).all()

def students_unpaid(db: Session) -> List[models.Student]:
    return db.query(models.Student).filter((models.Student.fee_paid == 0) & (models.Student.fee_total > 0)).all()

def students_partial_paid(db: Session) -> List[models.Student]:
    return db.query(models.Student).filter((models.Student.fee_paid > 0) & (models.Student.fee_paid < models.Student.fee_total)).all()

def students_by_grade(db: Session, grade: int) -> List[models.Student]:
    return db.query(models.Student).filter(models.Student.grade == grade).all()


# ---------- TEACHER CRUD ----------
def create_teacher(db: Session, teacher: schemas.TeacherCreate) -> models.Teacher:
    data = teacher.model_dump()
    grades = data.pop("grades", []) or []
    db_teacher = models.Teacher(**data)
    db.add(db_teacher)
    db.flush()
    for g in grades:
        db.add(models.GradeAssignment(teacher_id=db_teacher.id, grade=g))
    db.commit()
    db.refresh(db_teacher)
    return db_teacher

def get_teacher(db: Session, teacher_id: int) -> Optional[models.Teacher]:
    return db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()

def update_teacher(db: Session, teacher_id: int, updates: dict) -> Optional[models.Teacher]:
    t = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not t:
        return None
    grades = updates.pop("grades", None)
    for k, v in updates.items():
        setattr(t, k, v)
    if grades is not None:
        db.query(models.GradeAssignment).filter(models.GradeAssignment.teacher_id == teacher_id).delete(synchronize_session=False)
        for g in grades:
            db.add(models.GradeAssignment(teacher_id=teacher_id, grade=g))
    db.commit()
    db.refresh(t)
    return t

def delete_teacher(db: Session, teacher_id: int) -> bool:
    t = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not t:
        return False
    db.delete(t)
    db.commit()
    return True


# ---------- TEACHER QUERIES ----------
def list_teachers(db: Session, skip: int = 0, limit: int = 100) -> List[models.Teacher]:
    return db.query(models.Teacher).offset(skip).limit(limit).all()

def teachers_by_salary(db: Session, op: str, amount: float) -> List[models.Teacher]:
    if op == "gte":
        return db.query(models.Teacher).filter(models.Teacher.salary >= amount).all()
    if op == "lte":
        return db.query(models.Teacher).filter(models.Teacher.salary <= amount).all()
    return []

def teachers_for_grade(db: Session, grade: int) -> List[models.Teacher]:
    tas = db.query(models.GradeAssignment).filter(models.GradeAssignment.grade == grade).all()
    teacher_ids = [ta.teacher_id for ta in tas]
    if not teacher_ids:
        return []
    return db.query(models.Teacher).filter(models.Teacher.id.in_(teacher_ids)).all()


# ---------- HELPERS TO CONVERT ORM -> Pydantic ----------

def teacher_to_out(teacher: models.Teacher) -> schemas.TeacherOut:
    """Convert ORM Teacher -> TeacherOut, converting grades to integers."""
    grade_list = [g.grade for g in getattr(teacher, "grades", [])]
    return schemas.TeacherOut(
        id=teacher.id,
        name=teacher.name,
        email=teacher.email,
        phone=teacher.phone,
        subject=teacher.subject,
        salary=teacher.salary,
        grades=grade_list
    )

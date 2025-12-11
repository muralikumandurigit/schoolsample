# app/main.py
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app import db, crud, schemas

app = FastAPI(title="School CRUD API")

# dependency
def get_db():
    db_session = db.SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()

@app.on_event("startup")
def startup():
    db.init_db()

# ---------- Student endpoints ----------
@app.post("/students/", response_model=schemas.StudentOut)
def create_student(student: schemas.StudentCreate, database: Session = Depends(get_db)):
    return crud.create_student(database, student)

@app.get("/students/{student_id}", response_model=schemas.StudentOut)
def read_student(student_id: int, database: Session = Depends(get_db)):
    s = crud.get_student(database, student_id)
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    return s

@app.patch("/students/{student_id}", response_model=schemas.StudentOut)
def patch_student(student_id: int, updates: schemas.StudentUpdate, database: Session = Depends(get_db)):
    s = crud.update_student(database, student_id, updates.model_dump(exclude_none=True))
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    return s

@app.delete("/students/{student_id}")
def remove_student(student_id: int, database: Session = Depends(get_db)):
    ok = crud.delete_student(database, student_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"ok": True}

@app.get("/students/", response_model=list[schemas.StudentOut])
def list_students(skip: int = 0, limit: int = 100, database: Session = Depends(get_db)):
    return crud.list_students(database, skip, limit)

# Query endpoints
@app.get("/students/fee_due/", response_model=list[schemas.StudentOut])
def students_fee_due(min_due: float = Query(0.0), database: Session = Depends(get_db)):
    return crud.students_with_fee_due(database, min_due)

@app.get("/students/unpaid/", response_model=list[schemas.StudentOut])
def unpaid_students(database: Session = Depends(get_db)):
    return crud.students_unpaid(database)

@app.get("/students/partial_paid/", response_model=list[schemas.StudentOut])
def partial_paid_students(database: Session = Depends(get_db)):
    return crud.students_partial_paid(database)

@app.get("/students/fullpaid/", response_model=list[schemas.StudentOut])
def fullpaid_students(database: Session = Depends(get_db)):
    return crud.students_fullpaid(database)

@app.get("/students/grade/{grade}", response_model=list[schemas.StudentOut])
def students_in_grade(grade: int, database: Session = Depends(get_db)):
    return crud.students_by_grade(database, grade)


# ---------- Teacher endpoints ----------
@app.post("/teachers/", response_model=schemas.TeacherOut)
def create_teacher(teacher: schemas.TeacherCreate, database: Session = Depends(get_db)):
    t = crud.create_teacher(database, teacher)
    # convert GradeAssignment objects to integers for response
    grades = [ga.grade for ga in t.grades]
    return {**t.__dict__, "grades": grades}

@app.get("/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def read_teacher(teacher_id: int, database: Session = Depends(get_db)):
    t = crud.get_teacher(database, teacher_id)
    if not t:
        raise HTTPException(status_code=404, detail="Teacher not found")
    grades = [g.grade for g in t.grades]
    return {**t.__dict__, "grades": grades}

@app.patch("/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def patch_teacher(teacher_id: int, updates: schemas.TeacherUpdate, database: Session = Depends(get_db)):
    t = crud.update_teacher(database, teacher_id, updates.model_dump(exclude_none=True))
    if not t:
        raise HTTPException(status_code=404, detail="Teacher not found")
    grades = [g.grade for g in t.grades]
    return {**t.__dict__, "grades": grades}

@app.delete("/teachers/{teacher_id}")
def remove_teacher(teacher_id: int, database: Session = Depends(get_db)):
    ok = crud.delete_teacher(database, teacher_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return {"ok": True}

@app.get("/teachers/", response_model=list[schemas.TeacherOut])
def list_teachers(skip: int = 0, limit: int = 100, database: Session = Depends(get_db)):
    teachers = crud.list_teachers(database, skip, limit)
    out = []
    for t in teachers:
        grades = [g.grade for g in t.grades]
        out.append({**t.__dict__, "grades": grades})
    return out

@app.get("/teachers/salary/")
def teachers_by_salary(op: str = Query("gte", pattern="^(gte|lte)$"), amount: float = Query(0.0), database: Session = Depends(get_db)):
    teachers = crud.teachers_by_salary(database, op, amount)
    out = []
    for t in teachers:
        grades = [g.grade for g in t.grades]
        out.append({**t.__dict__, "grades": grades})
    return out

@app.get("/teachers/grade/{grade}", response_model=list[schemas.TeacherOut])
def teachers_for_grade(grade: int, database: Session = Depends(get_db)):
    teachers = crud.teachers_for_grade(database, grade)
    out = []
    for t in teachers:
        grades = [g.grade for g in t.grades]
        out.append({**t.__dict__, "grades": grades})
    return out

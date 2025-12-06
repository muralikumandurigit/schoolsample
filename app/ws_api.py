# app/ws_api.py
from fastapi import FastAPI, WebSocket
from sqlalchemy.orm import Session
from app import db, crud, schemas
import json

app = FastAPI(title="School CRUD WebSocket API")

# ---------------- Dependency ----------------------
def get_db():
    db_session = db.SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()

@app.on_event("startup")
def startup():
    db.init_db()

# ---------------------------------------------------
# JSON-RPC Response Helpers
# ---------------------------------------------------
def rpc_result(msg_id, result):
    return json.dumps({"id": msg_id, "result": result})

def rpc_error(msg_id, error):
    return json.dumps({"id": msg_id, "error": {"message": error}})

# ---------------------------------------------------
# Serialization Helpers (ORM → JSON)
# ---------------------------------------------------
def serialize_student(student):
    return schemas.StudentOut.model_validate(
        student, from_attributes=True
    ).model_dump(mode="json")

def serialize_teacher(teacher):
    data = schemas.TeacherOut.model_validate(
        teacher, from_attributes=True
    ).model_dump(mode="json")
    # Convert GradeAssignment objects
    data["grades"] = [g.grade for g in teacher.grades]
    return data

# ---------------------------------------------------
#  MAIN WEBSOCKET ENDPOINT
# ---------------------------------------------------
@app.websocket("/ws")
async def websocket_handler(ws: WebSocket):
    await ws.accept()

    while True:
        raw = await ws.receive_text()

        try:
            message = json.loads(raw)
            msg_id = message.get("id")
            method = message.get("method")
            params = message.get("params", {})

            db_session = next(get_db())

            # ======================================================
            #                     STUDENT METHODS
            # ======================================================

            if method == "students.create":
                data = schemas.StudentCreate(**params)
                s = crud.create_student(db_session, data)
                await ws.send_text(rpc_result(msg_id, serialize_student(s)))

            elif method == "students.get":
                sid = params["student_id"]
                s = crud.get_student(db_session, sid)
                if not s:
                    await ws.send_text(rpc_error(msg_id, "Student not found"))
                else:
                    await ws.send_text(rpc_result(msg_id, serialize_student(s)))

            elif method == "students.update":
                sid = params["student_id"]
                updates = schemas.StudentUpdate(**params.get("updates", {}))
                s = crud.update_student(db_session, sid, updates.model_dump(exclude_none=True))
                if not s:
                    await ws.send_text(rpc_error(msg_id, "Student not found"))
                else:
                    await ws.send_text(rpc_result(msg_id, serialize_student(s)))

            elif method == "students.delete":
                sid = params["student_id"]
                ok = crud.delete_student(db_session, sid)
                await ws.send_text(rpc_result(msg_id, {"ok": ok}))

            elif method == "students.list":
                skip = params.get("skip", 0)
                limit = params.get("limit", 100)
                students = crud.list_students(db_session, skip, limit)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_student(s) for s in students])
                )

            # Query endpoints
            elif method == "students.fee_due":
                min_due = params.get("min_due", 0.0)
                students = crud.students_with_fee_due(db_session, min_due)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_student(s) for s in students])
                )

            elif method == "students.unpaid":
                students = crud.students_unpaid(db_session)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_student(s) for s in students])
                )

            elif method == "students.partial_paid":
                students = crud.students_partial_paid(db_session)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_student(s) for s in students])
                )

            elif method == "students.by_grade":
                grade = params["grade"]
                students = crud.students_by_grade(db_session, grade)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_student(s) for s in students])
                )

            # ======================================================
            #                     TEACHER METHODS
            # ======================================================

            elif method == "teachers.create":
                data = schemas.TeacherCreate(**params)
                t = crud.create_teacher(db_session, data)
                await ws.send_text(rpc_result(msg_id, serialize_teacher(t)))

            elif method == "teachers.get":
                tid = params["teacher_id"]
                t = crud.get_teacher(db_session, tid)
                if not t:
                    await ws.send_text(rpc_error(msg_id, "Teacher not found"))
                else:
                    await ws.send_text(rpc_result(msg_id, serialize_teacher(t)))

            elif method == "teachers.update":
                tid = params["teacher_id"]
                updates = schemas.TeacherUpdate(**params.get("updates", {}))
                t = crud.update_teacher(db_session, tid, updates.model_dump(exclude_none=True))
                if not t:
                    await ws.send_text(rpc_error(msg_id, "Teacher not found"))
                else:
                    await ws.send_text(rpc_result(msg_id, serialize_teacher(t)))

            elif method == "teachers.delete":
                tid = params["teacher_id"]
                ok = crud.delete_teacher(db_session, tid)
                await ws.send_text(rpc_result(msg_id, {"ok": ok}))

            elif method == "teachers.list":
                teachers = crud.list_teachers(db_session)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_teacher(t) for t in teachers])
                )

            elif method == "teachers.by_salary":
                op = params.get("op", "gte")
                amount = params.get("amount", 0.0)
                teachers = crud.teachers_by_salary(db_session, op, amount)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_teacher(t) for t in teachers])
                )

            elif method == "teachers.by_grade":
                grade = params["grade"]
                teachers = crud.teachers_for_grade(db_session, grade)
                await ws.send_text(
                    rpc_result(msg_id, [serialize_teacher(t) for t in teachers])
                )

            # ======================================================
            #                      UNKNOWN METHOD
            # ======================================================
            else:
                await ws.send_text(rpc_error(msg_id, f"Unknown method '{method}'"))

        except Exception as e:
            await ws.send_text(rpc_error(message.get("id"), str(e)))

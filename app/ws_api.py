# app/ws_api.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from contextlib import contextmanager
from app import db, crud, schemas
import json

app = FastAPI(title="School CRUD WebSocket API")

# ---------------- Database Dependency ----------------------
@contextmanager
def get_db():
    db_session = db.SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()

@app.on_event("startup")
def startup():
    db.init_db()

# ---------------- JSON-RPC Helpers ----------------------
def rpc_result(msg_id, result):
    return json.dumps({"id": msg_id, "result": result})

def rpc_error(msg_id, error):
    return json.dumps({"id": msg_id, "error": {"message": error}})

# ---------------- Serialization Helpers ----------------------
def serialize_student(student):
    return schemas.StudentOut.model_validate(student, from_attributes=True).model_dump(mode="json")

def serialize_teacher(teacher):
    return schemas.TeacherOut(
        id=teacher.id,
        name=teacher.name,
        email=teacher.email,
        phone=teacher.phone,
        subject=teacher.subject,
        salary=teacher.salary,
        grades=[g.grade for g in teacher.grades]
    ).model_dump(mode="json")

# ---------------- CRUD Mappings ----------------------
ENTITY_MAP = {
    "students": {
        "create": (crud.create_student, serialize_student, schemas.StudentCreate),
        "get": (crud.get_student, serialize_student, None),
        "update": (crud.update_student, serialize_student, schemas.StudentUpdate),
        "delete": (crud.delete_student, None, None),
        "list": (crud.list_students, serialize_student, None),
        "fee_due": (crud.students_with_fee_due, serialize_student, None),
        "unpaid": (crud.students_unpaid, serialize_student, None),
        "partial_paid": (crud.students_partial_paid, serialize_student, None),
        "by_grade": (crud.students_by_grade, serialize_student, None),
    },
    "teachers": {
        "create": (crud.create_teacher, serialize_teacher, schemas.TeacherCreate),
        "get": (crud.get_teacher, serialize_teacher, None),
        "update": (crud.update_teacher, serialize_teacher, schemas.TeacherUpdate),
        "delete": (crud.delete_teacher, None, None),
        "list": (crud.list_teachers, serialize_teacher, None),
        "by_salary": (crud.teachers_by_salary, serialize_teacher, None),
        "by_grade": (crud.teachers_for_grade, serialize_teacher, None),
    }
}

# ---------------- WebSocket Endpoint ----------------------
@app.websocket("/ws")
async def websocket_handler(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            message = json.loads(raw)
            msg_id = message.get("id")
            method = message.get("method")
            params = message.get("params", {})

            if "." not in method:
                await ws.send_text(rpc_error(msg_id, f"Invalid method '{method}'"))
                continue

            entity_name, action = method.split(".", 1)
            entity = ENTITY_MAP.get(entity_name)
            if not entity or action not in entity:
                await ws.send_text(rpc_error(msg_id, f"Unknown method '{method}'"))
                continue

            func, serializer, schema_cls = entity[action]

            try:
                with get_db() as db_session:
                    # Handle create/update with Pydantic schema
                    if schema_cls and action in ("create", "update"):
                        data = schema_cls(**params) if action == "create" else schema_cls(**params.get("updates", {}))
                        result = func(db_session, data)
                    # Handle list and queries
                    elif action in ("list", "fee_due", "unpaid", "partial_paid", "by_grade", "by_salary"):
                        if method == "teachers.by_salary":
                            op = params.get("op", "gte")
                            amount = params.get("amount", 0.0)
                            result = func(db_session, op, amount)
                        elif method.endswith("by_grade"):
                            grade = params.get("grade")
                            result = func(db_session, grade)
                        else:
                            skip = params.get("skip", 0)
                            limit = params.get("limit", 100)
                            result = func(db_session, skip, limit) if action == "list" else func(db_session)
                    # Handle get/delete
                    else:
                        obj_id = params.get(f"{entity_name[:-1]}_id")
                        if obj_id is None:
                            await ws.send_text(rpc_error(msg_id, f"Missing '{entity_name[:-1]}_id'"))
                            continue
                        result = func(db_session, obj_id)

                    # Serialize result if needed
                    if serializer:
                        if isinstance(result, list):
                            result_data = [serializer(r) for r in result]
                        else:
                            result_data = serializer(result)
                        await ws.send_text(rpc_result(msg_id, result_data))
                    else:
                        await ws.send_text(rpc_result(msg_id, {"ok": result}))

            except Exception as e:
                # Only send error if websocket is still open
                try:
                    await ws.send_text(rpc_error(msg_id, str(e)))
                except WebSocketDisconnect:
                    break

    except WebSocketDisconnect:
        print("WebSocket client disconnected")

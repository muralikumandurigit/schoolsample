import asyncio
import websockets
import json
import random
import string

WS_URL = "ws://localhost:8000/ws"

# -------------------------------------------------------
# Helper: send JSON-RPC message
# -------------------------------------------------------
async def rpc(ws, method, params=None, msg_id=None):
    if msg_id is None:
        msg_id = random.randint(1, 999999)

    await ws.send(json.dumps({
        "id": msg_id,
        "method": method,
        "params": params or {}
    }))

    resp_raw = await ws.recv()
    try:
        resp = json.loads(resp_raw)
        print(f"\n<<< Response for {method}:")
        print(json.dumps(resp, indent=4))

        # Return either 'result' or 'error' for easier handling
        if "result" in resp:
            return resp["result"]
        elif "error" in resp:
            return {"error": resp["error"]}
        else:
            return {"error": {"message": "Unknown response format"}}

    except Exception:
        print("Invalid JSON:", resp_raw)
        return {"error": {"message": "Invalid JSON"}}

# -------------------------------------------------------
# Utility to generate random names and emails
# -------------------------------------------------------
def rand_name(prefix):
    return prefix + "_" + ''.join(random.choices(string.ascii_lowercase, k=6))

def rand_email(prefix):
    return f"{prefix}_{''.join(random.choices(string.ascii_lowercase, k=4))}@test.com"

# -------------------------------------------------------
# MAIN TEST EXECUTION
# -------------------------------------------------------
async def main():
    print(f"Connecting to: {WS_URL}")
    async with websockets.connect(WS_URL) as ws:

        print("\n================ STUDENT TESTS ================\n")

        # 1. CREATE students
        stu_ids = []
        for i in range(3):
            resp = await rpc(ws, "students.create", {
                "name": rand_name("student"),
                "email": rand_email(f"s{i}"),
                "phone": "123456",
                "grade": random.randint(1, 10),
                "fee_total": 1000,
                "fee_paid": random.choice([0, 300, 1000])
            })
            if "id" in resp:
                stu_ids.append(resp["id"])
            else:
                print("Student creation failed:", resp.get("error"))

        # 2. LIST students
        await rpc(ws, "students.list")

        # 3. GET each student
        for sid in stu_ids:
            await rpc(ws, "students.get", {"student_id": sid})

        # 4. UPDATE one student
        if stu_ids:
            await rpc(ws, "students.update", {
                "student_id": stu_ids[0],
                "updates": {"fee_paid": 500}
            })

        # 5. Queries
        await rpc(ws, "students.fee_due", {"min_due": 200})
        await rpc(ws, "students.unpaid")
        await rpc(ws, "students.partial_paid")
        await rpc(ws, "students.by_grade", {"grade": 5})

        # 6. DELETE one student
        if stu_ids:
            await rpc(ws, "students.delete", {"student_id": stu_ids[-1]})

        # Try deleting again → error expected
        await rpc(ws, "students.delete", {"student_id": 99999})

        print("\n================ TEACHER TESTS ================\n")

        # 1. CREATE teachers
        teacher_ids = []
        for i in range(2):
            resp = await rpc(ws, "teachers.create", {
                "name": rand_name("teacher"),
                "email": rand_email(f"t{i}"),
                "phone": "987654",
                "subject": "Math",
                "salary": random.randint(30000, 80000),
                "grades": [3, 4, 5]  # integers only
            })
            if "id" in resp:
                teacher_ids.append(resp["id"])
            else:
                print("Teacher creation failed:", resp.get("error"))

        # 2. LIST teachers
        await rpc(ws, "teachers.list")

        # 3. GET one teacher
        if teacher_ids:
            await rpc(ws, "teachers.get", {"teacher_id": teacher_ids[0]})

        # 4. UPDATE teacher
        if teacher_ids:
            await rpc(ws, "teachers.update", {
                "teacher_id": teacher_ids[0],
                "updates": {"salary": 90000, "grades": [8, 9]}
            })

        # 5. Query teachers by salary
        await rpc(ws, "teachers.by_salary", {"op": "gte", "amount": 50000})

        # 6. Teachers by grade
        await rpc(ws, "teachers.by_grade", {"grade": 8})

        # 7. DELETE teacher
        if teacher_ids:
            await rpc(ws, "teachers.delete", {"teacher_id": teacher_ids[-1]})

        # Error case
        await rpc(ws, "teachers.get", {"teacher_id": 99999})

        print("\n================ NEGATIVE TESTS ================\n")

        # Unknown method
        await rpc(ws, "unknown.method", {"x": 1})

        # Missing params
        await rpc(ws, "students.get")

        print("\n\n========== ALL TESTS COMPLETED ==========\n")


if __name__ == "__main__":
    asyncio.run(main())

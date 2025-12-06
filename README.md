Instructions:
1. Create a virtualenv and install dependencies from requirements.txt
python -m venv venv
source venv/bin/activate (or venv\Scripts\activate on Windows)
pip install -r requirements.txt


2. Seed the DB:
python seed_data.py


3. Run the API server:
uvicorn app.main:app --reload


4. Run tests (in a separate shell):
python test_api.py


Notes:
- The project uses SQLite (file: school.db by default). To change, set DATABASE_URL env var.
- APIs implemented: CRUD for students and teachers, queries for fee due/unpaid/partial, salary filters, teachers-for-grade.
- Everything is modularized into app/db.py, app/models.py, app/schemas.py, app/crud.py, app/main.py
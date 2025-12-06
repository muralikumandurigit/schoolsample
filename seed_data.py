# Run: python seed_data.py (creates/overwrites school.db)
from faker import Faker
import random
from datetime import datetime, timedelta
from app.db import SessionLocal, init_db
from app import models


fake = Faker()


NUM_STUDENTS = 500
NUM_TEACHERS = 50


def seed():
    init_db()
    db = SessionLocal()
    # clear existing
    db.query(models.GradeAssignment).delete()
    db.query(models.Student).delete()
    db.query(models.Teacher).delete()
    db.commit()


    # create teachers
    for i in range(NUM_TEACHERS):
        name = fake.name()
        email = f"{name.replace(' ','_').lower()}{i}@example.com"
        phone = fake.msisdn()[:10]
        subject = random.choice(['Math','Science','English','History','Geography','Arts','Computer'])
        salary = round(random.uniform(30000, 200000), 2)
        teacher = models.Teacher(name=name, email=email, phone=phone, subject=subject, salary=salary)
        db.add(teacher)
        db.flush()
        # assign 1-3 grades
        grades = random.sample(range(1,13), k=random.randint(1,3))
        for g in grades:
            db.add(models.GradeAssignment(teacher_id=teacher.id, grade=g))
    db.commit()

    # create students
    for i in range(NUM_STUDENTS):
        name = fake.name()
        email = f"{name.replace(' ','_').lower()}{i}@student.example.com"
        phone = fake.msisdn()[:10]
        grade = random.randint(1,12)
        dob = fake.date_between(start_date='-18y', end_date='-6y')
        address = fake.address()
        parent_name = fake.name()
        fee_total = random.choice([30000, 50000, 75000, 100000])
        # simulate some having paid none, partial, or full
        paid_choice = random.random()
        if paid_choice < 0.25:
            fee_paid = 0.0
        elif paid_choice < 0.75:
            fee_paid = round(random.uniform(1000, fee_total*0.9), 2)
        else:
            fee_paid = fee_total
        active = random.random() > 0.05 # 5% left
        s = models.Student(name=name, email=email, phone=phone, grade=grade, dob=dob, address=address, parent_name=parent_name, fee_total=fee_total, fee_paid=fee_paid, active=active)
        db.add(s)
    db.commit()
    db.close()
    print('Seeded database with', NUM_STUDENTS, 'students and', NUM_TEACHERS, 'teachers')

if __name__ == '__main__':
    seed()
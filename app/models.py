from sqlalchemy import Column, Integer, String, Float, Boolean, Date, ForeignKey, Table
from sqlalchemy.orm import relationship
from app.db import Base


class Student(Base):
    __tablename__ = 'students'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, index=True)
    grade = Column(Integer, index=True)
    dob = Column(Date, nullable=True)
    address = Column(String, nullable=True)
    parent_name = Column(String, nullable=True)
    fee_total = Column(Float, default=0.0)
    fee_paid = Column(Float, default=0.0)
    active = Column(Boolean, default=True)

    @property
    def fee_due(self):
        return max(0.0, (self.fee_total or 0.0) - (self.fee_paid or 0.0))


class Teacher(Base):
    __tablename__ = 'teachers'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, index=True)
    subject = Column(String, nullable=True)
    salary = Column(Float, default=0.0)
    # relationship to grades through association table
    grades = relationship('GradeAssignment', back_populates='teacher', cascade='all, delete-orphan')


class GradeAssignment(Base):
    __tablename__ = 'grade_assignments'
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey('teachers.id'))
    grade = Column(Integer, nullable=False)
    teacher = relationship('Teacher', back_populates='grades')
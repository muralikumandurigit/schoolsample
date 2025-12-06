import requests
import random

BASE = 'http://127.0.0.1:8000'

def safe_json(r):
    try:
        return r.json()
    except Exception:
        return r.text   # fallback for errors / non-JSON bodies

def run_tests():
    print('Listing students (first 5)')
    r = requests.get(BASE + '/students?skip=0&limit=5')
    print(r.status_code, safe_json(r))

    print('Unpaid students count')
    r = requests.get(BASE + '/students/unpaid/')
    print(len(safe_json(r)))

    print('Partial paid students count')
    r = requests.get(BASE + '/students/partial_paid/')
    print(len(safe_json(r)))

    print('Students with fee due >= 50000')
    r = requests.get(BASE + '/students/fee_due/?min_due=50000')
    print(len(safe_json(r)))

    print('Teachers with salary >= 100000')
    r = requests.get(BASE + '/teachers/salary/?op=gte&amount=100000')
    print(len(safe_json(r)))

    print('Teachers for grade 7')
    r = requests.get(BASE + '/teachers/grade/7')
    print(len(safe_json(r)))

    # Create a student
    print('Creating a test student')
    payload = {
        'name': 'Test Student',
        'email': f'teststudent{random.randint(1,10000)}@example.com',
        'phone': '9999999999',
        'grade': 7,
        'fee_total': 50000,
        'fee_paid': 10000
    }
    r = requests.post(BASE + '/students/', json=payload)
    print('create status', r.status_code, safe_json(r))

    # If creation failed → stop
    if r.status_code != 200:
        print("Student creation failed, stopping.")
        return

    sid = r.json().get('id')

    print('Updating student fee_paid to 30000')
    r = requests.patch(BASE + f'/students/{sid}', json={'fee_paid': 30000})
    print(r.status_code, safe_json(r))

    print('Deleting student')
    r = requests.delete(BASE + f'/students/{sid}')
    print(r.status_code, safe_json(r))


if __name__ == '__main__':
    run_tests()

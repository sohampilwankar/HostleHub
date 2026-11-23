"""End-to-end tests for HostelHub.  Run:  python -m unittest discover tests -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from database import db as dbm  # noqa: E402


class HostelHubTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path,
                               "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test"})
        self.client = self.app.test_client()
        conn = dbm.connect(self.db_path)
        pw = generate_password_hash("pass1234")
        conn.executemany(
            "INSERT INTO students (name, email, password, year, student_code, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [("Asha Rao", "asha@college.edu", pw, 1, "S001", dbm.now()),
             ("Vikram Iyer", "vikram@college.edu", pw, 2, "S002", dbm.now()),
             ("Neha Joshi", "neha@college.edu", pw, 3, "S003", dbm.now())])
        conn.execute("INSERT INTO rooms (room_number, block, room_type, capacity) "
                     "VALUES ('101', 'A', 'Triple', 3)")
        conn.commit()
        conn.close()

    def tearDown(self):
        os.remove(self.db_path)

    # ---------------------------------------------------------------- helpers
    def login(self, ident, password, role="student"):
        return self.client.post("/login", data={"role": role, "identifier": ident,
                                                "password": password}, follow_redirects=True)

    def login_admin(self):
        return self.login("admin", "admin123", "admin")

    def logout(self):
        return self.client.post("/logout", follow_redirects=True)

    def query(self, sql, params=()):
        conn = dbm.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def apply(self, email, room_type="Double"):
        self.logout()
        self.login(email, "pass1234")
        return self.client.post("/student/apply", data={"room_type": room_type, "block": ""},
                                follow_redirects=True)

    def app_id(self, email):
        return self.query("SELECT a.id FROM applications a JOIN students s ON s.id = a.student_id "
                          "WHERE s.email = ? ORDER BY a.id DESC", (email,))[0]["id"]

    def room_id(self):
        return self.query("SELECT id FROM rooms WHERE block='A' AND room_number='101'")[0]["id"]

    def approve_and_assign(self, email):
        self.logout()
        self.login_admin()
        aid = self.app_id(email)
        self.client.post(f"/admin/applications/{aid}/approve")
        return self.client.post(f"/admin/applications/{aid}/assign",
                                data={"room_id": self.room_id()}, follow_redirects=True)

    # ----------------------------------------------------------------- auth
    def test_student_login(self):
        r = self.login("asha@college.edu", "pass1234")
        self.assertIn(b"Welcome, Asha Rao", r.data)

    def test_admin_login(self):
        r = self.login_admin()
        self.assertIn(b"Admin Dashboard", r.data)

    def test_bad_login(self):
        r = self.login("asha@college.edu", "wrong")
        self.assertIn(b"Invalid credentials", r.data)

    def test_unauthenticated_redirected(self):
        for url in ["/student/dashboard", "/admin/dashboard", "/admin/policies"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])

    def test_student_cannot_access_admin(self):
        self.login("asha@college.edu", "pass1234")
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 403)
        self.assertEqual(self.client.post("/admin/policies", data={}).status_code, 403)

    def test_admin_cannot_access_student_pages(self):
        self.login_admin()
        self.assertEqual(self.client.get("/student/dashboard").status_code, 403)

    def test_all_pages_render(self):
        self.login("asha@college.edu", "pass1234")
        for url in ["/student/dashboard", "/student/apply", "/student/allocation",
                    "/student/complaints", "/student/notices", "/student/profile"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.logout()
        self.login_admin()
        for url in ["/admin/dashboard", "/admin/students", "/admin/students?edit=1",
                    "/admin/rooms", "/admin/rooms?edit=1", "/admin/applications",
                    "/admin/applications?status=Pending", "/admin/complaints",
                    "/admin/notices", "/admin/policies"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/nope").status_code, 404)

    def test_csrf_enforced(self):
        app = create_app({"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "x"})
        r = app.test_client().post("/login", data={"role": "admin", "identifier": "admin",
                                                   "password": "admin123"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("/admin", r.headers["Location"])

    def test_passwords_hashed(self):
        for row in self.query("SELECT password FROM students UNION ALL SELECT password FROM admins"):
            self.assertNotIn(row["password"], ("pass1234", "admin123"))

    # ----------------------------------------------------- workflow 1: rooms
    def test_workflow_room_allocation(self):
        r = self.apply("asha@college.edu")
        self.assertIn(b"Application submitted successfully", r.data)
        self.assertEqual(self.query("SELECT status FROM applications")[0]["status"], "Pending")

        # duplicate active application blocked
        r = self.client.post("/student/apply", data={"room_type": "Single"}, follow_redirects=True)
        self.assertIn(b"already have an active application", r.data)
        self.assertEqual(len(self.query("SELECT id FROM applications")), 1)

        self.logout()
        self.login_admin()
        self.assertIn(b"Asha Rao", self.client.get("/admin/applications?status=Pending").data)
        r = self.approve_and_assign("asha@college.edu")
        self.assertIn(b"allocated successfully", r.data)
        self.assertEqual(self.query("SELECT occupied FROM rooms")[0]["occupied"], 1)
        self.assertEqual(self.query("SELECT status FROM applications")[0]["status"], "Allocated")

        self.logout()
        self.login("asha@college.edu", "pass1234")
        self.assertIn(b"A-101", self.client.get("/student/dashboard").data)
        self.assertIn(b"Block A", self.client.get("/student/allocation").data)

    def test_reject_application(self):
        self.apply("asha@college.edu")
        self.logout()
        self.login_admin()
        r = self.client.post(f"/admin/applications/{self.app_id('asha@college.edu')}/reject",
                             follow_redirects=True)
        self.assertIn(b"Application rejected", r.data)
        self.assertEqual(self.query("SELECT status FROM applications")[0]["status"], "Rejected")

    def test_cannot_assign_unapproved(self):
        self.apply("asha@college.edu")
        self.logout()
        self.login_admin()
        r = self.client.post(f"/admin/applications/{self.app_id('asha@college.edu')}/assign",
                             data={"room_id": self.room_id()}, follow_redirects=True)
        self.assertIn(b"Only approved applications", r.data)

    def test_no_duplicate_allocation(self):
        self.apply("asha@college.edu")
        self.approve_and_assign("asha@college.edu")
        r = self.apply("asha@college.edu")
        self.assertIn(b"already have a room allocated", r.data)
        # even forcing a second approved application at DB level cannot allocate twice
        conn = dbm.connect(self.db_path)
        cur = conn.execute("INSERT INTO applications (student_id, preferences, status, application_date)"
                           " VALUES (1, '{}', 'Approved', '2026-01-01 00:00:00')")
        conn.commit()
        with self.assertRaises(dbm.RuleError):
            dbm.allocate_room(conn, cur.lastrowid, self.room_id())
        conn.close()
        self.assertEqual(self.query("SELECT occupied FROM rooms")[0]["occupied"], 1)

    def test_capacity_never_exceeded(self):
        conn = dbm.connect(self.db_path)
        conn.execute("UPDATE rooms SET capacity = 2")
        conn.execute("INSERT INTO students (name, email, password, year, student_code) "
                     "VALUES ('D', 'd@c.edu', 'x', 1, 'S004')")
        conn.commit()
        conn.close()
        for email in ["asha@college.edu", "vikram@college.edu"]:
            self.apply(email)
            self.approve_and_assign(email)
        self.apply("neha@college.edu")
        r = self.approve_and_assign("neha@college.edu")
        self.assertIn(b"is full", r.data)
        self.assertEqual(self.query("SELECT occupied FROM rooms")[0]["occupied"], 2)

    def test_vacate_releases_bed(self):
        self.apply("asha@college.edu")
        self.approve_and_assign("asha@college.edu")
        alloc = self.query("SELECT id FROM allocations")[0]["id"]
        r = self.client.post(f"/admin/allocations/{alloc}/vacate", follow_redirects=True)
        self.assertIn(b"Room vacated", r.data)
        self.assertEqual(self.query("SELECT occupied FROM rooms")[0]["occupied"], 0)

    # ------------------------------------------------ workflow 2: complaints
    def test_workflow_complaints(self):
        self.login("asha@college.edu", "pass1234")
        r = self.client.post("/student/complaints", data={"subject": "Fan broken",
                             "description": "Ceiling fan not working"}, follow_redirects=True)
        self.assertIn(b"Complaint submitted", r.data)
        r = self.client.post("/student/complaints", data={"subject": "", "description": ""},
                             follow_redirects=True)
        self.assertIn(b"Subject is required", r.data)

        self.logout()
        self.login_admin()
        self.assertIn(b"Fan broken", self.client.get("/admin/dashboard").data)
        cid = self.query("SELECT id FROM complaints")[0]["id"]
        self.client.post(f"/admin/complaints/{cid}/status", data={"status": "In Progress"})
        self.logout()
        self.login("asha@college.edu", "pass1234")
        self.assertIn(b"In Progress", self.client.get("/student/complaints").data)

        self.logout()
        self.login_admin()
        self.client.post(f"/admin/complaints/{cid}/status", data={"status": "Resolved"})
        self.client.post(f"/admin/complaints/{cid}/status", data={"status": "Bogus"})
        self.assertEqual(self.query("SELECT status FROM complaints")[0]["status"], "Resolved")

    # ------------------------------------------------------------ notices
    def test_notices_crud(self):
        self.login_admin()
        self.client.post("/admin/notices", data={"title": "Water cut", "description": "Sunday 10-12"})
        nid = self.query("SELECT id FROM notices")[0]["id"]
        self.client.post("/admin/notices", data={"notice_id": nid, "title": "Water cut (updated)",
                                                 "description": "Sunday 10-1"})
        self.logout()
        self.login("asha@college.edu", "pass1234")
        self.assertIn(b"Water cut (updated)", self.client.get("/student/notices").data)
        self.assertIn(b"Water cut (updated)", self.client.get("/student/dashboard").data)
        self.logout()
        self.login_admin()
        self.client.post(f"/admin/notices/{nid}/delete")
        self.assertEqual(self.query("SELECT COUNT(*) c FROM notices")[0]["c"], 0)

    # ------------------------------------------------- workflow 3: policies
    def test_workflow_policy_capacity_change(self):
        self.login_admin()
        r = self.client.post("/admin/policies", data={"max_room_capacity": "2", "priority_year": "1",
                             "applications_open": "Open", "min_year": "1"}, follow_redirects=True)
        self.assertIn(b"policies updated successfully", r.data)
        self.assertEqual(self.query("SELECT policy_value FROM policies WHERE policy_name="
                                    "'max_room_capacity'")[0]["policy_value"], "2")
        # The 3-bed room now only accepts 2 allocations.
        for email in ["asha@college.edu", "vikram@college.edu"]:
            self.apply(email)
            self.approve_and_assign(email)
        self.apply("neha@college.edu")
        r = self.approve_and_assign("neha@college.edu")
        self.assertIn(b"is full", r.data)
        self.assertEqual(self.query("SELECT occupied FROM rooms")[0]["occupied"], 2)
        # New rooms above the limit are refused.
        r = self.client.post("/admin/rooms", data={"room_number": "9", "block": "Z",
                             "room_type": "Triple", "capacity": "3"}, follow_redirects=True)
        self.assertIn(b"Capacity must be between 1 and 2", r.data)

    def test_policy_applications_closed_and_min_year(self):
        self.login_admin()
        self.client.post("/admin/policies", data={"max_room_capacity": "3", "priority_year": "0",
                         "applications_open": "Closed", "min_year": "1"})
        r = self.apply("asha@college.edu")
        self.assertIn(b"applications are currently closed", r.data)
        self.logout()
        self.login_admin()
        self.client.post("/admin/policies", data={"max_room_capacity": "3", "priority_year": "0",
                         "applications_open": "Open", "min_year": "2"})
        r = self.apply("asha@college.edu")  # year 1
        self.assertIn(b"year 2 and above", r.data)
        r = self.apply("vikram@college.edu")  # year 2
        self.assertIn(b"Application submitted successfully", r.data)

    def test_invalid_policy_rejected(self):
        self.login_admin()
        r = self.client.post("/admin/policies", data={"max_room_capacity": "0", "priority_year": "9",
                             "applications_open": "Maybe", "min_year": "1"}, follow_redirects=True)
        self.assertIn(b"between 1 and 10", r.data)
        conn = dbm.connect(self.db_path)
        self.assertEqual(dbm.get_policies(conn)["max_room_capacity"], 3)
        conn.close()

    # ------------------------------------------------------ admin CRUD
    def test_student_crud(self):
        self.login_admin()
        r = self.client.post("/admin/students", data={"name": "New Kid", "email": "kid@c.edu",
                             "student_code": "K100", "year": "1", "password": "secret1"},
                             follow_redirects=True)
        self.assertIn(b"added successfully", r.data)
        r = self.client.post("/admin/students", data={"name": "Dup", "email": "kid@c.edu",
                             "student_code": "K101", "year": "1", "password": "secret1"},
                             follow_redirects=True)
        self.assertIn(b"already exists", r.data)
        sid = self.query("SELECT id FROM students WHERE email='kid@c.edu'")[0]["id"]
        # editing keeps its own email/ID without tripping the duplicate check
        r = self.client.post("/admin/students", data={"student_id": sid, "name": "New Kid",
                             "email": "kid@c.edu", "student_code": "K100", "year": "2",
                             "password": ""}, follow_redirects=True)
        self.assertIn(b"updated successfully", r.data)
        self.assertEqual(self.query("SELECT year FROM students WHERE id=?", (sid,))[0]["year"], 2)
        self.logout()
        self.assertIn(b"Welcome, New Kid", self.login("kid@c.edu", "secret1").data)
        self.logout()
        self.login_admin()
        self.client.post(f"/admin/students/{sid}/delete")
        self.assertEqual(self.query("SELECT COUNT(*) c FROM students WHERE id=?", (sid,))[0]["c"], 0)

    def test_cannot_delete_allocated_student_or_occupied_room(self):
        self.apply("asha@college.edu")
        self.approve_and_assign("asha@college.edu")
        r = self.client.post("/admin/students/1/delete", follow_redirects=True)
        self.assertIn(b"Vacate the room before deleting", r.data)
        r = self.client.post(f"/admin/rooms/{self.room_id()}/delete", follow_redirects=True)
        self.assertIn(b"Only vacant rooms can be deleted", r.data)
        r = self.client.post("/admin/rooms", data={"room_id": self.room_id(), "room_number": "101",
                             "block": "A", "room_type": "Triple", "capacity": "0"},
                             follow_redirects=True)
        self.assertIn(b"Capacity must be between", r.data)

    def test_room_crud(self):
        self.login_admin()
        self.client.post("/admin/rooms", data={"room_number": "5", "block": "c",
                         "room_type": "Single", "capacity": "1"})
        rows = self.query("SELECT * FROM rooms WHERE block='C'")
        self.assertEqual(len(rows), 1)
        r = self.client.post("/admin/rooms", data={"room_number": "5", "block": "C",
                             "room_type": "Single", "capacity": "1"}, follow_redirects=True)
        self.assertIn(b"already exists", r.data)
        self.client.post(f"/admin/rooms/{rows[0]['id']}/delete")
        self.assertEqual(len(self.query("SELECT * FROM rooms WHERE block='C'")), 0)

    # ----------------------------------------------------- persistence
    def test_persistence_across_restart(self):
        self.apply("asha@college.edu")
        restarted = create_app({"TESTING": True, "DATABASE": self.db_path,
                                "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test"})
        c = restarted.test_client()
        c.post("/login", data={"role": "student", "identifier": "asha@college.edu",
                               "password": "pass1234"})
        self.assertIn(b"Pending", c.get("/student/dashboard").data)

    def test_demo_seed(self):
        self.assertTrue(dbm.seed_demo(self.db_path))
        self.assertFalse(dbm.seed_demo(self.db_path))
        self.login_admin()
        page = self.client.get("/admin/students").data
        self.assertIn(b"Demo", page)
        occ = self.query("SELECT occupied FROM rooms WHERE block='B' AND room_number='201'")
        self.assertEqual(occ[0]["occupied"], 1)


    # --------------------------------------------------------- student signup
    SIGNUP = {"name": "Meera Nair", "student_code": "2026cs042", "email": "Meera@College.edu",
              "year": "2", "password": "hostel2026", "confirm_password": "hostel2026"}

    def signup(self, **overrides):
        return self.client.post("/signup", data=dict(self.SIGNUP, **overrides),
                                follow_redirects=True)

    def test_signup_login_profile_and_dashboard_count(self):
        self.assertIn(b"Create an account", self.client.get("/login").data)
        self.assertEqual(self.client.get("/signup").status_code, 200)
        r = self.signup()
        self.assertIn(b"Registration successful", r.data)
        self.assertIn(b"Welcome to HostelHub", r.data)  # landed on the login page
        row = self.query("SELECT * FROM students WHERE email = 'meera@college.edu'")[0]
        self.assertEqual(row["student_code"], "2026CS042")
        self.assertEqual(row["year"], 2)
        self.assertNotEqual(row["password"], "hostel2026")
        self.assertTrue(row["password"].startswith(("scrypt:", "pbkdf2:")))
        self.assertIsNotNone(row["created_at"])

        r = self.login("meera@college.edu", "hostel2026")
        self.assertIn(b"Welcome, Meera Nair", r.data)
        profile = self.client.get("/student/profile").data
        for text in (b"Meera Nair", b"2026CS042", b"meera@college.edu", b"2nd Year"):
            self.assertIn(text, profile)

        self.logout()
        self.login_admin()
        html = self.client.get("/admin/dashboard").data.decode()
        self.assertRegex(html, r"Total Students</div>\s*<div class=\"stat-value\">4<")
        self.assertIn("2026CS042", self.client.get("/admin/students").data.decode())

    def test_signup_duplicates_rejected(self):
        self.signup()
        r = self.signup(student_code="OTHER1")
        self.assertIn(b"account with this email address already exists", r.data)
        r = self.signup(email="new@college.edu", student_code="2026CS042")
        self.assertIn(b"account with this Student ID already exists", r.data)
        r = self.signup(email="asha@college.edu", student_code="S001")  # existing student
        self.assertIn(b"email address already exists", r.data)
        self.assertIn(b"Student ID already exists", r.data)
        self.assertEqual(self.query("SELECT COUNT(*) c FROM students")[0]["c"], 4)

    def test_signup_validation(self):
        cases = [
            ({"name": ""}, b"Full name is required"),
            ({"name": "R2D2"}, b"Full name may contain only letters"),
            ({"student_code": ""}, b"Student ID is required"),
            ({"student_code": "a b"}, b"Student ID must be 3"),
            ({"email": "not-an-email"}, b"valid email address"),
            ({"year": "7"}, b"valid academic year"),
            ({"password": "short1", "confirm_password": "short1"}, b"at least 8 characters"),
            ({"password": "allletters", "confirm_password": "allletters"}, b"one letter and one number"),
            ({"confirm_password": "different1"}, b"do not match"),
        ]
        for override, message in cases:
            r = self.signup(**override)
            self.assertIn(message, r.data, override)
        self.assertEqual(self.query("SELECT COUNT(*) c FROM students")[0]["c"], 3)
        # form values are kept after an error (except passwords)
        r = self.signup(confirm_password="different1")
        self.assertIn(b'value="Meera Nair"', r.data)

    def test_signup_is_student_only_and_admin_unchanged(self):
        before = self.query("SELECT id, username, password FROM admins")
        self.signup(role="admin", username="hacker")
        self.assertEqual([tuple(r) for r in self.query("SELECT id, username, password FROM admins")],
                         [tuple(r) for r in before])
        for url in ("/admin/signup", "/signup/admin", "/admin/register"):
            self.assertIn(self.client.get(url).status_code, (302, 404))
        self.assertIn(b"Admin Dashboard", self.login_admin().data)
        # logged-in users are sent to their dashboard instead of the signup form
        self.assertEqual(self.client.get("/signup").status_code, 302)

    def test_set_admin_password_command(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["set-admin-password", "--password", "short"])
        self.assertNotEqual(result.exit_code, 0)
        result = runner.invoke(args=["set-admin-password", "--password", "N3wSecret!"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(b"Invalid credentials", self.login_admin().data)  # old password gone
        self.assertIn(b"Admin Dashboard", self.login("admin", "N3wSecret!", "admin").data)

    # ------------------------------------------------------- dashboard charts
    def test_dashboard_chart_data_matches_database(self):
        self.signup()
        self.apply("asha@college.edu")
        self.apply("vikram@college.edu")
        self.approve_and_assign("vikram@college.edu")
        self.apply("neha@college.edu")
        self.logout()
        self.login_admin()
        self.client.post(f"/admin/applications/{self.app_id('neha@college.edu')}/reject")
        self.logout()
        self.login("asha@college.edu", "pass1234")
        for subject in ("Fan", "Light", "Tap"):
            self.client.post("/student/complaints", data={"subject": subject, "description": "x"})
        self.logout()
        self.login_admin()
        cids = [r["id"] for r in self.query("SELECT id FROM complaints ORDER BY id")]
        self.client.post(f"/admin/complaints/{cids[0]}/status", data={"status": "In Progress"})
        self.client.post(f"/admin/complaints/{cids[1]}/status", data={"status": "Resolved"})

        with self.app.test_request_context():
            from app import dashboard_charts
            conn = dbm.connect(self.db_path)
            rooms = conn.execute("SELECT * FROM rooms").fetchall()
            charts = dashboard_charts(conn, rooms, {"students": 4, "available_beds": 2})
            conn.close()
        self.assertEqual(charts["registrations"]["total"], 4)
        self.assertEqual(charts["registrations"]["today"], 4)
        self.assertEqual(charts["registrations"]["last7"], 4)
        self.assertEqual(sum(charts["registrations"]["values"]), 4)
        self.assertEqual(len(charts["registrations"]["values"]), 30)
        self.assertEqual(charts["occupancy"], {"occupied": 1, "available": 2})
        self.assertEqual(dict(zip(charts["applications"]["labels"], charts["applications"]["values"])),
                         {"Pending": 1, "Approved": 0, "Allocated": 1, "Rejected": 1})
        self.assertEqual(dict(zip(charts["complaints"]["labels"], charts["complaints"]["values"])),
                         {"Submitted": 1, "In Progress": 1, "Resolved": 1})

        html = self.client.get("/admin/dashboard").data.decode()
        for marker in ('id="chart-registrations"', 'id="chart-occupancy"', 'id="chart-applications"',
                       'id="chart-complaints"', 'id="dashboard-data"', "chart.umd.min.js",
                       "Total Rooms", "Available Beds", "Pending Applications", "Open Complaints"):
            self.assertIn(marker, html)
        self.assertIn('"occupancy": {"available": 2, "occupied": 1}', html)

    def test_migration_keeps_existing_students(self):
        fd, old_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = dbm.connect(old_path)
        conn.executescript("""
            CREATE TABLE students (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE, password TEXT NOT NULL,
                year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 4));
        """)
        conn.execute("INSERT INTO students (name, email, password, year) VALUES ('Old One', "
                     "'old@c.edu', ?, 3)", (generate_password_hash("pass1234"),))
        conn.commit()
        conn.close()
        try:
            dbm.init_db(old_path)
            dbm.init_db(old_path)  # idempotent
            conn = dbm.connect(old_path)
            row = conn.execute("SELECT * FROM students").fetchone()
            conn.close()
            self.assertEqual((row["name"], row["student_code"]), ("Old One", "HH0001"))
            self.assertIsNotNone(row["created_at"])
        finally:
            os.remove(old_path)


if __name__ == "__main__":
    unittest.main()

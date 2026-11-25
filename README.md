# HostelHub – Online Hostel Management System

A web portal for running a college hostel. Students apply for rooms, check their allocation, raise complaints and read notices. Administrators manage students, rooms, applications, complaints, notices and the hostel allocation policies.

**Stack:** HTML5, CSS3, JavaScript, Bootstrap 5 · Python, Flask · SQLite (`sqlite3`) · Flask sessions, Werkzeug password hashing, Flask-WTF CSRF protection.

---

## 1. Setup and running

Requires Python 3.10 or newer.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialise the database (tables, default policies, default admin)
flask --app app init-db
#    ...or include demo records for a presentation:
flask --app app init-db --demo

# 4. Run the application
python app.py
```

Open **http://127.0.0.1:5000** in the browser.

`hostel.db` is also created automatically the first time the app starts. To start again from nothing, stop the app, delete `hostel.db` and run step 3 again.

### Login accounts

| Role | Login ID | Password |
|------|----------|----------|
| Administrator (default, local only) | `admin` | `admin123` |
| Demo students (only with `--demo`) | `aarav@demo.hostelhub.local`, `priya@…`, `rohan@…`, `sneha@…` | `student123` |

Change the default admin password before any real use.

Students can also create their own account. Click **Create an account** on the login page, or go to `/signup`. Signup is for students only: there is no admin registration, and administrators log in exactly as before.

**Demo data is kept separate from real records.** Demo students use the `@demo.hostelhub.local` email domain and have a **Demo** badge in the admin tables. Demo notices start with `[Demo]`. Demo data is only added when you run `--demo`.

### Configuration

- `HOSTELHUB_SECRET_KEY`: the session/CSRF secret key. If this is not set, a random key is generated and saved in `instance/secret_key`. That file is not committed to Git.
- `FLASK_DEBUG=1`: turns on debug mode for development.

---

## 2. Features

### Student portal
| Page | What it does |
|------|--------------|
| Sign up | Self-registration with full name, Student ID / enrollment number, email, academic year, password and confirm password. Every field is validated, duplicate emails and Student IDs are rejected, and the password is hashed. On success the student is sent to the login page |
| Dashboard | Welcome message, room allocation, application status, open complaints, recent notices, quick links |
| Apply for Room | Application form (room type, optional block) with the current rules shown alongside. A student cannot have two active applications. Applications are blocked while they are closed or if the student is below the minimum year |
| My Allocation | Block, room number, type, allocation date and status, plus history. Shows a message if no room has been allocated |
| Complaints | Submit a complaint (subject and description) and follow its status: Submitted → In Progress → Resolved |
| Notices | Announcements from the administration |
| Profile | Name, Student ID, email, academic year, room |

### Admin portal
| Page | What it does |
|------|--------------|
| Dashboard | Five stat cards (total students, total rooms, available beds, pending applications, open complaints) and four charts (Chart.js): **student registrations** per day for the last 30 days, with totals for today, 7 days and 30 days; **room occupancy** doughnut (occupied vs available beds); **applications by status**; **complaints by status**. All figures are queried from the database each time the page loads |
| Students | Add, edit, search and delete students. Vacate a student's room. A student who still has a room cannot be deleted |
| Rooms | Add and edit rooms, set capacity, see occupied and available beds and who lives in each room. Only vacant rooms can be deleted |
| Applications | Filter by status, approve or reject, and assign a room to an approved application. Only rooms with free beds are listed, and ★ marks rooms that match the student's preference |
| Complaints | Open a complaint's details and change its status |
| Notices | Create, edit and delete notices |
| Policies & Rules | Set the maximum room capacity, priority year, whether applications are open, and the minimum eligible year |

### Policy management (dynamic rules)
Policies are stored in the `policies` table and read from the database on every request. Nothing is hard-coded and no restart is needed.

| Policy | Effect |
|--------|--------|
| `max_room_capacity` | No room can be created or edited above this capacity. For allocations, a room's usable capacity is `min(room capacity, policy)`. *Example:* if the policy changes from 3 to 2, a 3-bed room accepts only 2 allocations from then on. Students already living there are not affected |
| `priority_year` | Applications from this year are listed first and marked **Priority** |
| `applications_open` | When this is `Closed`, students cannot submit applications |
| `min_year` | Students below this academic year cannot apply |

---

## 3. Database design (`hostel.db`)

The database has eight tables: `students`, `admins`, `rooms`, `applications`, `allocations`, `complaints`, `notices` and `policies`. The full schema is in [database/db.py](database/db.py).

`students` also stores `student_code` (Student ID / enrollment number, unique regardless of upper or lower case) and `created_at` (registration time, used by the registrations chart). Databases created before signup existed are upgraded automatically when the app starts, and no records are deleted. Existing students receive an `HH0001`-style ID, and their registration time is set to the time of the upgrade.

How the data is protected:
- Foreign keys are enforced (`PRAGMA foreign_keys = ON`), with `ON DELETE CASCADE` for a student's records.
- `CHECK` constraints limit status values, and `0 ≤ occupied ≤ capacity` on rooms.
- A partial unique index allows each student **only one active allocation**. A second index allows **only one Pending or Approved application**.
- Room allocation and vacating each run in a single `BEGIN IMMEDIATE` transaction. The occupancy update is guarded (`UPDATE … WHERE occupied < limit`), so a room can never be over-filled.
- Every query is parameterised.
- `applications.preferences` stores the academic year, room type and block as JSON.

Application lifecycle: `Pending → Approved → Allocated`, and either `Pending` or `Approved` can become `Rejected`.
Allocation lifecycle: `Active → Vacated`. Vacating frees the bed, and the student can then apply again.

---

## 4. Project structure

```
app.py                 Flask app: routes (auth / student / admin blueprints), validation, CLI
database/db.py         Schema, connections, policies, allocation transactions, demo seed
templates/
  base.html            Sidebar dashboard layout (responsive offcanvas on mobile)
  _macros.html         Reusable badges, stat cards, CSRF field, empty states
  _flashes.html        Flash messages
  login.html, signup.html, error.html
  student/*.html       6 student pages
  admin/*.html         7 admin pages
static/css/style.css   Theme (navy accent, cards, tables)
static/js/script.js    Confirm dialogs, form validation, small UX helpers
static/js/dashboard.js Admin dashboard charts (Chart.js)
tests/test_app.py      Automated tests (31)
requirements.txt
```

---

## 5. Testing

```bash
python -m unittest discover tests -v
```

The 31 automated tests use a temporary database, so `hostel.db` is never touched. They cover:

- Student and admin login, wrong credentials, and redirecting users who are not logged in
- Role-based access control (a student gets 403 on admin pages, and an admin gets 403 on student pages)
- CSRF: a POST without a token is rejected. Passwords are stored as hashes
- **Workflow 1:** apply → duplicate application blocked → approve → assign → occupancy updated → student sees the room
- Reject, assigning an application that has not been approved, preventing a duplicate allocation, never exceeding capacity, vacating a room
- **Workflow 2:** student complaint → admin sees it → status updated → student sees the new status
- **Workflow 3:** capacity policy changed from 3 to 2 → the third allocation is refused and new rooms above 2 are refused. Closing applications and the minimum year both take effect. Invalid policy values are rejected
- Add, edit and delete for students, rooms and notices. Students who still have a room and occupied rooms are protected from deletion
- Data persists across an app restart. The demo data loader can be run more than once safely
- Student signup: register, then log in; the profile and the admin Total Students count both update; duplicate emails and Student IDs are rejected; every validation rule is checked; the admin account is not changed and there is no admin signup
- Dashboard chart data matches the database for registrations, occupancy, applications and complaints
- Upgrading a database from before signup keeps every existing student

---

## 6. Agile sprint plan

| Sprint | Scope | Delivered in |
|--------|-------|--------------|
| 1 | Project setup, database, login and authentication | `database/db.py`, `auth` blueprint, `login.html`, `base.html` |
| 2 | Student dashboard, room application, viewing the allocation | `student/dashboard`, `apply_room`, `allocation`, `profile` |
| 3 | Admin dashboard, students, rooms, approval and allocation | `admin/dashboard`, `students`, `rooms`, `applications`, `allocate_room()` |
| 4 | Complaints, notices, policies | `complaints`, `notices`, `policies` (student and admin) |
| 5 | Integration, testing, bug fixing, UI polish | `tests/test_app.py`, `style.css`, error pages, CSRF handling |

---

## 7. Deployment (PythonAnywhere)

PythonAnywhere is recommended because SQLite needs a disk that persists. Its free tier keeps `hostel.db` between restarts. Hosts with temporary disks, such as the free tiers of Render or Railway, wipe the database on every redeploy.

1. Create a free account at https://www.pythonanywhere.com. Open **Consoles → Bash** and run:
   ```bash
   git clone https://github.com/sohampilwankar/HostleHub.git
   cd HostleHub
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   flask --app app init-db             # add --demo for sample data
   flask --app app set-admin-password  # REQUIRED: replace the public default password
   ```
2. **Web** tab → **Add a new web app** → **Manual configuration** → **Python 3.13**.
3. On the Web tab, set:
   - **Source code:** `/home/<username>/HostleHub`
   - **Virtualenv:** `/home/<username>/HostleHub/.venv`
   - **Static files:** URL `/static/` → directory `/home/<username>/HostleHub/static`
   - **Force HTTPS:** enabled
4. Open the **WSGI configuration file** link, replace its contents with the following, and save:
   ```python
   import os, sys
   path = "/home/<username>/HostleHub"
   if path not in sys.path:
       sys.path.insert(0, path)
   os.environ["HOSTELHUB_HTTPS"] = "1"
   from wsgi import application  # noqa
   ```
5. Click **Reload**. The site is at `https://<username>.pythonanywhere.com`.

**Updating a deployed site** after you push changes to GitHub:
```bash
cd ~/HostleHub && git pull && source .venv/bin/activate && pip install -r requirements.txt
```
Then click **Reload** on the Web tab. `git pull` never touches `hostel.db`, so no data is lost. Database changes are applied automatically when the app reloads.

**Backups:** download `hostel.db` from the **Files** tab from time to time.

Free-tier notes: you must click "Run until 3 months from today" on the Web tab every 3 months, or the app is disabled. The CDN libraries used (Bootstrap, Chart.js, Google Fonts) are loaded by the visitor's browser, so the free tier's outbound internet restrictions don't affect them.

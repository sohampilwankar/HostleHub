"""HostelHub - Online Hostel Management System (Flask + SQLite)."""
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

import click
from flask import (Blueprint, Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from database import db as dbm
from database.db import (ACADEMIC_YEARS, COMPLAINT_STATUSES, ROOM_TYPES, RuleError,
                         get_db)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STUDENT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{2,19}$")
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ .'-]+[^\W\d_]+)*\.?$")  # letters + . ' - separators
SIGNUP_MIN_PASSWORD = 8
csrf = CSRFProtect()

auth_bp = Blueprint("auth", __name__)
student_bp = Blueprint("student", __name__, url_prefix="/student")
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ======================================================================
# Helpers
# ======================================================================

def home_for(role):
    return url_for("admin.dashboard" if role == "admin" else "student.dashboard")


def back(default_endpoint, **values):
    return redirect(request.referrer or url_for(default_endpoint, **values))


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def require_role(role):
    """Blueprint guard: user must be logged in with the given role."""
    def guard():
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        if g.role != role:
            abort(403)
    return guard


def active_allocation(student_id):
    return get_db().execute(
        """SELECT al.*, r.room_number, r.block, r.room_type
           FROM allocations al JOIN rooms r ON r.id = al.room_id
           WHERE al.student_id = ? AND al.status = 'Active'""",
        (student_id,),
    ).fetchone()


def active_application(student_id):
    return get_db().execute(
        "SELECT * FROM applications WHERE student_id = ? AND status IN ('Pending', 'Approved')",
        (student_id,),
    ).fetchone()


def latest_application(student_id):
    return get_db().execute(
        "SELECT * FROM applications WHERE student_id = ? ORDER BY id DESC LIMIT 1",
        (student_id,),
    ).fetchone()


def room_blocks():
    return [r["block"] for r in get_db().execute(
        "SELECT DISTINCT block FROM rooms ORDER BY block")]


def validate_text(value, label, max_len, errors, required=True):
    value = (value or "").strip()
    if required and not value:
        errors.append(f"{label} is required.")
    elif len(value) > max_len:
        errors.append(f"{label} must be at most {max_len} characters.")
    return value


def flash_errors(errors):
    for e in errors:
        flash(e, "danger")


def validate_student_fields(form, errors, exclude_id=None):
    """Validate the common student fields (signup and admin form).

    Checks format and uniqueness of email / Student ID. Returns cleaned values.
    """
    name = " ".join(validate_text(form.get("name"), "Full name", 100, errors).split())
    code = validate_text(form.get("student_code"), "Student ID", 20, errors).upper()
    email = validate_text(form.get("email"), "Email", 120, errors).lower()
    year = to_int(form.get("year"))

    if name and (len(name) < 2 or not NAME_RE.match(name)):
        errors.append("Full name may contain only letters, spaces, dots, hyphens and apostrophes.")
    if code and not STUDENT_CODE_RE.match(code):
        errors.append("Student ID must be 3–20 characters: letters, numbers, '/' or '-'.")
    if email and not EMAIL_RE.match(email):
        errors.append("Please enter a valid email address.")
    if year not in ACADEMIC_YEARS:
        errors.append("Please select a valid academic year.")

    db = get_db()
    other = exclude_id if exclude_id is not None else -1
    if email and db.execute("SELECT 1 FROM students WHERE email = ? AND id != ?",
                            (email, other)).fetchone():
        errors.append("An account with this email address already exists.")
    if code and db.execute("SELECT 1 FROM students WHERE student_code = ? COLLATE NOCASE "
                           "AND id != ?", (code, other)).fetchone():
        errors.append("An account with this Student ID already exists.")
    return {"name": name, "student_code": code, "email": email, "year": year}


# ======================================================================
# Authentication
# ======================================================================

@auth_bp.route("/", methods=["GET", "POST"])
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_for(g.role))

    role = request.form.get("role", "student")
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        if role not in ("student", "admin"):
            role = "student"
        if not identifier or not password:
            flash("Please enter your login ID and password.", "danger")
        else:
            db = get_db()
            if role == "admin":
                user = db.execute("SELECT * FROM admins WHERE username = ?",
                                  (identifier,)).fetchone()
            else:
                user = db.execute("SELECT * FROM students WHERE email = ?",
                                  (identifier,)).fetchone()
            if user and check_password_hash(user["password"], password):
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["role"] = role
                return redirect(home_for(role))
            flash("Invalid credentials. Please check the role, login ID and password.", "danger")
    return render_template("login.html", role=role)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Self-registration for students only (there is no admin signup)."""
    if g.user is not None:
        return redirect(home_for(g.role))

    if request.method == "POST":
        errors = []
        data = validate_student_fields(request.form, errors)
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < SIGNUP_MIN_PASSWORD:
            errors.append(f"Password must be at least {SIGNUP_MIN_PASSWORD} characters.")
        elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
            errors.append("Password must contain at least one letter and one number.")
        elif password != confirm:
            errors.append("Password and Confirm Password do not match.")

        if not errors:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO students (name, email, password, year, student_code, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (data["name"], data["email"], generate_password_hash(password),
                     data["year"], data["student_code"], dbm.now()),
                )
                db.commit()
                flash("Registration successful! Please log in with your email and password.",
                      "success")
                return redirect(url_for("auth.login", email=data["email"]))
            except sqlite3.IntegrityError:
                db.rollback()
                errors.append("An account with this email address or Student ID already exists.")
        flash_errors(errors)

    return render_template("signup.html", years=ACADEMIC_YEARS,
                           min_password=SIGNUP_MIN_PASSWORD)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


# ======================================================================
# Student module
# ======================================================================

student_bp.before_request(require_role("student"))


@student_bp.route("/dashboard")
def dashboard():
    db = get_db()
    sid = g.user["id"]
    notices = db.execute("SELECT * FROM notices ORDER BY created_at DESC, id DESC LIMIT 3").fetchall()
    open_complaints = db.execute(
        "SELECT COUNT(*) FROM complaints WHERE student_id = ? AND status != 'Resolved'", (sid,)
    ).fetchone()[0]
    return render_template(
        "student/dashboard.html",
        allocation=active_allocation(sid),
        application=latest_application(sid),
        notices=notices,
        open_complaints=open_complaints,
    )


@student_bp.route("/apply", methods=["GET", "POST"])
def apply_room():
    db = get_db()
    sid = g.user["id"]
    policies = dbm.get_policies(db)
    allocation = active_allocation(sid)
    pending = active_application(sid)
    blocks = room_blocks()

    blocker = None
    if allocation:
        blocker = "You already have a room allocated, so you cannot apply again."
    elif pending:
        blocker = f"You already have an active application (status: {pending['status']})."
    elif not policies["applications_open"]:
        blocker = "Room applications are currently closed by the hostel administration."
    elif g.user["year"] < policies["min_year"]:
        blocker = (f"Applications are currently open only to students in year "
                   f"{policies['min_year']} and above.")

    if request.method == "POST":
        if blocker:
            flash(blocker, "danger")
            return redirect(url_for("student.apply_room"))
        room_type = request.form.get("room_type", "")
        block = request.form.get("block", "").strip()
        errors = []
        if room_type not in ROOM_TYPES:
            errors.append("Please select a valid room type.")
        if block and block not in blocks:
            errors.append("Please select a valid hostel block.")
        if errors:
            flash_errors(errors)
        else:
            prefs = json.dumps({"academic_year": g.user["year"],
                                "room_type": room_type, "block": block})
            try:
                db.execute(
                    "INSERT INTO applications (student_id, preferences, status, application_date) "
                    "VALUES (?, ?, 'Pending', ?)",
                    (sid, prefs, dbm.now()),
                )
                db.commit()
                flash("Application submitted successfully. Status: Pending.", "success")
                return redirect(url_for("student.dashboard"))
            except sqlite3.IntegrityError:
                db.rollback()
                flash("You already have an active application.", "danger")
                return redirect(url_for("student.apply_room"))

    history = db.execute(
        "SELECT * FROM applications WHERE student_id = ? ORDER BY id DESC", (sid,)
    ).fetchall()
    return render_template("student/apply_room.html", blocker=blocker, policies=policies,
                           blocks=blocks, room_types=ROOM_TYPES, history=history)


@student_bp.route("/allocation")
def allocation():
    history = get_db().execute(
        """SELECT al.*, r.room_number, r.block, r.room_type
           FROM allocations al JOIN rooms r ON r.id = al.room_id
           WHERE al.student_id = ? ORDER BY al.id DESC""",
        (g.user["id"],),
    ).fetchall()
    return render_template("student/allocation.html",
                           allocation=active_allocation(g.user["id"]),
                           history=history, application=latest_application(g.user["id"]))


@student_bp.route("/complaints", methods=["GET", "POST"])
def complaints():
    db = get_db()
    if request.method == "POST":
        errors = []
        subject = validate_text(request.form.get("subject"), "Subject", 120, errors)
        description = validate_text(request.form.get("description"), "Description", 1000, errors)
        if errors:
            flash_errors(errors)
        else:
            db.execute(
                "INSERT INTO complaints (student_id, subject, description, status, created_at) "
                "VALUES (?, ?, ?, 'Submitted', ?)",
                (g.user["id"], subject, description, dbm.now()),
            )
            db.commit()
            flash("Complaint submitted successfully.", "success")
            return redirect(url_for("student.complaints"))
    rows = db.execute(
        "SELECT * FROM complaints WHERE student_id = ? ORDER BY id DESC", (g.user["id"],)
    ).fetchall()
    return render_template("student/complaints.html", complaints=rows)


@student_bp.route("/notices")
def notices():
    rows = get_db().execute("SELECT * FROM notices ORDER BY created_at DESC, id DESC").fetchall()
    return render_template("student/notices.html", notices=rows)


@student_bp.route("/profile")
def profile():
    return render_template("student/profile.html", allocation=active_allocation(g.user["id"]))


# ======================================================================
# Admin module
# ======================================================================

admin_bp.before_request(require_role("admin"))


@admin_bp.route("/dashboard")
def dashboard():
    db = get_db()
    policies = dbm.get_policies(db)
    rooms = db.execute("SELECT * FROM rooms").fetchall()
    stats = {
        "students": db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "rooms": len(rooms),
        "available_beds": sum(dbm.available_beds(r, policies) for r in rooms),
        "pending": db.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'Pending'").fetchone()[0],
        "open_complaints": db.execute(
            "SELECT COUNT(*) FROM complaints WHERE status != 'Resolved'").fetchone()[0],
    }
    recent_apps = db.execute(
        """SELECT a.*, s.name, s.year FROM applications a JOIN students s ON s.id = a.student_id
           WHERE a.status = 'Pending' ORDER BY a.id DESC LIMIT 5""").fetchall()
    recent_complaints = db.execute(
        """SELECT c.*, s.name FROM complaints c JOIN students s ON s.id = c.student_id
           WHERE c.status != 'Resolved' ORDER BY c.id DESC LIMIT 5""").fetchall()
    return render_template("admin/dashboard.html", stats=stats, policies=policies,
                           recent_apps=recent_apps, recent_complaints=recent_complaints,
                           charts=dashboard_charts(db, rooms, stats))


REGISTRATION_DAYS = 30


def dashboard_charts(db, rooms, stats):
    """Build chart datasets from live database records (no hard-coded numbers)."""
    today = datetime.now().date()
    start = today - timedelta(days=REGISTRATION_DAYS - 1)
    per_day = dict(db.execute(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM students "
        "WHERE created_at >= ? GROUP BY day", (start.isoformat(),)).fetchall())
    days = [start + timedelta(days=i) for i in range(REGISTRATION_DAYS)]

    def registered_since(n_days):
        since = (today - timedelta(days=n_days - 1)).isoformat()
        return db.execute("SELECT COUNT(*) FROM students WHERE created_at >= ?",
                          (since,)).fetchone()[0]

    def counts(table, statuses):
        found = dict(db.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status"))
        return [found.get(s, 0) for s in statuses]

    app_statuses = ["Pending", "Approved", "Allocated", "Rejected"]
    occupied = sum(r["occupied"] for r in rooms)
    return {
        "registrations": {
            "labels": [d.strftime("%d %b") for d in days],
            "values": [per_day.get(d.isoformat(), 0) for d in days],
            "total": stats["students"],
            "today": per_day.get(today.isoformat(), 0),
            "last7": registered_since(7),
            "last30": registered_since(REGISTRATION_DAYS),
        },
        "occupancy": {"occupied": occupied, "available": stats["available_beds"]},
        "applications": {"labels": app_statuses,
                         "values": counts("applications", app_statuses)},
        "complaints": {"labels": COMPLAINT_STATUSES,
                       "values": counts("complaints", COMPLAINT_STATUSES)},
    }


# ---------------------------------------------------------------- students

@admin_bp.route("/students", methods=["GET", "POST"])
def students():
    db = get_db()
    if request.method == "POST":
        student_id = to_int(request.form.get("student_id"))
        errors = []
        data = validate_student_fields(request.form, errors, exclude_id=student_id)
        name, email, year, code = data["name"], data["email"], data["year"], data["student_code"]
        password = request.form.get("password", "")
        if student_id is None and len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if student_id is not None and password and len(password) < 6:
            errors.append("New password must be at least 6 characters.")

        if errors:
            flash_errors(errors)
            return redirect(url_for("admin.students", edit=student_id) if student_id
                            else url_for("admin.students"))
        try:
            if student_id is None:
                db.execute(
                    "INSERT INTO students (name, email, password, year, student_code, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, email, generate_password_hash(password), year, code, dbm.now()),
                )
                flash(f"Student {name} added successfully.", "success")
            else:
                db.execute(
                    "UPDATE students SET name = ?, email = ?, year = ?, student_code = ? WHERE id = ?",
                    (name, email, year, code, student_id),
                )
                if password:
                    db.execute("UPDATE students SET password = ? WHERE id = ?",
                               (generate_password_hash(password), student_id))
                flash(f"Student {name} updated successfully.", "success")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("A student with this email or Student ID already exists.", "danger")
        return redirect(url_for("admin.students"))

    q = request.args.get("q", "").strip()
    sql = """SELECT s.*, al.id AS allocation_id, r.block, r.room_number
             FROM students s
             LEFT JOIN allocations al ON al.student_id = s.id AND al.status = 'Active'
             LEFT JOIN rooms r ON r.id = al.room_id"""
    params = ()
    if q:
        sql += " WHERE s.name LIKE ? OR s.email LIKE ? OR s.student_code LIKE ?"
        params = (f"%{q}%", f"%{q}%", f"%{q}%")
    rows = db.execute(sql + " ORDER BY s.name", params).fetchall()
    edit = None
    if request.args.get("edit"):
        edit = db.execute("SELECT * FROM students WHERE id = ?",
                          (to_int(request.args["edit"], 0),)).fetchone()
    return render_template("admin/students.html", students=rows, edit=edit, q=q,
                           years=ACADEMIC_YEARS)


@admin_bp.route("/students/<int:student_id>/delete", methods=["POST"])
def delete_student(student_id):
    db = get_db()
    if active_allocation(student_id):
        flash("This student has an active room allocation. Vacate the room before deleting.",
              "danger")
    else:
        db.execute("DELETE FROM students WHERE id = ?", (student_id,))
        db.commit()
        flash("Student record deleted.", "success")
    return redirect(url_for("admin.students"))


@admin_bp.route("/allocations/<int:allocation_id>/vacate", methods=["POST"])
def vacate(allocation_id):
    try:
        dbm.vacate_allocation(get_db(), allocation_id)
        flash("Room vacated and bed released.", "success")
    except RuleError as e:
        flash(str(e), "danger")
    return back("admin.students")


# ------------------------------------------------------------------- rooms

@admin_bp.route("/rooms", methods=["GET", "POST"])
def rooms():
    db = get_db()
    policies = dbm.get_policies(db)
    if request.method == "POST":
        room_id = to_int(request.form.get("room_id"))
        errors = []
        number = validate_text(request.form.get("room_number"), "Room number", 10, errors)
        block = validate_text(request.form.get("block"), "Hostel block", 10, errors).upper()
        room_type = request.form.get("room_type", "")
        capacity = to_int(request.form.get("capacity"))
        if room_type not in ROOM_TYPES:
            errors.append("Please select a valid room type.")
        if capacity is None or not 1 <= capacity <= policies["max_room_capacity"]:
            errors.append(f"Capacity must be between 1 and {policies['max_room_capacity']} "
                          f"(current maximum room capacity policy).")
        if room_id is not None and capacity is not None:
            existing = db.execute("SELECT occupied FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if existing is None:
                errors.append("Room not found.")
            elif capacity < existing["occupied"]:
                errors.append(f"Capacity cannot be lower than the {existing['occupied']} "
                              f"bed(s) currently occupied.")
        if errors:
            flash_errors(errors)
            return redirect(url_for("admin.rooms", edit=room_id) if room_id
                            else url_for("admin.rooms"))
        try:
            if room_id is None:
                db.execute(
                    "INSERT INTO rooms (room_number, block, room_type, capacity) VALUES (?, ?, ?, ?)",
                    (number, block, room_type, capacity),
                )
                flash(f"Room {block}-{number} added.", "success")
            else:
                db.execute(
                    "UPDATE rooms SET room_number = ?, block = ?, room_type = ?, capacity = ? "
                    "WHERE id = ?",
                    (number, block, room_type, capacity, room_id),
                )
                flash(f"Room {block}-{number} updated.", "success")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash(f"Room {block}-{number} already exists.", "danger")
        return redirect(url_for("admin.rooms"))

    rows = db.execute(
        """SELECT r.*, GROUP_CONCAT(s.name, ', ') AS occupants
           FROM rooms r
           LEFT JOIN allocations al ON al.room_id = r.id AND al.status = 'Active'
           LEFT JOIN students s ON s.id = al.student_id
           GROUP BY r.id ORDER BY r.block, r.room_number""").fetchall()
    room_list = [dict(r, effective=dbm.effective_capacity(r, policies),
                      available=dbm.available_beds(r, policies)) for r in rows]
    edit = None
    if request.args.get("edit"):
        edit = db.execute("SELECT * FROM rooms WHERE id = ?",
                          (to_int(request.args["edit"], 0),)).fetchone()
    totals = {
        "capacity": sum(r["effective"] for r in room_list),
        "occupied": sum(r["occupied"] for r in room_list),
        "available": sum(r["available"] for r in room_list),
    }
    return render_template("admin/rooms.html", rooms=room_list, edit=edit, totals=totals,
                           room_types=ROOM_TYPES, policies=policies)


@admin_bp.route("/rooms/<int:room_id>/delete", methods=["POST"])
def delete_room(room_id):
    db = get_db()
    room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        flash("Room not found.", "danger")
    elif room["occupied"] > 0:
        flash("Only vacant rooms can be deleted.", "danger")
    else:
        db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        db.commit()
        flash(f"Room {room['block']}-{room['room_number']} deleted.", "success")
    return redirect(url_for("admin.rooms"))


# ------------------------------------------------------------ applications

APPLICATION_FILTERS = ["All", "Pending", "Approved", "Allocated", "Rejected"]


@admin_bp.route("/applications")
def applications():
    db = get_db()
    policies = dbm.get_policies(db)
    status = request.args.get("status", "All")
    if status not in APPLICATION_FILTERS:
        status = "All"
    sql = """SELECT a.*, s.name, s.email, s.year FROM applications a
             JOIN students s ON s.id = a.student_id"""
    params = [policies["priority_year"]]
    if status != "All":
        sql += " WHERE a.status = ?"
        params.insert(0, status)
    sql += """ ORDER BY CASE a.status WHEN 'Pending' THEN 0 WHEN 'Approved' THEN 1 ELSE 2 END,
                        CASE WHEN s.year = ? THEN 0 ELSE 1 END, a.id"""
    rows = db.execute(sql, params).fetchall()

    rooms = db.execute("SELECT * FROM rooms ORDER BY block, room_number").fetchall()
    free_rooms = [dict(r, available=dbm.available_beds(r, policies)) for r in rooms
                  if dbm.available_beds(r, policies) > 0]
    counts = {s: db.execute("SELECT COUNT(*) FROM applications WHERE status = ?",
                            (s,)).fetchone()[0] for s in APPLICATION_FILTERS[1:]}
    counts["All"] = sum(counts.values())
    return render_template("admin/applications.html", applications=rows, status=status,
                           filters=APPLICATION_FILTERS, counts=counts,
                           free_rooms=free_rooms, policies=policies)


@admin_bp.route("/applications/<int:app_id>/<action>", methods=["POST"])
def application_action(app_id, action):
    db = get_db()
    app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if app_row is None:
        abort(404)

    if action == "approve":
        if app_row["status"] != "Pending":
            flash("Only pending applications can be approved.", "danger")
        else:
            db.execute("UPDATE applications SET status = 'Approved' WHERE id = ?", (app_id,))
            db.commit()
            flash("Application approved. You can now assign a room.", "success")
    elif action == "reject":
        if app_row["status"] not in ("Pending", "Approved"):
            flash("This application can no longer be rejected.", "danger")
        else:
            db.execute("UPDATE applications SET status = 'Rejected' WHERE id = ?", (app_id,))
            db.commit()
            flash("Application rejected.", "success")
    elif action == "assign":
        room_id = to_int(request.form.get("room_id"))
        if room_id is None:
            flash("Please select a room to assign.", "danger")
        else:
            try:
                room = dbm.allocate_room(db, app_id, room_id)
                flash(f"Room {room['block']}-{room['room_number']} allocated successfully.",
                      "success")
            except RuleError as e:
                flash(str(e), "danger")
    else:
        abort(404)
    return back("admin.applications")


# -------------------------------------------------------------- complaints

@admin_bp.route("/complaints")
def complaints():
    status = request.args.get("status", "All")
    sql = """SELECT c.*, s.name, s.email FROM complaints c
             JOIN students s ON s.id = c.student_id"""
    params = ()
    if status in COMPLAINT_STATUSES:
        sql += " WHERE c.status = ?"
        params = (status,)
    else:
        status = "All"
    sql += """ ORDER BY CASE c.status WHEN 'Submitted' THEN 0 WHEN 'In Progress' THEN 1
               ELSE 2 END, c.id DESC"""
    rows = get_db().execute(sql, params).fetchall()
    return render_template("admin/complaints.html", complaints=rows, status=status,
                           statuses=COMPLAINT_STATUSES)


@admin_bp.route("/complaints/<int:complaint_id>/status", methods=["POST"])
def complaint_status(complaint_id):
    db = get_db()
    new_status = request.form.get("status")
    if new_status not in COMPLAINT_STATUSES:
        flash("Invalid complaint status.", "danger")
    else:
        cur = db.execute("UPDATE complaints SET status = ? WHERE id = ?",
                         (new_status, complaint_id))
        db.commit()
        if cur.rowcount:
            flash(f"Complaint marked as {new_status}.", "success")
        else:
            flash("Complaint not found.", "danger")
    return back("admin.complaints")


# ----------------------------------------------------------------- notices

@admin_bp.route("/notices", methods=["GET", "POST"])
def notices():
    db = get_db()
    if request.method == "POST":
        notice_id = to_int(request.form.get("notice_id"))
        errors = []
        title = validate_text(request.form.get("title"), "Title", 150, errors)
        description = validate_text(request.form.get("description"), "Description", 2000, errors)
        if errors:
            flash_errors(errors)
            return redirect(url_for("admin.notices", edit=notice_id) if notice_id
                            else url_for("admin.notices"))
        if notice_id is None:
            db.execute("INSERT INTO notices (title, description, created_at) VALUES (?, ?, ?)",
                       (title, description, dbm.now()))
            flash("Notice published.", "success")
        else:
            db.execute("UPDATE notices SET title = ?, description = ? WHERE id = ?",
                       (title, description, notice_id))
            flash("Notice updated.", "success")
        db.commit()
        return redirect(url_for("admin.notices"))

    rows = db.execute("SELECT * FROM notices ORDER BY created_at DESC, id DESC").fetchall()
    edit = None
    if request.args.get("edit"):
        edit = db.execute("SELECT * FROM notices WHERE id = ?",
                          (to_int(request.args["edit"], 0),)).fetchone()
    return render_template("admin/notices.html", notices=rows, edit=edit)


@admin_bp.route("/notices/<int:notice_id>/delete", methods=["POST"])
def delete_notice(notice_id):
    db = get_db()
    db.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
    db.commit()
    flash("Notice deleted.", "success")
    return redirect(url_for("admin.notices"))


# ---------------------------------------------------------------- policies

@admin_bp.route("/policies", methods=["GET", "POST"])
def policies():
    db = get_db()
    if request.method == "POST":
        max_cap = to_int(request.form.get("max_room_capacity"))
        priority = to_int(request.form.get("priority_year"))
        min_year = to_int(request.form.get("min_year"))
        status = request.form.get("applications_open")
        errors = []
        if max_cap is None or not 1 <= max_cap <= 10:
            errors.append("Maximum room capacity must be between 1 and 10.")
        if priority not in [0] + ACADEMIC_YEARS:
            errors.append("Please select a valid priority academic year.")
        if min_year not in ACADEMIC_YEARS:
            errors.append("Please select a valid minimum academic year.")
        if status not in ("Open", "Closed"):
            errors.append("Application availability must be Open or Closed.")
        if errors:
            flash_errors(errors)
        else:
            dbm.save_policies(db, {"max_room_capacity": max_cap, "priority_year": priority,
                                   "min_year": min_year, "applications_open": status})
            flash("Hostel policies updated successfully. New rules apply immediately.", "success")
            over = db.execute("SELECT COUNT(*) FROM rooms WHERE capacity > ?",
                              (max_cap,)).fetchone()[0]
            if over:
                flash(f"{over} room(s) have a configured capacity above {max_cap}. "
                      f"New allocations to those rooms are limited to {max_cap} bed(s); "
                      f"existing residents are not affected.", "info")
        return redirect(url_for("admin.policies"))
    return render_template("admin/policies.html", policies=dbm.get_policies(db),
                           years=ACADEMIC_YEARS)


# ======================================================================
# App factory
# ======================================================================

def _secret_key(app):
    key = os.environ.get("HOSTELHUB_SECRET_KEY")
    if key:
        return key
    os.makedirs(app.instance_path, exist_ok=True)
    path = os.path.join(app.instance_path, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        DATABASE=os.path.join(app.root_path, "hostel.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        # Set HOSTELHUB_HTTPS=1 on an HTTPS deployment so cookies are never sent over plain HTTP.
        SESSION_COOKIE_SECURE=os.environ.get("HOSTELHUB_HTTPS") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key(app)

    csrf.init_app(app)
    app.teardown_appcontext(dbm.close_db)
    dbm.init_db(app.config["DATABASE"])

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def load_user():
        g.user, g.role = None, None
        if request.endpoint == "static":
            return
        uid, role = session.get("user_id"), session.get("role")
        if uid is None:
            return
        db = get_db()
        if role == "admin":
            g.user = db.execute("SELECT id, username AS name FROM admins WHERE id = ?",
                                (uid,)).fetchone()
        elif role == "student":
            g.user = db.execute("SELECT id, name, email, year, student_code, created_at "
                                "FROM students WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()  # account was removed
        else:
            g.role = role

    @app.after_request
    def no_cache(resp):
        # Prevent viewing protected pages via the back button after logout.
        if g.get("user") is not None:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- template helpers
    @app.template_filter("prefs")
    def prefs_filter(value):
        try:
            return json.loads(value or "{}")
        except ValueError:
            return {}

    @app.template_filter("year_label")
    def year_label(year):
        year = to_int(year, 0)
        return {1: "1st Year", 2: "2nd Year", 3: "3rd Year"}.get(year, f"{year}th Year")

    @app.template_filter("date")
    def date_filter(value, with_time=False):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return value or ""
        return dt.strftime("%d %b %Y, %I:%M %p" if with_time else "%d %b %Y")

    @app.template_global()
    def is_demo(email):
        return (email or "").endswith("@" + dbm.DEMO_EMAIL_DOMAIN)

    # ---- error pages
    @app.errorhandler(CSRFError)
    def csrf_error(e):
        flash("Your form session expired. Please try again.", "warning")
        return redirect(request.referrer or url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               message="You do not have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message="The page you are looking for does not exist."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500,
                               message="Something went wrong. Please try again."), 500

    # ---- CLI commands
    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Also insert demo records.")
    def init_db_command(demo):
        """Create tables, default policies and the default admin account."""
        dbm.init_db(app.config["DATABASE"])
        click.echo(f"Database ready: {app.config['DATABASE']}")
        conn = dbm.connect(app.config["DATABASE"])
        admin = conn.execute("SELECT password FROM admins WHERE username = ?",
                             (dbm.DEFAULT_ADMIN[0],)).fetchone()
        conn.close()
        if admin and check_password_hash(admin["password"], dbm.DEFAULT_ADMIN[1]):
            click.echo(f"Default admin login: {dbm.DEFAULT_ADMIN[0]} / {dbm.DEFAULT_ADMIN[1]}")
        if demo:
            if dbm.seed_demo(app.config["DATABASE"]):
                click.echo(f"Demo data added. Demo student password: {dbm.DEMO_PASSWORD}")
            else:
                click.echo("Demo data already present - skipped.")

    @app.cli.command("set-admin-password")
    @click.option("--username", default=dbm.DEFAULT_ADMIN[0], show_default=True)
    @click.password_option(prompt="New admin password")
    def set_admin_password(username, password):
        """Change an administrator's password (use on any public deployment)."""
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters.")
        conn = dbm.connect(app.config["DATABASE"])
        try:
            cur = conn.execute("UPDATE admins SET password = ? WHERE username = ?",
                               (generate_password_hash(password), username))
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            raise click.ClickException(f"No administrator named '{username}'.")
        click.echo(f"Password updated for administrator '{username}'.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")

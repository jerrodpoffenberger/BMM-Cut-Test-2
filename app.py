import os
import io
import csv
import json
import time
import smtplib
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, Response
)
import bcrypt
import psycopg2
import psycopg2.extras
from database import init_db, get_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bmm-cut-test-2-dev-secret-change-me")
app.config["SESSION_PERMANENT"] = False

IDLE_TIMEOUT = 3600  # 1 hour in seconds


# ── Idle timeout enforcement ──────────────────────────────────────────────────

@app.before_request
def enforce_idle_timeout():
    if not session.get("user_id"):
        return
    last_active = session.get("last_active")
    now = time.time()
    if last_active and (now - last_active) > IDLE_TIMEOUT:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "Session expired"}), 401
        return redirect(url_for("login"))
    session["last_active"] = now


# ── Decorators ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") not in ("admin", "superadmin"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Forbidden"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "superadmin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Forbidden"}), 403
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def tenant_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("tenant_id") is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "No tenant context"}), 403
            return redirect(url_for("admin_panel"))
        return f(*args, **kwargs)
    return decorated


# ── Calculation helper ────────────────────────────────────────────────────────

def _calc(row):
    d = dict(row)
    pw = float(d.get("purchase_weight") or 0)
    tw = float(d.get("trim_weight") or 0)
    pp = float(d.get("purchase_price") or 0)
    d["yield_loss"] = round((tw / pw * 100) if pw > 0 else 0, 2)
    d["yield_pct"] = round(100 - d["yield_loss"], 2)
    yp = d["yield_pct"]
    d["adjusted_cost"] = round((pp / yp * 100) if yp > 0 else 0, 4)
    d["weekly_cost"] = round(pp * pw, 2)
    # Convert date to string if it's a date object
    if "entry_date" in d and hasattr(d["entry_date"], "isoformat"):
        d["entry_date"] = d["entry_date"].isoformat()
    return d


# ── Settings helpers ──────────────────────────────────────────────────────────

def _get_setting(tenant_id, key, default=""):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM settings WHERE tenant_id = %s AND key = %s",
            (tenant_id, key)
        )
        row = cur.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def _set_setting(tenant_id, key, value):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO settings (tenant_id, key, value) VALUES (%s, %s, %s)
               ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value""",
            (tenant_id, key, value)
        )
        conn.commit()
    finally:
        conn.close()


def _write_audit(conn, tenant_id, user_id, action, table_name, record_id=None, details=None):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO audit_log (tenant_id, user_id, action, table_name, record_id, details)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (tenant_id, user_id, action, table_name, record_id,
         json.dumps(details) if details else None)
    )


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db()
        try:
            cur = conn.cursor()
            # Search globally — no tenant field on login
            cur.execute(
                """SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
                   FROM users u
                   LEFT JOIN tenants t ON u.tenant_id = t.id
                   WHERE u.username = %s AND u.active = TRUE
                   LIMIT 1""",
                (username,)
            )
            user = cur.fetchone()
            if user and bcrypt.checkpw(
                password.encode(),
                user["password_hash"].encode()
            ):
                # Check tenant is active (skip for superadmin)
                if user["role"] != "superadmin" and not user["tenant_id"]:
                    error = "Invalid username or password."
                elif user["role"] != "superadmin" and user.get("tenant_name") is None:
                    error = "Account tenant not found."
                else:
                    session["user_id"] = user["id"]
                    session["tenant_id"] = user["tenant_id"]
                    session["role"] = user["role"]
                    session["username"] = user["username"]
                    session["last_active"] = time.time()

                    # Update last_login
                    cur.execute(
                        "UPDATE users SET last_login = NOW() WHERE id = %s",
                        (user["id"],)
                    )
                    conn.commit()

                    if user["role"] == "superadmin":
                        return redirect(url_for("admin_panel"))
                    return redirect(url_for("index"))
            else:
                error = "Invalid username or password."
        finally:
            conn.close()

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    success = None
    if request.method == "POST":
        current_pw = request.form.get("current_password", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        confirm_pw = request.form.get("confirm_password", "").strip()

        if not current_pw or not new_pw or not confirm_pw:
            error = "All fields are required."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."
        elif len(new_pw) < 4:
            error = "New password must be at least 4 characters."
        else:
            conn = get_db()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT password_hash FROM users WHERE id = %s",
                    (session["user_id"],)
                )
                row = cur.fetchone()
                if row and bcrypt.checkpw(current_pw.encode(), row["password_hash"].encode()):
                    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                    cur.execute(
                        "UPDATE users SET password_hash = %s WHERE id = %s",
                        (new_hash, session["user_id"])
                    )
                    conn.commit()
                    success = "Password changed successfully."
                else:
                    error = "Current password is incorrect."
            finally:
                conn.close()

    return render_template("change_password.html", error=error, success=success,
                           username=session.get("username"), user_role=session.get("role"))


# ── Superadmin routes ─────────────────────────────────────────────────────────

@app.route("/admin")
@superadmin_required
def admin_panel():
    return render_template("admin.html", username=session.get("username"))


@app.route("/api/admin/tenants", methods=["GET", "POST"])
@superadmin_required
def api_admin_tenants():
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "GET":
            cur.execute("""
                SELECT t.*, COUNT(u.id) as user_count
                FROM tenants t
                LEFT JOIN users u ON u.tenant_id = t.id AND u.active = TRUE
                GROUP BY t.id
                ORDER BY t.created_at DESC
            """)
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                result.append(d)
            return jsonify(result)

        # POST — create tenant + first admin user
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        slug = (data.get("slug") or "").strip().lower().replace(" ", "-")
        admin_username = (data.get("admin_username") or "").strip()
        admin_password = (data.get("admin_password") or "").strip()
        admin_email = (data.get("admin_email") or "").strip()

        if not name or not slug:
            return jsonify({"error": "Name and slug are required"}), 400
        if not admin_username or not admin_password:
            return jsonify({"error": "Admin username and password are required"}), 400

        # Check slug uniqueness
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        if cur.fetchone():
            return jsonify({"error": "Slug already in use"}), 409

        cur.execute(
            "INSERT INTO tenants (name, slug, active) VALUES (%s, %s, TRUE) RETURNING id",
            (name, slug)
        )
        tenant_id = cur.fetchone()["id"]

        pw_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            """INSERT INTO users (tenant_id, username, email, password_hash, role, active)
               VALUES (%s, %s, %s, %s, 'admin', TRUE)""",
            (tenant_id, admin_username, admin_email, pw_hash)
        )

        # Create default settings
        cur.execute(
            "INSERT INTO settings (tenant_id, key, value) VALUES (%s, 'app_name', %s)",
            (tenant_id, name)
        )
        cur.execute(
            "INSERT INTO settings (tenant_id, key, value) VALUES (%s, 'update_interval', '7')",
            (tenant_id,)
        )

        conn.commit()
        return jsonify({"ok": True, "tenant_id": tenant_id})
    finally:
        conn.close()


@app.route("/api/admin/tenants/<int:tid>", methods=["DELETE"])
@superadmin_required
def api_admin_tenant(tid):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tenants SET active = FALSE WHERE id = %s", (tid,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/admin/tenants/<int:tid>/users", methods=["GET", "POST"])
@superadmin_required
def api_admin_tenant_users(tid):
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "GET":
            cur.execute("""
                SELECT id, username, email, role, active, last_login, created_at
                FROM users
                WHERE tenant_id = %s
                ORDER BY created_at DESC
            """, (tid,))
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                for k in ("last_login", "created_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                result.append(d)
            return jsonify(result)

        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        email = (data.get("email") or "").strip()
        role = data.get("role", "butcher")
        if role not in ("admin", "butcher"):
            role = "butcher"

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            """INSERT INTO users (tenant_id, username, email, password_hash, role, active)
               VALUES (%s, %s, %s, %s, %s, TRUE)""",
            (tid, username, email, pw_hash, role)
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/admin/users/<int:uid>", methods=["PUT", "DELETE"])
@superadmin_required
def api_admin_user(uid):
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "DELETE":
            cur.execute("UPDATE users SET active = FALSE WHERE id = %s", (uid,))
            conn.commit()
            return jsonify({"ok": True})

        data = request.get_json() or {}
        role = data.get("role")
        active = data.get("active")
        new_password = data.get("new_password")

        if role is not None and role in ("admin", "butcher"):
            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, uid))
        if active is not None:
            cur.execute("UPDATE users SET active = %s WHERE id = %s", (bool(active), uid))
        if new_password:
            pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, uid))

        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
@login_required
@tenant_required
def index():
    tenant_id = session["tenant_id"]
    app_name = _get_setting(tenant_id, "app_name", "BMM Cut Test 2.0")
    user_role = session.get("role", "butcher")
    username = session.get("username", "")
    return render_template("index.html", app_name=app_name, user_role=user_role, username=username)


@app.route("/print-report")
@login_required
@tenant_required
@admin_required
def print_report():
    tenant_id = session["tenant_id"]
    app_name = _get_setting(tenant_id, "app_name", "BMM Cut Test 2.0")
    conn = get_db()
    try:
        cur = conn.cursor()
        entry_date = request.args.get("date")
        if not entry_date:
            cur.execute(
                """SELECT MAX(entry_date) as d FROM cut_entries
                   WHERE tenant_id = %s AND deleted_at IS NULL""",
                (tenant_id,)
            )
            row = cur.fetchone()
            entry_date = row["d"].isoformat() if row and row["d"] else None

        cur.execute(
            """SELECT * FROM cuts
               WHERE tenant_id = %s AND active = TRUE AND deleted_at IS NULL
               ORDER BY category, name""",
            (tenant_id,)
        )
        cuts = cur.fetchall()

        entries = {}
        if entry_date:
            cur.execute(
                """SELECT * FROM cut_entries
                   WHERE tenant_id = %s AND entry_date = %s AND deleted_at IS NULL""",
                (tenant_id, entry_date)
            )
            for r in cur.fetchall():
                entries[r["cut_id"]] = _calc(r)

        cut_data = [
            {"name": c["name"], "category": c["category"] or "—", "entry": entries.get(c["id"])}
            for c in cuts
        ]

        cur.execute(
            """SELECT DISTINCT entry_date FROM cut_entries
               WHERE tenant_id = %s AND deleted_at IS NULL
               ORDER BY entry_date DESC""",
            (tenant_id,)
        )
        available_dates = [r["entry_date"].isoformat() for r in cur.fetchall()]

        return render_template("print_report.html",
                               app_name=app_name,
                               cut_data=cut_data,
                               entry_date=entry_date,
                               available_dates=available_dates)
    finally:
        conn.close()


@app.route("/print-template")
@login_required
@tenant_required
@admin_required
def print_template():
    tenant_id = session["tenant_id"]
    app_name = _get_setting(tenant_id, "app_name", "BMM Cut Test 2.0")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM cuts
               WHERE tenant_id = %s AND active = TRUE AND deleted_at IS NULL
               ORDER BY category, name""",
            (tenant_id,)
        )
        cuts = cur.fetchall()
        return render_template("print_template.html", app_name=app_name, cuts=cuts)
    finally:
        conn.close()


# ── API: Settings ─────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET", "POST"])
@login_required
@tenant_required
def api_settings():
    tenant_id = session["tenant_id"]
    if request.method == "GET":
        return jsonify({
            "app_name": _get_setting(tenant_id, "app_name", "BMM Cut Test 2.0"),
            "update_interval": int(_get_setting(tenant_id, "update_interval", "7")),
            "alert_email": _get_setting(tenant_id, "alert_email", ""),
            "alert_enabled": _get_setting(tenant_id, "alert_enabled", "0") == "1",
        })

    if session.get("role") not in ("admin", "superadmin"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    if "app_name" in data:
        _set_setting(tenant_id, "app_name", (data["app_name"] or "").strip() or "BMM Cut Test 2.0")
    if "update_interval" in data:
        try:
            val = max(1, int(data["update_interval"]))
        except (ValueError, TypeError):
            val = 7
        _set_setting(tenant_id, "update_interval", str(val))
    if "alert_email" in data:
        _set_setting(tenant_id, "alert_email", (data["alert_email"] or "").strip())
    if "alert_enabled" in data:
        _set_setting(tenant_id, "alert_enabled", "1" if data["alert_enabled"] else "0")
    return jsonify({"ok": True})


# ── API: Cuts ─────────────────────────────────────────────────────────────────

@app.route("/api/cuts", methods=["GET", "POST"])
@login_required
@tenant_required
def api_cuts():
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "GET":
            role = session.get("role")
            if role in ("admin", "superadmin"):
                cur.execute(
                    """SELECT * FROM cuts
                       WHERE tenant_id = %s AND deleted_at IS NULL
                       ORDER BY category, name""",
                    (tenant_id,)
                )
            else:
                cur.execute(
                    """SELECT * FROM cuts
                       WHERE tenant_id = %s AND active = TRUE AND deleted_at IS NULL
                       ORDER BY category, name""",
                    (tenant_id,)
                )
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                for k in ("created_at", "deleted_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                result.append(d)
            return jsonify(result)

        if session.get("role") not in ("admin", "superadmin"):
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name is required"}), 400
        raw_ty = data.get("target_yield")
        target_yield = float(raw_ty) if raw_ty not in (None, "", "null") else None

        cur.execute(
            """INSERT INTO cuts (tenant_id, name, category, description, target_yield)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, name,
             (data.get("category") or "").strip(),
             (data.get("description") or "").strip(),
             target_yield)
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
        return jsonify({"ok": True, "id": new_id})
    finally:
        conn.close()


@app.route("/api/cuts/<int:cut_id>", methods=["PUT", "DELETE"])
@login_required
@tenant_required
@admin_required
def api_cut(cut_id):
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "DELETE":
            data = request.get_json() or {}
            if data.get("password") != "1981":
                return jsonify({"error": "Incorrect password"}), 403
            cur.execute(
                "UPDATE cuts SET deleted_at = NOW() WHERE id = %s AND tenant_id = %s",
                (cut_id, tenant_id)
            )
            _write_audit(conn, tenant_id, session["user_id"], "delete", "cuts", cut_id,
                         {"note": "soft delete"})
            conn.commit()
            return jsonify({"ok": True})

        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name is required"}), 400
        raw_ty = data.get("target_yield")
        target_yield = float(raw_ty) if raw_ty not in (None, "", "null") else None

        cur.execute(
            """UPDATE cuts SET name=%s, category=%s, description=%s, active=%s, target_yield=%s
               WHERE id=%s AND tenant_id=%s""",
            (
                name,
                (data.get("category") or "").strip(),
                (data.get("description") or "").strip(),
                bool(data.get("active", True)),
                target_yield,
                cut_id,
                tenant_id,
            ),
        )
        _write_audit(conn, tenant_id, session["user_id"], "update", "cuts", cut_id, data)
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ── API: Entries ──────────────────────────────────────────────────────────────

@app.route("/api/entries", methods=["GET", "POST"])
@login_required
@tenant_required
def api_entries():
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "GET":
            entry_date = request.args.get("date")
            cut_id = request.args.get("cut_id")

            if entry_date:
                cur.execute(
                    """SELECT e.*, c.name as cut_name, c.category
                       FROM cut_entries e JOIN cuts c ON e.cut_id = c.id
                       WHERE e.tenant_id = %s AND e.entry_date = %s AND e.deleted_at IS NULL
                       ORDER BY c.category, c.name""",
                    (tenant_id, entry_date)
                )
            elif cut_id:
                cur.execute(
                    """SELECT * FROM cut_entries
                       WHERE tenant_id = %s AND cut_id = %s AND deleted_at IS NULL
                       ORDER BY entry_date DESC""",
                    (tenant_id, int(cut_id))
                )
            else:
                return jsonify({"error": "date or cut_id required"}), 400

            return jsonify([_calc(r) for r in cur.fetchall()])

        # POST — batch upsert
        data = request.get_json() or []
        if isinstance(data, dict):
            data = [data]
        user_id = session["user_id"]
        for e in data:
            cur.execute(
                """INSERT INTO cut_entries
                       (tenant_id, cut_id, entry_date, purchase_price, purchase_weight,
                        trim_weight, notes, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (cut_id, entry_date) DO UPDATE SET
                       purchase_price = EXCLUDED.purchase_price,
                       purchase_weight = EXCLUDED.purchase_weight,
                       trim_weight = EXCLUDED.trim_weight,
                       notes = EXCLUDED.notes,
                       created_by = EXCLUDED.created_by,
                       deleted_at = NULL""",
                (
                    tenant_id,
                    e["cut_id"],
                    e["entry_date"],
                    float(e.get("purchase_price") or 0),
                    float(e.get("purchase_weight") or 0),
                    float(e.get("trim_weight") or 0),
                    (e.get("notes") or "").strip(),
                    user_id,
                ),
            )
            _write_audit(conn, tenant_id, user_id, "upsert", "cut_entries", e.get("cut_id"),
                         {"date": e["entry_date"]})
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/entries/export")
@login_required
@tenant_required
def api_entries_export():
    tenant_id = session["tenant_id"]
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    where_clauses = ["e.tenant_id = %s", "e.deleted_at IS NULL", "c.deleted_at IS NULL"]
    params = [tenant_id]
    if date_from:
        where_clauses.append("e.entry_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("e.entry_date <= %s")
        params.append(date_to)

    where = "WHERE " + " AND ".join(where_clauses)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT e.entry_date, c.name as cut_name, c.category,
                       e.purchase_price, e.purchase_weight, e.trim_weight,
                       e.notes, u.username as recorded_by
                FROM cut_entries e
                JOIN cuts c ON e.cut_id = c.id
                LEFT JOIN users u ON e.created_by = u.id
                {where}
                ORDER BY e.entry_date, c.category, c.name""",
            params
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Cut", "Category", "Purchase $/lb", "Purchase Lbs",
        "Trim Lbs", "Yield Loss %", "Yield %", "True Cost/lb", "Notes", "Recorded By"
    ])
    for r in rows:
        calc = _calc(r)
        writer.writerow([
            calc.get("entry_date", ""),
            r["cut_name"],
            r["category"] or "",
            round(float(r["purchase_price"] or 0), 4),
            round(float(r["purchase_weight"] or 0), 2),
            round(float(r["trim_weight"] or 0), 2),
            calc["yield_loss"],
            calc["yield_pct"],
            calc["adjusted_cost"],
            r["notes"] or "",
            r["recorded_by"] or "",
        ])

    csv_data = output.getvalue()
    filename = f"bmm_cut_test_export_{date.today().isoformat()}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/entries/delete", methods=["POST"])
@login_required
@tenant_required
@admin_required
def api_delete_entries():
    tenant_id = session["tenant_id"]
    data = request.get_json() or {}
    if data.get("password") != "1981":
        return jsonify({"error": "Incorrect password"}), 403
    entry_date = (data.get("date") or "").strip()
    cut_id = data.get("cut_id")
    if not entry_date:
        return jsonify({"error": "date is required"}), 400

    conn = get_db()
    try:
        cur = conn.cursor()
        if cut_id:
            cur.execute(
                """UPDATE cut_entries SET deleted_at = NOW()
                   WHERE tenant_id = %s AND entry_date = %s AND cut_id = %s""",
                (tenant_id, entry_date, cut_id)
            )
        else:
            cur.execute(
                """UPDATE cut_entries SET deleted_at = NOW()
                   WHERE tenant_id = %s AND entry_date = %s""",
                (tenant_id, entry_date)
            )
        _write_audit(conn, tenant_id, session["user_id"], "delete", "cut_entries", None,
                     {"date": entry_date, "cut_id": cut_id})
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ── API: Dashboard ────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
@login_required
@tenant_required
@admin_required
def api_dashboard():
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT e.*, c.name as cut_name, c.category, c.target_yield
               FROM cut_entries e
               JOIN cuts c ON e.cut_id = c.id
               WHERE c.tenant_id = %s
                 AND c.active = TRUE
                 AND c.deleted_at IS NULL
                 AND e.deleted_at IS NULL
                 AND e.entry_date = (
                     SELECT MAX(e2.entry_date) FROM cut_entries e2
                     WHERE e2.cut_id = e.cut_id AND e2.deleted_at IS NULL
                 )
               ORDER BY c.category, c.name""",
            (tenant_id,)
        )
        rows = cur.fetchall()

        cur.execute(
            """SELECT MAX(entry_date) as d FROM cut_entries
               WHERE tenant_id = %s AND deleted_at IS NULL""",
            (tenant_id,)
        )
        latest_row = cur.fetchone()
        latest_date = latest_row["d"].isoformat() if latest_row and latest_row["d"] else None

        interval = int(_get_setting(tenant_id, "update_interval", "7"))

        days_since = None
        days_until = None
        status = "no_data"
        if latest_date:
            try:
                ld = date.fromisoformat(latest_date)
                days_since = (date.today() - ld).days
                days_until = interval - days_since
                if days_since > interval:
                    status = "overdue"
                elif days_since >= interval - 1:
                    status = "due_soon"
                else:
                    status = "ok"
            except ValueError:
                pass

        cur.execute(
            """SELECT cut_id,
                      AVG(100 - (CASE WHEN purchase_weight > 0
                                 THEN trim_weight * 100.0 / purchase_weight
                                 ELSE 0 END)) as avg_yield_pct,
                      AVG(CASE WHEN purchase_weight > 0 AND trim_weight > 0
                               THEN purchase_price / (100 - trim_weight * 100.0 / purchase_weight) * 100
                               ELSE 0 END) as avg_true_cost
               FROM cut_entries
               WHERE tenant_id = %s AND deleted_at IS NULL
               GROUP BY cut_id""",
            (tenant_id,)
        )
        avg_map = {
            r["cut_id"]: {
                "avg_yield_pct": round(float(r["avg_yield_pct"] or 0), 2),
                "avg_true_cost": round(float(r["avg_true_cost"] or 0), 4),
            }
            for r in cur.fetchall()
        }

        cuts = [_calc(r) for r in rows]
        for c in cuts:
            avgs = avg_map.get(c["cut_id"], {"avg_yield_pct": 0, "avg_true_cost": 0})
            c["avg_yield_pct"] = avgs["avg_yield_pct"]
            c["avg_true_cost"] = avgs["avg_true_cost"]

        # Auto-send alert if overdue and not recently sent
        if status == "overdue":
            alert_enabled = _get_setting(tenant_id, "alert_enabled", "0") == "1"
            alert_email = _get_setting(tenant_id, "alert_email", "")
            last_alert = _get_setting(tenant_id, "last_alert_sent", "")
            if alert_enabled and alert_email:
                should_send = True
                if last_alert:
                    try:
                        last_dt = datetime.fromisoformat(last_alert)
                        if (datetime.utcnow() - last_dt).total_seconds() < 86400:
                            should_send = False
                    except ValueError:
                        pass
                if should_send:
                    _send_alert_email(tenant_id, alert_email, latest_date, days_since)
                    _set_setting(tenant_id, "last_alert_sent", datetime.utcnow().isoformat())

        return jsonify({
            "cuts": cuts,
            "latest_date": latest_date,
            "update_interval": interval,
            "days_since": days_since,
            "days_until": days_until,
            "status": status,
        })
    finally:
        conn.close()


# ── API: Entry Dates ──────────────────────────────────────────────────────────

@app.route("/api/entry-dates")
@login_required
@tenant_required
def api_entry_dates():
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT entry_date FROM cut_entries
               WHERE tenant_id = %s AND deleted_at IS NULL
               ORDER BY entry_date DESC""",
            (tenant_id,)
        )
        return jsonify([r["entry_date"].isoformat() for r in cur.fetchall()])
    finally:
        conn.close()


# ── API: Reports ──────────────────────────────────────────────────────────────

@app.route("/api/reports")
@login_required
@tenant_required
@admin_required
def api_reports():
    tenant_id = session["tenant_id"]
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    cut_ids = request.args.getlist("cut_id")

    where_clauses = [
        "e.tenant_id = %s",
        "e.deleted_at IS NULL",
        "c.deleted_at IS NULL"
    ]
    params = [tenant_id]

    if date_from:
        where_clauses.append("e.entry_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("e.entry_date <= %s")
        params.append(date_to)
    if cut_ids:
        placeholders = ",".join(["%s"] * len(cut_ids))
        where_clauses.append(f"e.cut_id IN ({placeholders})")
        params.extend([int(c) for c in cut_ids])

    where = "WHERE " + " AND ".join(where_clauses)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT e.entry_date, e.cut_id, c.name as cut_name, c.category,
                       e.purchase_price, e.purchase_weight, e.trim_weight, e.notes
                FROM cut_entries e JOIN cuts c ON e.cut_id = c.id
                {where}
                ORDER BY e.entry_date, c.category, c.name""",
            params
        )
        return jsonify([_calc(r) for r in cur.fetchall()])
    finally:
        conn.close()


# ── API: Users (tenant-scoped, admin only) ────────────────────────────────────

@app.route("/api/users", methods=["GET", "POST"])
@login_required
@tenant_required
@admin_required
def api_users():
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        if request.method == "GET":
            cur.execute(
                """SELECT id, username, email, role, active, last_login, created_at
                   FROM users
                   WHERE tenant_id = %s
                   ORDER BY created_at""",
                (tenant_id,)
            )
            result = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("last_login", "created_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                result.append(d)
            return jsonify(result)

        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        email = (data.get("email") or "").strip()
        role = data.get("role", "butcher")
        if role not in ("admin", "butcher"):
            role = "butcher"

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            """INSERT INTO users (tenant_id, username, email, password_hash, role, active)
               VALUES (%s, %s, %s, %s, %s, TRUE)""",
            (tenant_id, username, email, pw_hash, role)
        )
        conn.commit()
        return jsonify({"ok": True})
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Username already exists in this tenant"}), 409
    finally:
        conn.close()


@app.route("/api/users/<int:uid>", methods=["PUT", "DELETE"])
@login_required
@tenant_required
@admin_required
def api_user(uid):
    tenant_id = session["tenant_id"]
    conn = get_db()
    try:
        cur = conn.cursor()
        # Verify user belongs to this tenant
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND tenant_id = %s",
            (uid, tenant_id)
        )
        if not cur.fetchone():
            return jsonify({"error": "User not found"}), 404

        if request.method == "DELETE":
            if uid == session["user_id"]:
                return jsonify({"error": "Cannot deactivate your own account"}), 400
            cur.execute("UPDATE users SET active = FALSE WHERE id = %s", (uid,))
            conn.commit()
            return jsonify({"ok": True})

        data = request.get_json() or {}
        role = data.get("role")
        active = data.get("active")
        email = data.get("email")
        new_password = data.get("new_password")

        if role is not None and role in ("admin", "butcher"):
            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, uid))
        if active is not None:
            if not active and uid == session["user_id"]:
                return jsonify({"error": "Cannot deactivate your own account"}), 400
            cur.execute("UPDATE users SET active = %s WHERE id = %s", (bool(active), uid))
        if email is not None:
            cur.execute("UPDATE users SET email = %s WHERE id = %s", (email.strip(), uid))
        if new_password:
            pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, uid))

        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ── API: Send Alert Email ─────────────────────────────────────────────────────

def _send_alert_email(tenant_id, to_email, latest_date, days_since):
    mail_server = os.environ.get("MAIL_SERVER", "")
    mail_port = int(os.environ.get("MAIL_PORT", "587"))
    mail_username = os.environ.get("MAIL_USERNAME", "")
    mail_password = os.environ.get("MAIL_PASSWORD", "")
    mail_from = os.environ.get("MAIL_FROM", mail_username)
    app_name = _get_setting(tenant_id, "app_name", "BMM Cut Test 2.0")

    if not mail_server or not to_email:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{app_name}] Weekly Update Overdue"
        msg["From"] = mail_from
        msg["To"] = to_email

        body = (
            f"This is an automated reminder from {app_name}.\n\n"
            f"Your weekly cut test prices are overdue.\n"
            f"Last update: {latest_date or 'never'}\n"
            f"Days since last update: {days_since}\n\n"
            f"Please log in and enter new prices as soon as possible."
        )
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(mail_server, mail_port) as server:
            server.ehlo()
            server.starttls()
            if mail_username and mail_password:
                server.login(mail_username, mail_password)
            server.sendmail(mail_from, [to_email], msg.as_string())
        return True
    except Exception:
        return False


@app.route("/api/send-alert", methods=["POST"])
@login_required
@tenant_required
@admin_required
def api_send_alert():
    tenant_id = session["tenant_id"]
    alert_email = _get_setting(tenant_id, "alert_email", "")
    if not alert_email:
        return jsonify({"error": "No alert email configured"}), 400

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT MAX(entry_date) as d FROM cut_entries
               WHERE tenant_id = %s AND deleted_at IS NULL""",
            (tenant_id,)
        )
        row = cur.fetchone()
        latest_date = row["d"].isoformat() if row and row["d"] else None
    finally:
        conn.close()

    days_since = None
    if latest_date:
        try:
            ld = date.fromisoformat(latest_date)
            days_since = (date.today() - ld).days
        except ValueError:
            pass

    ok = _send_alert_email(tenant_id, alert_email, latest_date, days_since)
    if ok:
        _set_setting(tenant_id, "last_alert_sent", datetime.utcnow().isoformat())
        return jsonify({"ok": True})
    return jsonify({"error": "Failed to send email. Check server MAIL_* environment variables."}), 500


# ── Boot ──────────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5004)

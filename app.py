from __future__ import annotations

import json
import os
import io
import secrets
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Any, Iterable, cast
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, abort, url_for, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import func, text
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
from sqlalchemy import text
try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore


# ============================================================
# APP SETUP
# ============================================================
app = Flask(__name__)

# Load SECRET_KEY from environment or use secure default for development
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///draftroom_inventory.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Security headers and session configuration
app.config["SESSION_COOKIE_HTTPONLY"] = True  # Prevent JavaScript from accessing session cookie
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"  # HTTPS only in production
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # Prevent CSRF attacks
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
app.config["PREFERRED_URL_SCHEME"] = "https" if os.environ.get("FLASK_ENV") == "production" else "http"

db = SQLAlchemy(app)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Register before_request to add security headers
@app.before_request
def log_page_visits():
    """Log all page visits for audit trail"""
    # Only log for logged-in users (not login page, not static files)
    if request.endpoint and request.endpoint not in ('login', 'static') and is_logged_in():
        # Map endpoints to human-readable page names
        page_names = {
            'index': 'Dashboard',
            'items': 'Items',
            'items_bulk': 'Bulk Edit Items',
            'item': 'Item Details',
            'item_hub': 'Item Hub',
            'item_lots': 'Item Lots',
            'lot_add': 'Add Lot',
            'lot_view': 'View Lot',
            'lot_edit': 'Edit Lot',
            'lot_bulk': 'Bulk Lots',
            'lots': 'Lots',
            'order': 'Create Order',
            'orders': 'Orders',
            'order_edit': 'Edit Order',
            'edit_order': 'Edit Order',
            'beers': 'Beers',
            'beers_dashboard': 'Beer Dashboard',
            'beers_manage': 'Manage Beers',
            'beers_bulk_edit': 'Bulk Edit Beers',
            'suppliers': 'Suppliers',
            'supplier_form': 'Supplier Form',
            'users': 'Users',
            'user_form': 'User Form',
            'audit': 'Audit Log',
            'audit_log': 'Audit Log',
            'prep': 'Prep',
            'reconcile': 'Reconcile',
            'reconcile_edit': 'Reconcile Edit',
            'reconcile_history': 'Reconcile History',
            'sales_edit': 'Sales Edit',
            'move_boxes': 'Move Boxes',
            'batch': 'Batch',
        }
        
        page_title = page_names.get(request.endpoint, request.endpoint or 'Unknown')
        
        try:
            audit_log(
                action='page_visit',
                entity_type='Page',
                entity_id=None,
                page=request.path,
                page_title=page_title,
                message=f'User visited {page_title}',
                details={'endpoint': request.endpoint, 'method': request.method}
            )
            db.session.commit()
        except Exception:
            pass  # Don't block request if audit fails

@app.after_request
def add_security_headers_response(response):
    """Add security headers to all responses"""
    # Prevent clickjacking attacks
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    
    # Enable XSS protection in older browsers
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # Content Security Policy - restrictive by default
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' https://cdnjs.cloudflare.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'"
    )
    
    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # Feature policy
    response.headers["Permissions-Policy"] = (
        "geolocation=(), "
        "microphone=(), "
        "camera=(), "
        "payment=()"
    )
    
    return response


# ============================================================
# TIMEZONE (DISPLAY LOCAL, STORE UTC)
# ============================================================
APP_TZ = "America/New_York"

def utcnow() -> datetime:
    # store in UTC
    return datetime.now(timezone.utc)

def local_now() -> datetime:
    if ZoneInfo is None:
        # fallback - still avoids "wrong timezone" issues by staying consistent
        return datetime.now()
    return datetime.now(ZoneInfo(APP_TZ))

def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if ZoneInfo is None:
        return dt
    # if naive, treat as UTC (legacy)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(APP_TZ))

def _db_tables():
    # Only real tables (skip sqlite internal)
    rows = db.session.execute(text("""
        SELECT name FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)).fetchall()
    return [r[0] for r in rows]

def _export_db_to_dict():
    tables = _db_tables()
    payload = {
        "meta": {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "app": "inventory_system",
            "format": "sqlite-json-backup-v1",
        },
        "tables": {}
    }

    for t in tables:
        cols = db.session.execute(text(f"PRAGMA table_info({t})")).fetchall()
        col_names = [c[1] for c in cols]  # (cid, name, type, notnull, dflt_value, pk)

        data_rows = db.session.execute(text(f"SELECT * FROM {t}")).fetchall()
        payload["tables"][t] = {
            "columns": col_names,
            "rows": [list(r) for r in data_rows]
        }

    return payload

def _import_db_from_dict(payload):
    """
    Import a JSON backup into the CURRENT database using an isolated engine transaction
    (avoids: "A transaction is already begun on this Session.").

    Supports:
      A) New format:
        {"meta": {...}, "tables": {"items": {"columns": [...], "rows": [[...], ...]}, ...}}

      B) Legacy format:
        {"items": [ {col: val, ...}, ... ], "inventory_lots": [...], ...}

    Notes:
      - This function intentionally does NOT use db.session.begin().
      - It runs everything through db.engine.begin() to avoid session transaction conflicts.
    """
    if not isinstance(payload, dict):
        raise ValueError("Invalid backup file (payload is not a JSON object).")

    # If the request handler already started a session transaction, make sure
    # we aren't carrying a broken/partial state into later ORM work.
    try:
        db.session.rollback()
    except Exception:
        pass

    existing_tables = _db_tables()  # your helper that returns current sqlite tables (excluding sqlite_*)

    # --- helpers ---
    def _pragma_table_cols(conn, table_name):
        rows = conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
        # (cid, name, type, notnull, dflt_value, pk)
        return [r[1] for r in rows]

    # ---------------------------
    # Case A: New format (tables)
    # ---------------------------
    if isinstance(payload.get("tables"), dict):
        tables = payload["tables"]

        with db.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")

            # Clear existing data (reverse order helps FK chains)
            for t in reversed(existing_tables):
                conn.exec_driver_sql(f'DELETE FROM "{t}"')

            # Insert rows table-by-table
            for t, block in tables.items():
                if t not in existing_tables:
                    # ignore tables not present in this code version
                    continue
                if not isinstance(block, dict):
                    continue

                columns = block.get("columns", [])
                rows = block.get("rows", [])

                if not isinstance(columns, list) or not columns:
                    continue
                if not isinstance(rows, list):
                    continue

                col_sql = ", ".join([f'"{c}"' for c in columns])
                placeholders = ", ".join([f":c{i}" for i in range(len(columns))])
                sql = f'INSERT INTO "{t}" ({col_sql}) VALUES ({placeholders})'

                for row in rows:
                    if not isinstance(row, (list, tuple)):
                        continue
                    params = {f"c{i}": (row[i] if i < len(row) else None) for i in range(len(columns))}
                    conn.execute(text(sql), params)

            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

        # Clear ORM identity map so subsequent ORM reads reflect imported data
        try:
            db.session.expire_all()
        except Exception:
            pass

        return

    # --------------------------------
    # Case B: Legacy format (list rows)
    # --------------------------------
    legacy_table_blocks = {
        k: v for k, v in payload.items()
        if isinstance(k, str) and k in existing_tables and isinstance(v, list)
    }

    if not legacy_table_blocks:
        raise ValueError("Invalid backup file (missing 'tables' and no recognizable legacy table lists).")

    with db.engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")

        # Clear existing data
        for t in reversed(existing_tables):
            conn.exec_driver_sql(f'DELETE FROM "{t}"')

        # Insert legacy rows
        for t, rows in legacy_table_blocks.items():
            db_cols = _pragma_table_cols(conn, t)
            if not db_cols:
                continue

            col_sql = ", ".join([f'"{c}"' for c in db_cols])
            placeholders = ", ".join([f":{c}" for c in db_cols])
            sql = f'INSERT INTO "{t}" ({col_sql}) VALUES ({placeholders})'

            for r in rows:
                if not isinstance(r, dict):
                    continue
                params = {c: r.get(c, None) for c in db_cols}
                conn.execute(text(sql), params)

        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    try:
        db.session.expire_all()
    except Exception:
        pass

def _import_db_from_dict(payload):
    """
    Import a JSON backup into the CURRENT database using an isolated engine transaction.

    Fixes:
      - Avoids: "A transaction is already begun on this Session."
      - Avoids: NOT NULL constraint failures when the backup is missing newer NOT NULL columns
               (e.g., items.main_bar_on_hand) by auto-filling safe defaults.

    Supports:
      A) New format:
        {"meta": {...}, "tables": {"items": {"columns": [...], "rows": [[...], ...]}, ...}}

      B) Legacy format:
        {"items": [ {col: val, ...}, ... ], "inventory_lots": [...], ...}
    """
    if not isinstance(payload, dict):
        raise ValueError("Invalid backup file (payload is not a JSON object).")

    # Ensure the ORM session isn't left mid-transaction from earlier work in the request
    try:
        db.session.rollback()
    except Exception:
        pass

    existing_tables = _db_tables()  # your helper that returns current sqlite tables (excluding sqlite_*)

    def _pragma_table_info(conn, table_name):
        # returns list of dicts: name, type, notnull, dflt_value, pk
        rows = conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
        out = []
        for r in rows:
            out.append({
                "name": r[1],
                "type": (r[2] or "").upper(),
                "notnull": int(r[3] or 0),
                "dflt_value": r[4],  # can be None
                "pk": int(r[5] or 0),
            })
        return out

    def _default_for_col(colinfo):
        """
        Pick a safe default for a missing NOT NULL col with no DB default.
        We keep this conservative to satisfy constraints without guessing business logic.
        """
        t = (colinfo.get("type") or "").upper()

        # Common numeric types
        if "INT" in t or "REAL" in t or "FLOA" in t or "DOUB" in t or "NUM" in t or "DEC" in t:
            return 0

        # Boolean-ish stored as integer in sqlite often
        if "BOOL" in t:
            return 0

        # Dates/timestamps: allow empty string (better than NULL if NOT NULL)
        if "DATE" in t or "TIME" in t:
            return ""

        # Text-like
        return ""

    def _compute_insert_plan(conn, table_name, backup_cols):
        """
        Determine:
          - columns to insert (backup_cols that exist in DB + any required missing cols)
          - required missing cols that must be filled to satisfy NOT NULL constraints
        """
        tinfo = _pragma_table_info(conn, table_name)
        db_cols = [c["name"] for c in tinfo]

        # Only keep backup columns that exist in the current DB
        base_cols = [c for c in backup_cols if c in db_cols]

        # Missing required cols: NOT NULL and no default and not present in base_cols
        required_fill = []
        for c in tinfo:
            if c["name"] in base_cols:
                continue
            if c["notnull"] == 1 and c["dflt_value"] is None:
                required_fill.append(c)

        # Insert columns = base + required_fill names (required_fill appended so we can fill)
        insert_cols = base_cols + [c["name"] for c in required_fill]
        return insert_cols, required_fill

    # ---------------------------
    # Case A: New format (tables)
    # ---------------------------
    if isinstance(payload.get("tables"), dict):
        tables = payload["tables"]

        with db.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")

            # Clear existing data
            for t in reversed(existing_tables):
                conn.exec_driver_sql(f'DELETE FROM "{t}"')

            # Insert
            for t, block in tables.items():
                if t not in existing_tables:
                    continue
                if not isinstance(block, dict):
                    continue

                backup_cols = block.get("columns", [])
                rows = block.get("rows", [])

                if not isinstance(backup_cols, list) or not backup_cols:
                    continue
                if not isinstance(rows, list):
                    continue

                insert_cols, required_fill = _compute_insert_plan(conn, t, backup_cols)
                if not insert_cols:
                    continue

                col_sql = ", ".join([f'"{c}"' for c in insert_cols])
                placeholders = ", ".join([f":{c}" for c in insert_cols])
                sql = f'INSERT INTO "{t}" ({col_sql}) VALUES ({placeholders})'

                # Map backup column index positions for quick lookup
                idx = {name: i for i, name in enumerate(backup_cols)}

                for row in rows:
                    if not isinstance(row, (list, tuple)):
                        continue

                    params = {}

                    # Fill from backup where available
                    for c in insert_cols:
                        if c in idx:
                            i = idx[c]
                            params[c] = row[i] if i < len(row) else None

                    # Fill required missing NOT NULL cols when missing/None
                    for cinfo in required_fill:
                        name = cinfo["name"]
                        if name not in params or params[name] is None:
                            params[name] = _default_for_col(cinfo)

                    conn.execute(text(sql), params)

            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

        try:
            db.session.expire_all()
        except Exception:
            pass
        return

    # --------------------------------
    # Case B: Legacy format (list rows)
    # --------------------------------
    legacy_table_blocks = {
        k: v for k, v in payload.items()
        if isinstance(k, str) and k in existing_tables and isinstance(v, list)
    }

    if not legacy_table_blocks:
        raise ValueError("Invalid backup file (missing 'tables' and no recognizable legacy table lists).")

    with db.engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")

        # Clear existing data
        for t in reversed(existing_tables):
            conn.exec_driver_sql(f'DELETE FROM "{t}"')

        for t, rows in legacy_table_blocks.items():
            if not isinstance(rows, list):
                continue

            # Determine columns based on DB + backup keys
            # Use union of all backup keys (filtered to DB columns) plus required fills
            # so we don't miss fields spread across records.
            backup_keys = set()
            for r in rows:
                if isinstance(r, dict):
                    backup_keys.update(r.keys())

            insert_cols, required_fill = _compute_insert_plan(conn, t, list(backup_keys))
            if not insert_cols:
                continue

            col_sql = ", ".join([f'"{c}"' for c in insert_cols])
            placeholders = ", ".join([f":{c}" for c in insert_cols])
            sql = f'INSERT INTO "{t}" ({col_sql}) VALUES ({placeholders})'

            for r in rows:
                if not isinstance(r, dict):
                    continue

                params = {c: r.get(c, None) for c in insert_cols}

                for cinfo in required_fill:
                    name = cinfo["name"]
                    if params.get(name) is None:
                        params[name] = _default_for_col(cinfo)

                conn.execute(text(sql), params)

        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    try:
        db.session.expire_all()
    except Exception:
        pass


@app.post("/items/<int:item_id>/on-hand/save")
def save_on_hand(item_id):
    item = db.session.get(Item, item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect("/items")

    def to_float(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    # These field names must match your form input names:
    item.main_bar_on_hand = to_float(request.form.get("main_bar_on_hand"))
    item.low_bar_on_hand = to_float(request.form.get("low_bar_on_hand"))

    db.session.commit()
    flash("On-hand saved.", "success")
    return redirect(f"/items/{item_id}")

@app.template_filter("fmt_dt")
def fmt_dt(dt: Optional[datetime], fmt: str = "%b %d, %Y %I:%M %p") -> str:
    d = to_local(dt)
    if not d:
        return "—"
    return d.strftime(fmt)


# ============================================================
# AUTH SETTINGS (hardcoded fallback)
# ============================================================
# IMPORTANT: Change these credentials in production via environment variables!
# Set BREAK_GLASS_ADMIN_USERNAME and BREAK_GLASS_ADMIN_PASSWORD environment variables
BREAK_GLASS_ADMIN_USERNAME = os.environ.get("BREAK_GLASS_ADMIN_USERNAME", "admin")
BREAK_GLASS_ADMIN_PASSWORD = os.environ.get("BREAK_GLASS_ADMIN_PASSWORD", "change_me_immediately")

# Helper function for password validation
def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets minimum security requirements:
    - Minimum 12 characters
    - Mix of uppercase and lowercase
    - Numbers
    - Special characters
    Returns (is_valid, error_message)
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        return False, "Password must contain at least one special character"
    return True, ""


# Helper functions for input validation
def sanitize_input(value: str, max_length: int = 512, allow_special: bool = True) -> str:
    """
    Sanitize user input by stripping whitespace and enforcing length limits.
    Note: This does NOT prevent XSS - rely on Jinja2 auto-escaping for that.
    """
    if not isinstance(value, str):
        return ""
    sanitized = value.strip()
    return sanitized[:max_length]

def is_valid_username(username: str) -> bool:
    """Validate username format"""
    if not username or len(username) < 3 or len(username) > 255:
        return False
    # Allow alphanumeric, dots, underscores, hyphens
    import re
    return bool(re.match(r'^[a-zA-Z0-9._-]+$', username))

def is_valid_email(email: str) -> bool:
    """Validate email format"""
    if not email or len(email) > 255:
        return False
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


# ============================================================
# PERMISSIONS
# ============================================================
ROLE_RANK = {"staff": 1, "manager": 2, "admin": 3}

def role_rank(role: str | None) -> int:
    if not role:
        return 0
    return ROLE_RANK.get(role.strip().lower(), 0)

def current_user() -> Optional["User"]:
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        return User.query.get(int(uid))
    except Exception:
        return None

def current_user_role() -> str:
    if session.get("break_glass_admin") is True:
        return "admin"
    u = current_user()
    if not u:
        return ""
    return (u.role or "").lower()

def is_logged_in() -> bool:
    return (session.get("break_glass_admin") is True) or (session.get("user_id") is not None)

def can_manage_users() -> bool:
    u = current_user()
    if not u:
        return False
    # Break glass admin bypass
    if session.get("break_glass_admin") is True:
        return True
    # Check new role-based admin flag
    if u.role_id:
        role = Role.query.get(u.role_id)
        if role and role.is_admin:
            return True
    # Fallback: legacy admin role
    if u.role == "admin":
        return True
    return False

def can_edit_inventory() -> bool:
    u = current_user()
    if not u:
        return False
    # Break glass admin bypass
    if session.get("break_glass_admin") is True:
        return True
    # Check new role-based permissions
    if u.role_id:
        role = Role.query.get(u.role_id)
        if role and role.has_permission("can_edit_items"):
            return True
    # Fallback: legacy admin role
    if u.role == "admin":
        return True
    return False

def can_view_inventory() -> bool:
    u = current_user()
    if not u:
        return False
    if session.get("break_glass_admin") is True:
        return True
    # Check new role-based permissions
    if u.role_id:
        role = Role.query.get(u.role_id)
        if role and role.has_permission("can_view_items"):
            return True
    # Fallback: legacy admin role
    if u.role == "admin":
        return True
    return False

def require_view_access():
    if not is_logged_in():
        return redirect("/login")
    if session.get("break_glass_admin") is True:
        return None
    if not can_view_inventory():
        session.clear()
        flash("Access denied.", "error")
        return redirect("/login")
    return None

def require_inventory_edit():
    if not is_logged_in():
        return redirect("/login")
    if session.get("break_glass_admin") is True:
        return None
    if not can_edit_inventory():
        flash("Manager or Admin required for changes.", "error")
        return redirect("/")
    return None

def require_admin():
    if not is_logged_in():
        return redirect("/login")
    if session.get("break_glass_admin") is True:
        return None
    if not can_manage_users():
        flash("Admin required.", "error")
        return redirect("/")
    return None

def _load_reconcile_or_404(rec_id: int) -> tuple["ReconcileRecord", "Item"]:
    rec = ReconcileRecord.query.get_or_404(rec_id)
    item = Item.query.get_or_404(rec.item_id)
    return rec, item

def require_beer_edit():
    # Hook into your real permission logic
    guard = require_inventory_edit()
    return guard

@app.context_processor
def inject_user():
    u = current_user()
    role_name = u.get_role_name() if u else None
    return {
        "current_user": u,
        "auth_user": u,
        "auth_break_glass": bool(session.get("break_glass_admin") is True),
        "auth_role": current_user_role(),
        "auth_role_name": role_name,
        "can_edit_inventory": can_edit_inventory(),
        "can_manage_users": can_manage_users(),
    }


# ============================================================
# MODELS
# ============================================================

class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    permissions = db.Column(db.JSON, nullable=False, default=dict)  # Store permissions as JSON
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    
    def has_permission(self, permission):
        """Check if role has a specific permission"""
        if self.is_admin:
            return True
        return self.permissions.get(permission, False)
    
    def set_permission(self, permission, value):
        """Set a permission for this role"""
        if not isinstance(self.permissions, dict):
            self.permissions = {}
        self.permissions[permission] = bool(value)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(80), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)

    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")  # Backward compatibility
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True)  # New: reference to Role
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    
    # Relationship
    role_obj = db.relationship('Role', backref='users')

    def display_name(self) -> str:
        full = f"{(self.first_name or '').strip()} {(self.last_name or '').strip()}".strip()
        return full if full else self.username
    
    def get_role_name(self):
        """Get the role name (from Role object or legacy string)"""
        if self.role_obj:
            return self.role_obj.name
        return self.role
    
    def has_permission(self, permission):
        """Check if user has a specific permission"""
        if self.role_obj:
            return self.role_obj.has_permission(permission)
        # Fallback: legacy admin role can do everything
        if self.role == "admin":
            return True
        return False


class Beer(db.Model):
    __tablename__ = "beers"

    id = db.Column(db.Integer, primary_key=True)

    # Product info
    name = db.Column(db.String(200), nullable=False)
    brewery = db.Column(db.String(200), nullable=True)
    style = db.Column(db.String(120), nullable=True)

    abv = db.Column(db.Float, nullable=True)
    cost = db.Column(db.Float, nullable=True)          # cost per keg
    price = db.Column(db.Float, nullable=True)         # selling price per pour

    # "full" or "half" (or allow "sixtel" later)
    keg_size = db.Column(db.String(40), nullable=True)

    # Optional user-entered estimate
    cups_per_keg = db.Column(db.Integer, nullable=True)

    # ✅ Canonical name (OPTION A)
    on_hand_kegs = db.Column(db.Integer, nullable=False, default=0)
    

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    taps = db.relationship("BeerTap", backref="beer", lazy=True)

class BeerTap(db.Model):
    __tablename__ = "beer_taps"

    id = db.Column(db.Integer, primary_key=True)

    # ✅ Canonical: store which bar this tap belongs to
    # values: "main" or "lower"
    bar_location = db.Column(db.String(20), nullable=False)

    beer_id = db.Column(db.Integer, db.ForeignKey("beers.id"), nullable=True)

    # 0–100
    percent_remaining = db.Column(db.Integer, nullable=False, default=100)

    tapped_on = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(160), nullable=False, unique=True)
    contact_name = db.Column(db.String(160), nullable=True)
    phone = db.Column(db.String(60), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    notes = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # ✅ FIX: no backref here
    items = db.relationship("Item", back_populates="supplier", lazy=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)

    # who
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_name = db.Column(db.String(120), nullable=False, default="unknown")
    actor_role = db.Column(db.String(30), nullable=True)

    # what
    action = db.Column(db.String(40), nullable=False)          # create/update/delete/move/prep/reconcile/login/logout/page_visit
    entity_type = db.Column(db.String(60), nullable=False)     # InventoryLot, Item, Supplier, PrepBatch, ReconcileRecord, User, Page
    entity_id = db.Column(db.Integer, nullable=True)

    # page context
    page = db.Column(db.String(200), nullable=True)            # URL path visited
    page_title = db.Column(db.String(200), nullable=True)      # Human-readable page name

    # details
    message = db.Column(db.String(400), nullable=True)
    details = db.Column(db.Text, nullable=True)  # JSON string or long text

    # request context
    ip = db.Column(db.String(60), nullable=True)
    user_agent = db.Column(db.String(240), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(120), nullable=False)
    prep_type = db.Column(db.String(50), nullable=False, default="generic")

    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    supplier = db.relationship("Supplier", back_populates="items")


    unit = db.Column(db.String(50), nullable=True)
    default_units_per_box = db.Column(
    db.Integer,
    nullable=True,
)
    multiplier = db.Column(db.Float, nullable=True)
    
    raw_freezer_days = db.Column(db.Integer, nullable=True)
    raw_cooler_days = db.Column(db.Integer, nullable=True)
    raw_out_days = db.Column(db.Integer, nullable=True)

    prepped_freezer_days = db.Column(db.Integer, nullable=True)
    prepped_cooler_days = db.Column(db.Integer, nullable=True)
    prepped_out_days = db.Column(db.Integer, nullable=True)

    sales_mode = db.Column(db.String(40), nullable=True)

    pack1_label = db.Column(db.String(80), nullable=True)
    pack1_mult  = db.Column(db.Integer, nullable=True)
    pack2_label = db.Column(db.String(80), nullable=True)
    pack2_mult  = db.Column(db.Integer, nullable=True)
    pack3_label = db.Column(db.String(80), nullable=True)
    pack3_mult  = db.Column(db.Integer, nullable=True)
    pack4_label = db.Column(db.String(80), nullable=True)
    pack4_mult  = db.Column(db.Integer, nullable=True)

    # ✅ ADD THESE (generic bar on-hand)
    main_bar_on_hand = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )

    low_bar_on_hand = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )

    on_hand_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InventoryLot(db.Model):
    __tablename__ = "inventory_lots"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    lot_number = db.Column(db.Integer, nullable=True)
    lot_label = db.Column(db.String(60), nullable=True)

    received_date = db.Column(db.Date, nullable=False, default=date.today)

    quantity = db.Column(db.Float, nullable=False, default=1.0)

    count_units = db.Column(db.Integer, nullable=True)

    unit_cost = db.Column(db.Float, nullable=True)

    storage = db.Column(db.String(20), nullable=False, default="cooler")  # freezer|cooler|out
    state = db.Column(db.String(20), nullable=False, default="raw")       # raw|prepped

    expiration_override = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(400), nullable=True)

    is_consumed = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    item = db.relationship("Item", backref="orders")

    quantity = db.Column(db.Integer, nullable=False, default=0)
    order_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(400), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|ordered|received|cancelled
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class BeerOrder(db.Model):
    __tablename__ = "beer_orders"
    id = db.Column(db.Integer, primary_key=True)

    beer_id = db.Column(db.Integer, db.ForeignKey("beers.id"), nullable=False)
    beer = db.relationship("Beer", backref="orders")

    quantity = db.Column(db.Integer, nullable=False, default=0)  # number of kegs
    order_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(400), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|ordered|received|cancelled
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PrepBatch(db.Model):
    __tablename__ = "prep_batches"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    prep_date = db.Column(db.Date, nullable=False, default=date.today)

    from_loc = db.Column(db.String(20), nullable=False, default="cooler")
    to_loc = db.Column(db.String(20), nullable=False, default="cooler")

    mode = db.Column(db.String(20), nullable=False, default="first_n")
    boxes_used = db.Column(db.Integer, nullable=False, default=0)

    source_lot_ids = db.Column(db.String(800), nullable=True)

    produced_units = db.Column(db.Integer, nullable=False, default=0)

    # legacy DB requires this
    output_units = db.Column(db.Integer, nullable=False, default=0)

    created_prepped_lot_id = db.Column(db.Integer, nullable=True)
    expires_on = db.Column(db.Date, nullable=True)

    shelf_life_days = db.Column(db.Integer, nullable=False, default=0)

    notes = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    item = db.relationship("Item")


class ReconcileRecord(db.Model):
    __tablename__ = "reconcile_records"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    event_date = db.Column(db.Date, nullable=False, default=date.today)

    starting_units = db.Column(db.Integer, nullable=False, default=0)

    pack1_qty = db.Column(db.Integer, nullable=False, default=0)
    pack2_qty = db.Column(db.Integer, nullable=False, default=0)
    pack3_qty = db.Column(db.Integer, nullable=False, default=0)
    pack4_qty = db.Column(db.Integer, nullable=False, default=0)

    sales_units = db.Column(db.Integer, nullable=False, default=0)
    expected_units = db.Column(db.Integer, nullable=False, default=0)

    actual_units = db.Column(db.Integer, nullable=False, default=0)
    missing_units = db.Column(db.Integer, nullable=False, default=0)

    notes = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    source_prepped_lot_ids = db.Column(db.String(800), nullable=True)
    is_applied = db.Column(db.Boolean, nullable=False, default=False)
    applied_at = db.Column(db.DateTime, nullable=True)
    applied_lot_units = db.Column(db.Text, nullable=True)  # JSON: {"before":{lot_id:units}, "after":{lot_id:units}}


    item = db.relationship("Item")

class ReconcileConsumption(db.Model):
    __tablename__ = "reconcile_consumptions"
    id = db.Column(db.Integer, primary_key=True)

    rec_id = db.Column(db.Integer, db.ForeignKey("reconcile_records.id"), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey("inventory_lots.id"), nullable=False)

    units_used = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    rec = db.relationship("ReconcileRecord")
    lot = db.relationship("InventoryLot")

# ============================================================
# AUDIT LOGGER (safe, never blocks)
# ============================================================
def audit_log(
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    message: str | None = None,
    details: dict | str | None = None,
    page: str | None = None,
    page_title: str | None = None
):
    """
    Log audit trail with optional page tracking.
    Call this BEFORE commit. It's ok if entity_id isn't known yet.
    """
    try:
        u = current_user()
        actor_id = u.id if u else None
        actor_name = "Break-glass Admin" if session.get("break_glass_admin") else (u.display_name() if u else "unknown")
        actor_role = "admin" if session.get("break_glass_admin") else ((u.role or "") if u else "")

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        ua = (request.headers.get("User-Agent") or "")[:240]

        # If page not provided, try to get from request
        if page is None:
            page = request.path

        if isinstance(details, dict):
            details_str = json.dumps(details, ensure_ascii=False)
        elif isinstance(details, str):
            details_str = details
        else:
            details_str = None

        db.session.add(AuditLog(
            actor_user_id=actor_id,
            actor_name=actor_name,
            actor_role=actor_role,
            action=(action or "").strip().lower(),
            entity_type=(entity_type or "").strip(),
            entity_id=entity_id,
            page=page,
            page_title=page_title,
            message=message,
            details=details_str,
            ip=ip,
            user_agent=ua,
        ))
    except Exception:
        pass

def ensure_default_beer_taps():
    """
    Creates 25 taps for main + 25 taps for lower if they don't exist.
    This ensures the dashboard always has tap rows.
    """
    main_count = BeerTap.query.filter_by(bar_location="main").count()
    lower_count = BeerTap.query.filter_by(bar_location="lower").count()

    changed = False

    if main_count < 25:
        for _ in range(25 - main_count):
            db.session.add(BeerTap(bar_location="main", percent_remaining=100))
        changed = True

    if lower_count < 25:
        for _ in range(25 - lower_count):
            db.session.add(BeerTap(bar_location="lower", percent_remaining=100))
        changed = True

    if changed:
        db.session.commit()

def ensure_beer_taps():
    for bar in ["main", "lower"]:
        existing = BeerTap.query.filter_by(bar_location=bar).count()
        if existing < 25:
            # create missing taps
            for _ in range(25 - existing):
                db.session.add(BeerTap(bar_location=bar, beer_id=None, percent_remaining=100))
            db.session.commit()

# ============================================================
# HELPERS
# ============================================================
def to_int(val: str | None, default: int = 0) -> int:
    try:
        if val is None or str(val).strip() == "":
            return default
        return int(val)
    except Exception:
        return default

def parse_date(val: str | None, fallback: Optional[date]) -> Optional[date]:
    try:
        if not val or not val.strip():
            return fallback
        return datetime.strptime(val.strip(), "%Y-%m-%d").date()
    except Exception:
        return fallback

def parse_required_date(val: str | None, fallback: date) -> date:
    d = parse_date(val, fallback)
    return d if d else fallback

def norm_storage(val: str | None) -> str:
    v = (val or "").strip().lower()
    return v if v in {"freezer", "cooler", "out"} else "cooler"

def norm_state(val: str | None) -> str:
    v = (val or "").strip().lower()
    return v if v in {"raw", "prepped"} else "raw"

def compute_lot_expiration(item: Item, lot: InventoryLot) -> Optional[date]:
    if lot.expiration_override:
        return lot.expiration_override

    storage = (lot.storage or "cooler").lower()
    state = (lot.state or "raw").lower()

    days = None
    if state == "raw":
        if storage == "freezer":
            days = item.raw_freezer_days
        elif storage == "cooler":
            days = item.raw_cooler_days
        elif storage == "out":
            days = item.raw_out_days
    else:
        if storage == "freezer":
            days = item.prepped_freezer_days
        elif storage == "cooler":
            days = item.prepped_cooler_days
        elif storage == "out":
            days = item.prepped_out_days

    if not days or days <= 0:
        return None
    return lot.received_date + timedelta(days=int(days))

def days_left(exp: Optional[date]) -> Optional[int]:
    if not exp:
        return None
    return (exp - date.today()).days

def next_lot_number(item_id: int) -> int:
    # Get all existing lot numbers for this item
    existing = db.session.query(InventoryLot.lot_number)\
        .filter(InventoryLot.item_id == item_id, InventoryLot.lot_number.isnot(None))\
        .all()
    
    if not existing:
        return 1
    
    # Extract the numbers and sort them
    numbers = sorted([int(row[0]) for row in existing if row[0] is not None])
    
    # Find the first gap
    for i in range(1, len(numbers) + 2):
        if i not in numbers:
            return i
    
    return 1

def parse_csv_ints(csv: Optional[str]) -> list[int]:
    if not csv:
        return []
    out: list[int] = []
    for p in csv.split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out

def unique_ints(values: Iterable[Any]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for v in values:
        try:
            i = int(v)
        except Exception:
            continue
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

def _lots_fifo_distribute_remaining(lots: list["InventoryLot"], remaining_units: int) -> dict[int, int]:
    """
    Given FIFO-ordered lots, distribute 'remaining_units' across them in order:
    - Fill earlier lots to 0 first
    - Put any remaining into the last lot(s) in order
    Returns mapping lot_id -> new_units
    """
    remaining_units = max(int(remaining_units), 0)
    after: dict[int, int] = {}

    for lot in lots:
        cur = int(lot.count_units or 0)
        if remaining_units <= 0:
            after[lot.id] = 0
            continue

        if remaining_units >= cur:
            # this lot can remain full (we are distributing remaining, not consuming)
            # BUT we want the FIFO distribution where earlier lots hold as much as possible first.
            # So we keep this lot as-is until remaining runs out.
            after[lot.id] = cur
            remaining_units -= cur
        else:
            after[lot.id] = remaining_units
            remaining_units = 0

    # If remaining_units is still > 0, that means remaining exceeded total current units.
    # Clamp to current totals (can't invent inventory).
    return after
def _safe_int(val, default=0):
    try:
        if val is None or val == "":
            return default
        return int(val)
    except Exception:
        return default

def _safe_float(val, default=None):
    try:
        if val is None or val == "":
            return default
        return float(val)
    except Exception:
        return default

def _safe_date(val):
    # Accept YYYY-MM-DD or None
    try:
        if not val:
            return None
        return date.fromisoformat(val)
    except Exception:
        return None




def _apply_reconcile_inventory(item_id: int, selected_lot_ids: list[int], actual_left: int) -> dict[str, dict[str, int]]:
    """
    Updates inventory_lots.count_units for the selected prepped lots so their total equals actual_left.
    Saves FIFO distribution across the selected lots.
    Marks is_consumed=True for lots that become 0.
    Returns snapshot dict with before/after mappings for undo.
    """
    # Pull FIFO lots (only prepped, not consumed)
    lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item_id,
            InventoryLot.state == "prepped",
            InventoryLot.id.in_(selected_lot_ids),
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    before: dict[str, int] = {}
    for l in lots:
        before[str(l.id)] = int(l.count_units or 0)

    total_before = sum(before.values())
    actual_left = max(int(actual_left), 0)
    if actual_left > total_before:
        actual_left = total_before  # clamp

    # Distribute remaining across FIFO lots
    after_int = _lots_fifo_distribute_remaining(lots, actual_left)
    after: dict[str, int] = {str(k): int(v) for k, v in after_int.items()}

    # Apply updates
    for l in lots:
        new_units = int(after.get(str(l.id), 0))
        l.count_units = new_units

        # Mark consumed if zero
        if new_units <= 0:
            l.is_consumed = True
        else:
            l.is_consumed = False

    return {"before": before, "after": after}


def _undo_reconcile_inventory(snapshot: dict[str, dict[str, int]]):
    """
    Restores lots to the 'before' state in snapshot.
    Handles both prepped items (count_units) and generic_food (quantity + is_consumed).
    """
    before = snapshot.get("before") or {}
    if not isinstance(before, dict):
        return

    lot_ids = []
    for k in before.keys():
        try:
            lot_ids.append(int(k))
        except Exception:
            pass

    if not lot_ids:
        return

    lots = InventoryLot.query.filter(InventoryLot.id.in_(lot_ids)).all()
    lot_map = {l.id: l for l in lots}

    for k, state_data in before.items():
        try:
            lid = int(k)
        except Exception:
            continue

        l = lot_map.get(lid)
        if not l:
            continue

        # Handle both prepped items (count_units) and generic_food (quantity)
        if isinstance(state_data, dict):
            # generic_food: restore quantity and is_consumed
            if "quantity" in state_data:
                l.quantity = float(state_data.get("quantity", 1.0))
            if "is_consumed" in state_data:
                l.is_consumed = bool(state_data.get("is_consumed", False))
            # Also restore count_units if present (for prepped items)
            if "count_units" in state_data:
                l.count_units = int(state_data.get("count_units", 0))
        else:
            # Legacy: just a number (count_units)
            u = int(state_data or 0)
            l.count_units = u
            l.is_consumed = True if u <= 0 else False


# -------------------------
# Helpers
# -------------------------
def _clamp_percent(val):
    try:
        n = int(val)
    except Exception:
        return 0
    return max(0, min(100, n))

def _ensure_taps_exist():
    """
    Ensure we always have exactly two tap rows: main + lower.
    """
    main = BeerTap.query.filter_by(bar_location="main").first()
    lower = BeerTap.query.filter_by(bar_location="lower").first()

    created = False
    if not main:
        main = BeerTap(bar_location="main", beer_id=None, percent_remaining=0, tapped_on=None)
        db.session.add(main)
        created = True
    if not lower:
        lower = BeerTap(bar_location="lower", beer_id=None, percent_remaining=0, tapped_on=None)
        db.session.add(lower)
        created = True
    if created:
        db.session.commit()

def _beer_dashboard_rows():
    """
    Builds lists used by the dashboard: low tap, low backstock, etc.
    """
    _ensure_taps_exist()

    taps = BeerTap.query.order_by(BeerTap.bar_location.asc()).all()
    beers = Beer.query.order_by(Beer.name.asc()).all()

    # thresholds (easy to change later)
    TAP_LOW = 30
    TAP_CRITICAL = 15
    BACKSTOCK_LOW = 1
    BACKSTOCK_CRITICAL = 0

    # Low on tap
    low_on_tap = []
    for t in taps:
        if t.beer_id is None:
            continue
        if t.percent_remaining <= TAP_LOW:
            status = "low"
            if t.percent_remaining <= TAP_CRITICAL:
                status = "critical"

            cups_left = None
            if t.beer and t.beer.cups_per_keg:
                cups_left = int(round((t.percent_remaining / 100.0) * t.beer.cups_per_keg))

            low_on_tap.append({
                "tap": t,
                "status": status,
                "cups_left": cups_left
            })

    low_on_tap.sort(key=lambda x: x["tap"].percent_remaining)

    # Low backstock
    low_backstock = []
    for b in beers:
        if b.on_hand_kegs <= BACKSTOCK_LOW:
            status = "low"
            if b.on_hand_kegs <= BACKSTOCK_CRITICAL:
                status = "critical"
            low_backstock.append({"beer": b, "status": status})
    low_backstock.sort(key=lambda x: x["beer"].on_hand_kegs)

    # “panic list”: low tap AND no backstock
    panic = []
    for row in low_on_tap:
        b = row["tap"].beer
        if b and b.on_hand_keg <= 0 and row["tap"].percent_remaining <= 20:
            panic.append(row)

    return {
        "taps": taps,
        "beers": beers,
        "low_on_tap": low_on_tap,
        "low_backstock": low_backstock,
        "panic": panic
    }
# ============================================================
# SIMPLE SQLITE MIGRATION
# ============================================================
def sqlite_table_exists(table_name: str) -> bool:
    row = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).fetchone()
    return row is not None


def _get(d, key, default=None):
    try:
        v = d.get(key, default)
        return default if v is None else v
    except Exception:
        return default

def sqlite_table_columns(table_name: str) -> set[str]:
    rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {r[1] for r in rows}

def sqlite_add_column_if_missing(table_name: str, col_name: str, col_sql: str):
    if not sqlite_table_exists(table_name):
        return
    cols = sqlite_table_columns(table_name)
    if col_name in cols:
        return
    db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_sql}"))
    db.session.commit()

def run_migrations():
    sqlite_add_column_if_missing("items", "prep_type", "VARCHAR(40) NOT NULL DEFAULT 'generic'")
    sqlite_add_column_if_missing("items", "sales_mode", "VARCHAR(30) NOT NULL DEFAULT 'simple'")
    sqlite_add_column_if_missing("items", "pack1_label", "VARCHAR(80) DEFAULT 'Single (10)'")
    sqlite_add_column_if_missing("items", "pack1_mult", "INTEGER DEFAULT 10")
    sqlite_add_column_if_missing("items", "pack2_label", "VARCHAR(80) DEFAULT 'Double (20)'")
    sqlite_add_column_if_missing("items", "pack2_mult", "INTEGER DEFAULT 20")
    sqlite_add_column_if_missing("items", "pack3_label", "VARCHAR(80) DEFAULT 'Room 120 Single (10)'")
    sqlite_add_column_if_missing("items", "pack3_mult", "INTEGER DEFAULT 10")
    sqlite_add_column_if_missing("items", "pack4_label", "VARCHAR(80) DEFAULT 'Room 120 Double (20)'")
    sqlite_add_column_if_missing("items", "pack4_mult", "INTEGER DEFAULT 20")
    sqlite_add_column_if_missing("reconcile_records", "source_prepped_lot_ids", "VARCHAR(800)")
    sqlite_add_column_if_missing("reconcile_records", "is_applied", "BOOLEAN NOT NULL DEFAULT 0")
    sqlite_add_column_if_missing("reconcile_records", "applied_at", "DATETIME")
    sqlite_add_column_if_missing("reconcile_records", "applied_lot_units", "TEXT")

    sqlite_add_column_if_missing("items", "on_hand_count", "INTEGER NOT NULL DEFAULT 0")

    sqlite_add_column_if_missing("inventory_lots", "count_units", "INTEGER")
    sqlite_add_column_if_missing("inventory_lots", "lot_label", "VARCHAR(60)")
    sqlite_add_column_if_missing("inventory_lots", "expiration_override", "DATE")
    sqlite_add_column_if_missing("inventory_lots", "lot_number", "INTEGER")
    sqlite_add_column_if_missing("inventory_lots", "notes", "VARCHAR(400)")
    sqlite_add_column_if_missing("inventory_lots", "is_consumed", "BOOLEAN NOT NULL DEFAULT 0")

    sqlite_add_column_if_missing("prep_batches", "from_loc", "VARCHAR(20) DEFAULT 'cooler'")
    sqlite_add_column_if_missing("prep_batches", "to_loc", "VARCHAR(20) DEFAULT 'cooler'")
    sqlite_add_column_if_missing("prep_batches", "mode", "VARCHAR(20) DEFAULT 'first_n'")
    sqlite_add_column_if_missing("prep_batches", "boxes_used", "INTEGER DEFAULT 0")
    sqlite_add_column_if_missing("prep_batches", "source_lot_ids", "VARCHAR(800)")
    sqlite_add_column_if_missing("prep_batches", "produced_units", "INTEGER DEFAULT 0")
    sqlite_add_column_if_missing("prep_batches", "created_prepped_lot_id", "INTEGER")
    sqlite_add_column_if_missing("prep_batches", "expires_on", "DATE")
    sqlite_add_column_if_missing("prep_batches", "notes", "VARCHAR(400)")
    sqlite_add_column_if_missing("prep_batches", "created_at", "DATETIME")
    sqlite_add_column_if_missing("prep_batches", "output_units", "INTEGER NOT NULL DEFAULT 0")

    # audit table safety (if older DB)
    sqlite_add_column_if_missing("audit_logs", "created_at", "DATETIME")
    sqlite_add_column_if_missing("audit_logs", "details", "TEXT")
    sqlite_add_column_if_missing("audit_logs", "ip", "VARCHAR(60)")
    sqlite_add_column_if_missing("audit_logs", "user_agent", "VARCHAR(240)")

def fifo_reduce_prepped_lots(item_id: int, lot_ids: list[int], units_to_reduce: int) -> list[dict]:
    """
    Reduce count_units across the given prepped lots in FIFO order.
    Returns a list of {"lot_id": int, "used": int, "before": int, "after": int}.
    Raises ValueError if inventory isn't sufficient.
    """
    if units_to_reduce <= 0:
        return []

    lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item_id,
            InventoryLot.state == "prepped",
            InventoryLot.is_consumed == False,
            InventoryLot.id.in_(lot_ids),
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    remaining = int(units_to_reduce)
    moves: list[dict] = []

    total_available = sum(int(l.count_units or 0) for l in lots)
    if total_available < remaining:
        raise ValueError(f"Not enough units in selected lots. Need {remaining}, have {total_available}.")

    for lot in lots:
        before = int(lot.count_units or 0)
        if before <= 0:
            lot.is_consumed = True
            continue

        use = min(before, remaining)
        after = before - use

        lot.count_units = after
        if after <= 0:
            lot.count_units = 0
            lot.is_consumed = True

        moves.append({"lot_id": lot.id, "used": use, "before": before, "after": int(lot.count_units or 0)})

        remaining -= use
        if remaining <= 0:
            break

    return moves


def restore_prepped_lots(consumptions: list["ReconcileConsumption"]):
    """
    Undo reductions from a reconcile by restoring count_units and is_consumed.
    """
    lot_ids = [c.lot_id for c in consumptions]
    lots = InventoryLot.query.filter(InventoryLot.id.in_(lot_ids)).all()
    lot_map = {l.id: l for l in lots}

    for c in consumptions:
        lot = lot_map.get(c.lot_id)
        if not lot:
            continue
        before = int(lot.count_units or 0)
        lot.count_units = before + int(c.units_used or 0)
        lot.is_consumed = False

# ============================================================
# AUTH ROUTES
# ============================================================
@app.get("/login")
def login():
    if is_logged_in():
        return redirect("/")
    return render_template("login.html")

@app.post("/login")
@limiter.limit("5 per minute")  # Rate limit: max 5 login attempts per minute
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    # Input validation
    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect("/login")
    
    if len(username) > 255 or len(password) > 512:
        flash("Invalid input.", "error")
        return redirect("/login")

    if username.lower() == BREAK_GLASS_ADMIN_USERNAME.lower() and password == BREAK_GLASS_ADMIN_PASSWORD:
        session.clear()
        session["break_glass_admin"] = True
        session.permanent = True  # Use permanent session with configured lifetime

        audit_log(
            action="login",
            entity_type="User",
            entity_id=None,
            message="Logged in via break-glass admin",
            details={"username": username, "method": "break_glass"}
        )
        db.session.commit()

        flash("Logged in (break-glass admin).", "success")
        return redirect("/")

    u = User.query.filter(func.lower(User.username) == username.lower()).first()
    if not u or not u.is_active:
        # Use generic message to prevent user enumeration
        flash("Invalid username/password.", "error")
        return redirect("/login")

    if not check_password_hash(u.password_hash, password):
        # Use generic message to prevent user enumeration
        flash("Invalid username/password.", "error")
        return redirect("/login")

    session.clear()
    session["user_id"] = u.id
    session.permanent = True  # Use permanent session with configured lifetime

    audit_log(
        action="login",
        entity_type="User",
        entity_id=u.id,
        message="Logged in",
        details={"username": u.username, "role": u.role}
    )
    db.session.commit()

    flash("Logged in.", "success")
    return redirect("/")

@app.get("/logout")
def logout():
    # log before clearing session (we want actor in record)
    u = current_user()
    is_bg = bool(session.get("break_glass_admin") is True)

    audit_log(
        action="logout",
        entity_type="User",
        entity_id=(u.id if u else None),
        message="Logged out",
        details={"break_glass": is_bg}
    )
    db.session.commit()

    session.clear()
    flash("Logged out.", "success")
    return redirect("/login")

# -------------------------
# ============================================================
# BEERS ROUTES
# ============================================================
@app.route("/beers/dashboard", methods=["GET"])
def beers_dashboard():
    ensure_beer_taps()

    beers = Beer.query.order_by(Beer.brewery.asc().nullslast(), Beer.name.asc()).all()

    main_taps = BeerTap.query.filter_by(bar_location="main").order_by(BeerTap.id.asc()).all()
    lower_taps = BeerTap.query.filter_by(bar_location="lower").order_by(BeerTap.id.asc()).all()

    return render_template(
        "beers_dashboard.html",
        beers=beers,
        main_taps=main_taps,
        lower_taps=lower_taps
    )



@app.route("/beers/taps/assign", methods=["POST"])
def assign_beer_to_tap():
    tap_id = request.form.get("tap_id")
    beer_id = request.form.get("beer_id")  # can be blank for "None"
    tapped_on = request.form.get("tapped_on")  # optional
    notes = request.form.get("notes")  # optional
    set_percent = request.form.get("set_percent")

    if not tap_id:
        flash("Missing tap selection.", "error")
        return redirect(url_for("beers_dashboard"))

    tap = BeerTap.query.get(int(tap_id))
    if not tap:
        flash("Tap not found.", "error")
        return redirect(url_for("beers_dashboard"))

    # assign / clear
    tap.beer_id = int(beer_id) if (beer_id and beer_id.strip()) else None

    # percent (default to 100 when assigning a new beer unless provided)
    if set_percent and str(set_percent).strip() != "":
        try:
            tap.percent_remaining = max(0, min(100, int(set_percent)))
        except:
            tap.percent_remaining = 100
    else:
        tap.percent_remaining = 100 if tap.beer_id else tap.percent_remaining

    # tapped_on optional
    if tapped_on and tapped_on.strip():
        try:
            tap.tapped_on = datetime.strptime(tapped_on, "%Y-%m-%d").date()
        except:
            tap.tapped_on = None
    else:
        tap.tapped_on = None

    tap.notes = notes.strip() if notes else None

    db.session.commit()
    flash("Tap updated.", "success")
    return redirect(url_for("beers_dashboard"))


@app.route("/beers/taps/remove", methods=["POST"])
def remove_beer_from_tap():
    tap_id = request.form.get("tap_id")
    if not tap_id:
        flash("Missing tap.", "error")
        return redirect(url_for("beers_dashboard"))

    tap = BeerTap.query.get(int(tap_id))
    if not tap:
        flash("Tap not found.", "error")
        return redirect(url_for("beers_dashboard"))

    tap.beer_id = None
    tap.tapped_on = None
    tap.notes = None
    # leave percent as-is, or reset — your call; resetting feels cleaner:
    tap.percent_remaining = 0

    db.session.commit()
    flash("Beer removed from tap.", "success")
    return redirect(url_for("beers_dashboard"))


@app.route("/beers/taps/save", methods=["POST"])
def save_tap_percents():
    # expects inputs like percent_<tap_id>
    taps = BeerTap.query.all()
    updated = 0

    for tap in taps:
        key = f"percent_{tap.id}"
        if key in request.form:
            raw = request.form.get(key)
            try:
                val = int(raw)
                val = max(0, min(100, val))
                if tap.percent_remaining != val:
                    tap.percent_remaining = val
                    updated += 1
            except:
                pass

    if updated:
        db.session.commit()
        flash("Saved changes.", "success")
    else:
        flash("No changes to save.", "info")

    return redirect(url_for("beers_dashboard"))

@app.route("/beers/bulk", methods=["GET", "POST"])
def beers_bulk():
    if request.method == "GET":
        return render_template("beers_bulk_add.html")

    payload = request.form.get("payload", "[]")
    try:
        beers = json.loads(payload)
        if not isinstance(beers, list):
            raise ValueError("payload must be list")
    except Exception:
        return "Invalid bulk payload", 400

    created = 0
    for b in beers:
        name = (b.get("name") or "").strip()
        if not name:
            continue

        beer = Beer(
            name=name,
            brewery=(b.get("brewery") or "").strip(),
            style=(b.get("style") or "").strip(),
            abv=b.get("abv"),
            cost=b.get("cost"),
            keg_size=(b.get("keg_size") or "half").lower() if (b.get("keg_size") or "").lower() in ("full", "half") else "half",
            price=b.get("price"),
            cups_per_keg=b.get("cups_per_keg")
        )
        db.session.add(beer)
        created += 1

    db.session.commit()
    return redirect("/beers/dashboard")


@app.get("/beers/bulk")
def beers_bulk_get():
    guard = require_inventory_edit()
    if guard:
        return guard
    return render_template("beers_bulk_add.html")

@app.post("/beers/bulk")
def beers_bulk_post():
    guard = require_inventory_edit()
    if guard:
        return guard

    payload = request.form.get("payload", "[]")
    try:
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ValueError("payload must be list")
    except Exception:
        flash("Invalid bulk payload JSON.", "error")
        return redirect("/beers/bulk")

    created = 0
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name:
            continue

        keg_size = (r.get("keg_size") or "").strip().lower()
        if keg_size not in ("full", "half"):
            keg_size = None

        b = Beer(
            name=name,
            brewery=(r.get("brewery") or "").strip() or None,
            style=(r.get("style") or "").strip() or None,
            abv=_safe_float(r.get("abv")),
            cost=_safe_float(r.get("cost")),
            price=_safe_float(r.get("price")),
            keg_size=keg_size,
            cups_per_keg=_safe_int(r.get("cups_per_keg"), default=None),
            on_hand_kegs=_safe_int(r.get("on_hand_kegs"), default=0),
        )
        db.session.add(b)
        created += 1

    db.session.commit()
    flash(f"Bulk add complete. Added {created} beer(s).", "success")
    return redirect("/beers/dashboard")

@app.get("/beers/bulk-edit")
def beers_bulk_edit():
    guard = require_inventory_edit()
    if guard:
        return guard

    beers = Beer.query.order_by(Beer.name.asc()).all()
    return render_template("beers_bulk_edit.html", beers=beers)

@app.post("/beers/bulk-edit-save")
def beers_bulk_edit_save():
    guard = require_inventory_edit()
    if guard:
        return guard

    data = request.get_json()
    if not data or "beers" not in data:
        return {"error": "No beers data"}, 400

    try:
        for beer_data in data["beers"]:
            beer_id = beer_data.get("id")
            beer = Beer.query.get(beer_id)
            if not beer:
                continue

            # Update basic fields
            if "name" in beer_data:
                beer.name = beer_data["name"].strip()
            if "brewery" in beer_data:
                beer.brewery = beer_data["brewery"].strip() or None
            if "style" in beer_data:
                beer.style = beer_data["style"].strip() or None

            # Update numeric fields
            if "abv" in beer_data:
                try:
                    beer.abv = float(beer_data["abv"]) if beer_data["abv"] else None
                except (ValueError, TypeError):
                    pass

            if "cost" in beer_data:
                try:
                    beer.cost = float(beer_data["cost"]) if beer_data["cost"] else None
                except (ValueError, TypeError):
                    pass

            if "price" in beer_data:
                try:
                    beer.price = float(beer_data["price"]) if beer_data["price"] else None
                except (ValueError, TypeError):
                    pass

            if "keg_size" in beer_data:
                keg_size = beer_data["keg_size"].strip().lower() if beer_data["keg_size"] else None
                if keg_size in ("full", "half"):
                    beer.keg_size = keg_size
                else:
                    beer.keg_size = None

            if "cups_per_keg" in beer_data:
                try:
                    beer.cups_per_keg = int(beer_data["cups_per_keg"]) if beer_data["cups_per_keg"] else None
                except (ValueError, TypeError):
                    pass

            if "on_hand_kegs" in beer_data:
                try:
                    beer.on_hand_kegs = int(beer_data["on_hand_kegs"])
                except (ValueError, TypeError):
                    pass

        db.session.commit()
        return {"success": True, "message": f"Updated {len(data['beers'])} beers", "redirect": "/beers"}
    except Exception as e:
        db.session.rollback()
        print(f"Error bulk saving beers: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.route("/beers", methods=["GET", "POST"])
def beers_manage():
    can_edit_inventory = True  # replace with your real permission logic if needed

    if request.method == "POST":
        if not can_edit_inventory:
            flash("Manager/Admin only.", "error")
            return redirect("/beers")

        name = (request.form.get("name") or "").strip()
        brewery = (request.form.get("brewery") or "").strip()
        style = (request.form.get("style") or "").strip()

        abv = request.form.get("abv")
        cost = request.form.get("cost")
        price = request.form.get("price")

        keg_size = (request.form.get("keg_size") or "half").strip().lower()
        cups_per_keg = request.form.get("cups_per_keg")
        on_hand_kegs = request.form.get("on_hand_kegs")

        if not name or not brewery:
            flash("Name and brewery are required.", "error")
            return redirect("/beers")

        b = Beer(
            name=name,
            brewery=brewery,
            style=style if style else None,
            abv=float(abv) if abv else None,
            cost=float(cost) if cost else None,
            price=float(price) if price else None,
            keg_size="full" if keg_size == "full" else "half",
            cups_per_keg=int(cups_per_keg) if cups_per_keg else None,
            on_hand_kegs=int(on_hand_kegs) if on_hand_kegs else 0
        )
        db.session.add(b)
        db.session.commit()

        flash("Beer added.", "success")
        return redirect("/beers")

    beers = Beer.query.order_by(Beer.name.asc()).all()
    _ensure_taps_exist()
    taps = BeerTap.query.order_by(BeerTap.bar_location.asc()).all()

    return render_template("beers_manage.html", beers=beers, taps=taps, can_edit_inventory=can_edit_inventory)


@app.route("/beers/taps/cups_preview", methods=["POST"])
def taps_cups_preview():
    """
    Returns computed cups left for each tap row using:
    cups_left = round((percent_left/100) * cups_per_keg)
    cups_per_keg comes from the selected Beer record (or null if not set)
    """
    try:
        data = request.get_json(force=True)
        taps = data.get("taps", [])
        if not isinstance(taps, list):
            return jsonify(ok=False, error="Invalid payload"), 400
    except Exception:
        return jsonify(ok=False, error="Invalid JSON"), 400

    out = {}
    for t in taps:
        tap_id = str(t.get("id"))
        beer_id = t.get("beer_id")
        percent_left = t.get("percent_left", 0)
        try:
            percent_left = float(percent_left)
        except Exception:
            percent_left = 0

        if not beer_id:
            out[tap_id] = None
            continue

        beer = Beer.query.get(int(beer_id))
        if not beer or not beer.cups_per_keg:
            out[tap_id] = None
            continue

        cups_left = round((max(0, min(100, percent_left)) / 100.0) * float(beer.cups_per_keg))
        out[tap_id] = int(cups_left)

    return jsonify(ok=True, cups_left=out)

@app.route("/beers/<int:beer_id>/edit", methods=["GET", "POST"])
def beers_edit(beer_id):
    can_edit_inventory = True  # replace with your real permission logic if needed
    b = Beer.query.get_or_404(beer_id)

    def _clean(s):
        return (s or "").strip()

    def _to_int(val, default=None):
        val = _clean(val)
        if val == "":
            return default
        return int(val)

    def _to_float(val, default=None):
        val = _clean(val)
        if val == "":
            return default
        return float(val)

    if request.method == "POST":
        if not can_edit_inventory:
            flash("Manager/Admin only.", "error")
            return redirect(url_for("beers_edit", beer_id=beer_id))

        try:
            # ---- text fields ----
            b.name = _clean(request.form.get("name"))
            b.brewery = _clean(request.form.get("brewery"))
            b.style = _clean(request.form.get("style")) or None

            if not b.name or not b.brewery:
                flash("Name and brewery are required.", "error")
                return redirect(url_for("beers_edit", beer_id=beer_id))

            # ---- numeric fields ----
            b.abv = _to_float(request.form.get("abv"), None)
            b.cost = _to_float(request.form.get("cost"), None)
            b.price = _to_float(request.form.get("price"), None)

            keg_size = _clean(request.form.get("keg_size")).lower() or "half"
            b.keg_size = "full" if keg_size == "full" else "half"

            b.cups_per_keg = _to_int(request.form.get("cups_per_keg"), None)

            # ---- ON HAND KEGS (accept many possible input names) ----
            # We will look for the first one that exists in the POST.
            possible_on_hand_names = [
                "on_hand_kegs",          # most common
                "kegs_on_hand",
                "kegs_onhand",
                "kegs_on_hand",
                "onhand_kegs",
                "on_hand",               # sometimes people shorten it
            ]

            possible_extra_names = [
                "extra_kegs_on_hand",    # if you intended "add extra"
                "extra_kegs",
                "add_kegs",
                "add_on_hand_kegs",
            ]

            posted_on_hand = None
            for n in possible_on_hand_names:
                if n in request.form:
                    posted_on_hand = request.form.get(n)
                    break

            posted_extra = None
            for n in possible_extra_names:
                if n in request.form:
                    posted_extra = request.form.get(n)
                    break

            current = int(b.on_hand_kegs or 0)

            # If "extra" exists, ADD it. Otherwise SET absolute value if provided.
            if posted_extra is not None and _clean(posted_extra) != "":
                extra = _to_int(posted_extra, 0)
                if extra < 0:
                    extra = 0
                b.on_hand_kegs = current + extra
            elif posted_on_hand is not None and _clean(posted_on_hand) != "":
                b.on_hand_kegs = _to_int(posted_on_hand, current)
            else:
                # Nothing came through -> do NOT pretend it updated
                flash(
                    "Kegs on hand was NOT received from the form. "
                    "Fix beers_edit.html input name or make sure the input is inside the form.",
                    "error",
                )
                # This tells you exactly what the server received
                flash("POST keys received: " + ", ".join(sorted(request.form.keys())), "error")
                return redirect(url_for("beers_edit", beer_id=beer_id))

            db.session.add(b)
            db.session.commit()

            flash(
                f"Beer updated. Kegs on hand is now {b.on_hand_kegs}.",
                "success",
            )
            return redirect("/beers")

        except ValueError:
            db.session.rollback()
            flash("Please enter valid numbers for ABV/cost/price/cups/kegs.", "error")
            flash("POST keys received: " + ", ".join(sorted(request.form.keys())), "error")
            return redirect(url_for("beers_edit", beer_id=beer_id))
        except Exception as e:
            db.session.rollback()
            flash("Could not save changes (server error).", "error")
            flash(f"Error: {e}", "error")
            flash("POST keys received: " + ", ".join(sorted(request.form.keys())), "error")
            return redirect(url_for("beers_edit", beer_id=beer_id))

    return render_template("beers_edit.html", beer=b, can_edit_inventory=can_edit_inventory)



@app.post("/beers/<int:beer_id>/delete")
def beers_delete(beer_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    b = Beer.query.get_or_404(beer_id)

    # Block delete if on tap
    on_tap = BeerTap.query.filter_by(beer_id=b.id).first()
    if on_tap:
        flash("This beer is currently on a tap. Clear the tap first.", "error")
        return redirect("/beers/dashboard")

    db.session.delete(b)
    db.session.commit()
    flash("Beer deleted.", "success")
    return redirect("/beers/dashboard")




@app.post("/beers/on-tap/save/main")
def beers_on_tap_save_main():
    rows = BeerTap.query.filter_by(bar="main").all()
    for r in rows:
        key = f"percent_full_{r.id}"
        if key in request.form:
            val = request.form.get(key, type=int)
            if val is None:
                continue
            r.percent_full = max(0, min(100, int(val)))
            r.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Main Bar tap levels saved.", "success")
    return redirect("/beers/dashboard")


@app.post("/beers/on-tap/save/lower")
def beers_on_tap_save_lower():
    rows = BeerTap.query.filter_by(bar="lower").all()
    for r in rows:
        key = f"percent_full_{r.id}"
        if key in request.form:
            val = request.form.get(key, type=int)
            if val is None:
                continue
            r.percent_full = max(0, min(100, int(val)))
            r.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Lower Bar tap levels saved.", "success")
    return redirect("/beers/dashboard")


@app.get("/beers/on-tap/<int:on_tap_id>/remove")
def beers_on_tap_remove(on_tap_id):
    r = BeerTap.query.get_or_404(on_tap_id)
    db.session.delete(r)
    db.session.commit()
    flash("Removed from tap.", "success")
    return redirect("/beers/dashboard")

@app.route("/beers/receive", methods=["POST"])
def beers_receive_kegs():
    can_edit_inventory = True  # replace with your real permission logic if needed

    if not can_edit_inventory:
        flash("Manager/Admin only.", "error")
        return redirect("/beers")

    beer_id = request.form.get("beer_id")
    qty = request.form.get("qty")

    try:
        beer_id = int(beer_id)
        qty = int(qty)
    except Exception:
        flash("Invalid receive input.", "error")
        return redirect("/beers")

    if qty <= 0:
        flash("Qty must be positive.", "error")
        return redirect("/beers")

    b = Beer.query.get_or_404(beer_id)
    b.on_hand_kegs += qty
    db.session.commit()

    flash(f"Received {qty} keg(s) for {b.name}.", "success")
    return redirect("/beers")

@app.post("/beers/save_sheet")
def beers_save_sheet():
    guard = require_inventory_edit()
    if guard:
        return jsonify(ok=False, error="Not authorized"), 403

    try:
        data = request.get_json(force=True, silent=False)
    except Exception as e:
        print("❌ /beers/save_sheet invalid JSON:", e)
        return jsonify(ok=False, error="Invalid JSON"), 400

    print("✅ /beers/save_sheet RECEIVED:", data)

    updated = 0
    saved = []

    beers_in = data.get("beers", [])
    if not isinstance(beers_in, list):
        beers_in = []

    for row in beers_in:
        bid = row.get("id")
        if not bid:
            print("⚠️  Missing beer id in row:", row)
            continue

        b = Beer.query.get(int(bid))
        if not b:
            print("⚠️  Beer not found id:", bid)
            continue

        # Accept multiple key names from frontend
        raw_on_hand = None
        for k in ("on_hand_kegs", "kegs_on_hand", "kegsOnHand", "onHandKegs", "on_hand"):
            if k in row:
                raw_on_hand = row.get(k)
                break

        if raw_on_hand is None:
            print("⚠️  Missing kegs field for beer id:", bid, "row:", row)
            continue

        try:
            b.on_hand_kegs = int(raw_on_hand or 0)
        except Exception:
            b.on_hand_kegs = 0

        updated += 1

    db.session.commit()

    # Re-read saved values to confirm persistence
    for row in beers_in:
        bid = row.get("id")
        if not bid:
            continue
        b = Beer.query.get(int(bid))
        if b:
            saved.append({"id": b.id, "name": b.name, "on_hand_kegs": b.on_hand_kegs})

    print("✅ /beers/save_sheet UPDATED:", updated, "SAVED:", saved)
    return jsonify(ok=True, updated=updated, saved=saved)

@app.post("/items/<int:item_id>/generic_on_hand")
def item_generic_on_hand(item_id: int):
    # --- auth/permissions (match your existing pattern) ---
    if not can_edit_inventory:
        flash("Manager/Admin only.", "error")
        return redirect(f"/items/{item_id}")

    item = Item.query.get_or_404(item_id)

    # Only for generic items
    if (item.prep_type or "").lower() != "generic":
        flash("On-hand entry is only for generic items.", "error")
        return redirect(f"/items/{item_id}")

    # Parse + sanitize
    def _to_int(val, default=0):
        try:
            n = int(val)
            return n if n >= 0 else 0
        except Exception:
            return default

    main_val = _to_int(request.form.get("main_bar_on_hand", 0), 0)

    # ✅ accept either key (supports older templates)
    low_raw = request.form.get("low_bar_on_hand", None)
    if low_raw is None:
        low_raw = request.form.get("lower_bar_on_hand", 0)

    low_val = _to_int(low_raw, 0)

    item.main_bar_on_hand = main_val
    item.low_bar_on_hand = low_val

    db.session.commit()

    flash("On-hand counts saved.", "success")
    return redirect(f"/items/{item_id}")

@app.route("/items/<int:item_id>/on_hand", methods=["POST"])
def update_item_on_hand(item_id):
    # ----- permission check (match your existing pattern) -----
    role = (session.get("role") or "").lower()
    if role not in ("admin", "manager", "breakglass"):
        abort(403)

    item = Item.query.get_or_404(item_id)

    # Only allowed for generic items
    if (item.prep_type or "").lower() != "generic":
        abort(400, "On-hand counts only apply to generic items.")

    def _to_int_strict(val):
        # strict int parsing (keeps your existing behavior)
        return int(val)

    try:
        main_bar = _to_int_strict(request.form.get("main_bar_on_hand", 0))

        # ✅ accept either key
        low_raw = request.form.get("low_bar_on_hand", None)
        if low_raw is None:
            low_raw = request.form.get("lower_bar_on_hand", 0)

        low_bar = _to_int_strict(low_raw)

    except ValueError:
        flash("On-hand values must be whole numbers.", "error")
        return redirect(request.referrer or url_for("item_hub", item_id=item.id))

    if main_bar < 0 or low_bar < 0:
        flash("On-hand values cannot be negative.", "error")
        return redirect(request.referrer or url_for("item_hub", item_id=item.id))

    item.main_bar_on_hand = main_bar
    item.low_bar_on_hand = low_bar

    db.session.commit()
    flash("On-hand counts updated.", "success")

    return redirect(url_for("item_hub", item_id=item.id))








@app.post("/beers/create")
def beers_create():
    guard = require_inventory_edit()
    if guard:
        return guard

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Beer name is required.", "error")
        return redirect("/beers/dashboard")

    b = Beer(
        name=name,
        brewery=(request.form.get("brewery") or "").strip() or None,
        style=(request.form.get("style") or "").strip() or None,
        abv=_safe_float(request.form.get("abv")),
        cost=_safe_float(request.form.get("cost")),
        price=_safe_float(request.form.get("price")),
        keg_size=((request.form.get("keg_size") or "").strip().lower() or None),
        cups_per_keg=_safe_int(request.form.get("cups_per_keg"), default=None),
        on_hand_kegs=_safe_int(request.form.get("on_hand_kegs"), default=0),
    )
    if b.keg_size not in (None, "full", "half"):
        b.keg_size = None

    db.session.add(b)
    db.session.commit()

    flash("Beer added.", "success")
    return redirect("/beers/dashboard")





@app.route("/beers/tap/set", methods=["POST"])
def beers_set_tap():
    can_edit_inventory = True  # replace with your real permission logic if needed
    if not can_edit_inventory:
        flash("Manager/Admin only.", "error")
        return redirect("/beers/dashboard")

    _ensure_taps_exist()

    bar = (request.form.get("bar") or "").strip().lower()     # main / lower
    beer_id = request.form.get("beer_id")
    percent = request.form.get("percent_remaining")

    if bar not in ("main", "lower"):
        flash("Invalid bar.", "error")
        return redirect("/beers/dashboard")

    tap = BeerTap.query.filter_by(bar_location=bar).first()

    # allow empty selection
    if not beer_id:
        tap.beer_id = None
        tap.percent_remaining = 0
        tap.tapped_on = None
        tap.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Tap cleared.", "success")
        return redirect("/beers/dashboard")

    try:
        beer_id = int(beer_id)
    except Exception:
        flash("Invalid beer.", "error")
        return redirect("/beers/dashboard")

    tap.beer_id = beer_id
    tap.percent_remaining = _clamp_percent(percent)
    tap.tapped_on = date.today()
    tap.updated_at = datetime.utcnow()
    db.session.commit()

    flash("Tap updated.", "success")
    return redirect("/beers/dashboard")

@app.route("/beers/tap/percent", methods=["POST"])
def beers_update_tap_percent():
    can_edit_inventory = True  # replace with your real permission logic if needed
    if not can_edit_inventory:
        flash("Manager/Admin only.", "error")
        return redirect("/beers/dashboard")

    _ensure_taps_exist()
    bar = (request.form.get("bar") or "").strip().lower()
    percent = request.form.get("percent_remaining")

    if bar not in ("main", "lower"):
        flash("Invalid bar.", "error")
        return redirect("/beers/dashboard")

    tap = BeerTap.query.filter_by(bar_location=bar).first()
    tap.percent_remaining = _clamp_percent(percent)
    tap.updated_at = datetime.utcnow()
    db.session.commit()

    flash("Tap percentage updated.", "success")
    return redirect("/beers/dashboard")



@app.route("/beers/taps/save_json", methods=["POST"])
def save_beer_taps():
    try:
        data = request.get_json(force=True)
        taps = data.get("taps", [])
        if not isinstance(taps, list):
            return jsonify(ok=False, error="Invalid taps payload"), 400
    except Exception:
        return jsonify(ok=False, error="Invalid JSON"), 400

    for t in taps:
        tap_id = t.get("id")
        tap = BeerTap.query.get(int(tap_id)) if tap_id else None
        if not tap:
            continue

        bar = (t.get("bar") or "").strip().lower()
        if bar not in ("main", "lower"):
            bar = tap.bar

        percent_left = t.get("percent_left")
        try:
            percent_left = int(percent_left) if percent_left is not None else tap.percent_left
        except Exception:
            percent_left = tap.percent_left
        percent_left = max(0, min(100, percent_left))

        keg_size = (t.get("keg_size") or "half").lower()
        if keg_size not in ("full", "half"):
            keg_size = "half"

        beer_id = t.get("beer_id")
        if beer_id is not None:
            # allow clearing
            if beer_id:
                beer = Beer.query.get(int(beer_id))
                tap.beer_id = beer.id if beer else None
            else:
                tap.beer_id = None

        price = t.get("price")
        try:
            price = float(price) if price is not None else None
        except Exception:
            price = None

        tap.bar = bar
        tap.keg_size = keg_size
        tap.percent_left = percent_left
        tap.price = price

    db.session.commit()
    return jsonify(ok=True)



@app.get("/beers/export")
def beers_export():
    # Export all beer + tap data in a single JSON blob
    beers = Beer.query.order_by(Beer.id.asc()).all()
    taps = BeerTap.query.order_by(BeerTap.id.asc()).all()

    payload = {
        "schema_version": 1,
        "exported_at": datetime.utcnow().isoformat(),
        "beers": [],
        "beer_taps": []
    }

    for b in beers:
        payload["beers"].append({
            "id": b.id,
            "name": b.name,
            "brewery": b.brewery,
            "style": b.style,
            "abv": b.abv,
            "cost": b.cost,
            "price": b.price,
            "keg_size": b.keg_size,
            "cups_per_keg": b.cups_per_keg,

            # ✅ Canonical export key (Option A)
            "on_hand_kegs": b.on_hand_kegs,

            "created_at": b.created_at.isoformat() if b.created_at else None
        })

    for t in taps:
        payload["beer_taps"].append({
            "id": t.id,
            "bar_location": t.bar_location,
            "beer_id": t.beer_id,
            "percent_remaining": t.percent_remaining,
            "tapped_on": t.tapped_on.isoformat() if t.tapped_on else None,
            "notes": t.notes,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None
        })

    data = json.dumps(payload, indent=2)
    mem = io.BytesIO(data.encode("utf-8"))
    mem.seek(0)

    return send_file(
        mem,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"beers_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    )

@app.post("/beers/import")
def beers_import():
    """
    Accepts JSON file exported from /beers/export.

    Supports older exports too:
    - beers.on_hand_kegs -> beers.on_hand_kegs
    - taps may have missing bar_location or use 'bar' -> map
    """
    file = request.files.get("file")
    mode = request.form.get("mode", "replace")  # "replace" or "merge"

    if not file:
        flash("No file uploaded.", "error")
        return redirect("/beers")

    try:
        raw = file.read().decode("utf-8")
        payload = json.loads(raw)
    except Exception as e:
        flash(f"Import failed: invalid JSON ({e})", "error")
        return redirect("/beers")

    beers_in = payload.get("beers", [])
    taps_in = payload.get("beer_taps", [])

    try:
        if mode == "replace":
            # ✅ wipe only beer tables (safe: won’t touch other inventory tables)
            BeerTap.query.delete()
            Beer.query.delete()
            db.session.commit()

        # --- import beers ---
        for row in beers_in:
            beer_id = row.get("id")
            if mode == "merge" and beer_id:
                b = Beer.query.get(beer_id)
            else:
                b = None

            if not b:
                b = Beer()
                if beer_id is not None:
                    b.id = beer_id

            b.name = (row.get("name") or "").strip()
            b.brewery = (row.get("brewery") or "").strip() or None
            b.style = (row.get("style") or "").strip() or None

            b.abv = _safe_float(row.get("abv"))
            b.cost = _safe_float(row.get("cost"))
            b.price = _safe_float(row.get("price"))

            b.keg_size = (row.get("keg_size") or "").strip() or None
            b.cups_per_keg = _safe_int(row.get("cups_per_keg"), default=None)

            # ✅ OPTION A canonical column, but accept legacy key "kegs_on_hand"
            on_hand = row.get("on_hand_kegs", None)
            if on_hand is None:
                on_hand = row.get("kegs_on_hand", None)  # legacy key
            b.on_hand_kegs = _safe_int(on_hand, default=0)

            # created_at is optional; don’t crash if missing
            ca = row.get("created_at")
            if ca:
                try:
                    b.created_at = datetime.fromisoformat(ca)
                except Exception:
                    pass

            db.session.add(b)

        db.session.commit()

        # --- import taps ---
        for row in taps_in:
            tap_id = row.get("id")
            if mode == "merge" and tap_id:
                t = BeerTap.query.get(tap_id)
            else:
                t = None

            if not t:
                t = BeerTap()
                if tap_id is not None:
                    t.id = tap_id

            # ✅ Canonical key: bar_location
            bar_loc = row.get("bar_location")
            if not bar_loc:
                bar_loc = row.get("bar")  # legacy key support
            bar_loc = (bar_loc or "main").strip().lower()
            if bar_loc not in ("main", "lower"):
                bar_loc = "main"
            t.bar_location = bar_loc

            t.beer_id = row.get("beer_id", None)

            pr = row.get("percent_remaining", 100)
            t.percent_remaining = max(0, min(100, _safe_int(pr, 100)))

            t.tapped_on = _safe_date(row.get("tapped_on"))
            t.notes = row.get("notes")

            ua = row.get("updated_at")
            if ua:
                try:
                    t.updated_at = datetime.fromisoformat(ua)
                except Exception:
                    pass

            db.session.add(t)

        db.session.commit()

        flash(f"Import complete ({mode}).", "success")
        return redirect("/beers")

    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {e}", "error")
        return redirect("/beers")
    
@app.get("/beers")
def beers_page():
    return render_template("beers.html")


@app.route("/beers/dashboard/add-to-tap", methods=["POST"])
def beers_add_to_tap():
    # if "user_id" not in session: return redirect("/login")

    ensure_default_beer_taps()

    beer_id = request.form.get("beer_id", type=int)
    bar_choice = request.form.get("bar_choice", "").strip()

    tap_main_id = request.form.get("tap_main_id", type=int)
    tap_lower_id = request.form.get("tap_lower_id", type=int)

    tapped_on_raw = request.form.get("tapped_on", "").strip()
    notes = request.form.get("notes", "").strip()

    if not beer_id:
        flash("Select a beer first.", "error")
        return redirect("/beers/dashboard")

    beer = Beer.query.get(beer_id)
    if not beer:
        flash("That beer was not found.", "error")
        return redirect("/beers/dashboard")

    tapped_on = None
    if tapped_on_raw:
        try:
            tapped_on = datetime.strptime(tapped_on_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Tapped On date is invalid.", "error")
            return redirect("/beers/dashboard")

    def assign_to_tap(tap_id, expected_bar):
        if not tap_id:
            return False, f"Select a {expected_bar} bar tap."

        tap = BeerTap.query.get(tap_id)
        if not tap:
            return False, "Tap not found."
        if tap.bar_location != expected_bar:
            return False, "Selected tap does not match the bar."

        tap.beer_id = beer.id
        tap.percent_remaining = 100  # default full when assigned
        tap.tapped_on = tapped_on
        tap.notes = notes if notes else None
        return True, None

    if bar_choice == "main":
        ok, err = assign_to_tap(tap_main_id, "main")
        if not ok:
            flash(err, "error")
            return redirect("/beers/dashboard")

    elif bar_choice == "lower":
        ok, err = assign_to_tap(tap_lower_id, "lower")
        if not ok:
            flash(err, "error")
            return redirect("/beers/dashboard")

    elif bar_choice == "both":
        ok1, err1 = assign_to_tap(tap_main_id, "main")
        ok2, err2 = assign_to_tap(tap_lower_id, "lower")
        if not ok1 or not ok2:
            flash(err1 or err2 or "Select both taps.", "error")
            return redirect("/beers/dashboard")

    else:
        flash("Choose Main Bar, Lower Bar, or Both Bars.", "error")
        return redirect("/beers/dashboard")

    db.session.commit()
    flash("Beer assigned to tap(s).", "success")
    return redirect("/beers/dashboard")


@app.post("/beer-taps/assign")
def beer_taps_assign():
    beer_id = request.form.get("beer_id", type=int)
    assign_where = request.form.get("assign_where", default="main")
    default_percent = request.form.get("default_percent", type=int)

    main_tap_id = request.form.get("main_tap_id", type=int)
    lower_tap_id = request.form.get("lower_tap_id", type=int)

    if not beer_id:
        flash("Select a beer.", "error")
        return redirect("/beers/dashboard")

    if default_percent is None:
        default_percent = 100
    default_percent = max(0, min(100, default_percent))

    # Helper to assign a beer to a specific tap
    def assign_to_tap(tap_id):
        if not tap_id:
            return False, "Select a tap."
        tap = BeerTap.query.get(tap_id)
        if not tap:
            return False, "Tap not found."

        tap.beer_id = beer_id
        tap.percent_remaining = default_percent
        tap.tapped_on = datetime.utcnow().date()
        tap.updated_at = datetime.utcnow()
        return True, None

    # Assign based on selection
    if assign_where == "main":
        ok, err = assign_to_tap(main_tap_id)
        if not ok:
            flash(err, "error")
            return redirect("/beers/dashboard")

    elif assign_where == "lower":
        ok, err = assign_to_tap(lower_tap_id)
        if not ok:
            flash(err, "error")
            return redirect("/beers/dashboard")

    elif assign_where == "both":
        ok1, err1 = assign_to_tap(main_tap_id)
        ok2, err2 = assign_to_tap(lower_tap_id)
        if not ok1 or not ok2:
            flash(err1 or err2 or "Select taps for both bars.", "error")
            return redirect("/beers/dashboard")

    else:
        flash("Invalid bar selection.", "error")
        return redirect("/beers/dashboard")

    db.session.commit()
    flash("Beer assigned to tap successfully.", "success")
    return redirect("/beers/dashboard")


@app.post("/beer-taps/bulk-update")
def beer_taps_bulk_update():
    # Expects inputs like name="percent_<tap_id>"
    taps = BeerTap.query.all()
    updated = 0

    for tap in taps:
        key = f"percent_{tap.id}"
        if key not in request.form:
            continue

        # Only update if tap currently has a beer
        if not tap.beer_id:
            continue

        val = request.form.get(key, type=int)
        if val is None:
            continue

        val = max(0, min(100, val))
        tap.percent_remaining = val
        tap.updated_at = datetime.utcnow()
        updated += 1

    db.session.commit()
    flash(f"Saved changes for {updated} tap(s).", "success")
    return redirect("/beers/dashboard")


@app.post("/beer-taps/<int:tap_id>/clear")
def beer_taps_clear(tap_id):
    tap = BeerTap.query.get_or_404(tap_id)

    # hard clear
    tap.beer_id = None
    tap.percent_remaining = 0
    tap.tapped_on = None
    tap.notes = None
    tap.updated_at = datetime.utcnow()

    db.session.add(tap)
    db.session.commit()

    # ensure next request cannot reuse a cached relationship/object
    db.session.expire_all()

    flash("Beer removed from tap.", "success")
    return redirect("/beers/dashboard")

@app.post("/beer-taps/<int:tap_id>/clear")
def clear_beer_tap(tap_id):
    tap = BeerTap.query.get_or_404(tap_id)

    # Clear what’s on the tap
    tap.beer_id = None
    tap.percent_remaining = 0          # optional: set to 0 when empty
    tap.tapped_on = None
    tap.notes = None
    tap.updated_at = datetime.utcnow()

    db.session.commit()
    flash("Tap cleared.", "success")
    return redirect(url_for("beers_dashboard"))

# ============================================================
# DASHBOARD + CATEGORIES
# ============================================================
CATEGORY_ORDER = ["Food", "Alcohol", "NA Beverages"]

# ============================================================
# DASHBOARD + CATEGORIES
# ============================================================
@app.get("/dashboard")
def dashboard_alias():
    return redirect("/")

@app.get("/")
def dashboard():
    guard = require_view_access()
    if guard:
        return guard

    counts = db.session.query(Item.category, func.count(Item.id)).group_by(Item.category).all()
    counts_map = {c: int(n) for (c, n) in counts}

    categories = [{"name": cat, "count": counts_map.get(cat, 0)} for cat in CATEGORY_ORDER]
    extras = sorted([c for c in counts_map.keys() if c not in CATEGORY_ORDER])
    for cat in extras:
        categories.append({"name": cat, "count": counts_map.get(cat, 0)})

    # Ordering Analytics
    total_items = Item.query.count()
    low_stock_items = Item.query.filter(Item.on_hand_count < 5).count()
    items_needing_order = Item.query.filter(Item.on_hand_count == 0).count()
    average_stock = db.session.query(func.avg(Item.on_hand_count)).scalar() or 0
    
    order_analytics = {
        "total_items": total_items,
        "low_stock": low_stock_items,
        "out_of_stock": items_needing_order,
        "avg_stock": round(float(average_stock), 1)
    }

    return render_template("dashboard.html", categories=categories, order_analytics=order_analytics)

@app.get("/category/<path:category_name>")
def category_view(category_name: str):
    guard = require_view_access()
    if guard:
        return guard

    category_name = (category_name or "").strip()
    if not category_name:
        return redirect("/")

    q = (request.args.get("q") or "").strip()
    query = Item.query.filter(Item.category == category_name)
    if q:
        query = query.filter(Item.name.ilike(f"%{q}%"))
    items = query.order_by(Item.name.asc()).all()

    # Get all categories for sidebar
    counts = db.session.query(Item.category, func.count(Item.id)).group_by(Item.category).all()
    counts_map = {c: int(n) for (c, n) in counts}
    all_categories = [cat for cat in CATEGORY_ORDER if counts_map.get(cat, 0) > 0]
    extras = sorted([c for c in counts_map.keys() if c not in CATEGORY_ORDER and counts_map.get(c, 0) > 0])
    all_categories.extend(extras)

    return render_template("category.html", category_name=category_name, items=items, q=q, all_categories=all_categories)


# ============================================================
# ITEMS (single /items route only - fixes your duplicate)
# ============================================================
# ============================================================
# ITEMS ROUTES
# ============================================================
@app.get("/items")
def items_all():
    guard = require_view_access()
    if guard:
        return guard

    q = (request.args.get("q") or "").strip()

    query = Item.query
    if q:
        query = query.filter(Item.name.ilike(f"%{q}%"))

    items = query.order_by(Item.category.asc(), Item.name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    return render_template("items.html", items=items, suppliers=suppliers, q=q)

@app.get("/api/beers")
def api_get_beers():
    """API endpoint to get all beers as JSON"""
    guard = require_view_access()
    if guard:
        return guard
    
    beers = Beer.query.order_by(Beer.name.asc()).all()
    beers_data = [
        {
            'id': b.id,
            'name': b.name,
            'brewery': b.brewery,
            'style': b.style,
            'abv': b.abv,
            'cost': b.cost,
            'price': b.price,
            'keg_size': b.keg_size,
            'cups_per_keg': b.cups_per_keg,
            'on_hand_kegs': b.on_hand_kegs
        }
        for b in beers
    ]
    return jsonify(beers_data)

@app.get("/order")
def order_items():
    guard = require_view_access()
    if guard:
        return guard

    q = (request.args.get("q") or "").strip()
    category_filter = (request.args.get("category") or "").strip()

    query = Item.query
    if q:
        query = query.filter(Item.name.ilike(f"%{q}%"))
    if category_filter:
        query = query.filter(Item.category == category_filter)

    items = query.order_by(Item.category.asc(), Item.name.asc()).all()
    categories = db.session.query(Item.category).distinct().order_by(Item.category.asc()).all()
    categories = [c[0] for c in categories if c[0]]

    return render_template("order.html", items=items, categories=categories, q=q, category_filter=category_filter)

@app.post("/items/<int:item_id>/update-onhand")
def update_item_onhand(item_id):
    guard = require_view_access()
    if guard:
        return guard

    try:
        item = Item.query.get_or_404(item_id)
        data = request.get_json()
        
        if "on_hand_count" in data:
            new_count = int(data["on_hand_count"])
            old_count = item.on_hand_count
            difference = new_count - old_count
            
            if difference > 0:
                # Increase: create new InventoryLot records
                max_lot = db.session.query(func.max(InventoryLot.lot_number)).filter(
                    InventoryLot.item_id == item_id
                ).scalar()
                next_lot_number = (max_lot + 1) if max_lot else 1
                
                # Create individual InventoryLot records for each new box
                for i in range(difference):
                    lot = InventoryLot(
                        item_id=item_id,
                        lot_number=next_lot_number + i,
                        quantity=1.0,  # One box per lot
                        storage="cooler",  # Default storage location
                        state="raw",  # Default state for items
                        received_date=date.today(),
                        is_consumed=False
                    )
                    db.session.add(lot)
            elif difference < 0:
                # Decrease: mark the most recent unconsumed lots as consumed
                lots_to_consume = (
                    InventoryLot.query
                    .filter(
                        InventoryLot.item_id == item_id,
                        InventoryLot.is_consumed == False
                    )
                    .order_by(InventoryLot.id.desc())
                    .limit(abs(difference))
                    .all()
                )
                for lot in lots_to_consume:
                    lot.is_consumed = True
            
            item.on_hand_count = new_count
        
        db.session.commit()
        return {"success": True, "message": "On-hand count updated"}
    except Exception as e:
        db.session.rollback()
        print(f"Error updating on-hand count: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.post("/order/save")
def save_order():
    guard = require_view_access()
    if guard:
        return guard

    try:
        data = request.get_json()
        item_id = data.get("item_id")
        quantity = data.get("quantity", 0)
        order_date = data.get("order_date")
        notes = data.get("notes", "")

        if not item_id:
            return {"error": "Invalid item"}, 400

        # Convert order_date string to date object if provided
        if order_date:
            try:
                order_date = datetime.strptime(order_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                order_date = None

        # Check if order already exists for this item
        existing_order = Order.query.filter(
            Order.item_id == item_id,
            Order.status == "pending"
        ).first()

        if existing_order:
            existing_order.quantity = quantity
            existing_order.order_date = order_date
            existing_order.notes = notes
            existing_order.updated_at = utcnow()
        else:
            new_order = Order(
                item_id=item_id,
                quantity=quantity,
                order_date=order_date,
                notes=notes
            )
            db.session.add(new_order)

        db.session.commit()
        return {"success": True, "message": "Order saved"}
    except Exception as e:
        db.session.rollback()
        print(f"Error in save_order: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.post("/beer-order/save")
def save_beer_order():
    guard = require_view_access()
    if guard:
        return guard

    try:
        data = request.get_json()
        beer_id = data.get("beer_id")
        quantity = data.get("quantity", 0)
        order_date = data.get("order_date")
        notes = data.get("notes", "")

        if not beer_id:
            return {"error": "Invalid beer"}, 400

        # Convert order_date string to date object if provided
        if order_date:
            try:
                order_date = datetime.strptime(order_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                order_date = None

        # Check if order already exists for this beer
        existing_order = BeerOrder.query.filter(
            BeerOrder.beer_id == beer_id,
            BeerOrder.status == "pending"
        ).first()

        if existing_order:
            existing_order.quantity = quantity
            existing_order.order_date = order_date
            existing_order.notes = notes
            existing_order.updated_at = utcnow()
        else:
            new_order = BeerOrder(
                beer_id=beer_id,
                quantity=quantity,
                order_date=order_date,
                notes=notes
            )
            db.session.add(new_order)

        db.session.commit()
        return {"success": True, "message": "Beer order saved"}
    except Exception as e:
        db.session.rollback()
        print(f"Error in save_beer_order: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.get("/orders")
def view_orders():
    guard = require_view_access()
    if guard:
        return guard

    status_filter = request.args.get("status", "pending")
    
    query = Order.query.join(Item)
    if status_filter and status_filter != "all":
        query = query.filter(Order.status == status_filter)
    
    orders = query.order_by(Order.created_at.desc()).all()

    # Get beer orders
    beer_query = BeerOrder.query.join(Beer)
    if status_filter and status_filter != "all":
        beer_query = beer_query.filter(BeerOrder.status == status_filter)
    
    beer_orders = beer_query.order_by(BeerOrder.created_at.desc()).all()
    
    return render_template("orders.html", orders=orders, beer_orders=beer_orders, status_filter=status_filter)

@app.get("/order/<int:order_id>/edit")
def edit_order(order_id):
    guard = require_view_access()
    if guard:
        return guard
    
    order = Order.query.get_or_404(order_id)
    return render_template("edit_order.html", order=order)

@app.post("/order/<int:order_id>/update")
def update_order(order_id):
    guard = require_view_access()
    if guard:
        return guard

    try:
        order = Order.query.get_or_404(order_id)
        data = request.get_json()
        old_status = order.status

        if "quantity" in data:
            order.quantity = int(data["quantity"])
        if "order_date" in data:
            order_date = data["order_date"]
            if order_date:
                try:
                    order.order_date = datetime.strptime(order_date, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    order.order_date = None
            else:
                order.order_date = None
        if "notes" in data:
            order.notes = data["notes"]
        if "status" in data:
            order.status = data["status"]

        # If status is changing to "received", add the order quantity to the item's on_hand_count and create individual lots
        if old_status != "received" and order.status == "received":
            item = Item.query.get(order.item_id)
            if item:
                item.on_hand_count += order.quantity
                
                # Get the next lot number for this item
                max_lot = db.session.query(func.max(InventoryLot.lot_number)).filter(
                    InventoryLot.item_id == order.item_id
                ).scalar()
                next_lot_number = (max_lot + 1) if max_lot else 1
                
                # Create individual InventoryLot records for each box
                for i in range(order.quantity):
                    lot = InventoryLot(
                        item_id=order.item_id,
                        lot_number=next_lot_number + i,
                        quantity=1.0,  # One box per lot
                        storage="cooler",  # Default storage location
                        state="raw",  # Default state for items
                        received_date=date.today(),
                        is_consumed=False
                    )
                    db.session.add(lot)

        order.updated_at = utcnow()
        db.session.commit()
        return {"success": True, "message": "Order updated"}
    except Exception as e:
        db.session.rollback()
        print(f"Error in update_order: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.post("/order/<int:order_id>/delete")
def delete_order(order_id):
    guard = require_view_access()
    if guard:
        return guard

    try:
        order = Order.query.get_or_404(order_id)
        db.session.delete(order)
        db.session.commit()
        return {"success": True, "message": "Order deleted"}
    except Exception as e:
        db.session.rollback()
        return {"error": str(e)}, 500

@app.get("/items/bulk")
def items_bulk():
    guard = require_inventory_edit()
    if guard:
        return guard

    items = Item.query.order_by(Item.category.asc(), Item.name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    
    return render_template("items_bulk.html", items=items, suppliers=suppliers)

@app.post("/items/bulk-save")
def items_bulk_save():
    guard = require_inventory_edit()
    if guard:
        return guard

    data = request.get_json()
    if not data or "items" not in data:
        return {"error": "No items data"}, 400

    try:
        for item_data in data["items"]:
            item_id = item_data.get("id")
            item = Item.query.get(item_id)
            if not item:
                continue

            # Update basic fields
            if "name" in item_data:
                item.name = item_data["name"].strip()
            if "category" in item_data:
                item.category = item_data["category"].strip()
            if "unit" in item_data:
                item.unit = item_data["unit"].strip()
            if "supplier_id" in item_data:
                supplier_id = item_data["supplier_id"]
                item.supplier_id = int(supplier_id) if supplier_id and str(supplier_id).isdigit() else None

            # Update items-specific fields
            if "on_hand_count" in item_data:
                try:
                    item.on_hand_count = int(item_data["on_hand_count"])
                except (ValueError, TypeError):
                    pass

            # Update generic-specific fields
            if "default_units_per_box" in item_data:
                try:
                    item.default_units_per_box = int(item_data["default_units_per_box"])
                except (ValueError, TypeError):
                    pass

            if "multiplier" in item_data:
                try:
                    item.multiplier = float(item_data["multiplier"])
                except (ValueError, TypeError):
                    pass

            # Update shelf-life fields
            if "raw_freezer_days" in item_data:
                try:
                    item.raw_freezer_days = int(item_data["raw_freezer_days"]) if item_data["raw_freezer_days"] else None
                except (ValueError, TypeError):
                    pass
            if "raw_cooler_days" in item_data:
                try:
                    item.raw_cooler_days = int(item_data["raw_cooler_days"]) if item_data["raw_cooler_days"] else None
                except (ValueError, TypeError):
                    pass
            if "raw_out_days" in item_data:
                try:
                    item.raw_out_days = int(item_data["raw_out_days"]) if item_data["raw_out_days"] else None
                except (ValueError, TypeError):
                    pass

        db.session.commit()
        return {"success": True, "message": f"Updated {len(data['items'])} items"}
    except Exception as e:
        db.session.rollback()
        print(f"Error bulk saving items: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

@app.get("/items/new")
def item_new():
    guard = require_inventory_edit()
    if guard:
        return guard

    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    return render_template("item_form.html", item=None, suppliers=suppliers)

@app.post("/items/new")
def item_new_post():
    guard = require_inventory_edit()
    if guard:
        return guard

    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "Food").strip()
    unit = (request.form.get("unit") or "each").strip()
    prep_type = (request.form.get("prep_type") or "generic").strip()
    sales_mode = (request.form.get("sales_mode") or "simple").strip()

    supplier_id_raw = (request.form.get("supplier_id") or "").strip()
    supplier_id = int(supplier_id_raw) if supplier_id_raw.isdigit() else None

    # ✅ NEW: default units per box (used to autofill Receive/Boxes)
    default_units_per_box = to_int(request.form.get("default_units_per_box"), 0)
    if default_units_per_box is None or default_units_per_box < 0:
        default_units_per_box = 0

    # ✅ NEW: multiplier for reconcile calculations - safe conversion
    multiplier_val = request.form.get("multiplier", "").strip()
    multiplier = None
    if multiplier_val:
        try:
            multiplier = float(multiplier_val)
        except (ValueError, TypeError):
            multiplier = None
    
    # ✅ NEW: on_hand_count for items prep type
    on_hand_count = to_int(request.form.get("on_hand_count"), 0)

    if not name:
        flash("Item name is required.", "error")
        return redirect("/items/new")

    existing = Item.query.filter(func.lower(Item.name) == name.lower()).first()
    if existing:
        flash("That item already exists.", "error")
        return redirect("/items/new")

    raw_freezer_days = to_int(request.form.get("raw_freezer_days"), 0) or None
    raw_cooler_days = to_int(request.form.get("raw_cooler_days"), 0) or None
    raw_out_days = to_int(request.form.get("raw_out_days"), 0) or None

    prepped_freezer_days = to_int(request.form.get("prepped_freezer_days"), 0) or None
    prepped_cooler_days = to_int(request.form.get("prepped_cooler_days"), 0) or None
    prepped_out_days = to_int(request.form.get("prepped_out_days"), 0) or None

    pack1_label = (request.form.get("pack1_label") or "Single (10)").strip()
    pack1_mult = to_int(request.form.get("pack1_mult"), 10)
    pack2_label = (request.form.get("pack2_label") or "Double (20)").strip()
    pack2_mult = to_int(request.form.get("pack2_mult"), 20)
    pack3_label = (request.form.get("pack3_label") or "Room 120 Single (10)").strip()
    pack3_mult = to_int(request.form.get("pack3_mult"), 10)
    pack4_label = (request.form.get("pack4_label") or "Room 120 Double (20)").strip()
    pack4_mult = to_int(request.form.get("pack4_mult"), 20)

    item = Item(
        name=name,
        category=category,
        unit=unit,
        prep_type=prep_type,
        sales_mode=sales_mode,
        supplier_id=supplier_id,

        # ✅ NEW FIELDS SAVED HERE
        default_units_per_box=default_units_per_box,
        multiplier=multiplier,
        on_hand_count=on_hand_count,

        raw_freezer_days=raw_freezer_days,
        raw_cooler_days=raw_cooler_days,
        raw_out_days=raw_out_days,
        prepped_freezer_days=prepped_freezer_days,
        prepped_cooler_days=prepped_cooler_days,
        prepped_out_days=prepped_out_days,
        pack1_label=pack1_label,
        pack1_mult=pack1_mult,
        pack2_label=pack2_label,
        pack2_mult=pack2_mult,
        pack3_label=pack3_label,
        pack3_mult=pack3_mult,
        pack4_label=pack4_label,
        pack4_mult=pack4_mult,
    )

    db.session.add(item)
    db.session.flush()

    audit_log(
        action="create",
        entity_type="Item",
        entity_id=item.id,
        message="Item created",
        details={
            "name": item.name,
            "category": item.category,
            "supplier_id": item.supplier_id,
            "default_units_per_box": item.default_units_per_box,
        }
    )

    try:
        db.session.commit()
        flash("Item created.", "success")
        return redirect(f"/items/{item.id}")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating item: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error creating item: {str(e)}", "error")
        return redirect("/items/new")


@app.get("/items/<int:item_id>/edit")
def item_edit(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    return render_template("item_form.html", item=item, suppliers=suppliers)

@app.post("/items/<int:item_id>/edit")
def item_edit_post(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    before = {
        "name": item.name,
        "category": item.category,
        "unit": item.unit,
        "prep_type": item.prep_type,
        "sales_mode": item.sales_mode,
        "supplier_id": item.supplier_id,
        "raw_freezer_days": item.raw_freezer_days,
        "raw_cooler_days": item.raw_cooler_days,
        "raw_out_days": item.raw_out_days,
        "prepped_freezer_days": item.prepped_freezer_days,
        "prepped_cooler_days": item.prepped_cooler_days,
        "prepped_out_days": item.prepped_out_days,
        "pack1_label": item.pack1_label, "pack1_mult": item.pack1_mult,
        "pack2_label": item.pack2_label, "pack2_mult": item.pack2_mult,
        "pack3_label": item.pack3_label, "pack3_mult": item.pack3_mult,
        "pack4_label": item.pack4_label, "pack4_mult": item.pack4_mult,
        "default_units_per_box": item.default_units_per_box,
        "multiplier": item.multiplier,
        "on_hand_count": item.on_hand_count,
    }

    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "Food").strip()
    unit = (request.form.get("unit") or "each").strip()
    prep_type = (request.form.get("prep_type") or "generic").strip()
    sales_mode = (request.form.get("sales_mode") or "simple").strip()

    supplier_id_raw = (request.form.get("supplier_id") or "").strip()
    supplier_id = int(supplier_id_raw) if supplier_id_raw.isdigit() else None

    if not name:
        flash("Item name is required.", "error")
        return redirect(f"/items/{item.id}/edit")

    existing = Item.query.filter(func.lower(Item.name) == name.lower(), Item.id != item.id).first()
    if existing:
        flash("Another item already uses that name.", "error")
        return redirect(f"/items/{item.id}/edit")

    item.name = name
    item.category = category
    item.unit = unit
    item.prep_type = prep_type
    item.sales_mode = sales_mode
    item.supplier_id = supplier_id

    item.raw_freezer_days = to_int(request.form.get("raw_freezer_days"), 0) or None
    item.raw_cooler_days = to_int(request.form.get("raw_cooler_days"), 0) or None
    item.raw_out_days = to_int(request.form.get("raw_out_days"), 0) or None

    item.prepped_freezer_days = to_int(request.form.get("prepped_freezer_days"), 0) or None
    item.prepped_cooler_days = to_int(request.form.get("prepped_cooler_days"), 0) or None
    item.prepped_out_days = to_int(request.form.get("prepped_out_days"), 0) or None

    item.pack1_label = (request.form.get("pack1_label") or "Single (10)").strip()
    item.pack1_mult = to_int(request.form.get("pack1_mult"), 10)
    item.pack2_label = (request.form.get("pack2_label") or "Double (20)").strip()
    item.pack2_mult = to_int(request.form.get("pack2_mult"), 20)
    item.pack3_label = (request.form.get("pack3_label") or "Room 120 Single (10)").strip()
    item.pack3_mult = to_int(request.form.get("pack3_mult"), 10)
    item.pack4_label = (request.form.get("pack4_label") or "Room 120 Double (20)").strip()
    item.pack4_mult = to_int(request.form.get("pack4_mult"), 20)

    # ✅ GENERIC BOX SETTINGS
    item.default_units_per_box = to_int(request.form.get("default_units_per_box"), 0) or None
    
    # Safe multiplier conversion
    multiplier_val = request.form.get("multiplier", "").strip()
    if multiplier_val:
        try:
            item.multiplier = float(multiplier_val)
        except (ValueError, TypeError):
            item.multiplier = None
    else:
        item.multiplier = None
    
    # ✅ ITEMS SETTINGS
    item.on_hand_count = to_int(request.form.get("on_hand_count"), 0)

    after = {
        "name": item.name,
        "category": item.category,
        "unit": item.unit,
        "prep_type": item.prep_type,
        "sales_mode": item.sales_mode,
        "supplier_id": item.supplier_id,
        "raw_freezer_days": item.raw_freezer_days,
        "raw_cooler_days": item.raw_cooler_days,
        "raw_out_days": item.raw_out_days,
        "prepped_freezer_days": item.prepped_freezer_days,
        "prepped_cooler_days": item.prepped_cooler_days,
        "prepped_out_days": item.prepped_out_days,
        "pack1_label": item.pack1_label, "pack1_mult": item.pack1_mult,
        "pack2_label": item.pack2_label, "pack2_mult": item.pack2_mult,
        "pack3_label": item.pack3_label, "pack3_mult": item.pack3_mult,
        "pack4_label": item.pack4_label, "pack4_mult": item.pack4_mult,
        "default_units_per_box": item.default_units_per_box,
        "multiplier": item.multiplier,
        "on_hand_count": item.on_hand_count,
    }

    audit_log(
        action="update",
        entity_type="Item",
        entity_id=item.id,
        message="Item updated",
        details={"before": before, "after": after}
    )

    try:
        db.session.commit()
        flash("Item updated.", "success")
        return redirect(f"/items/{item.id}")
    except Exception as e:
        db.session.rollback()
        print(f"Error updating item: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error saving item: {str(e)}", "error")
        return redirect(f"/items/{item.id}/edit")

@app.post("/items/<int:item_id>/delete")
def item_delete(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    lots_count = InventoryLot.query.filter(InventoryLot.item_id == item.id).count()
    prep_count = PrepBatch.query.filter(PrepBatch.item_id == item.id).count()
    rec_count = ReconcileRecord.query.filter(ReconcileRecord.item_id == item.id).count()

    if lots_count > 0 or prep_count > 0 or rec_count > 0:
        flash("Can't delete this item because it already has inventory/prep/reconcile history.", "error")
        return redirect("/items")

    audit_log(
        action="delete",
        entity_type="Item",
        entity_id=item.id,
        message="Item deleted",
        details={"name": item.name, "category": item.category}
    )

    db.session.delete(item)
    db.session.commit()
    flash("Item deleted.", "success")
    return redirect("/items")


# ============================================================
# ITEM HUB
# ============================================================
# ============================================================
# ITEM HUB (adds LIVE inventory counters)
# ============================================================
# ============================================================
# ITEM HUB
# ============================================================
@app.get("/items/<int:item_id>")
def item_hub(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    # ✅ Get all non-consumed lots for calculations
    all_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.is_consumed == False
        )
        .all()
    )

    # ✅ Calculate totals by storage location
    totals = {
        "freezer_boxes": 0,
        "cooler_boxes": 0,
        "out_boxes": 0,
        "freezer_units": 0,
        "cooler_units": 0,
        "out_units": 0,
    }

    units_per_box = int(item.default_units_per_box or 1)

    for lot in all_lots:
        # Use actual quantity without rounding (preserves fractional boxes = fractional units)
        lot_quantity = lot.quantity or 1.0
        
        # For display: box_count for box totals
        box_count = int(round(lot_quantity))
        if box_count < 1:
            box_count = 1
        
        # For units: calculate from actual quantity (don't round quantity first)
        lot_units = int(round(lot_quantity * units_per_box))
        
        if lot.storage == "freezer":
            totals["freezer_boxes"] += box_count
            totals["freezer_units"] += lot_units
        elif lot.storage == "cooler":
            totals["cooler_boxes"] += box_count
            totals["cooler_units"] += lot_units
        else:
            totals["out_boxes"] += box_count
            totals["out_units"] += lot_units

    cooler_boxes_available = totals["cooler_boxes"]
    cooler_units_available = totals["cooler_units"]

    # ✅ Units available = total prepped units not consumed (what you can sell from)
    prepped_units_available = (
        db.session.query(func.coalesce(func.sum(InventoryLot.count_units), 0))
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.state == "prepped",
            InventoryLot.storage == "cooler",
            InventoryLot.is_consumed == False
        )
        .scalar()
    )
    try:
        prepped_units_available = int(prepped_units_available or 0)
    except Exception:
        prepped_units_available = 0

    # ✅ Expiring soon = cooler lots (raw or prepped) that have an expiration date within next 2 days
    #    (and not consumed)
    cooler_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.storage == "cooler",
            InventoryLot.is_consumed == False
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    expiring_soon = []
    for lot in cooler_lots:
        exp = compute_lot_expiration(item, lot)
        left = days_left(exp)
        if exp and left is not None and 0 <= left <= 4:
            expiring_soon.append({
                "lot": lot,
                "expires_on": exp,
                "days_left": left,
                "units": int(lot.count_units or 0)
            })

    # Sort by soonest expiration first, then limit list for display
    expiring_soon = sorted(expiring_soon, key=lambda r: (r["days_left"], r["lot"].id))[:8]
    expiring_soon_count = len(expiring_soon)

    return render_template(
        "item_hub.html",
        item=item,
        prepped_units_available=prepped_units_available,
        cooler_boxes_available=cooler_boxes_available,
        cooler_units_available=cooler_units_available,
        totals=totals,
        expiring_soon=expiring_soon,
        expiring_soon_count=expiring_soon_count
    )


# ============================================================
# ITEMS STOCK ADJUSTMENT (for "items" prep type)
# ============================================================
@app.post("/items/<int:item_id>/adjust-stock")
def adjust_item_stock(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    
    # Check if this is an "items" prep type
    if (item.prep_type or "").lower() != "items":
        flash("Stock adjustment only available for 'items' prep type.", "error")
        return redirect(f"/items/{item_id}")
    
    adjustment = to_int(request.form.get("adjustment"), 0)
    notes = (request.form.get("notes") or "").strip() or None
    
    old_count = item.on_hand_count or 0
    new_count = max(0, old_count + adjustment)
    item.on_hand_count = new_count
    
    audit_log(
        action="stock_adjust",
        entity_type="Item",
        entity_id=item.id,
        message=f"Stock adjusted for {item.name}",
        details={
            "item_id": item.id,
            "old_count": old_count,
            "adjustment": adjustment,
            "new_count": new_count,
            "notes": notes,
        }
    )
    
    db.session.commit()
    flash(f"Stock updated: {old_count} → {new_count}", "success")
    return redirect(f"/items/{item_id}")


# ============================================================
# ADMIN BACKUP
# ============================================================
@app.route("/admin/backup/download", methods=["POST"])
def admin_backup_download():
    # TODO: Apply your admin check here (same pattern you use elsewhere)
    # if session.get("role") != "admin": abort(403)

    payload = _export_db_to_dict()
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"inventory_backup_{stamp}.json"

    bio = BytesIO(raw)
    bio.seek(0)
    return send_file(
        bio,
        mimetype="application/json",
        as_attachment=True,
        download_name=filename
    )

@app.route("/admin/backup/import", methods=["POST"])
def admin_backup_import():
    # TODO: Apply your admin check here (same pattern you use elsewhere)
    # if session.get("role") != "admin": abort(403)

    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("No backup file selected.", "error")
        return redirect(request.referrer or url_for("home"))

    try:
        payload = json.loads(f.read().decode("utf-8"))
        _import_db_from_dict(payload)
        db.session.commit()
        flash("Import completed successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {str(e)}", "error")

    return redirect(request.referrer or url_for("home"))

# ============================================================
# SUPPLIERS
# ============================================================
# ============================================================
# SUPPLIERS ROUTES
# ============================================================
@app.get("/suppliers")
def suppliers():
    guard = require_view_access()
    if guard:
        return guard

    q = (request.args.get("q") or "").strip()
    query = Supplier.query
    if q:
        query = query.filter(Supplier.name.ilike(f"%{q}%"))
    suppliers = query.order_by(Supplier.name.asc()).all()
    return render_template("suppliers.html", suppliers=suppliers, q=q)

@app.get("/suppliers/new")
def supplier_new():
    guard = require_inventory_edit()
    if guard:
        return guard
    return render_template("supplier_form.html", supplier=None)

@app.post("/suppliers/new")
def supplier_new_post():
    guard = require_inventory_edit()
    if guard:
        return guard

    name = (request.form.get("name") or "").strip()
    contact_name = (request.form.get("contact_name") or "").strip() or None
    phone = (request.form.get("phone") or "").strip() or None
    email = (request.form.get("email") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    if not name:
        flash("Supplier name is required.", "error")
        return redirect("/suppliers/new")

    existing = Supplier.query.filter(func.lower(Supplier.name) == name.lower()).first()
    if existing:
        flash("That supplier already exists.", "error")
        return redirect("/suppliers/new")

    s = Supplier(name=name, contact_name=contact_name, phone=phone, email=email, notes=notes)
    db.session.add(s)
    db.session.flush()

    audit_log(
        action="create",
        entity_type="Supplier",
        entity_id=s.id,
        message="Supplier created",
        details={"name": s.name, "contact_name": s.contact_name, "phone": s.phone, "email": s.email}
    )

    db.session.commit()
    flash("Supplier added.", "success")
    return redirect("/suppliers")

@app.get("/suppliers/<int:supplier_id>/edit")
def supplier_edit(supplier_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard
    supplier = Supplier.query.get_or_404(supplier_id)
    return render_template("supplier_form.html", supplier=supplier)

@app.post("/suppliers/<int:supplier_id>/edit")
def supplier_edit_post(supplier_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    supplier = Supplier.query.get_or_404(supplier_id)
    before = {"name": supplier.name, "contact_name": supplier.contact_name, "phone": supplier.phone, "email": supplier.email, "notes": supplier.notes}

    name = (request.form.get("name") or "").strip()
    contact_name = (request.form.get("contact_name") or "").strip() or None
    phone = (request.form.get("phone") or "").strip() or None
    email = (request.form.get("email") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    if not name:
        flash("Supplier name is required.", "error")
        return redirect(f"/suppliers/{supplier.id}/edit")

    existing = Supplier.query.filter(func.lower(Supplier.name) == name.lower(), Supplier.id != supplier.id).first()
    if existing:
        flash("Another supplier already uses that name.", "error")
        return redirect(f"/suppliers/{supplier.id}/edit")

    supplier.name = name
    supplier.contact_name = contact_name
    supplier.phone = phone
    supplier.email = email
    supplier.notes = notes

    after = {"name": supplier.name, "contact_name": supplier.contact_name, "phone": supplier.phone, "email": supplier.email, "notes": supplier.notes}

    audit_log(
        action="update",
        entity_type="Supplier",
        entity_id=supplier.id,
        message="Supplier updated",
        details={"before": before, "after": after}
    )

    db.session.commit()
    flash("Supplier updated.", "success")
    return redirect("/suppliers")

@app.post("/suppliers/<int:supplier_id>/delete")
def supplier_delete(supplier_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    supplier = Supplier.query.get_or_404(supplier_id)
    linked_count = Item.query.filter(Item.supplier_id == supplier.id).count()
    if linked_count > 0:
        flash(f"Can't delete. {linked_count} item(s) are linked to this supplier.", "error")
        return redirect("/suppliers")

    audit_log(
        action="delete",
        entity_type="Supplier",
        entity_id=supplier.id,
        message="Supplier deleted",
        details={"name": supplier.name}
    )

    db.session.delete(supplier)
    db.session.commit()
    flash("Supplier deleted.", "success")
    return redirect("/suppliers")


# ============================================================
# USERS
# ============================================================
# ============================================================
# USERS ROUTES
# ============================================================
@app.get("/users")
def users_page():
    guard = require_admin()
    if guard:
        return guard
    users = User.query.order_by(User.created_at.desc(), User.id.desc()).all()
    roles = Role.query.order_by(Role.name).all()
    return render_template("users.html", users=users, roles=roles)

@app.get("/users/new")
def users_new():
    guard = require_admin()
    if guard:
        return guard
    roles = Role.query.order_by(Role.name).all()
    return render_template("user_form.html", user=None, roles=roles)

@app.get("/users/<int:user_id>/edit")
def users_edit(user_id: int):
    guard = require_admin()
    if guard:
        return guard
    user = User.query.get_or_404(user_id)
    roles = Role.query.order_by(Role.name).all()
    return render_template("user_form.html", user=user, roles=roles)

@app.post("/users/create")
def users_create():
    guard = require_admin()
    if guard:
        return guard

    username = (request.form.get("username") or "").strip()
    first_name = (request.form.get("first_name") or "").strip() or None
    last_name = (request.form.get("last_name") or "").strip() or None
    role_id_str = (request.form.get("role_id") or "").strip()
    password = (request.form.get("password") or "").strip()

    if not username:
        flash("Username is required.", "error")
        return redirect("/users")

    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect("/users")

    if not role_id_str:
        flash("Role is required.", "error")
        return redirect("/users")

    # Validate and get role_id
    try:
        role_id = int(role_id_str)
        role = Role.query.get(role_id)
        if not role:
            flash("Invalid role selected.", "error")
            return redirect("/users")
    except (ValueError, TypeError):
        flash("Invalid role selected.", "error")
        return redirect("/users")

    exists = User.query.filter(func.lower(User.username) == username.lower()).first()
    if exists:
        flash("That username already exists.", "error")
        return redirect("/users")

    u = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        role_id=role_id,
        role="",  # Don't set role field anymore
        is_active=True,
        password_hash=generate_password_hash(password),
    )
    db.session.add(u)
    db.session.flush()

    audit_log(
        action="create",
        entity_type="User",
        entity_id=u.id,
        message="User created",
        details={"username": u.username, "role": role.name}
    )

    db.session.commit()
    flash(f"User '{username}' created with role '{role.name}'.", "success")
    return redirect("/users")

@app.post("/users/<int:user_id>/edit")
def users_edit_post(user_id: int):
    guard = require_admin()
    if guard:
        return guard

    user = User.query.get_or_404(user_id)
    
    first_name = (request.form.get("first_name") or "").strip() or None
    last_name = (request.form.get("last_name") or "").strip() or None
    role_id_str = (request.form.get("role_id") or "").strip()
    password = (request.form.get("password") or "").strip()

    if not role_id_str:
        flash("Role is required.", "error")
        return redirect(f"/users/{user_id}/edit")

    # Validate and get role_id
    try:
        role_id = int(role_id_str)
        role = Role.query.get(role_id)
        if not role:
            flash("Invalid role selected.", "error")
            return redirect(f"/users/{user_id}/edit")
    except (ValueError, TypeError):
        flash("Invalid role selected.", "error")
        return redirect(f"/users/{user_id}/edit")

    user.first_name = first_name
    user.last_name = last_name
    user.role_id = role_id

    if password:
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(f"/users/{user_id}/edit")
        user.password_hash = generate_password_hash(password)

    db.session.commit()
    
    audit_log(
        action="update",
        entity_type="User",
        entity_id=user_id,
        message="User updated",
        details={"username": user.username, "role": role.name}
    )

    flash(f"User '{user.username}' updated.", "success")
    return redirect("/users")

@app.post("/users/<int:user_id>/toggle_active")
def users_toggle_active(user_id: int):
    guard = require_admin()
    if guard:
        return guard

    if session.get("break_glass_admin") is not True and session.get("user_id") == user_id:
        flash("You can't deactivate your own account.", "error")
        return redirect("/users")

    u = User.query.get_or_404(user_id)
    before = bool(u.is_active)
    u.is_active = not bool(u.is_active)

    audit_log(
        action="update",
        entity_type="User",
        entity_id=u.id,
        message="User active toggled",
        details={"username": u.username, "before": before, "after": bool(u.is_active)}
    )

    db.session.commit()
    flash("User status updated.", "success")
    return redirect("/users")

@app.post("/users/<int:user_id>/reset_password")
def users_reset_password(user_id: int):
    guard = require_admin()
    if guard:
        return guard

    new_pw = (request.form.get("new_password") or "").strip()
    if not new_pw or len(new_pw) < 6:
        flash("New password must be at least 6 characters.", "error")
        return redirect("/users")

    u = User.query.get_or_404(user_id)
    u.password_hash = generate_password_hash(new_pw)

    audit_log(
        action="update",
        entity_type="User",
        entity_id=u.id,
        message="Password reset",
        details={"username": u.username}
    )

    db.session.commit()
    flash("Password reset.", "success")
    return redirect("/users")

@app.post("/users/<int:user_id>/delete")
def users_delete(user_id: int):
    guard = require_admin()
    if guard:
        return guard

    if session.get("break_glass_admin") is not True and session.get("user_id") == user_id:
        flash("You can't delete your own account.", "error")
        return redirect("/users")

    u = User.query.get_or_404(user_id)

    audit_log(
        action="delete",
        entity_type="User",
        entity_id=u.id,
        message="User deleted",
        details={"username": u.username}
    )

    db.session.delete(u)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect("/users")


# ============================================================
# MOVE BOXES
# ============================================================
# ============================================================
# MOVE BOXES
# ============================================================
@app.get("/items/<int:item_id>/move_boxes")
def item_move_boxes(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    from_loc = (request.args.get("from") or "freezer").strip().lower()
    if from_loc not in {"freezer", "cooler", "out"}:
        from_loc = "freezer"

    rows = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.storage == from_loc,
            InventoryLot.is_consumed == False
        )
        .order_by(
            InventoryLot.received_date.asc(),
            InventoryLot.lot_number.asc().nulls_last(),
            InventoryLot.id.asc()
        )
        .all()
    )

    view_rows = []
    for lot in rows:
        exp = compute_lot_expiration(item, lot)
        left = days_left(exp)
        view_rows.append({"lot": lot, "expires_on": exp, "days_left": left})

    default_to = "cooler"
    if from_loc == "cooler":
        default_to = "freezer"
    if from_loc == "out":
        default_to = "cooler"

    return render_template(
        "move_boxes.html",
        item=item,
        from_loc=from_loc,
        rows=view_rows,
        default_to=default_to
    )

@app.post("/items/<int:item_id>/move_boxes")
def item_move_boxes_post(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    from_loc = norm_storage(request.form.get("from_loc"))
    to_loc = norm_storage(request.form.get("to_loc"))
    if to_loc == from_loc:
        flash("Destination location must be different.", "error")
        return redirect(f"/items/{item.id}/move_boxes?from={from_loc}")

    mode = (request.form.get("mode") or "first_n").strip().lower()
    if mode not in {"first_n", "selected"}:
        mode = "first_n"

    fifo_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.storage == from_loc,
            InventoryLot.is_consumed == False
        )
        .order_by(
            InventoryLot.received_date.asc(),
            InventoryLot.lot_number.asc().nulls_last(),
            InventoryLot.id.asc()
        )
        .all()
    )

    moved_ids: list[int] = []
    moved = 0

    if mode == "first_n":
        n = to_int(request.form.get("first_n"), 0)
        if n <= 0:
            flash("Enter how many boxes to move.", "error")
            return redirect(f"/items/{item.id}/move_boxes?from={from_loc}")

        to_move = fifo_lots[:n]
        if not to_move:
            flash("No boxes available to move.", "error")
            return redirect(f"/items/{item.id}/move_boxes?from={from_loc}")

        for lot in to_move:
            lot.storage = to_loc
            moved += 1
            moved_ids.append(lot.id)

    else:
        selected_ids_int = unique_ints(request.form.getlist("lot_ids"))
        if not selected_ids_int:
            flash("Select at least one box to move.", "error")
            return redirect(f"/items/{item.id}/move_boxes?from={from_loc}")

        selected_lots = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.storage == from_loc,
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(selected_ids_int),
            )
            .all()
        )
        if not selected_lots:
            flash("Nothing matched your selection.", "error")
            return redirect(f"/items/{item.id}/move_boxes?from={from_loc}")

        for lot in selected_lots:
            lot.storage = to_loc
            moved += 1
            moved_ids.append(lot.id)

    audit_log(
        action="move",
        entity_type="InventoryLot",
        entity_id=None,
        message=f"Moved {moved} box(es) from {from_loc} to {to_loc}",
        details={
            "item_id": item.id,
            "from_loc": from_loc,
            "to_loc": to_loc,
            "mode": mode,
            "boxes_moved": moved,
            "lot_ids": moved_ids
        }
    )

    db.session.commit()
    flash(f"Moved {moved} box(es) from {from_loc} to {to_loc}.", "success")
    return redirect(f"/items/{item.id}/lots")


# ============================================================
# LOTS LIST / RECEIVE / EDIT / DELETE / BULK DELETE SELECTED
# ============================================================
# ============================================================
# LOTS LIST / RECEIVE / EDIT / DELETE / BULK DELETE SELECTED
# ============================================================
@app.get("/items/<int:item_id>/lots")
def item_lots(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    # Only fetch active (non-consumed) lots for display
    lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.is_consumed == False
        )
        .order_by(
            InventoryLot.received_date.asc(),
            InventoryLot.lot_number.asc().nulls_last(),
            InventoryLot.id.asc()
        )
        .all()
    )

    totals = {
        "freezer_boxes": 0,
        "cooler_boxes": 0,
        "out_boxes": 0,
        "freezer_units": 0,
        "cooler_units": 0,
        "out_units": 0,
        "count": len(lots),
    }

    rows = []
    for lot in lots:
        exp = compute_lot_expiration(item, lot)
        left = days_left(exp)

        box_count = int(round(lot.quantity or 1.0))
        if box_count < 1:
            box_count = 1

        if lot.storage == "freezer":
            totals["freezer_boxes"] += box_count
            totals["freezer_units"] += int(lot.count_units or 0)
        elif lot.storage == "cooler":
            totals["cooler_boxes"] += box_count
            totals["cooler_units"] += int(lot.count_units or 0)
        else:
            totals["out_boxes"] += box_count
            totals["out_units"] += int(lot.count_units or 0)

        rows.append({"lot": lot, "expires_on": exp, "days_left": left})

    start_num = next_lot_number(item.id)
    return render_template("item_lots.html", item=item, rows=rows, totals=totals, start_num=start_num)








@app.get("/items/<int:item_id>/lots/new")
def lot_new(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    return render_template(
        "lot_new.html",
        item=item,
        today=date.today().strftime("%Y-%m-%d"),
        suggested_num=next_lot_number(item.id),
    )

@app.post("/items/<int:item_id>/lots/receive")
@app.post("/items/<int:item_id>/lots/new")
def lot_new_post(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    received_date = parse_required_date(request.form.get("received_date"), date.today())
    state = norm_state(request.form.get("state"))
    storage = norm_storage(request.form.get("storage"))

    lot_number_raw = (request.form.get("lot_number") or "").strip()
    lot_number = to_int(lot_number_raw, 0) if lot_number_raw else None

    lot_label = (request.form.get("lot_label") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    count_units_raw = (request.form.get("count_units") or "").strip()
    count_units = to_int(count_units_raw, 0) if count_units_raw else None
    if state == "raw" and count_units is not None and count_units <= 0:
        count_units = None

    # Get quantity - default to 1 if not provided
    quantity_raw = (request.form.get("quantity") or "").strip()
    quantity = to_int(quantity_raw, 1) if quantity_raw else 1
    if quantity < 1:
        quantity = 1

    # Check if a single lot_number is being used - if so, use old behavior
    if lot_number is not None and quantity == 1:
        existing = InventoryLot.query.filter(
            InventoryLot.item_id == item.id,
            InventoryLot.lot_number == lot_number
        ).first()
        if existing:
            flash("That box # already exists for this item.", "error")
            return redirect(f"/items/{item.id}/lots/new")

        lot = InventoryLot(
            item_id=item.id,
            received_date=received_date,
            lot_number=lot_number,
            lot_label=lot_label,
            storage=storage,
            state=state,
            count_units=count_units,
            quantity=1.0,
            notes=notes,
            is_consumed=False
        )

        db.session.add(lot)
        db.session.flush()

        audit_log(
            action="create",
            entity_type="InventoryLot",
            entity_id=lot.id,
            message="Box received",
            details={"item_id": item.id, "lot_number": lot.lot_number, "storage": lot.storage, "state": lot.state}
        )

        db.session.commit()
        flash("Box received.", "success")
    else:
        # Create multiple individual lots with auto-incrementing lot numbers
        if lot_number is None:
            lot_number = next_lot_number(item.id)

        for i in range(quantity):
            current_lot_num = lot_number + i

            # Check if this lot number already exists
            existing = InventoryLot.query.filter(
                InventoryLot.item_id == item.id,
                InventoryLot.lot_number == current_lot_num
            ).first()
            if existing:
                flash(f"Box #{current_lot_num} already exists. Skipping.", "warning")
                continue

            lot = InventoryLot(
                item_id=item.id,
                received_date=received_date,
                lot_number=current_lot_num,
                lot_label=lot_label,
                storage=storage,
                state=state,
                count_units=count_units,
                quantity=1.0,
                notes=notes,
                is_consumed=False
            )

            db.session.add(lot)
            db.session.flush()

            audit_log(
                action="create",
                entity_type="InventoryLot",
                entity_id=lot.id,
                message="Box received",
                details={"item_id": item.id, "lot_number": current_lot_num, "storage": storage, "state": state}
            )

        db.session.commit()
        flash(f"{quantity} boxes received with lot numbers {lot_number}-{lot_number + quantity - 1}.", "success")
    return redirect(f"/items/{item.id}/lots")

@app.get("/items/<int:item_id>/lots/bulk")
def lot_bulk(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    start_num = next_lot_number(item.id)

    return render_template(
        "lot_bulk.html",
        item=item,
        today=date.today().strftime("%Y-%m-%d"),
        start_num=start_num
    )

@app.post("/items/<int:item_id>/lots/bulk")
def lot_bulk_post(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    received_date = parse_required_date(request.form.get("received_date"), date.today())
    state = norm_state(request.form.get("state"))

    lot_numbers = request.form.getlist("lot_number[]")
    lot_labels = request.form.getlist("lot_label[]")
    storages = request.form.getlist("storage[]")
    count_units_list = request.form.getlist("count_units[]")
    notes_list = request.form.getlist("notes[]")

    n = max(len(lot_numbers), len(lot_labels), len(storages), len(count_units_list), len(notes_list), 0)

    def safe_get(lst, i):
        return lst[i] if i < len(lst) else ""

    created = 0
    skipped = 0
    created_ids: list[int] = []

    existing_nums = set(
        x[0] for x in db.session.query(InventoryLot.lot_number)
        .filter(InventoryLot.item_id == item.id, InventoryLot.lot_number.isnot(None))
        .all()
    )

    for i in range(n):
        ln_raw = (safe_get(lot_numbers, i) or "").strip()
        ll_raw = (safe_get(lot_labels, i) or "").strip()
        st_raw = norm_storage(safe_get(storages, i))
        cu_raw = (safe_get(count_units_list, i) or "").strip()
        nt_raw = (safe_get(notes_list, i) or "").strip()

        if not ln_raw and not ll_raw and not cu_raw and not nt_raw:
            skipped += 1
            continue

        lot_number = None
        if ln_raw:
            try:
                lot_number = int(ln_raw)
            except Exception:
                lot_number = None

        lot_label = ll_raw or None
        notes = nt_raw or None

        count_units = None
        if cu_raw:
            try:
                count_units = int(cu_raw)
            except Exception:
                count_units = None

        if state == "raw" and (count_units is not None and count_units <= 0):
            count_units = None

        if lot_number is not None:
            if lot_number in existing_nums:
                skipped += 1
                continue
            existing_nums.add(lot_number)

        lot = InventoryLot(
            item_id=item.id,
            received_date=received_date,
            lot_number=lot_number,
            lot_label=lot_label,
            storage=st_raw,
            state=state,
            count_units=count_units,
            quantity=1.0,
            notes=notes,
            is_consumed=False
        )
        db.session.add(lot)
        db.session.flush()
        created += 1
        created_ids.append(lot.id)

    if created == 0:
        db.session.rollback()
        flash("No boxes were saved. (All rows were blank or duplicates.)", "error")
        return redirect(f"/items/{item.id}/lots/bulk")

    audit_log(
        action="create",
        entity_type="InventoryLot",
        entity_id=None,
        message="Bulk receive saved",
        details={"item_id": item.id, "created": created, "skipped": skipped, "lot_ids": created_ids}
    )

    db.session.commit()
    flash(f"Saved {created} box(es). Skipped {skipped} row(s).", "success")
    return redirect(f"/items/{item.id}/lots")

@app.get("/lots/<int:lot_id>/edit")
def lot_edit(lot_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    lot = InventoryLot.query.get_or_404(lot_id)
    item = Item.query.get_or_404(lot.item_id)
    return render_template("lot_edit.html", lot=lot, item=item)

@app.post("/lots/<int:lot_id>/edit")
def lot_edit_post(lot_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    lot = InventoryLot.query.get_or_404(lot_id)
    item = Item.query.get_or_404(lot.item_id)

    before = {
        "received_date": lot.received_date.strftime("%Y-%m-%d") if lot.received_date else None,
        "state": lot.state,
        "storage": lot.storage,
        "lot_number": lot.lot_number,
        "lot_label": lot.lot_label,
        "count_units": lot.count_units,
        "notes": lot.notes
    }

    lot.received_date = parse_required_date(request.form.get("received_date"), lot.received_date or date.today())
    lot.state = norm_state(request.form.get("state"))
    lot.storage = norm_storage(request.form.get("storage"))

    lot_number_raw = (request.form.get("lot_number") or "").strip()
    lot_label_raw = (request.form.get("lot_label") or "").strip()

    new_num = None
    if lot_number_raw:
        try:
            new_num = int(lot_number_raw)
        except Exception:
            new_num = None

    if new_num is not None and new_num != lot.lot_number:
        exists = InventoryLot.query.filter(
            InventoryLot.item_id == item.id,
            InventoryLot.lot_number == new_num,
            InventoryLot.id != lot.id
        ).first()
        if exists:
            flash("That box # already exists for this item.", "error")
            return redirect(f"/lots/{lot.id}/edit")

    lot.lot_number = new_num
    lot.lot_label = lot_label_raw or None

    cu_raw = (request.form.get("count_units") or "").strip()
    if cu_raw:
        lot.count_units = to_int(cu_raw, 0)
        if lot.state == "raw" and lot.count_units <= 0:
            lot.count_units = None
    else:
        lot.count_units = None

    lot.notes = (request.form.get("notes") or "").strip() or None

    after = {
        "received_date": lot.received_date.strftime("%Y-%m-%d") if lot.received_date else None,
        "state": lot.state,
        "storage": lot.storage,
        "lot_number": lot.lot_number,
        "lot_label": lot.lot_label,
        "count_units": lot.count_units,
        "notes": lot.notes
    }

    audit_log(
        action="update",
        entity_type="InventoryLot",
        entity_id=lot.id,
        message="Box updated",
        details={"item_id": item.id, "before": before, "after": after}
    )

    db.session.commit()
    flash("Box updated.", "success")
    return redirect(f"/items/{item.id}/lots")

@app.post("/lots/<int:lot_id>/delete")
def lot_delete(lot_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    lot = InventoryLot.query.get_or_404(lot_id)
    item_id = lot.item_id
    item = Item.query.get(item_id)

    audit_log(
        action="delete",
        entity_type="InventoryLot",
        entity_id=lot.id,
        message="Box deleted",
        details={"item_id": item_id, "lot_number": lot.lot_number, "storage": lot.storage, "state": lot.state}
    )

    # Decrease on_hand_count when deleting a lot
    if item and not lot.is_consumed:
        item.on_hand_count -= int(lot.quantity)
        if item.on_hand_count < 0:
            item.on_hand_count = 0

    db.session.delete(lot)
    db.session.commit()
    flash("Box deleted.", "success")
    return redirect(f"/items/{item_id}/lots")

# ✅ BULK DELETE SELECTED BOXES (WORKING + SAFE)
@app.post("/items/<int:item_id>/lots/bulk_delete")
def lots_bulk_delete(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    ids = unique_ints(request.form.getlist("lot_ids"))
    if not ids:
        flash("Select at least one box to delete.", "error")
        return redirect(f"/items/{item.id}/lots")

    lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.id.in_(ids),
        )
        .all()
    )

    if not lots:
        flash("Nothing matched your selection.", "error")
        return redirect(f"/items/{item.id}/lots")

    # Safety: don't allow deleting consumed history lots
    protected = [l for l in lots if bool(l.is_consumed)]
    if protected:
        flash("Some selected boxes are already consumed and cannot be deleted.", "error")
        return redirect(f"/items/{item.id}/lots")

    deleted_ids = [l.id for l in lots]
    deleted_count = len(lots)

    # Calculate total quantity to decrease from on_hand_count
    total_quantity = sum(int(l.quantity) for l in lots if not l.is_consumed)
    
    for l in lots:
        db.session.delete(l)

    # Decrease on_hand_count for all deleted lots
    if total_quantity > 0:
        item.on_hand_count -= total_quantity
        if item.on_hand_count < 0:
            item.on_hand_count = 0

    audit_log(
        action="delete",
        entity_type="InventoryLot",
        entity_id=None,
        message="Bulk delete boxes",
        details={"item_id": item.id, "deleted_count": deleted_count, "lot_ids": deleted_ids}
    )

    db.session.commit()
    flash(f"Deleted {deleted_count} box(es).", "success")
    return redirect(f"/items/{item.id}/lots")



# ============================================================
# PREP + RECONCILE
# ============================================================
# ============================================================
# PREP + RECONCILE
# ============================================================
@app.get("/items/<int:item_id>/prep")
def prep_home(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    from_loc = (request.args.get("from") or "cooler").strip().lower()
    if from_loc not in {"freezer", "cooler", "out"}:
        from_loc = "cooler"

    raw_boxes = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.state == "raw",
            InventoryLot.storage == from_loc,
            InventoryLot.is_consumed == False
        )
        .order_by(
            InventoryLot.received_date.asc(),
            InventoryLot.lot_number.asc().nulls_last(),
            InventoryLot.id.asc()
        )
        .all()
    )

    box_rows = []
    for lot in raw_boxes:
        exp = compute_lot_expiration(item, lot)
        box_rows.append({"lot": lot, "expires_on": exp, "days_left": days_left(exp)})

    history = (
        PrepBatch.query
        .filter(PrepBatch.item_id == item.id)
        .order_by(PrepBatch.prep_date.desc(), PrepBatch.id.desc())
        .limit(25)
        .all()
    )

    default_to = "cooler"
    return render_template(
        "prep.html",
        item=item,
        today=date.today().strftime("%Y-%m-%d"),
        from_loc=from_loc,
        default_to=default_to,
        box_rows=box_rows,
        history=history,
        edit_batch=None
    )

def _prepped_expiration(item: Item, prep_date: date, to_loc: str):
    days = None
    if to_loc == "freezer":
        days = item.prepped_freezer_days
    elif to_loc == "cooler":
        days = item.prepped_cooler_days
    elif to_loc == "out":
        days = item.prepped_out_days

    if not days or days <= 0:
        return None, 0

    days_int = int(days)
    return prep_date + timedelta(days=days_int), days_int

@app.post("/items/<int:item_id>/prep")
def prep_create(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    prep_date = parse_required_date(request.form.get("prep_date"), date.today())
    from_loc = norm_storage(request.form.get("from_loc"))
    to_loc = norm_storage(request.form.get("to_loc"))
    mode = (request.form.get("mode") or "first_n").strip().lower()
    if mode not in {"first_n", "selected"}:
        mode = "first_n"

    produced_units = to_int(request.form.get("produced_units"), 0)
    if produced_units <= 0:
        flash("Enter how many units you produced.", "error")
        return redirect(f"/items/{item.id}/prep?from={from_loc}")

    notes = (request.form.get("notes") or "").strip() or None

    fifo = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.state == "raw",
            InventoryLot.storage == from_loc,
            InventoryLot.is_consumed == False
        )
        .order_by(
            InventoryLot.received_date.asc(),
            InventoryLot.lot_number.asc().nulls_last(),
            InventoryLot.id.asc()
        )
        .all()
    )

    chosen: list[InventoryLot] = []

    if mode == "first_n":
        n = to_int(request.form.get("first_n"), 0)
        if n <= 0:
            flash("Enter how many boxes you used.", "error")
            return redirect(f"/items/{item.id}/prep?from={from_loc}")
        chosen = fifo[:n]
    else:
        ids_int = unique_ints(request.form.getlist("lot_ids"))
        if not ids_int:
            flash("Select at least one box used for prep.", "error")
            return redirect(f"/items/{item.id}/prep?from={from_loc}")
        chosen = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.state == "raw",
                InventoryLot.storage == from_loc,
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(ids_int),
            )
            .all()
        )

    if not chosen:
        flash("No boxes available for prep.", "error")
        return redirect(f"/items/{item.id}/prep?from={from_loc}")

    chosen_ids = []
    for lot in chosen:
        lot.is_consumed = True
        chosen_ids.append(lot.id)

    prepped_lot = InventoryLot(
        item_id=item.id,
        received_date=prep_date,
        quantity=1.0,
        count_units=produced_units,
        storage=to_loc,
        state="prepped",
        is_consumed=False,
        notes="Auto-created by Prep"
    )
    expires_on, shelf_days = _prepped_expiration(item, prep_date, to_loc)
    prepped_lot.expiration_override = expires_on

    db.session.add(prepped_lot)
    db.session.flush()

    batch = PrepBatch(
        item_id=item.id,
        prep_date=prep_date,
        from_loc=from_loc,
        to_loc=to_loc,
        mode=mode,
        boxes_used=len(chosen_ids),
        source_lot_ids=",".join(str(x) for x in sorted(chosen_ids)),
        produced_units=produced_units,
        created_prepped_lot_id=prepped_lot.id,
        expires_on=prepped_lot.expiration_override,
        shelf_life_days=shelf_days,
        output_units=produced_units,
        notes=notes,
    )
    db.session.add(batch)
    db.session.flush()  # ✅ so batch.id exists for audit

    audit_log(
        action="prep",
        entity_type="PrepBatch",
        entity_id=batch.id,
        message=f"Prep created for {item.name}",
        details={
            "item_id": item.id,
            "produced_units": produced_units,
            "from_loc": from_loc,
            "to_loc": to_loc,
            "source_lot_ids": chosen_ids,
            "created_prepped_lot_id": prepped_lot.id
        }
    )

    db.session.commit()
    flash("Prep saved. Raw boxes marked consumed and prepped lot created.", "success")
    return redirect(f"/items/{item.id}/prep?from={from_loc}")

@app.post("/prep/<int:batch_id>/delete")
def prep_delete(batch_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    b = PrepBatch.query.get_or_404(batch_id)
    item_id = b.item_id

    source_ids = parse_csv_ints(b.source_lot_ids)
    prepped_lot_id = b.created_prepped_lot_id

    # undo consumed raw lots
    if source_ids:
        raw_lots = InventoryLot.query.filter(InventoryLot.id.in_(source_ids)).all()
        for lot in raw_lots:
            lot.is_consumed = False

    # delete created prepped lot
    if prepped_lot_id:
        pl = InventoryLot.query.filter_by(id=prepped_lot_id).first()
        if pl:
            db.session.delete(pl)

    audit_log(
        action="prep_delete",
        entity_type="PrepBatch",
        entity_id=b.id,
        message="Prep batch deleted (undo raw consumption + remove prepped lot)",
        details={
            "prep_batch_id": b.id,
            "item_id": item_id,
            "prep_date": b.prep_date.strftime("%Y-%m-%d") if b.prep_date else None,
            "from_loc": b.from_loc,
            "to_loc": b.to_loc,
            "produced_units": int(b.produced_units or 0),
            "source_lot_ids": source_ids,
            "created_prepped_lot_id": prepped_lot_id,
        },
    )

    db.session.delete(b)
    db.session.commit()

    flash("Prep deleted and changes were undone.", "success")
    return redirect(f"/items/{item_id}/prep")

@app.get("/items/<int:item_id>/reconcile")
def reconcile_home(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    # ✅ Get all non-consumed lots for calculations
    all_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.is_consumed == False
        )
        .all()
    )

    # ✅ Calculate totals by storage location
    totals = {
        "freezer_boxes": 0,
        "cooler_boxes": 0,
        "out_boxes": 0,
        "freezer_units": 0,
        "cooler_units": 0,
        "out_units": 0,
        "starting_units": 0,
    }

    units_per_box = int(item.default_units_per_box or 1)

    for lot in all_lots:
        # Skip lots with 0 or negative quantity
        lot_quantity = lot.quantity or 1.0
        box_count = int(round(lot_quantity))
        
        if box_count < 1:
            # Mark very small quantities as consumed if not already
            if lot_quantity <= 0 and not lot.is_consumed:
                lot.is_consumed = True
            continue
        
        # Calculate units from actual quantity (don't round quantity first)
        lot_units = int(round(lot_quantity * units_per_box))
        
        # Track total units for generic_food reconcile
        totals["starting_units"] += lot_units
        
        if lot.storage == "freezer":
            totals["freezer_boxes"] += box_count
            totals["freezer_units"] += lot_units
        elif lot.storage == "cooler":
            totals["cooler_boxes"] += box_count
            totals["cooler_units"] += lot_units
        else:
            totals["out_boxes"] += box_count
            totals["out_units"] += lot_units

    cooler_boxes_available = totals["cooler_boxes"]
    cooler_units_available = totals["cooler_units"]

    # ✅ For generic_food: starting units already in totals
    prepped_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.state == "prepped",
            InventoryLot.is_consumed == False
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    prepped_rows = []
    for lot in prepped_lots:
        exp = compute_lot_expiration(item, lot)
        prepped_rows.append({
            "lot": lot,
            "expires_on": exp,
            "days_left": days_left(exp),
            "units": int(lot.count_units or 0)
        })

    # ✅ Expiring soon = cooler lots that have an expiration within next 0-4 days
    cooler_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.storage == "cooler",
            InventoryLot.is_consumed == False
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    expiring_soon = []
    for lot in cooler_lots:
        exp = compute_lot_expiration(item, lot)
        left = days_left(exp)
        if exp and left is not None and 0 <= left <= 4:
            expiring_soon.append({
                "lot": lot,
                "expires_on": exp,
                "days_left": left,
                "units": int(lot.count_units or 0)
            })

    expiring_soon = sorted(expiring_soon, key=lambda r: (r["days_left"], r["lot"].id))[:8]
    expiring_soon_count = len(expiring_soon)

    history = (
        ReconcileRecord.query
        .filter(ReconcileRecord.item_id == item.id)
        .order_by(ReconcileRecord.event_date.desc(), ReconcileRecord.id.desc())
        .limit(25)
        .all()
    )

    # ✅ For generic_food: provide available lots for box selection
    available_lots_for_selection = []
    if (item.prep_type or "").lower() == "generic_food":
        available_lots_for_selection = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False
            )
            .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
            .all()
        )

    return render_template(
        "reconcile.html",
        item=item,
        today=date.today().strftime("%Y-%m-%d"),
        prepped_rows=prepped_rows,
        cooler_boxes_available=cooler_boxes_available,
        cooler_units_available=cooler_units_available,
        totals=totals,
        expiring_soon=expiring_soon,
        expiring_soon_count=expiring_soon_count,
        history=history,
        available_lots_for_selection=available_lots_for_selection,
        rec=None,
    )


@app.route("/items/<int:item_id>/reconcile/history")
def reconcile_history(item_id):
    # whatever auth check pattern you already use in your app.py goes here

    item = Item.query.get_or_404(item_id)

    # IMPORTANT:
    # Replace ReconcileModelName with the SAME model class used in your existing reconcile routes.
    reconciles = ReconcileRecord.query.filter_by(item_id=item_id).order_by(ReconcileRecord.id.desc()).all()

    return render_template("reconcile_history.html", item=item, reconciles=reconciles)


@app.route("/items/<int:item_id>/history")
def item_history(item_id: int):
    """Comprehensive item history showing all reconciles, lots, and movements."""
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    # Get filter parameters from query string
    filter_type = request.args.get("type", "all")  # all, reconciles, lots
    filter_location = request.args.get("location", "")
    filter_state = request.args.get("state", "")

    # Get all reconcile records
    reconciles_query = ReconcileRecord.query.filter(ReconcileRecord.item_id == item.id)
    reconciles = reconciles_query.order_by(ReconcileRecord.event_date.desc(), ReconcileRecord.id.desc()).all()

    # Get all lots (including consumed)
    lots_query = InventoryLot.query.filter(InventoryLot.item_id == item.id)
    
    if filter_location:
        lots_query = lots_query.filter(InventoryLot.storage == filter_location)
    if filter_state:
        lots_query = lots_query.filter(InventoryLot.state == filter_state)
    
    all_lots = lots_query.order_by(InventoryLot.received_date.desc(), InventoryLot.id.desc()).all()

    # Build lot details with expiration info
    lot_details = []
    for lot in all_lots:
        exp = compute_lot_expiration(item, lot)
        lot_details.append({
            "lot": lot,
            "expires_on": exp,
            "days_left": days_left(exp),
            "units": int(lot.count_units or 0) if lot.state == "prepped" else int(round(lot.quantity or 1.0)),
        })

    # Get unique storage locations for filter dropdown
    storage_locations = db.session.execute(
        text("SELECT DISTINCT storage FROM inventory_lots WHERE item_id = :item_id ORDER BY storage"),
        {"item_id": item.id}
    ).fetchall()
    storage_list = [row[0] for row in storage_locations if row[0]]

    # Get unique states for filter dropdown
    states = db.session.execute(
        text("SELECT DISTINCT state FROM inventory_lots WHERE item_id = :item_id ORDER BY state"),
        {"item_id": item.id}
    ).fetchall()
    state_list = [row[0] for row in states if row[0]]

    return render_template(
        "item_history.html",
        item=item,
        reconciles=reconciles,
        lot_details=lot_details,
        storage_locations=storage_list,
        states=state_list,
        filter_type=filter_type,
        filter_location=filter_location,
        filter_state=filter_state,
    )
@app.post("/items/<int:item_id>/reconcile")
def reconcile_create(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    event_date = parse_required_date(request.form.get("event_date"), date.today())

    # ✅ GENERIC_FOOD: Simple reconcile (no prep required)
    if (item.prep_type or "").lower() == "generic_food":
        actual_units = to_int(request.form.get("actual_units"), 0)
        orders_sold = to_int(request.form.get("orders_sold"), 0)
        notes = (request.form.get("notes") or "").strip() or None

        # Use multiplier from form (user can edit it) or fall back to item multiplier
        multiplier = float(request.form.get("multiplier") or item.multiplier or 1.0)
        units_sold = orders_sold * multiplier
        
        # ✅ Get selected lots for generic_food (FIFO selection)
        lot_ids_list = request.form.getlist("generic_lot_ids") or []
        selected_ids = [int(x) for x in lot_ids_list if x.strip().isdigit()]
        
        # If no lots selected, use all non-consumed lots
        if not selected_ids:
            all_lots = InventoryLot.query.filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False
            ).order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc()).all()
        else:
            all_lots = InventoryLot.query.filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(selected_ids)
            ).order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc()).all()
        
        # Calculate starting units: boxes × units per box
        units_per_box = int(item.default_units_per_box or 1)
        starting_units = sum(int(round(l.quantity or 1.0)) * units_per_box for l in all_lots)
        missing_units = starting_units - units_sold - actual_units

        # 📸 SNAPSHOT: Capture BEFORE state for undo
        snapshot_before = {}
        for l in all_lots:
            snapshot_before[str(l.id)] = {
                "quantity": float(l.quantity or 1.0),
                "is_consumed": bool(l.is_consumed)
            }

        # Update inventory: FIFO - consume oldest lots first, keep the last lot with remaining units
        units_remaining = actual_units
        
        for lot in all_lots:
            lot_units = int(round(lot.quantity or 1.0)) * units_per_box
            if units_remaining >= lot_units:
                # This lot is fully consumed (counted as on-hand but we counted past it)
                lot.is_consumed = True
                units_remaining -= lot_units
            elif units_remaining > 0:
                # Partial lot - update quantity to reflect remaining units (converted back to boxes)
                new_quantity = units_remaining / units_per_box
                if new_quantity <= 0:
                    # If quantity would be 0 or negative, mark as consumed instead
                    lot.is_consumed = True
                else:
                    lot.quantity = new_quantity
                units_remaining = 0
            else:
                # No more units left, mark as consumed
                lot.is_consumed = True

        # 📸 SNAPSHOT: Capture AFTER state
        snapshot_after = {}
        for l in all_lots:
            snapshot_after[str(l.id)] = {
                "quantity": float(l.quantity or 1.0),
                "is_consumed": bool(l.is_consumed)
            }

        # Create reconcile record
        rec = ReconcileRecord(
            item_id=item.id,
            event_date=event_date,
            starting_units=starting_units,
            sales_units=int(units_sold),
            actual_units=actual_units,
            missing_units=int(missing_units),
            notes=notes,
            source_prepped_lot_ids=",".join(str(i) for i in selected_ids) if selected_ids else None,
        )
        db.session.add(rec)
        db.session.flush()

        # Save snapshot for undo
        snapshot = {"before": snapshot_before, "after": snapshot_after}
        rec.applied_lot_units = json.dumps(snapshot, ensure_ascii=False)

        audit_log(
            action="reconcile",
            entity_type="ReconcileRecord",
            entity_id=rec.id,
            message=f"Reconcile saved for {item.name} (generic_food)",
            details={
                "item_id": item.id,
                "event_date": event_date.strftime("%Y-%m-%d"),
                "starting_units": starting_units,
                "actual_units": actual_units,
                "orders_sold": orders_sold,
                "units_sold": units_sold,
                "missing_units": missing_units,
                "inventory_snapshot": snapshot,
            },
        )

        db.session.commit()
        flash("Reconcile saved and inventory updated.", "success")
        return redirect(f"/items/{item.id}/reconcile")

    # ✅ OTHER PREP TYPES: Prepped lot reconcile
    # Selected prepped lots (required for inventory updates)
    selected_ids = unique_ints(request.form.getlist("prepped_lot_ids"))

    selected_lots = []
    if selected_ids:
        selected_lots = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.state == "prepped",
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(selected_ids),
            )
            .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
            .all()
        )

    if not selected_lots:
        flash("You must select the prepped lot(s) you’re counting/selling from.", "error")
        return redirect(f"/items/{item.id}/reconcile")

    starting_units = sum(int(l.count_units or 0) for l in selected_lots)

    p1 = to_int(request.form.get("pack1_qty"), 0)
    p2 = to_int(request.form.get("pack2_qty"), 0)
    p3 = to_int(request.form.get("pack3_qty"), 0)
    p4 = to_int(request.form.get("pack4_qty"), 0)

    m1 = int(item.pack1_mult or 0)
    m2 = int(item.pack2_mult or 0)
    m3 = int(item.pack3_mult or 0)
    m4 = int(item.pack4_mult or 0)

    sales_units = (p1 * m1) + (p2 * m2) + (p3 * m3) + (p4 * m4)

    expected_units = starting_units - sales_units
    actual_units = to_int(request.form.get("actual_units"), 0)

    # Missing can be negative if actual > expected; clamp to 0 if you want.
    missing_units = expected_units - actual_units

    notes = (request.form.get("notes") or "").strip() or None

    rec = ReconcileRecord(
        item_id=item.id,
        event_date=event_date,
        starting_units=starting_units,
        pack1_qty=p1,
        pack2_qty=p2,
        pack3_qty=p3,
        pack4_qty=p4,
        sales_units=sales_units,
        expected_units=expected_units,
        actual_units=actual_units,
        missing_units=missing_units,
        notes=notes,
        source_prepped_lot_ids=",".join(str(i) for i in selected_ids),
    )
    db.session.add(rec)
    db.session.flush()  # so rec.id exists

    # ✅ APPLY INVENTORY: set selected lots total = actual_units (FIFO distribution)
    snapshot = _apply_reconcile_inventory(item.id, selected_ids, actual_units)
    rec.applied_lot_units = json.dumps(snapshot, ensure_ascii=False)

    audit_log(
        action="reconcile",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message=f"Reconcile saved for {item.name} (inventory updated)",
        details={
            "item_id": item.id,
            "event_date": event_date.strftime("%Y-%m-%d"),
            "starting_units": starting_units,
            "sales_units": sales_units,
            "actual_units": actual_units,
            "missing_units": missing_units,
            "source_prepped_lot_ids": selected_ids,
            "inventory_snapshot": snapshot,
        },
    )

    db.session.commit()
    flash("Reconcile saved and inventory updated.", "success")
    return redirect(f"/items/{item.id}/reconcile")


@app.post("/reconcile/<int:rec_id>/apply")
def reconcile_apply(rec_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec = ReconcileRecord.query.get_or_404(rec_id)
    item = Item.query.get_or_404(rec.item_id)

    if rec.is_applied:
        flash("This reconcile is already applied.", "error")
        return redirect(f"/items/{item.id}/reconcile")

    lot_ids = parse_csv_ints(rec.source_prepped_lot_ids)
    if not lot_ids:
        flash("This reconcile has no selected prepped lots. Edit it and select lots before applying.", "error")
        return redirect(f"/items/{item.id}/reconcile")

    # What actually reduces inventory?
    # You want to reduce by the sales units (what left the building).
    units_to_reduce = int(rec.sales_units or 0)
    if units_to_reduce <= 0:
        flash("Sales units are 0 — nothing to apply.", "error")
        return redirect(f"/items/{item.id}/reconcile")

    try:
        moves = fifo_reduce_prepped_lots(item.id, lot_ids, units_to_reduce)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(f"/items/{item.id}/reconcile")

    # Save consumption ledger
    for m in moves:
        db.session.add(ReconcileConsumption(
            rec_id=rec.id,
            lot_id=m["lot_id"],
            units_used=m["used"],
        ))

    rec.is_applied = True
    rec.applied_at = utcnow()

    audit_log(
        action="reconcile_apply",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message=f"Applied reconcile to inventory for {item.name}",
        details={
            "item_id": item.id,
            "rec_id": rec.id,
            "sales_units_applied": units_to_reduce,
            "moves": moves,
        }
    )

    db.session.commit()
    flash("Reconcile applied to inventory (prepped lots reduced FIFO).", "success")
    return redirect(f"/items/{item.id}/reconcile")


@app.post("/reconcile/<int:rec_id>/unapply")
def reconcile_unapply(rec_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec = ReconcileRecord.query.get_or_404(rec_id)
    item = Item.query.get_or_404(rec.item_id)

    if not rec.is_applied:
        flash("This reconcile is not applied.", "error")
        return redirect(f"/items/{item.id}/reconcile")

    consumptions = ReconcileConsumption.query.filter_by(rec_id=rec.id).all()
    if not consumptions:
        # still allow unapply to unlock if ledger missing
        rec.is_applied = False
        rec.applied_at = None
        db.session.commit()
        flash("Reconcile unlocked (no ledger found).", "success")
        return redirect(f"/items/{item.id}/reconcile")

    restore_prepped_lots(consumptions)

    # delete ledger rows
    for c in consumptions:
        db.session.delete(c)

    rec.is_applied = False
    rec.applied_at = None

    audit_log(
        action="reconcile_unapply",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message=f"Unapplied reconcile from inventory for {item.name}",
        details={"item_id": item.id, "rec_id": rec.id}
    )

    db.session.commit()
    flash("Reconcile unapplied (inventory restored).", "success")
    return redirect(f"/items/{item.id}/reconcile")

# ---------------------------------
# RECONCILE EDIT / DELETE (FIXED)
# ---------------------------------
@app.get("/reconcile/<int:rec_id>/edit")
@app.get("/items/<int:item_id>/reconcile/<int:rec_id>/edit")
def reconcile_edit(rec_id: int, item_id: int | None = None):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec, item = _load_reconcile_or_404(rec_id)

    # build list of current available prepped lots
    prepped_lots = (
        InventoryLot.query
        .filter(
            InventoryLot.item_id == item.id,
            InventoryLot.state == "prepped",
            InventoryLot.is_consumed == False
        )
        .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
        .all()
    )

    selected_ids = parse_csv_ints(rec.source_prepped_lot_ids)

    prepped_rows = []
    for lot in prepped_lots:
        exp = compute_lot_expiration(item, lot)
        prepped_rows.append({
            "lot": lot,
            "expires_on": exp,
            "days_left": days_left(exp),
            "units": int(lot.count_units or 0),
            "selected": lot.id in set(selected_ids)
        })

    # ✅ For generic_food: provide available lots for box selection during edit
    available_lots_for_selection = []
    current_selected_lot_ids = []
    current_units_on_hand = 0
    if (item.prep_type or "").lower() == "generic_food":
        available_lots_for_selection = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False
            )
            .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
            .all()
        )
        current_selected_lot_ids = parse_csv_ints(rec.source_prepped_lot_ids)
        # Calculate current units on hand from all unconsumed lots
        units_per_box = item.default_units_per_box or 1
        for lot in available_lots_for_selection:
            lot_units = int(round((lot.quantity or 1) * units_per_box))
            current_units_on_hand += lot_units

    return render_template(
        "reconcile_edit.html",
        item=item,
        rec=rec,
        prepped_rows=prepped_rows,
        available_lots_for_selection=available_lots_for_selection,
        current_selected_lot_ids=current_selected_lot_ids,
        current_units_on_hand=current_units_on_hand,
        today=date.today().strftime("%Y-%m-%d"),
    )


@app.post("/reconcile/<int:rec_id>/edit")
def reconcile_edit_post(rec_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec = ReconcileRecord.query.get_or_404(rec_id)
    item = Item.query.get_or_404(rec.item_id)

    event_date = parse_required_date(request.form.get("event_date"), rec.event_date or date.today())

    # ✅ GENERIC_FOOD: Simple reconcile edit
    if (item.prep_type or "").lower() == "generic_food":
        # Undo old inventory effect first
        if rec.applied_lot_units:
            try:
                snapshot_old = json.loads(rec.applied_lot_units)
                if isinstance(snapshot_old, dict):
                    _undo_reconcile_inventory(snapshot_old)
            except Exception:
                pass

        actual_units = to_int(request.form.get("actual_units"), 0)
        orders_sold = to_int(request.form.get("orders_sold"), 0)
        notes = (request.form.get("notes") or "").strip() or None

        # Use multiplier from form or fall back to item multiplier
        multiplier = float(request.form.get("multiplier") or item.multiplier or 1.0)
        units_sold = orders_sold * multiplier
        
        # ✅ Get selected lots for generic_food (FIFO selection)
        lot_ids_list = request.form.getlist("generic_lot_ids") or []
        selected_ids = [int(x) for x in lot_ids_list if x.strip().isdigit()]
        
        # Preserve the original starting_units (don't recalculate on edit)
        starting_units = rec.starting_units
        missing_units = starting_units - units_sold - actual_units

        # Get selected lots for inventory update, or all if none selected
        if not selected_ids:
            all_lots = InventoryLot.query.filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False
            ).order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc()).all()
        else:
            all_lots = InventoryLot.query.filter(
                InventoryLot.item_id == item.id,
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(selected_ids)
            ).order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc()).all()

        # 📸 SNAPSHOT: Capture BEFORE state for undo
        snapshot_before = {}
        for l in all_lots:
            snapshot_before[str(l.id)] = {
                "quantity": float(l.quantity or 1.0),
                "is_consumed": bool(l.is_consumed)
            }

        # Update inventory: distribute actual_units across lots (FIFO)
        units_remaining = actual_units
        
        for lot in all_lots:
            lot_units = int(round(lot.quantity or 1.0)) * int(item.default_units_per_box or 1)
            if units_remaining >= lot_units:
                # This lot is fully consumed
                lot.is_consumed = True
                units_remaining -= lot_units
            elif units_remaining > 0:
                lot.quantity = units_remaining / int(item.default_units_per_box or 1)
                units_remaining = 0
            else:
                lot.is_consumed = True

        # 📸 SNAPSHOT: Capture AFTER state
        snapshot_after = {}
        for l in all_lots:
            snapshot_after[str(l.id)] = {
                "quantity": float(l.quantity or 1.0),
                "is_consumed": bool(l.is_consumed)
            }

        # Update record
        rec.event_date = event_date
        rec.sales_units = int(units_sold)
        rec.actual_units = actual_units
        rec.missing_units = int(missing_units)
        rec.notes = notes
        rec.source_prepped_lot_ids = ",".join(str(i) for i in selected_ids) if selected_ids else None
        
        # Save snapshot for undo
        snapshot = {"before": snapshot_before, "after": snapshot_after}
        rec.applied_lot_units = json.dumps(snapshot, ensure_ascii=False)

        audit_log(
            action="update",
            entity_type="ReconcileRecord",
            entity_id=rec.id,
            message=f"Reconcile edited for {item.name} (generic_food)",
            details={
                "item_id": item.id,
                "event_date": event_date.strftime("%Y-%m-%d"),
                "starting_units": starting_units,
                "actual_units": actual_units,
                "orders_sold": orders_sold,
                "units_sold": units_sold,
                "missing_units": missing_units,
                "inventory_snapshot": snapshot,
            }
        )

        db.session.commit()
        flash("Count record updated.", "success")
        return redirect(f"/items/{item.id}/reconcile")

    # ✅ OTHER PREP TYPES: Prepped lot reconcile edit
    lot_ids_list = request.form.getlist("prepped_lot_ids") or []
    selected_ids = [int(x) for x in lot_ids_list if x.strip().isdigit()]
    selected_lots = []
    if selected_ids:
        selected_lots = (
            InventoryLot.query
            .filter(
                InventoryLot.item_id == item.id,
                InventoryLot.state == "prepped",
                InventoryLot.is_consumed == False,
                InventoryLot.id.in_(selected_ids),
            )
            .order_by(InventoryLot.received_date.asc(), InventoryLot.id.asc())
            .all()
        )

    if not selected_lots:
        flash("You must select the prepped lot(s) you’re counting/selling from.", "error")
        return redirect(f"/reconcile/{rec.id}/edit")

    starting_units = sum(int(l.count_units or 0) for l in selected_lots)

    p1 = to_int(request.form.get("pack1_qty"), 0)
    p2 = to_int(request.form.get("pack2_qty"), 0)
    p3 = to_int(request.form.get("pack3_qty"), 0)
    p4 = to_int(request.form.get("pack4_qty"), 0)

    m1 = int(item.pack1_mult or 0)
    m2 = int(item.pack2_mult or 0)
    m3 = int(item.pack3_mult or 0)
    m4 = int(item.pack4_mult or 0)

    sales_units = (p1 * m1) + (p2 * m2) + (p3 * m3) + (p4 * m4)

    expected_units = starting_units - sales_units
    actual_units = to_int(request.form.get("actual_units"), 0)
    missing_units = expected_units - actual_units

    notes = (request.form.get("notes") or "").strip() or None

    before = {
        "event_date": rec.event_date.strftime("%Y-%m-%d") if rec.event_date else None,
        "starting_units": rec.starting_units,
        "pack1_qty": rec.pack1_qty, "pack2_qty": rec.pack2_qty, "pack3_qty": rec.pack3_qty, "pack4_qty": rec.pack4_qty,
        "sales_units": rec.sales_units,
        "expected_units": rec.expected_units,
        "actual_units": rec.actual_units,
        "missing_units": rec.missing_units,
        "source_prepped_lot_ids": rec.source_prepped_lot_ids,
        "notes": rec.notes,
        "applied_lot_units": rec.applied_lot_units,
    }

    rec.event_date = event_date
    rec.starting_units = starting_units
    rec.pack1_qty = p1
    rec.pack2_qty = p2
    rec.pack3_qty = p3
    rec.pack4_qty = p4
    rec.sales_units = sales_units
    rec.expected_units = expected_units
    rec.actual_units = actual_units
    rec.missing_units = missing_units
    rec.source_prepped_lot_ids = ",".join(str(i) for i in selected_ids)
    rec.notes = notes

    # ✅ Re-apply inventory using the edited values
    snapshot_new = _apply_reconcile_inventory(item.id, selected_ids, actual_units)
    rec.applied_lot_units = json.dumps(snapshot_new, ensure_ascii=False)

    audit_log(
        action="reconcile_update",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message=f"Reconcile updated for {item.name} (inventory updated)",
        details={"item_id": item.id, "before": before, "after": {
            "event_date": event_date.strftime("%Y-%m-%d"),
            "starting_units": starting_units,
            "pack1_qty": p1, "pack2_qty": p2, "pack3_qty": p3, "pack4_qty": p4,
            "sales_units": sales_units,
            "expected_units": expected_units,
            "actual_units": actual_units,
            "missing_units": missing_units,
            "source_prepped_lot_ids": selected_ids,
            "notes": notes,
            "inventory_snapshot": snapshot_new,
        }},
    )

    db.session.commit()
    flash("Reconcile updated and inventory updated.", "success")
    return redirect(f"/items/{item.id}/reconcile")



@app.post("/reconcile/<int:rec_id>/delete")
def reconcile_delete(rec_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec = ReconcileRecord.query.get_or_404(rec_id)
    item_id = rec.item_id
    item = Item.query.get_or_404(item_id)

    # 📸 For generic_food: Restore to BEFORE state, then re-apply remaining reconciles
    if (item.prep_type or "").lower() == "generic_food":
        # Step 1: Restore lots to their state BEFORE this reconcile was applied
        if rec.applied_lot_units:
            try:
                snapshot = json.loads(rec.applied_lot_units)
                _undo_reconcile_inventory(snapshot)
                print(f"DEBUG DELETE: Restored to BEFORE state using snapshot")
            except Exception as e:
                print(f"Error restoring BEFORE state for reconcile {rec_id}: {e}")
                import traceback
                traceback.print_exc()
        
        # Step 2: Re-apply all remaining reconciles in chronological order
        remaining_reconciles = ReconcileRecord.query.filter(
            ReconcileRecord.item_id == item_id,
            ReconcileRecord.id != rec_id
        ).order_by(ReconcileRecord.event_date.asc(), ReconcileRecord.id.asc()).all()
        
        print(f"DEBUG DELETE: Re-applying {len(remaining_reconciles)} remaining reconciles")
        
        for remaining_rec in remaining_reconciles:
            lot_ids = parse_csv_ints(remaining_rec.source_prepped_lot_ids)
            if lot_ids:
                # Get the lots in FIFO order
                all_lots = InventoryLot.query.filter(InventoryLot.item_id == item_id).all()
                fifo_lots = [l for l in all_lots if l.id in lot_ids]
                fifo_lots.sort(key=lambda x: (x.received_date, x.id))
                
                # Re-apply this reconcile
                units_per_box = int(item.default_units_per_box or 1)
                actual_units = remaining_rec.actual_units or 0
                units_remaining = actual_units
                
                for lot in fifo_lots:
                    lot_units = int(round(lot.quantity or 1.0)) * units_per_box
                    if units_remaining >= lot_units:
                        lot.is_consumed = True
                        units_remaining -= lot_units
                    elif units_remaining > 0:
                        new_quantity = units_remaining / units_per_box
                        lot.quantity = max(0, new_quantity)
                        if new_quantity <= 0:
                            lot.is_consumed = True
                        units_remaining = 0
                    else:
                        lot.is_consumed = True
                
                print(f"DEBUG DELETE: Re-applied reconcile {remaining_rec.id}")
        
        print(f"DEBUG DELETE: Inventory recalculated for item {item_id}")
    else:
        # For prepped items: use the snapshot undo mechanism
        if rec.applied_lot_units:
            try:
                snapshot = json.loads(rec.applied_lot_units)
                if isinstance(snapshot, dict):
                    _undo_reconcile_inventory(snapshot)
            except Exception as e:
                print(f"Error undoing reconcile {rec_id}: {e}")
                import traceback
                traceback.print_exc()
                flash(f"Warning: Error restoring inventory: {e}", "warning")

    audit_log(
        action="reconcile_delete",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message="Reconcile deleted (inventory recalculated)",
        details={
            "item_id": item_id,
            "event_date": rec.event_date.strftime("%Y-%m-%d") if rec.event_date else None,
            "starting_units": rec.starting_units,
            "sales_units": rec.sales_units,
            "actual_units": rec.actual_units,
            "missing_units": rec.missing_units,
            "source_prepped_lot_ids": rec.source_prepped_lot_ids,
            "applied_lot_units": rec.applied_lot_units,
        },
    )

    db.session.delete(rec)
    db.session.commit()
    flash("Reconcile deleted and inventory restored.", "success")
    return redirect(f"/items/{item_id}/reconcile")

# ============================================================
# ROLE MANAGEMENT (ADMIN ONLY)
# ============================================================
@app.get("/admin/roles")
def roles_page():
    guard = require_admin()
    if guard:
        return guard
    
    roles = Role.query.order_by(Role.name).all()
    return render_template("roles.html", roles=roles)

@app.get("/admin/roles/new")
def new_role_page():
    guard = require_admin()
    if guard:
        return guard
    
    role = None
    return render_template("role_form.html", role=role)

@app.get("/admin/roles/<int:role_id>/edit")
def edit_role_page(role_id):
    guard = require_admin()
    if guard:
        return guard
    
    role = Role.query.get(role_id)
    if not role:
        return "Role not found", 404
    
    return render_template("role_form.html", role=role)

@app.post("/admin/roles/create")
def create_role():
    guard = require_admin()
    if guard:
        return guard
    
    try:
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Role name required", "error")
            return redirect("/admin/roles/new")
        
        if Role.query.filter_by(name=name).first():
            flash("Role name already exists", "error")
            return redirect("/admin/roles/new")
        
        # Collect permissions - first try JSON, then fall back to form data
        permissions = {}
        permissions_json = request.form.get("permissions", "")
        
        if permissions_json:
            try:
                permissions = json.loads(permissions_json)
                if not isinstance(permissions, dict):
                    permissions = {}
            except Exception as e:
                print(f"DEBUG: Failed to parse JSON permissions: {e}, falling back to form data")
                permissions = {}
        
        # If we don't have permissions yet, collect from form data
        if not permissions:
            for key in request.form.keys():
                if key.startswith('can_'):
                    # Checkbox values are typically 'on' if checked, absent if not
                    permissions[key] = request.form.get(key) in ('on', 'true', '1', 'yes')
        
        print(f"DEBUG: Creating role '{name}' with {len([p for p in permissions.values() if p])} permissions: {permissions}")
        
        role = Role(name=name, is_admin=False, permissions=permissions)
        db.session.add(role)
        db.session.commit()
        
        audit_log("create", "Role", role.id, f"Created role '{name}'", {"permissions": permissions})
        
        flash(f"Role '{name}' created successfully with {len([p for p in permissions.values() if p])} permissions", "success")
        return redirect("/admin/roles")
    except Exception as e:
        print(f"ERROR creating role: {str(e)}")
        flash(f"Error creating role: {str(e)}", "error")
        return redirect("/admin/roles/new")

@app.post("/admin/roles/<int:role_id>/update")
def update_role(role_id):
    guard = require_admin()
    if guard:
        return guard
    
    try:
        role = Role.query.get(role_id)
        if not role:
            flash("Role not found", "error")
            return redirect("/admin/roles")
        
        if role.is_admin:
            flash("Cannot modify admin role", "error")
            return redirect("/admin/roles")
        
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Role name required", "error")
            return redirect(f"/admin/roles/{role_id}/edit")
        
        # Check for duplicate name (different from current role)
        existing = Role.query.filter_by(name=name).first()
        if existing and existing.id != role_id:
            flash("Role name already exists", "error")
            return redirect(f"/admin/roles/{role_id}/edit")
        
        old_name = role.name
        role.name = name
        
        # Collect permissions - first try JSON, then fall back to form data
        permissions = {}
        permissions_json = request.form.get("permissions", "")
        
        if permissions_json:
            try:
                permissions = json.loads(permissions_json)
                if not isinstance(permissions, dict):
                    permissions = {}
            except Exception as e:
                print(f"DEBUG: Failed to parse JSON permissions: {e}, falling back to form data")
                permissions = {}
        
        # If we don't have permissions yet, collect from form data
        if not permissions:
            for key in request.form.keys():
                if key.startswith('can_'):
                    # Checkbox values are typically 'on' if checked, absent if not
                    permissions[key] = request.form.get(key) in ('on', 'true', '1', 'yes')
        
        print(f"DEBUG: Updating role '{old_name}' to '{name}' with {len([p for p in permissions.values() if p])} permissions: {permissions}")
        
        role.permissions = permissions
        
        db.session.commit()
        
        audit_log("update", "Role", role.id, f"Updated role '{old_name}' to '{name}'", {"permissions": permissions})
        
        flash(f"Role '{name}' updated successfully with {len([p for p in permissions.values() if p])} permissions", "success")
        return redirect("/admin/roles")
    except Exception as e:
        print(f"ERROR updating role: {str(e)}")
        flash(f"Error updating role: {str(e)}", "error")
        return redirect(f"/admin/roles/{role_id}/edit")

@app.post("/admin/roles/<int:role_id>/delete")
def delete_role(role_id):
    guard = require_admin()
    if guard:
        return guard
    
    try:
        role = Role.query.get(role_id)
        if not role:
            flash("Role not found", "error")
            return redirect("/admin/roles")
        
        if role.is_admin:
            flash("Cannot delete admin role", "error")
            return redirect("/admin/roles")
        
        # Check if any users have this role
        user_count = User.query.filter_by(role_id=role_id).count()
        if user_count > 0:
            flash(f"{user_count} users have this role. Reassign them first.", "error")
            return redirect("/admin/roles")
        
        role_name = role.name
        db.session.delete(role)
        db.session.commit()
        
        audit_log("delete", "Role", role_id, f"Deleted role '{role_name}'")
        
        flash(f"Role '{role_name}' deleted successfully", "success")
        return redirect("/admin/roles")
    except Exception as e:
        flash(f"Error deleting role: {str(e)}", "error")
        return redirect("/admin/roles")

@app.get("/admin/roles/<int:role_id>/data")
def get_role_data(role_id):
    guard = require_admin()
    if guard:
        return guard
    
    try:
        role = Role.query.get(role_id)
        if not role:
            return jsonify({"error": "Role not found"}), 404
        
        return jsonify({
            "id": role.id,
            "name": role.name,
            "is_admin": role.is_admin,
            "permissions": role.permissions or {}
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# AUDIT HISTORY PAGE (your filter/search kept + made stable)
# ============================================================
# ============================================================
# AUDIT HISTORY PAGE
# ============================================================
@app.get("/audit")
def audit_history():
    guard = require_view_access()
    if guard:
        return guard

    q = (request.args.get("q") or "").strip()
    user_q = (request.args.get("user") or "").strip()
    action = (request.args.get("action") or "").strip().lower()
    entity = (request.args.get("entity") or "").strip()
    page_q = (request.args.get("page") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    query = AuditLog.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            (AuditLog.message.ilike(like)) |
            (AuditLog.details.ilike(like)) |
            (AuditLog.entity_type.ilike(like)) |
            (func.cast(AuditLog.entity_id, db.String).ilike(like))
        )

    if user_q:
        like = f"%{user_q}%"
        query = query.filter(AuditLog.actor_name.ilike(like))

    if action:
        query = query.filter(AuditLog.action == action)

    if entity:
        query = query.filter(AuditLog.entity_type == entity)

    if page_q:
        like = f"%{page_q}%"
        query = query.filter(
            (AuditLog.page.ilike(like)) |
            (AuditLog.page_title.ilike(like))
        )

    # date filters treat input as local dates
    if date_from:
        try:
            d = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(AuditLog.created_at >= d)
        except Exception:
            pass

    if date_to:
        try:
            d = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(AuditLog.created_at < d)
        except Exception:
            pass

    logs = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(500).all()

    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action.asc()).all()]
    entities = [r[0] for r in db.session.query(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type.asc()).all()]

    return render_template(
        "audit.html",
        logs=logs,
        q=q,
        user_q=user_q,
        action=action,
        entity=entity,
        page_q=page_q,
        date_from=date_from,
        date_to=date_to,
        actions=actions,
        entities=entities,
    )


# ============================================================
# INIT DB + MIGRATE + SEED
# ============================================================
with app.app_context():
    db.create_all()
    try:
        run_migrations()
    except Exception:
        pass

    # Create default Admin role if it doesn't exist
    if not Role.query.filter_by(name="Admin").first():
        admin_role = Role(
            name="Admin",
            is_admin=True,
            permissions={
                "can_view_items": True,
                "can_edit_items": True,
                "can_delete_items": True,
                "can_view_lots": True,
                "can_edit_lots": True,
                "can_delete_lots": True,
                "can_view_orders": True,
                "can_create_orders": True,
                "can_delete_orders": True,
                "can_view_beers": True,
                "can_edit_beers": True,
                "can_delete_beers": True,
                "can_view_users": True,
                "can_create_users": True,
                "can_delete_users": True,
                "can_view_suppliers": True,
                "can_edit_suppliers": True,
                "can_delete_suppliers": True,
                "can_view_audit": True,
                "can_reconcile": True,
            }
        )
        db.session.add(admin_role)
        db.session.commit()

    if User.query.count() == 0:
        # Get the Admin role
        admin_role = Role.query.filter_by(name="Admin").first()
        db.session.add(User(
            username="owner",
            first_name="Draft",
            last_name="Room",
            role="admin",
            role_id=admin_role.id if admin_role else None,
            is_active=True,
            password_hash=generate_password_hash("ChangeMe123"),
        ))
        db.session.commit()

    if Item.query.count() == 0:
        db.session.add_all([
            Item(name="Wings", category="Food", prep_type="wings", unit="lb", sales_mode="packs_4",
                 raw_freezer_days=120, raw_cooler_days=3,
                 prepped_cooler_days=4, prepped_out_days=1),
            Item(name="Chicken Breast", category="Food", prep_type="raw_protein", unit="lb",
                 raw_freezer_days=180, raw_cooler_days=3,
                 prepped_cooler_days=3),
            Item(name="Mozzarella Sticks", category="Food", prep_type="portion_pack", unit="case",
                 raw_freezer_days=365,
                 prepped_cooler_days=3),
        ])
        db.session.commit()

if __name__ == "__main__":
    import os

    # Local dev default
    host = "127.0.0.1"
    port = 5000

    # Railway/production will provide PORT (and we must bind to 0.0.0.0)
    if os.environ.get("PORT"):
        host = "0.0.0.0"
        port = int(os.environ["PORT"])

    app.run(host=host, port=port, debug=True)

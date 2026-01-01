from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Any, Iterable, cast

from flask import Flask, flash, redirect, render_template, request, session, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from werkzeug.security import generate_password_hash, check_password_hash

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore


# ============================================================
# APP SETUP
# ============================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-change-this-to-a-long-random-string"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///draftroom_inventory.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


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


@app.template_filter("fmt_dt")
def fmt_dt(dt: Optional[datetime], fmt: str = "%b %d, %Y %I:%M %p") -> str:
    d = to_local(dt)
    if not d:
        return "—"
    return d.strftime(fmt)


# ============================================================
# AUTH SETTINGS (hardcoded fallback)
# ============================================================
BREAK_GLASS_ADMIN_USERNAME = "admin"
BREAK_GLASS_ADMIN_PASSWORD = "admin123"


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
    return current_user_role() == "admin"

def can_edit_inventory() -> bool:
    return role_rank(current_user_role()) >= ROLE_RANK["manager"]

def can_view_inventory() -> bool:
    return role_rank(current_user_role()) >= ROLE_RANK["staff"] or (session.get("break_glass_admin") is True)

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
@app.context_processor
def inject_user():
    u = current_user()
    return {
        "auth_user": u,
        "auth_break_glass": bool(session.get("break_glass_admin") is True),
        "auth_role": current_user_role(),
        "can_edit_inventory": can_edit_inventory(),
        "can_manage_users": can_manage_users(),
    }


# ============================================================
# MODELS
# ============================================================
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(80), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)

    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def display_name(self) -> str:
        full = f"{(self.first_name or '').strip()} {(self.last_name or '').strip()}".strip()
        return full if full else self.username


class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(160), nullable=False, unique=True)
    contact_name = db.Column(db.String(160), nullable=True)
    phone = db.Column(db.String(60), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    notes = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    items = db.relationship("Item", backref="supplier")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)

    # who
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_name = db.Column(db.String(120), nullable=False, default="unknown")
    actor_role = db.Column(db.String(30), nullable=True)

    # what
    action = db.Column(db.String(40), nullable=False)          # create/update/delete/move/prep/reconcile/login/logout
    entity_type = db.Column(db.String(60), nullable=False)     # InventoryLot, Item, Supplier, PrepBatch, ReconcileRecord, User
    entity_id = db.Column(db.Integer, nullable=True)

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

    name = db.Column(db.String(200), nullable=False, unique=True)

    category = db.Column(db.String(80), nullable=False, default="Food")
    prep_type = db.Column(db.String(40), nullable=False, default="generic")

    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)

    unit = db.Column(db.String(30), nullable=False, default="each")

    raw_freezer_days = db.Column(db.Integer, nullable=True)
    raw_cooler_days = db.Column(db.Integer, nullable=True)
    raw_out_days = db.Column(db.Integer, nullable=True)

    prepped_freezer_days = db.Column(db.Integer, nullable=True)
    prepped_cooler_days = db.Column(db.Integer, nullable=True)
    prepped_out_days = db.Column(db.Integer, nullable=True)

    sales_mode = db.Column(db.String(30), nullable=False, default="simple")

    pack1_label = db.Column(db.String(80), nullable=True, default="Single (10)")
    pack1_mult = db.Column(db.Integer, nullable=True, default=10)
    pack2_label = db.Column(db.String(80), nullable=True, default="Double (20)")
    pack2_mult = db.Column(db.Integer, nullable=True, default=20)
    pack3_label = db.Column(db.String(80), nullable=True, default="Room 120 Single (10)")
    pack3_mult = db.Column(db.Integer, nullable=True, default=10)
    pack4_label = db.Column(db.String(80), nullable=True, default="Room 120 Double (20)")
    pack4_mult = db.Column(db.Integer, nullable=True, default=20)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    lots = db.relationship("InventoryLot", backref="item", cascade="all, delete-orphan")


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
    details: dict | str | None = None
):
    """
    Call this BEFORE commit. It's ok if entity_id isn't known yet; you can:
      - db.session.flush() first, then audit_log with id
      - or log with entity_id=None (still records the event)
    """
    try:
        u = current_user()
        actor_id = u.id if u else None
        actor_name = "Break-glass Admin" if session.get("break_glass_admin") else (u.display_name() if u else "unknown")
        actor_role = "admin" if session.get("break_glass_admin") else ((u.role or "") if u else "")

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        ua = (request.headers.get("User-Agent") or "")[:240]

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
            message=message,
            details=details_str,
            ip=ip,
            user_agent=ua,
        ))
    except Exception:
        pass


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
    mx = db.session.query(func.max(InventoryLot.lot_number)).filter(InventoryLot.item_id == item_id).scalar()
    try:
        return int(mx or 0) + 1
    except Exception:
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
    Restores lots to the 'before' units in snapshot.
    Also fixes is_consumed flag accordingly.
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

    for k, units in before.items():
        try:
            lid = int(k)
        except Exception:
            continue

        l = lot_map.get(lid)
        if not l:
            continue

        u = int(units or 0)
        l.count_units = u
        l.is_consumed = True if u <= 0 else False


# ============================================================
# SIMPLE SQLITE MIGRATION
# ============================================================
def sqlite_table_exists(table_name: str) -> bool:
    row = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).fetchone()
    return row is not None

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
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if username.lower() == BREAK_GLASS_ADMIN_USERNAME.lower() and password == BREAK_GLASS_ADMIN_PASSWORD:
        session.clear()
        session["break_glass_admin"] = True

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
        flash("Invalid username/password.", "error")
        return redirect("/login")

    if not check_password_hash(u.password_hash, password):
        flash("Invalid username/password.", "error")
        return redirect("/login")

    session.clear()
    session["user_id"] = u.id

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


# ============================================================
# DASHBOARD + CATEGORIES
# ============================================================
CATEGORY_ORDER = ["Food", "Alcohol", "NA Beverages", "Paper/Disposables", "Cleaning", "Retail/Misc"]

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

    recent_items = Item.query.order_by(Item.created_at.desc(), Item.id.desc()).limit(8).all()
    return render_template("dashboard.html", categories=categories, recent_items=recent_items)

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

    return render_template("category.html", category_name=category_name, items=items, q=q)


# ============================================================
# ITEMS (single /items route only - fixes your duplicate)
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
        details={"name": item.name, "category": item.category, "supplier_id": item.supplier_id}
    )

    db.session.commit()
    flash("Item created.", "success")
    return redirect("/items")

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
    }

    audit_log(
        action="update",
        entity_type="Item",
        entity_id=item.id,
        message="Item updated",
        details={"before": before, "after": after}
    )

    db.session.commit()
    flash("Item updated.", "success")
    return redirect("/items")

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
@app.get("/items/<int:item_id>")
def item_hub(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

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
        expiring_soon=expiring_soon,
        expiring_soon_count=expiring_soon_count
    )


# ============================================================
# SUPPLIERS
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
@app.get("/users")
def users_page():
    guard = require_admin()
    if guard:
        return guard
    users = User.query.order_by(User.created_at.desc(), User.id.desc()).all()
    return render_template("users.html", users=users)

@app.post("/users/create")
def users_create():
    guard = require_admin()
    if guard:
        return guard

    username = (request.form.get("username") or "").strip()
    first_name = (request.form.get("first_name") or "").strip() or None
    last_name = (request.form.get("last_name") or "").strip() or None
    role = (request.form.get("role") or "staff").strip().lower()
    password = (request.form.get("password") or "").strip()

    if not username:
        flash("Username is required.", "error")
        return redirect("/users")

    if role not in {"staff", "manager", "admin"}:
        role = "staff"

    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect("/users")

    exists = User.query.filter(func.lower(User.username) == username.lower()).first()
    if exists:
        flash("That username already exists.", "error")
        return redirect("/users")

    u = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        role=role,
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
        details={"username": u.username, "role": u.role}
    )

    db.session.commit()
    flash("User created.", "success")
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
@app.get("/items/<int:item_id>/lots")
def item_lots(item_id: int):
    guard = require_view_access()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)

    lots = (
        InventoryLot.query
        .filter(InventoryLot.item_id == item.id)
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

        if not lot.is_consumed:
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

    if lot_number is not None:
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

    audit_log(
        action="delete",
        entity_type="InventoryLot",
        entity_id=lot.id,
        message="Box deleted",
        details={"item_id": item_id, "lot_number": lot.lot_number, "storage": lot.storage, "state": lot.state}
    )

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

    for l in lots:
        db.session.delete(l)

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

    history = (
        ReconcileRecord.query
        .filter(ReconcileRecord.item_id == item.id)
        .order_by(ReconcileRecord.event_date.desc(), ReconcileRecord.id.desc())
        .limit(25)
        .all()
    )

    return render_template(
        "reconcile.html",
        item=item,
        today=date.today().strftime("%Y-%m-%d"),
        prepped_rows=prepped_rows,
        history=history,
        rec=None,
    )

@app.post("/items/<int:item_id>/reconcile")
def reconcile_create(item_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    item = Item.query.get_or_404(item_id)
    event_date = parse_required_date(request.form.get("event_date"), date.today())

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

    return render_template(
        "reconcile_edit.html",
        item=item,
        rec=rec,
        prepped_rows=prepped_rows
    )


@app.post("/reconcile/<int:rec_id>/edit")
def reconcile_edit_post(rec_id: int):
    guard = require_inventory_edit()
    if guard:
        return guard

    rec = ReconcileRecord.query.get_or_404(rec_id)
    item = Item.query.get_or_404(rec.item_id)

    # ✅ Undo old inventory effect first (so edits are safe)
    if rec.applied_lot_units:
        try:
            snapshot_old = json.loads(rec.applied_lot_units)
            if isinstance(snapshot_old, dict):
                _undo_reconcile_inventory(snapshot_old)
        except Exception:
            pass

    event_date = parse_required_date(request.form.get("event_date"), rec.event_date or date.today())

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

    # ✅ Undo inventory changes from this reconcile
    if rec.applied_lot_units:
        try:
            snapshot = json.loads(rec.applied_lot_units)
            if isinstance(snapshot, dict):
                _undo_reconcile_inventory(snapshot)
        except Exception:
            pass

    audit_log(
        action="reconcile_delete",
        entity_type="ReconcileRecord",
        entity_id=rec.id,
        message="Reconcile deleted (inventory restored)",
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
# AUDIT HISTORY PAGE (your filter/search kept + made stable)
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

    if User.query.count() == 0:
        db.session.add(User(
            username="owner",
            first_name="Draft",
            last_name="Room",
            role="admin",
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

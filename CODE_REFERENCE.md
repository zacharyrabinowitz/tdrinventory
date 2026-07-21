# DRAFT ROOM INVENTORY - CODE REFERENCE & KEY SNIPPETS

## DATABASE MODELS (SQLAlchemy)

```python
# USER & AUTHENTICATION
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120))
    first_name = db.Column(db.String(120))
    last_name = db.Column(db.String(120))
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    disabled = db.Column(db.Boolean, default=False)
    role = db.relationship("Role")

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    permissions = db.Column(db.Text)  # JSON string

# INVENTORY MANAGEMENT
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    prep_type = db.Column(db.String(50))  # generic, generic_food, wings, etc
    category = db.Column(db.String(100))
    # Shelf life in days
    raw_freezer_days = db.Column(db.Integer, default=30)
    raw_cooler_days = db.Column(db.Integer, default=7)
    raw_out_days = db.Column(db.Integer, default=1)
    prepped_freezer_days = db.Column(db.Integer, default=30)
    prepped_cooler_days = db.Column(db.Integer, default=3)
    prepped_out_days = db.Column(db.Integer, default=1)
    on_hand_count = db.Column(db.Integer, default=0)
    main_bar_on_hand = db.Column(db.Integer, default=0)
    low_bar_on_hand = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    lots = db.relationship("InventoryLot", backref="item", cascade="all, delete-orphan")

class InventoryLot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    received_date = db.Column(db.Date, nullable=False)
    lot_number = db.Column(db.Integer)  # Box number
    lot_label = db.Column(db.String(255))  # Additional label
    storage = db.Column(db.String(20))  # freezer, cooler, out
    state = db.Column(db.String(20))  # raw, prepped
    count_units = db.Column(db.Integer)  # For prepped: units available
    quantity = db.Column(db.Float, default=1.0)  # Number of boxes
    expiration_override = db.Column(db.Date)
    is_consumed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)

class ItemHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"))
    action = db.Column(db.String(50))
    quantity = db.Column(db.Integer)
    before_value = db.Column(db.String(255))
    after_value = db.Column(db.String(255))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

# BEER MANAGEMENT
class Beer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    style = db.Column(db.String(100))
    abv = db.Column(db.Float)
    description = db.Column(db.Text)
    brewery = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True)
    taps = db.relationship("BeerTap", backref="beer", cascade="all, delete-orphan")

class BeerTap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    beer_id = db.Column(db.Integer, db.ForeignKey("beer.id"))
    tap_number = db.Column(db.Integer)
    active = db.Column(db.Boolean, default=True)
    percent_full = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

# ORDERS
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    status = db.Column(db.String(20), default="pending")  # pending, received, cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivery_date = db.Column(db.Date)
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"))
    quantity_ordered = db.Column(db.Integer)
    quantity_received = db.Column(db.Integer, default=0)
    item = db.relationship("Item")

# RECONCILIATION
class Reconcile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    units_consumed = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)
    consumptions = db.relationship("ReconcileConsumption", backref="reconcile", cascade="all, delete-orphan")
    item = db.relationship("Item")
    user = db.relationship("User")

class ReconcileConsumption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reconcile_id = db.Column(db.Integer, db.ForeignKey("reconcile.id"))
    lot_id = db.Column(db.Integer, db.ForeignKey("inventory_lot.id"))
    units_used = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    lot = db.relationship("InventoryLot")

# SUPPLIERS
class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    contact_person = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# AUDIT LOGGING
class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(50))  # create, update, delete, login, view, etc
    entity_type = db.Column(db.String(50))  # Item, InventoryLot, Order, etc
    entity_id = db.Column(db.Integer)
    message = db.Column(db.String(255))
    details = db.Column(db.Text)  # JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User")
```

---

## SECURITY & ACCESS CONTROL

```python
# Session management
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)

# Rate limiting
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Password strength validation
def validate_password_strength(password: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not any(c.isupper() for c in password):
        return False, "Password must contain uppercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain digit."
    return True, ""

# Input validation
def sanitize_input(text: str) -> str:
    """Clean user input"""
    if not text:
        return ""
    return text.strip()

def is_valid_username(username: str) -> bool:
    """Username must be alphanumeric + underscore, 3-20 chars"""
    if not (3 <= len(username) <= 20):
        return False
    return re.match(r"^[a-zA-Z0-9_]+$", username) is not None

def is_valid_email(email: str) -> bool:
    """Basic email validation"""
    return re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email) is not None

# Access control decorators
def require_view_access():
    if not is_logged_in():
        flash("You must be logged in to view this.", "error")
        return redirect(url_for("login"))
    return None

def require_inventory_edit():
    if not is_logged_in():
        return redirect(url_for("login"))
    
    user = User.query.filter_by(username=session.get("username")).first()
    if not user or user.disabled:
        flash("Access denied.", "error")
        abort(403)
    
    role = Role.query.get(user.role_id)
    permissions = json.loads(role.permissions or "{}") if role else {}
    
    if not permissions.get("edit_inventory"):
        flash("Manager or Admin required for changes.", "error")
        abort(403)
    
    return None

def require_admin_access():
    if not is_logged_in():
        return redirect(url_for("login"))
    
    user = User.query.filter_by(username=session.get("username")).first()
    if not user or user.disabled:
        abort(403)
    
    role = Role.query.get(user.role_id)
    permissions = json.loads(role.permissions or "{}") if role else {}
    
    if not permissions.get("admin"):
        abort(403)
    
    return None
```

---

## AUDIT LOGGING

```python
def audit_log(action: str, entity_type: str, entity_id: Optional[int] = None, 
              message: str = "", details: Optional[dict] = None):
    """Log action to audit trail"""
    user_id = None
    if is_logged_in():
        user = User.query.filter_by(username=session.get("username")).first()
        if user:
            user_id = user.id
    
    log_entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        details=json.dumps(details or {})
    )
    
    db.session.add(log_entry)
    db.session.commit()
```

---

## INVENTORY LOGIC - FIFO

```python
def fifo_reduce_prepped_lots(item_id: int, lot_ids: list[int], units_to_reduce: int) -> list[dict]:
    """
    Reduce count_units across prepped lots in FIFO order.
    Returns list of {"lot_id": int, "used": int, "before": int, "after": int}
    """
    if units_to_reduce <= 0:
        return []
    
    # Get lots in FIFO order (oldest first)
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
        raise ValueError(f"Not enough units. Need {remaining}, have {total_available}.")
    
    # Consume from oldest lot first
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
    """Undo lot reductions from a reconciliation"""
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
```

---

## EXPIRATION CALCULATION

```python
def compute_lot_expiration(item: Item, lot: InventoryLot) -> Optional[date]:
    """Calculate expiration date based on item settings, storage, and state"""
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
    else:  # prepped
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
    """Calculate days until expiration"""
    if not exp:
        return None
    return (exp - date.today()).days
```

---

## LOGIN & AUTHENTICATION

```python
@app.get("/login")
def login():
    if is_logged_in():
        return redirect("/")
    return render_template("login.html")

@app.post("/login")
@limiter.limit("5 per minute")
def login_post():
    username = sanitize_input(request.form.get("username") or "")
    password = request.form.get("password") or ""
    
    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("login"))
    
    if not is_valid_username(username):
        flash("Invalid input.", "error")
        return redirect(url_for("login"))
    
    user = User.query.filter_by(username=username).first()
    
    if user and check_password_hash(user.password_hash, password):
        if user.disabled:
            flash("Your account has been disabled.", "error")
            audit_log("login_failed", "User", user.id, "Account disabled")
            return redirect(url_for("login"))
        
        session["username"] = username
        session.permanent = True
        audit_log("login_success", "User", user.id, "Logged in")
        return redirect("/")
    
    # Generic error message to prevent user enumeration
    flash("Invalid username/password.", "error")
    audit_log("login_failed", "User", message=f"Failed login attempt for {username}")
    return redirect(url_for("login"))

@app.get("/logout")
def logout():
    audit_log("logout", "User", message="Logged out")
    session.clear()
    return redirect(url_for("login"))
```

---

## ROLE DEFINITIONS

```python
DEFAULT_ROLES = {
    "Viewer": {
        "view_inventory": True,
        "view_orders": True,
        "view_audit": False,
        "edit_inventory": False,
        "manage_users": False,
        "admin": False
    },
    "Manager": {
        "view_inventory": True,
        "view_orders": True,
        "view_audit": True,
        "edit_inventory": True,
        "manage_users": False,
        "admin": False
    },
    "Admin": {
        "view_inventory": True,
        "view_orders": True,
        "view_audit": True,
        "edit_inventory": True,
        "manage_users": True,
        "admin": True
    }
}
```

---

## CURRENT STATUS

**Routes Implemented**: 18 primary routes with multiple HTTP methods
**Database Models**: 11 core models with relationships
**Security Features**: Rate limiting, password validation, RBAC, audit logging
**Missing**: GET /lots (master lot list across all items)

---

## TO IMPLEMENT MISSING /lots ROUTE

Add to app.py after other lot routes (around line 4581):

```python
@app.get("/lots")
def view_all_lots():
    guard = require_view_access()
    if guard:
        return guard
    
    # Optional search/filter
    search_query = request.args.get('q', '').strip().lower()
    
    # Get all non-consumed lots
    query = InventoryLot.query.filter(InventoryLot.is_consumed == False)
    
    if search_query:
        query = query.join(Item).filter(Item.name.ilike(f"%{search_query}%"))
    
    lots = query.order_by(
        InventoryLot.received_date.asc(),
        InventoryLot.lot_number.asc().nulls_last()
    ).all()
    
    return render_template("lots.html", lots=lots, q=search_query)
```

This matches the template structure in templates/lots.html which already exists.
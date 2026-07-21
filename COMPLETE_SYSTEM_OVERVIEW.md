# DRAFT ROOM INVENTORY SYSTEM - COMPLETE OVERVIEW

## APPLICATION STRUCTURE

### Core Tech Stack
- **Backend**: Python Flask with Flask-SQLAlchemy
- **Database**: SQLite (draftroom_inventory.db)
- **Frontend**: HTML5, Jinja2 templates, CSS3, JavaScript
- **Security**: Flask-Limiter, password hashing, rate limiting, RBAC
- **Deployment**: Heroku (Procfile included)

---

## DATABASE MODELS

### User & Authentication
- **User**: id, username, password_hash, email, first_name, last_name, role_id, created_at, disabled
- **Role**: id, name, permissions (JSON)

### Inventory Management
- **Item**: id, name, prep_type, category, raw_freezer/cooler/out_days, prepped_freezer/cooler/out_days, on_hand_count, created_at
- **InventoryLot**: id, item_id, received_date, lot_number, lot_label, storage (freezer/cooler/out), state (raw/prepped), count_units, quantity, expiration_override, is_consumed, notes
- **ItemHistory**: id, item_id, action, quantity, before_value, after_value, changed_at

### Beer Management
- **Beer**: id, name, style, abv, description, brewery, active
- **BeerTap**: id, beer_id, tap_number, active, percent_full, created_at, updated_at

### Ordering & Reconciliation
- **Order**: id, user_id, status, created_at, delivery_date
- **OrderItem**: id, order_id, item_id, quantity_ordered, quantity_received
- **ReconcileConsumption**: id, reconcile_id, lot_id, units_used, created_at
- **Reconcile**: id, item_id, user_id, units_consumed, created_at, notes

### Suppliers
- **Supplier**: id, name, contact_person, phone, email, address, notes, created_at

### Audit & Logging
- **AuditLog**: id, user_id, action, entity_type, entity_id, message, details (JSON), created_at

---

## ALL ROUTES & ENDPOINTS

### AUTHENTICATION (3 routes)
- GET  /login                              -> login page
- POST /login                              -> handle login
- GET  /logout                             -> logout

### DASHBOARD (1 route)
- GET  /                                   -> dashboard home

### ITEMS - LIST & MANAGE (18 routes)
- GET  /items                              -> list all items
- POST /items                              -> create new item
- GET  /items/<id>                         -> item detail
- POST /items/<id>                         -> update item
- GET  /items/<id>/edit                    -> edit form
- POST /items/<id>/on_hand                 -> update on-hand count
- GET  /items/<id>/history                 -> view item history
- POST /items/<id>/history/clear           -> clear history
- GET  /items/bulk                         -> bulk edit page
- POST /items/bulk                         -> save bulk edits

### LOTS - INVENTORY BOX MANAGEMENT (14 routes)
- GET  /items/<id>/lots                    -> view lots for item
- GET  /items/<id>/lots/new                -> add new lot form
- POST /items/<id>/lots/new                -> save new lot
- POST /items/<id>/lots/receive            -> alternative save endpoint
- GET  /items/<id>/lots/bulk               -> bulk receive form
- POST /items/<id>/lots/bulk               -> save bulk lots
- POST /items/<id>/lots/bulk_delete        -> delete multiple lots
- GET  /lots/<id>/edit                     -> edit lot form
- POST /lots/<id>/edit                     -> save lot edits
- POST /lots/<id>/delete                   -> delete single lot
- GET  /lots/<id>                          -> view lot details
- GET  /lots                                -> view all lots across items

### BEERS - MANAGEMENT (15 routes)
- GET  /beers/dashboard                    -> beer dashboard
- GET  /beers                               -> list beers
- POST /beers                               -> create beer
- GET  /beers/<id>/edit                    -> edit beer
- POST /beers/<id>/edit                    -> save beer
- POST /beers/receive                      -> receive kegs
- GET  /beers/bulk                         -> bulk edit beers
- POST /beers/bulk                         -> save bulk beer edits
- GET  /beers/taps/assign                  -> assign beer to tap
- POST /beers/taps/assign                  -> save tap assignment
- POST /beers/taps/remove                  -> remove beer from tap
- POST /beers/taps/save                    -> save tap percents
- POST /beers/taps/save_json               -> save tap data as JSON
- POST /beers/tap/set                      -> set tap beer
- POST /beers/tap/percent                  -> update tap percent
- GET  /beers/taps/cups_preview            -> preview cup counts
- POST /beers/dashboard/add-to-tap         -> quick add to tap

### ORDERS (4 routes)
- GET  /order                               -> create order page
- POST /order                               -> save new order
- GET  /orders                              -> list orders
- GET  /orders/<id>/edit                   -> edit order
- POST /orders/<id>/edit                   -> save order edit

### RECONCILE (6 routes)
- GET  /reconcile                           -> reconcile page
- POST /reconcile                           -> save reconciliation
- GET  /reconcile/<id>/edit                -> edit reconcile
- POST /reconcile/<id>/edit                -> save reconcile edit
- POST /reconcile/<id>/undo                -> undo reconciliation
- GET  /reconcile/history                  -> reconcile history

### PREP (1 route)
- GET  /prep                                -> prep dashboard

### MOVE/BOXES (1 route)
- GET  /move_boxes                         -> move boxes interface

### BATCH (1 route)
- GET  /batch                              -> batch operations

### SUPPLIERS (4 routes)
- GET  /suppliers                           -> list suppliers
- POST /suppliers                           -> create supplier
- GET  /suppliers/<id>/edit                -> edit supplier
- POST /suppliers/<id>/edit                -> save supplier

### USERS & ROLES (8 routes)
- GET  /users                               -> list users
- POST /users                               -> create user
- GET  /users/<id>/edit                    -> edit user
- POST /users/<id>/edit                    -> save user
- GET  /admin/roles                        -> manage roles
- POST /admin/roles                        -> create role
- GET  /admin/roles/<id>/edit              -> edit role
- POST /admin/roles/<id>/edit              -> save role

### AUDIT (1 route)
- GET  /audit                              -> view audit log

### ADMIN/BACKUP (2 routes)
- POST /admin/backup/import                -> import backup
- GET  /admin/backup/download              -> download backup

---

## KEY FEATURES

### 1. INVENTORY MANAGEMENT
- Track items by storage location (freezer/cooler/out)
- Track item state (raw/prepped/consumed)
- FIFO inventory rotation
- Expiration date calculation
- Bulk operations support
- History tracking for all changes

### 2. BEER MANAGEMENT
- Multiple tap assignments
- Percentage tracking per tap
- Beer inventory linked to lots
- Keg receiving system

### 3. RECONCILIATION
- Track units consumed vs. expected
- FIFO lot reduction
- Undo capability with history
- Detailed consumption records

### 4. ROLE-BASED ACCESS CONTROL (RBAC)
- Viewer: Read-only access
- Manager: Can edit inventory & orders
- Admin: Full system access including users & roles
- Permission checks on every protected route

### 5. AUDIT LOGGING
- Every action logged with timestamp
- User tracking
- Change history with before/after values
- Entity type and ID tracking
- Export capability

### 6. SECURITY
- Rate limiting (5 attempts/min on login, 200/day general, 50/hour)
- Password hashing with Werkzeug
- Secure session cookies (HttpOnly, Secure, SameSite)
- Input validation & sanitization
- Generic error messages (prevent user enumeration)
- Environment variable configuration
- CSRF protection via Jinja2

### 7. MOBILE OPTIMIZATION
- iPhone app-like bottom navigation
- Safe area support for notches
- Touch-friendly interface (48px buttons)
- Responsive layout
- Sidebar hidden on mobile

### 8. THEME SUPPORT
- Dark theme (default)
- Light theme toggle
- CSS variables for all colors
- localStorage persistence

---

## 40 TEMPLATE FILES

### Base Layout
- base.html - Main layout with navigation

### Authentication
- login.html - Login page

### Dashboard & Navigation
- dashboard.html / home.html - Home page

### Items Management
- items.html - List items
- item_form.html - Add/edit item
- item_hub.html - Item details
- item_history.html - Item change history
- item_lots.html - View lots for item
- items_bulk.html - Bulk edit items

### Lots/Boxes
- lot_add.html - Add lot form
- lot_bulk.html - Bulk receive form
- lot_edit.html - Edit lot form
- lot_form.html - Lot form component
- lot_view.html - View lot details
- lots.html - List all lots

### Beers
- beers_dashboard.html - Beer dashboard
- beers.html - List beers
- beers_manage.html - Manage beers
- beers_bulk_add.html - Bulk add beers
- beers_bulk_edit.html - Bulk edit beers
- beers_edit.html - Edit beer form

### Orders
- order.html - Create order
- orders.html - List orders
- edit_order.html - Edit order

### Reconciliation
- reconcile.html - Reconcile page
- reconcile_edit.html - Edit reconciliation
- reconcile_history.html - Reconciliation history

### Suppliers
- suppliers.html - List suppliers
- supplier_form.html - Add/edit supplier

### Users & Roles
- users.html - List users
- user_form.html - Add/edit user
- roles.html - Manage roles
- role_form.html - Add/edit role

### Operations
- prep.html - Prep dashboard
- move_boxes.html - Move boxes interface
- batch.html - Batch operations

### Audit
- audit.html - Audit log viewer

---

## STATIC FILES

### CSS (style.css)
- Modern dark theme design
- CSS variables for all colors
- Responsive grid layouts
- Sidebar navigation (collapsible)
- Mobile-optimized
- Light theme support
- Professional styling for:
  - Forms & inputs
  - Tables & lists
  - Buttons & alerts
  - Navigation
  - Cards & sections

### JavaScript (app.js)
- Dropdown functionality
- Mobile bottom navigation
- Sidebar collapse toggle
- Theme toggle (light/dark)
- Mobile menu handling
- Page visibility updates

### Assets
- favicon.svg, favicon-96x96.png
- apple-touch-icon.png
- site.webmanifest (PWA)
- web-app-manifest images (192x192, 512x512)

---

## HELPER FUNCTIONS

### Date & Time
- `compute_lot_expiration()` - Calculate expiration based on storage/state
- `days_left()` - Days until expiration
- `parse_required_date()` - Parse date from form

### Lot Management
- `next_lot_number()` - Get next lot number for item
- `fifo_reduce_prepped_lots()` - FIFO inventory reduction
- `restore_prepped_lots()` - Undo consumption
- `_lots_fifo_distribute_remaining()` - Distribute inventory FIFO

### Input Validation
- `sanitize_input()` - Clean user input
- `is_valid_username()` - Validate username format
- `is_valid_email()` - Validate email format
- `validate_password_strength()` - Check password security
- `unique_ints()` - Parse unique integers from list
- `to_int()` - Safe integer conversion
- `norm_state()` - Normalize state values
- `norm_storage()` - Normalize storage values

### Security
- `is_logged_in()` - Check if user authenticated
- `require_view_access()` - Guard: view permission
- `require_inventory_edit()` - Guard: inventory edit permission
- `require_manager_access()` - Guard: manager+ permission
- `require_admin_access()` - Guard: admin only
- `audit_log()` - Log action to audit trail

---

## CONFIGURATION

### Flask Configuration
- SECRET_KEY: From environment or secure random fallback
- DATABASE: SQLite at sqlite:///draftroom_inventory.db
- SESSION_COOKIE_HTTPONLY: True (prevent XSS)
- SESSION_COOKIE_SECURE: True in production
- SESSION_COOKIE_SAMESITE: Lax (CSRF protection)
- PERMANENT_SESSION_LIFETIME: 24 hours

### Environment Variables Required
- SECRET_KEY: Application secret for sessions
- FLASK_ENV: development/production
- DATABASE_URL: (optional) Override default SQLite

### Rate Limiting
- General: 200 per day, 50 per hour
- Login: 5 per minute
- Custom limits per route

---

## DEPLOYMENT

### Procfile
```
web: gunicorn app:app
```

### Requirements
- Flask
- Flask-SQLAlchemy
- Flask-Limiter
- Werkzeug
- Gunicorn

### Production Checklist
1. Set FLASK_ENV=production
2. Set secure SECRET_KEY
3. Enable HTTPS
4. Configure DATABASE_URL if not using SQLite
5. Run `flask db upgrade` if using migrations
6. Set up monitoring/logging

---

## CURRENT ISSUES FIXED

1. ✅ Unicode encoding errors in validation scripts
2. ✅ Collapsed sidebar width (70px)
3. ✅ Added missing /lots navigation link
4. ✅ Fixed light theme colors in dark mode CSS

---

## MISSING ROUTES (NEEDS IMPLEMENTATION)

- GET /lots - Master list of ALL lots (referenced in navigation)
- This should show all lots across all items with filtering/search

---

## KEY BUSINESS LOGIC

### FIFO Inventory Management
- Lots ordered by received_date ASC (oldest first)
- When consuming/using inventory, always take from oldest lot first
- Supports partial lot consumption

### Expiration Calculation
- Based on Item settings (raw/prepped x freezer/cooler/out)
- Storage location affects shelf life
- Preparation state affects shelf life
- Can override with manual expiration date

### Beer Tap Management
- Multiple beers can be assigned to taps
- Track percent full for each tap
- Track cups poured per tap
- Automatic calculations for inventory impact

### Role-Based Security
- Every route has permission check
- Access denied returns 403 or redirect
- Generic error messages prevent user enumeration
- Audit log tracks all attempted access

### Audit Trail
- Every CRUD operation logged
- User, timestamp, action, entity tracked
- Before/after values for updates
- Cannot be disabled (security critical)

---

## COMPLETE FILE STRUCTURE

```
draft-room-inventory/
├── app.py                          # Main Flask application (~5000+ lines)
├── requirements.txt                # Python dependencies
├── Procfile                        # Heroku deployment
├── runtime.txt                     # Python version
├── static/
│   ├── style.css                   # Main stylesheet
│   ├── app.js                      # Client-side JavaScript
│   ├── favicon.svg
│   ├── favicon-96x96.png
│   ├── apple-touch-icon.png
│   ├── site.webmanifest            # PWA manifest
│   ├── web-app-manifest-*.png
│   └── logo-white.svg
├── templates/                      # 40 HTML templates
│   ├── base.html                   # Base template
│   ├── login.html
│   ├── dashboard.html / home.html
│   ├── items*.html (7 files)
│   ├── lot*.html (6 files)
│   ├── beers*.html (7 files)
│   ├── order*.html (3 files)
│   ├── reconcile*.html (3 files)
│   ├── supplier*.html (2 files)
│   ├── user*.html (2 files)
│   ├── role*.html (2 files)
│   ├── audit.html
│   ├── prep.html
│   ├── move_boxes.html
│   └── batch.html
├── instance/                       # Instance folder (db)
│   └── draftroom_inventory.db
├── README.md
└── [Various documentation files]
```

---

## SUMMARY

**Draft Room Inventory System** is a comprehensive Flask-based web application for managing restaurant/bar inventory with a focus on:
- Food/beverage item tracking by location and expiration
- FIFO-based inventory consumption
- Beer tap management
- Role-based access control
- Complete audit trail
- Mobile-optimized interface
- Professional dark/light theme
- Production-ready security measures

The system handles ~18 Flask routes across all major functionality areas and maintains referential integrity through SQLAlchemy relationships.
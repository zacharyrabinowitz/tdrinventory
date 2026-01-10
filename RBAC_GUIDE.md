# Role-Based Access Control (RBAC) System

## Overview

The system now includes a comprehensive Role-Based Access Control system that allows admins to create custom user roles with fine-grained permissions. Every role except "Admin" can have its access customized.

## Key Features

### 1. **Admin Access**
- The "Admin" role has access to everything
- Cannot be modified or deleted
- Users assigned to Admin role can see and do everything

### 2. **Custom Roles**
- Create unlimited custom roles
- Assign granular permissions to each role
- All custom roles can be edited or deleted (unless users are assigned to them)

### 3. **Available Permissions**

The following permissions can be configured for each role:

#### Items
- `can_view_items` - View and list items
- `can_edit_items` - Create, edit items
- `can_delete_items` - Delete items

#### Lots
- `can_view_lots` - View lot information
- `can_edit_lots` - Create, edit lots
- `can_delete_lots` - Delete lots

#### Orders
- `can_view_orders` - View orders
- `can_create_orders` - Create new orders
- `can_delete_orders` - Delete orders

#### Beers
- `can_view_beers` - View beer inventory
- `can_edit_beers` - Add, edit beers
- `can_delete_beers` - Delete beers

#### Users
- `can_view_users` - View user list
- `can_create_users` - Create new users
- `can_delete_users` - Delete users

#### Suppliers
- `can_view_suppliers` - View suppliers
- `can_edit_suppliers` - Add, edit suppliers
- `can_delete_suppliers` - Delete suppliers

#### System
- `can_view_audit` - View audit log
- `can_reconcile` - Reconcile inventory

## Usage

### Creating a New Role

1. Go to **Admin > Roles** (sidebar)
2. Click **+ Create New Role**
3. Enter a name for the role (e.g., "Inventory Manager", "Viewer")
4. Check the permissions you want this role to have
5. Click **Save Role**

### Editing a Role

1. Go to **Admin > Roles**
2. Click **Edit** on any custom role
3. Modify the role name or permissions
4. Click **Save Role**

### Deleting a Role

1. Go to **Admin > Roles**
2. Click **Delete** on the role
3. Confirm deletion
- Note: You cannot delete a role if users are assigned to it. Reassign those users first.

### Assigning Roles to Users

1. Go to **Admin > Users**
2. Edit a user
3. Select their role from the dropdown (shows available roles)
4. Save

## Default Behavior

### Legacy Users
- Users with string-based roles (admin, manager, staff) still work
- The system maintains backward compatibility
- To use new RBAC, assign users to new Role objects

### Creating Initial Admin Role
The system automatically recognizes "admin" role users as admins during migration.

## Implementation Details

### Database Schema

**roles** table:
- `id` - Primary key
- `name` - Unique role name
- `is_admin` - Boolean (true only for admin role)
- `permissions` - JSON object with permission flags
- `created_at` - Creation timestamp

**users** table additions:
- `role_id` - Foreign key to roles table (optional)
- `role` - Legacy string role (maintained for compatibility)

### Permission Storage

Permissions are stored as a JSON object in the `permissions` column:

```json
{
  "can_view_items": true,
  "can_edit_items": true,
  "can_delete_items": false,
  "can_view_lots": true,
  "can_edit_lots": true,
  "can_create_users": false
}
```

### Authorization Flow

1. When a user tries to access a feature, the system checks:
   - Is the user assigned to a Role object?
   - Does that role have the required permission?
2. If no Role object is assigned, falls back to legacy string role check
3. Admin role always returns `True` for any permission check

## Examples

### Example 1: Inventory Manager Role
```
Permissions:
✓ can_view_items
✓ can_edit_items
✓ can_delete_items
✓ can_view_lots
✓ can_edit_lots
✓ can_delete_lots
✓ can_view_orders
✓ can_view_beers
✓ can_edit_beers
```

### Example 2: Viewer Role (Read-Only)
```
Permissions:
✓ can_view_items
✓ can_view_lots
✓ can_view_orders
✓ can_view_beers
✓ can_view_suppliers
✓ can_view_users
```

### Example 3: Beer Manager Role
```
Permissions:
✓ can_view_beers
✓ can_edit_beers
✓ can_delete_beers
✓ can_view_orders
✓ can_create_orders
```

## API Endpoints

### Admin Only
- `GET /admin/roles` - View role management page
- `POST /admin/roles/create` - Create new role
- `POST /admin/roles/<id>/update` - Update role
- `POST /admin/roles/<id>/delete` - Delete role
- `GET /admin/roles/<id>/data` - Get role data (JSON)

## Audit Trail

All role management actions are logged in the audit log:
- Role creation
- Role updates
- Role deletion

You can view these in the **Audit Log** page.

## Security Notes

1. **Admin Role Protection** - Cannot be modified or deleted through UI
2. **Permission Validation** - All permission assignments are validated server-side
3. **User Assignment Validation** - Cannot delete a role if users are assigned to it
4. **Backward Compatibility** - Legacy string roles continue to work

## Migration Guide

If you have existing users with string roles:

1. The system will continue to work as-is
2. To use new RBAC:
   - Create the new roles you want
   - Edit each user and assign them to a Role object
   - Their permissions will then be based on the Role

## Troubleshooting

**"Cannot delete role" error**
- Check if any users are assigned to this role
- Edit users, change their role to another role
- Try deleting again

**User can't access a feature after role change**
- Verify the role has the permission enabled
- Check "Audit Log" for permission-related entries
- Ensure you clicked "Save Role"

**Role appears but permissions not working**
- Clear browser cache
- Refresh the page
- Verify permissions are checked in the role editor

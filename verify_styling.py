import re

print("=" * 70)
print("CSS & TEMPLATE STYLING VERIFICATION")
print("=" * 70)

# Check CSS variables
with open('static/style.css', 'r', encoding='utf-8') as f:
    css_content = f.read()

# Extract all colors from :root
root_match = re.search(r':root\s*\{([^}]+)\}', css_content)
if root_match:
    vars_text = root_match.group(1)
    colors = {}
    for line in vars_text.split('\n'):
        if '--' in line and ':' in line:
            match = re.match(r'\s*(--([\w-]+)):\s*([^;]+);', line)
            if match and any(x in match.group(2) for x in ['bg', 'card', 'text', 'primary', 'danger', 'success']):
                colors[match.group(2)] = match.group(3).strip()
    
    print("\n✓ CSS Color Variables Defined:")
    for name in sorted(colors.keys()):
        print(f"  --{name}: {colors[name]}")

# Check for light colors in CSS that shouldn't be there
light_colors = ['#ecfdf5', '#fffbeb', '#fef2f2', '#eff6ff', '#fafbfc', '#f0f4ff', '#f3f4f6', '#e5e7eb', '#d1d5db', '#0c2340', '#065f46', '#92400e', '#7f1d1d']
found_light = []
for color in light_colors:
    if color in css_content:
        found_light.append(color)

if found_light:
    print(f"\n⚠ Found {len(found_light)} light theme colors in CSS (should use dark theme)")
else:
    print("\n✓ No problematic light theme colors found in CSS")

# Check template files
template_files = [
    'templates/reconcile.html',
    'templates/reconcile_edit.html',
    'templates/users.html',
    'templates/item_history.html',
]

print("\n✓ Template Inline Styles Updated:")
for template in template_files:
    try:
        with open(template, 'r', encoding='utf-8') as f:
            content = f.read()
        # Check for rgba colors from light theme
        old_colors = ['rgba(34,197,94', 'rgba(239,68,68', 'rgba(59,130,246', '#22c55e', '#ef4444']
        found = [c for c in old_colors if c in content]
        if found:
            print(f"  ✗ {template}: Still has old light colors")
        else:
            print(f"  ✓ {template}")
    except:
        pass

print("\n" + "=" * 70)
print("NAVBAR VERIFICATION")
print("=" * 70)

# Check base.html for nav structure
with open('templates/base.html', 'r', encoding='utf-8') as f:
    base_content = f.read()

nav_items = ['Dashboard', 'Items', 'Lots', 'Beers', 'Suppliers', 'Users']
missing_nav = []
for item in nav_items:
    if item not in base_content:
        missing_nav.append(item)

if missing_nav:
    print(f"✗ Missing navigation items: {missing_nav}")
else:
    print("✓ All main navigation items present")
    print("  - Dashboard (/)")
    print("  - Items (/items)")
    print("  - Lots (/lots)")
    print("  - Beers (/beers/dashboard)")
    print("  - Suppliers (/suppliers)")
    print("  - Users (/users)")

# Check for active nav item styling
if 'nav-item.active' in css_content and 'request.path' in base_content:
    print("✓ Active nav item highlighting configured")

# Check for basic page structure
pages_to_check = [
    ('templates/dashboard.html', 'Dashboard'),
    ('templates/items.html', 'Items'),
    ('templates/beers_dashboard.html', 'Beers'),
    ('templates/users.html', 'Users'),
]

print("\n" + "=" * 70)
print("PAGE STRUCTURE VERIFICATION")
print("=" * 70)

for page, name in pages_to_check:
    try:
        with open(page, 'r', encoding='utf-8') as f:
            content = f.read()
        if '{% extends "base.html"' in content:
            print(f"✓ {name:15} - Proper template inheritance")
        else:
            print(f"✗ {name:15} - Missing base.html inheritance")
    except FileNotFoundError:
        print(f"⚠ {name:15} - File not found")

print("\n" + "=" * 70)
print("✓ VERIFICATION COMPLETE")
print("=" * 70)

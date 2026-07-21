import re

with open('static/style.css', 'r', encoding='utf-8') as f:
    content = f.read()

# Basic CSS syntax validation
issues = []

# Check for unmatched braces
open_braces = content.count('{')
close_braces = content.count('}')
if open_braces != close_braces:
    issues.append(f'Unmatched braces: {open_braces} open, {close_braces} close')

print(f"Braces: {open_braces} open, {close_braces} close")

# Check for variables that are used but not defined
used_vars = set(re.findall(r'var\((--[\w-]+)\)', content))
root_section = re.search(r':root\s*\{([^}]+)\}', content)
defined_vars = set()
if root_section:
    defined_vars = set(re.findall(r'--[\w-]+', root_section.group(1)))

print(f"Used vars: {len(used_vars)}")
print(f"Defined vars: {len(defined_vars)}")

undefined = used_vars - defined_vars
print(f"Undefined vars: {len(undefined)}")

if undefined:
    print('\nUndefined variables:')
    for var in sorted(undefined):
        print(f'  - {var}')

if issues:
    print('\nIssues found:')
    for issue in issues:
        print(f'  - {issue}')
else:
    if not undefined:
        print('\n[OK] CSS file is valid and complete!')

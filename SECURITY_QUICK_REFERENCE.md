# Security Quick Reference Guide

## For Development

### Running Locally with Security Features

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment variables
export SECRET_KEY="dev-secret-key"
export BREAK_GLASS_ADMIN_USERNAME="admin"
export BREAK_GLASS_ADMIN_PASSWORD="dev-password"

# 3. Run the application
python app.py
```

### Testing Password Validation

```python
from app import validate_password_strength

# Test weak password
is_valid, msg = validate_password_strength("weak")
print(f"Valid: {is_valid}, Message: {msg}")
# Output: Valid: False, Message: Password must be at least 12 characters long

# Test strong password
is_valid, msg = validate_password_strength("MyP@ssw0rd123")
print(f"Valid: {is_valid}, Message: {msg}")
# Output: Valid: True, Message: 
```

### Testing Input Validation

```python
from app import is_valid_username, is_valid_email, sanitize_input

# Username validation
assert is_valid_username("john_doe") == True
assert is_valid_username("a") == False  # Too short
assert is_valid_username("john doe") == False  # Spaces not allowed

# Email validation
assert is_valid_email("user@example.com") == True
assert is_valid_email("invalid-email") == False

# Input sanitization
result = sanitize_input("  hello  ", max_length=10)
print(result)  # Output: "hello"
```

---

## For Production Deployment

### Required Environment Variables

```bash
# SECURITY CRITICAL - Change these!
export FLASK_ENV=production
export SECRET_KEY="your-secure-32-character-secret-here"
export BREAK_GLASS_ADMIN_USERNAME="secure-username"
export BREAK_GLASS_ADMIN_PASSWORD="secure-password-12chars-with-numbers-and-symbols"

# Optional - defaults are secure
export SESSION_COOKIE_SECURE=true
export SESSION_COOKIE_HTTPONLY=true
export SESSION_COOKIE_SAMESITE=Lax
```

### Generating a Secure Secret Key

```bash
# Option 1: Python
python -c "import secrets; print(secrets.token_hex(32))"

# Option 2: OpenSSL
openssl rand -hex 32

# Output example: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### Quick Security Checklist Before Going Live

```bash
# 1. Verify environment variables are set
env | grep -E "FLASK_ENV|SECRET_KEY|BREAK_GLASS"

# 2. Check HTTPS is enabled (check reverse proxy/load balancer)
curl -I https://your-domain.com

# 3. Verify security headers are present
curl -I https://your-domain.com | grep -E "X-Frame|X-Content|CSP"

# 4. Test rate limiting on login
# Attempt 6 quick logins - 6th should be rate limited

# 5. Review audit logs
# SELECT * FROM audit_log WHERE action='login' ORDER BY created_at DESC LIMIT 10;
```

---

## Common Security Tasks

### Rotate Secrets

```bash
# 1. Generate new SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# 2. Update environment variable
export SECRET_KEY="new-secret-key"

# 3. Restart application (sessions will be invalidated)
# All users will need to log in again

# 4. Monitor for issues
tail -f logs/app.log | grep "login"
```

### Review Audit Logs for Security Events

```python
from app import db, AuditLog
from datetime import datetime, timedelta

# Check for failed login attempts in last 24 hours
yesterday = datetime.utcnow() - timedelta(days=1)
failed_logins = db.session.query(AuditLog).filter(
    AuditLog.action == 'login',
    AuditLog.created_at > yesterday
).all()

for log in failed_logins:
    print(f"{log.created_at} - {log.message} - {log.details}")

# Check for failed password changes
password_changes = db.session.query(AuditLog).filter(
    AuditLog.action.like('password%'),
    AuditLog.created_at > yesterday
).all()
```

### Check Session Configuration

```python
from app import app

# Verify secure session settings
print(f"HttpOnly: {app.config['SESSION_COOKIE_HTTPONLY']}")
print(f"Secure: {app.config['SESSION_COOKIE_SECURE']}")
print(f"SameSite: {app.config['SESSION_COOKIE_SAMESITE']}")
print(f"Lifetime: {app.config['PERMANENT_SESSION_LIFETIME']}")
```

### Change Break-Glass Credentials

```bash
# 1. Generate new credentials
NEW_USERNAME=$(python -c "import secrets; print(secrets.token_hex(8))")
NEW_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(16))")

# 2. Update environment variables
export BREAK_GLASS_ADMIN_USERNAME="$NEW_USERNAME"
export BREAK_GLASS_ADMIN_PASSWORD="$NEW_PASSWORD"

# 3. Store securely (e.g., in secrets manager)
# AWS Secrets Manager, HashiCorp Vault, or Azure Key Vault

# 4. Restart application
# systemctl restart draft-room-inventory
```

---

## Security Monitoring

### Monitor for Rate Limit Violations

```bash
# Check application logs for rate limit hits
grep "429\|Rate limit" logs/app.log

# Or query Flask's limiter storage
# Check Redis/cache logs if using Redis backend
```

### Monitor for Suspicious Login Activity

```python
from app import db, AuditLog
from datetime import datetime, timedelta

# Logins from unusual IPs
recent_logins = db.session.query(AuditLog).filter(
    AuditLog.action == 'login',
    AuditLog.created_at > datetime.utcnow() - timedelta(days=1)
).all()

# Find multiple failed attempts
suspicious_ips = {}
for log in recent_logins:
    ip = log.details.get('ip_address', 'unknown')
    suspicious_ips[ip] = suspicious_ips.get(ip, 0) + 1

# Alert if more than 10 failed attempts from same IP
for ip, count in suspicious_ips.items():
    if count > 10:
        print(f"ALERT: {count} login attempts from {ip}")
```

### Check Database Integrity

```python
from app import db

# Verify database is accessible
try:
    result = db.session.execute(text("SELECT COUNT(*) FROM user"))
    user_count = result.scalar()
    print(f"Database OK - {user_count} users")
except Exception as e:
    print(f"Database ERROR: {e}")
```

---

## Debugging Security Issues

### Enable Debug Mode for Investigation (DEV ONLY)

```python
# In app.py - DO NOT ENABLE IN PRODUCTION
app.config['DEBUG'] = False  # ALWAYS False in production
```

### Check What Headers Are Being Sent

```bash
# View all response headers
curl -v https://your-domain.com 2>&1 | grep "^<"

# Extract security headers only
curl -s -I https://your-domain.com | grep -E "^X-|^Content-Security|^Referrer|^Permissions"
```

### Test HTTPS Configuration

```bash
# Check SSL/TLS certificate validity
openssl s_client -connect your-domain.com:443 -servername your-domain.com

# Check certificate expiration
echo | openssl s_client -servername your-domain.com -connect your-domain.com:443 2>/dev/null | openssl x509 -noout -dates
```

---

## Incident Response Quick Steps

### If You Suspect a Breach:

1. **Isolate the system** from network
2. **Preserve logs** for analysis
3. **Rotate all credentials**
   ```bash
   export SECRET_KEY="emergency-new-secret"
   export BREAK_GLASS_ADMIN_PASSWORD="emergency-new-password"
   ```
4. **Restart the application**
5. **Review audit logs**
   ```python
   # Check for unauthorized access
   from app import AuditLog
   unauthorized = AuditLog.query.filter(AuditLog.action.like('%unauthorized%')).all()
   ```
6. **Notify users** to change passwords
7. **Post-incident review** of what went wrong

---

## Useful Commands

```bash
# Check Python security packages are updated
pip install --upgrade cryptography werkzeug flask flask-sqlalchemy

# Run security audit on dependencies
pip install safety
safety check

# Generate random passwords for testing
python -c "import secrets; print(secrets.token_hex(16))"

# Check if port 443 (HTTPS) is open
netstat -tlnp | grep 443

# View recent authentication logs
grep -i "auth\|login" logs/app.log | tail -20

# Monitor real-time logs
tail -f logs/app.log | grep -E "login|auth|error"
```

---

## Resources

- **Flask Security**: https://flask.palletsprojects.com/security/
- **OWASP Top 10**: https://owasp.org/www-project-top-ten/
- **Python Security**: https://python.readthedocs.io/en/latest/library/security_warnings.html
- **Werkzeug Security**: https://werkzeug.palletsprojects.com/en/latest/security/
- **SQLAlchemy Security**: https://docs.sqlalchemy.org/en/14/faq/security.html

---

**Last Updated:** January 9, 2026

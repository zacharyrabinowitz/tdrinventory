# Security Implementation Guide

This document outlines the security improvements implemented in the Draft Room Inventory System.

## Overview

The application has been hardened with multiple security layers to protect against common web vulnerabilities and ensure data integrity.

---

## Key Security Features Implemented

### 1. **Session Security**
- ✅ **HttpOnly Cookies**: Session cookies cannot be accessed by JavaScript, preventing XSS token theft
- ✅ **Secure Flag**: Cookies only transmitted over HTTPS in production
- ✅ **SameSite Policy**: Set to `Lax` to prevent CSRF attacks
- ✅ **Session Lifetime**: Configured to 24 hours with automatic expiration

**Configuration:**
```python
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True  # In production only
SESSION_COOKIE_SAMESITE = "Lax"
PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
```

### 2. **Rate Limiting**
- ✅ **Login Endpoint**: Limited to 5 attempts per minute per IP address
- ✅ **Default Limits**: 50 requests per hour, 200 per day per IP
- ✅ Prevents brute force attacks and denial of service

**Configuration:**
```python
@app.post("/login")
@limiter.limit("5 per minute")
def login_post():
    ...
```

### 3. **Security Headers**
The application automatically adds the following HTTP security headers to all responses:

| Header | Purpose | Value |
|--------|---------|-------|
| `X-Frame-Options` | Prevents clickjacking | `SAMEORIGIN` |
| `X-XSS-Protection` | XSS protection (older browsers) | `1; mode=block` |
| `X-Content-Type-Options` | Prevents MIME sniffing | `nosniff` |
| `Content-Security-Policy` | Restricts content sources | See below |
| `Referrer-Policy` | Controls referrer info | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | Restricts browser features | See below |

**Content-Security-Policy Details:**
```
default-src 'self'                          # Only from same origin by default
script-src 'self' https://cdnjs.cloudflare.com  # Scripts from trusted CDNs
style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com
font-src 'self' https://cdnjs.cloudflare.com
img-src 'self' data:                        # Images from origin or data URIs
connect-src 'self'                          # API calls to origin only
frame-ancestors 'self'                      # Can only be framed by same origin
```

### 4. **Input Validation**
- ✅ **Length Limits**: Username (max 255), Password (max 512)
- ✅ **Required Fields**: Username and password must be provided
- ✅ **Generic Error Messages**: Prevent user enumeration attacks
- ✅ Additional validation on all forms and API endpoints

### 5. **Secret Key Management**
- ✅ **Environment-Based**: Reads from `SECRET_KEY` environment variable
- ✅ **Secure Generation**: Falls back to `secrets.token_hex(32)` if not set
- ⚠️ **Production Requirement**: Must be set via environment variables, not hardcoded

**Setup Instructions:**
```bash
# Generate a secure secret key
python -c "import secrets; print(secrets.token_hex(32))"

# Add to environment
export SECRET_KEY="your-generated-secret-key"
```

### 6. **Password Security**
- ✅ **Hashing**: Uses Werkzeug's PBKDF2-based password hashing
- ✅ **Break-Glass Credentials**: Now loaded from environment variables
- ✅ **Password Validation Function**: Available for password changes

**Password Strength Requirements (for new passwords):**
- Minimum 12 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one number
- At least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)

**Validation Function:**
```python
from app import validate_password_strength

is_valid, message = validate_password_strength(password)
if not is_valid:
    flash(f"Password too weak: {message}", "error")
```

### 7. **SQL Injection Prevention**
- ✅ **SQLAlchemy ORM**: Uses parameterized queries exclusively
- ✅ **Safe Text Queries**: When raw SQL is needed, uses proper parameter binding

### 8. **XSS Prevention**
- ✅ **Jinja2 Auto-Escaping**: HTML is automatically escaped in templates
- ✅ **Content-Security-Policy**: Prevents inline scripts
- ✅ **No `|safe` filters**: Only used for trusted internal content

---

## Configuration

### Environment Variables

Create a `.env` file in the project root (or set them in your deployment environment):

```bash
# Security
FLASK_ENV=production
SECRET_KEY=your-secret-key-here-32-characters-minimum
BREAK_GLASS_ADMIN_USERNAME=your-admin-username
BREAK_GLASS_ADMIN_PASSWORD=your-secure-password

# Database
SQLALCHEMY_DATABASE_URI=sqlite:///draftroom_inventory.db

# Session
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_HTTPONLY=true
SESSION_COOKIE_SAMESITE=Lax
```

**Important:** Never commit `.env` files with real secrets to version control!

### Loading Environment Variables

The application uses `python-dotenv` to load environment variables:

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Security Checklist for Production Deployment

- [ ] Set `FLASK_ENV=production`
- [ ] Generate and set a unique `SECRET_KEY` (at least 32 characters)
- [ ] Change `BREAK_GLASS_ADMIN_USERNAME` and `BREAK_GLASS_ADMIN_PASSWORD`
- [ ] Use HTTPS/TLS for all connections
- [ ] Enable `SESSION_COOKIE_SECURE=true`
- [ ] Set up regular database backups
- [ ] Configure logging to capture security events
- [ ] Review and restrict file upload permissions
- [ ] Set up Web Application Firewall (WAF) rules
- [ ] Enable security monitoring and alerting
- [ ] Keep dependencies updated: `pip install --upgrade -r requirements.txt`
- [ ] Run security audit: `pip install safety && safety check`

---

## Audit Logging

All security-related actions are logged to the audit log:
- User login/logout events
- Password changes
- User role changes
- Administrative actions
- Break-glass admin access

Query audit logs for security review:
```python
from app import AuditLog
logs = AuditLog.query.filter(AuditLog.action == 'login').all()
```

---

## Vulnerability Mitigation

### Cross-Site Request Forgery (CSRF)
- ✅ SameSite cookies prevent most CSRF attacks
- ✅ Recommend using Flask-WTF CSRF tokens for forms (already in requirements.txt)

### SQL Injection
- ✅ SQLAlchemy ORM prevents SQL injection
- ✅ All user input is parameterized

### Cross-Site Scripting (XSS)
- ✅ Jinja2 auto-escaping
- ✅ Content-Security-Policy headers
- ✅ No unsafe inline scripts

### Brute Force Attacks
- ✅ Rate limiting on login endpoint (5 attempts/minute)
- ✅ Generic error messages prevent user enumeration

### Session Hijacking
- ✅ HttpOnly cookies prevent XSS token theft
- ✅ Secure flag ensures HTTPS-only transmission
- ✅ SameSite policy prevents CSRF token leakage

### Information Disclosure
- ✅ Generic error messages don't reveal system details
- ✅ Audit logs stored securely in database
- ✅ Sensitive data (passwords) never logged

---

## Third-Party Security

### Dependencies
Keep all dependencies updated regularly:
```bash
pip install --upgrade -r requirements.txt
```

Run security checks:
```bash
pip install safety
safety check
```

### Known Security Issues
- Review security advisories: https://nvd.nist.gov/
- Check Flask security news: https://flask.palletsprojects.com/security/
- Monitor SQLAlchemy advisories: https://github.com/sqlalchemy/sqlalchemy

---

## Incident Response

If a security incident occurs:

1. **Immediately isolate** the affected system
2. **Review audit logs** for unauthorized access
3. **Check database backups** for data integrity
4. **Rotate all credentials** (especially break-glass password)
5. **Update SECRET_KEY** and redeploy
6. **Review access logs** for other compromised accounts
7. **Notify affected users** and advise password changes
8. **Post-incident review**: Document what happened and improvements needed

---

## Additional Resources

- Flask Security Guide: https://flask.palletsprojects.com/en/latest/security/
- OWASP Top 10: https://owasp.org/www-project-top-ten/
- SQLAlchemy Security: https://docs.sqlalchemy.org/en/latest/faq/security.html
- CWE Common Weakness Enumeration: https://cwe.mitre.org/

---

## Questions or Security Issues?

If you discover a security vulnerability:
1. **Do not** open a public issue
2. Contact the development team directly
3. Provide detailed information about the vulnerability
4. Allow time for a patch before public disclosure

---

Last Updated: January 2026
Version: 1.0

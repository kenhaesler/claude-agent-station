---
name: Security
description: Input validation, auth, secrets management, and common vulnerabilities
role: employee
---

# Skill: Security

Apply this checklist to every code change touching user input, auth, or data.

## Input Validation
- Never trust user input — validate type, length, format, and range
- Use parameterized queries for all database operations (never string concatenation)
- Sanitize HTML output to prevent XSS (use framework escaping by default)
- Validate file paths to prevent path traversal (`../` attacks)
- Reject unexpected content types and oversized payloads

## Authentication & Authorization
- Hash passwords with bcrypt/argon2 (never MD5/SHA1 for passwords)
- Use constant-time comparison for tokens and secrets
- Enforce authorization on every endpoint — don't rely on UI hiding
- Set short expiry on tokens; implement refresh token rotation
- Rate-limit login attempts to prevent brute force

## Secrets Management
- Never hardcode secrets (API keys, passwords, tokens) in source code
- Use environment variables or a secrets manager
- Never log secrets — redact sensitive fields in log output
- Add `.env`, credential files, and private keys to `.gitignore`
- Rotate secrets after any suspected exposure

## Dependency Security
- Pin dependency versions to avoid supply-chain attacks
- Check for known CVEs before adding new dependencies
- Prefer well-maintained libraries with active security response

## Common Vulnerabilities
- **SQL injection**: use parameterized queries exclusively
- **XSS**: escape all dynamic content in HTML responses
- **CSRF**: require anti-CSRF tokens on state-changing requests
- **SSRF**: validate and allowlist outbound URLs
- **Command injection**: never pass user input to shell commands; use subprocess arrays

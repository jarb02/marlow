# Security Policy

## 🔒 Marlow takes security seriously

Marlow is a desktop automation tool that interacts with your operating system. We built security into the foundation — not as an afterthought.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | ✅ Yes             |
| 0.1.x   | ✅ Yes             |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Instead, report vulnerabilities privately:

1. **Email:** [TO BE CONFIGURED]
2. **GitHub Security Advisories:** Use the "Report a vulnerability" button in the Security tab of this repository.

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment:** Within 48 hours
- **Assessment:** Within 7 days
- **Fix:** As soon as possible, depending on severity

## Security Features

Marlow includes these security measures by default:

- **Kill Switch:** `Ctrl+Shift+Escape` immediately stops all automation
- **Confirmation Mode:** Enabled by default — every action requires approval
- **Blocked Apps:** Banking, password managers, and sensitive apps are never accessed
- **Blocked Commands:** Destructive system commands are blacklisted
- **Data Sanitization:** Credit cards, SSNs, passwords are redacted before sending to AI
- **Zero Telemetry:** No data ever leaves your machine. Ever.
- **Encrypted Logs:** Action logs are encrypted with AES-256

## Security Design Principles

1. **Default-secure:** New users start in full confirmation mode
2. **Least privilege:** Tools only access what they need
3. **Local-only:** All processing stays on your machine
4. **Transparent:** Every action is logged and auditable
5. **No telemetry:** We don't collect, send, or store any user data

---

*Política de Seguridad en Español: [Ver README-ES.md]*

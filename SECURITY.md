# Security Policy

## Reporting Vulnerabilities

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security issues through one of the following channels:

- **GitHub Security Advisories (preferred):**
  <https://github.com/allnew-llc/banto/security/advisories>

We will acknowledge your report within **48 hours** and aim to release a fix
within **7 days** for critical issues.

## What to Report

Examples of issues we want to hear about:

- Secret values leaked through `argv`, environment variables, or process listings
- Keychain access bypass or unauthorized secret retrieval
- CSRF or session issues in the web UI or register popup
- Value leakage in logs, error messages, or tracebacks
- Path traversal or file permission issues in vault storage
- Capability URL prediction or brute-force in tunneled MCP mode
- Injection attacks through driver sync parameters

## Scope

The following components are **in scope**:

| Component | Description |
|-----------|-------------|
| `banto` core | Vault, config, CLI, history, backup |
| Sync drivers | All platform drivers (Vercel, Fly.io, AWS, GCP, etc.) |
| MCP server | `banto-mcp` server and tool definitions |
| Web UI | Register popup, secret editor, CSRF protections |
| Register popup | Browser-based secret entry flow |

The following are **out of scope**:

| Component | Reason |
|-----------|--------|
| macOS Keychain / Windows Credential Manager | OS-level security; report to Apple / Microsoft |
| Third-party tunnel providers (ngrok, Cloudflare, etc.) | Report to the respective provider |
| Cloud provider APIs (AWS, GCP, Vercel, etc.) | Report to the respective provider |
| Denial of service via resource exhaustion | Low severity for a local CLI tool |

## Security Architecture

banto is designed with the following security principles:

- **ctypes + Security.framework** for macOS Keychain access -- no shell-out, no
  subprocess, no temporary files for Keychain operations.
- **stdin / tempfile for drivers** -- secret values are never passed through
  command-line arguments. All drivers receive values via stdin or
  mode-`0o600` temporary files.
- **CSRF protection** on the web UI -- all state-changing requests require a
  valid token.
- **Capability URLs** for tunneled MCP -- the MCP endpoint uses
  unguessable URLs rather than authentication headers.
- **Fail-closed history** -- if the audit log cannot be written, the operation
  is aborted rather than proceeding silently.
- **No external dependencies in core** -- the attack surface is limited to the
  Python standard library.

## Response Timeline

| Severity | Acknowledgment | Target Fix |
|----------|---------------|------------|
| Critical (secret leakage, RCE) | 48 hours | 7 days |
| High (auth bypass, CSRF) | 48 hours | 14 days |
| Medium (information disclosure) | 72 hours | 30 days |
| Low (hardening improvements) | 1 week | Next release |

## Credit

Security reporters will be acknowledged in the CHANGELOG and release notes
unless they prefer to remain anonymous. Let us know your preference when
reporting.

## Supported Versions

Security fixes are applied to the latest release only. We recommend always
running the most recent version of banto.

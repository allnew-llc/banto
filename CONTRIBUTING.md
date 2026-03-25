# Contributing to banto

Thank you for your interest in contributing to banto! This project provides a
zero-dependency secrets manager for Python, and we welcome contributions from the
community.

**Licensing note:** banto is dual-licensed under the Personal License (free for
individuals, students, and open-source projects) and the Commercial License (paid
for business use). By submitting a contribution, you agree that your work will be
made available under the same Dual License terms.

## How to Contribute

### Bug Reports

Open a [GitHub Issue](https://github.com/allnew-llc/banto/issues) with:

- Python version and OS (macOS / Linux / Windows)
- Steps to reproduce
- Expected vs. actual behavior
- Full traceback (redact any secret values)

### Feature Requests

Open a GitHub Issue with the `enhancement` label. Describe the use case and why
existing functionality does not cover it.

### Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes (see Development Setup below).
3. Ensure all tests pass.
4. Open a PR against `main` with a clear description of the change.

Small, focused PRs are easier to review. If your change is large, consider
opening an issue first to discuss the approach.

## Development Setup

```bash
git clone https://github.com/allnew-llc/banto.git
cd banto
pip install -e ".[dev]"   # or: uv pip install -e ".[dev]"
pytest                     # run tests (230+ should pass)
```

## Code Style

- **Python 3.10+** is required.
- Use **type hints** on all public functions and methods.
- **No external dependencies** in the core package (`banto/`). The core must run
  with the Python standard library alone.
- Sync drivers and optional extras may import their declared dependencies.
- Follow PEP 8. Keep lines at 88 characters (Black-compatible).

## Testing

- All changes must pass `pytest` before a PR will be reviewed.
- New features require corresponding tests.
- Bug fixes should include a regression test where practical.
- Tests live in the `tests/` directory and mirror the source layout.

## Security Vulnerabilities

**Do not open a public issue for security vulnerabilities.**

If you discover a security issue, please follow the responsible disclosure
process described in [SECURITY.md](SECURITY.md).

## Code of Conduct

Be respectful and constructive. We are building software together.

## Questions?

Open a Discussion on GitHub or comment on an existing issue. We are happy to help
you get started.

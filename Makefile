PYTHON ?= python3
.PHONY: verify verify-core

verify:
	$(PYTHON) core/tools/verify.py

verify-core:
	$(PYTHON) core/tools/verify.py --core-only

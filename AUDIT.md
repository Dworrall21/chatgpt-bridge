# Code Audit (2026-05-22)

## Scope
- Reviewed core runtime components: `bridge-host.py`, `background.js`, `content.js`.
- Reviewed existing test scripts under `tests/`.
- Ran basic syntax checks for Python modules.

## Executive summary
- Overall architecture is clean and understandable: clear split between local HTTP/WS host, MV3 background worker, and DOM-level content script.
- Reliability mechanisms are present (watchdog, reconnect backoff, request gating/rate-limit tests).
- Main risk area is SSRF posture: outbound URL policy currently allows all HTTPS hosts except blocked local/private ranges.

## Findings

### 1) SSRF policy is permissive by default (Medium)
**Where:** `bridge-host.py` (`_TRUSTED_URL_ALLOW_ALL_HTTPS = True` and `is_trusted_url`).

**Why it matters:**
- Current logic effectively allows arbitrary public HTTPS destinations.
- This is still safer than unrestricted fetches because loopback/link-local/private IPv4 ranges are blocked, but it is broader than a strict allowlist model.

**Recommendation:**
- Default `_TRUSTED_URL_ALLOW_ALL_HTTPS` to `False` and rely on `_TRUSTED_URL_HOSTS` + subdomain matching.
- If broad HTTPS access is needed for compatibility, gate it behind an explicit env var and log warning at startup.

### 2) Test discoverability mismatch (Low)
**Where:** `tests/` scripts are executable scripts, not pytest-style test functions.

**Why it matters:**
- `pytest` reports "no tests ran" even though there are meaningful test programs.
- This can lead to false confidence in CI if CI simply runs `pytest`.

**Recommendation:**
- Either convert scripts into pytest tests, or add a wrapper test that invokes these scripts and asserts exit code 0.
- Document canonical test commands in `DEVELOPMENT.md`.

## Positive observations
- Python host includes request gating + rate-limiting support and has dedicated test coverage scripts.
- Extension watchdog includes bounded retries and cooldown before extension reload, which limits runaway reinjection loops.
- Content script isolates DOM operations while transport and resiliency logic stay in background worker.

## Checks run
- `python -m py_compile bridge-host.py tests/test_rate_limits.py tests/test_model_catalog.py tests/test_integration.py` (pass)
- `pytest -q` (warn: no tests collected due to script-style tests)


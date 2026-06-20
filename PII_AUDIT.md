# Personally Identifying Information Audit (2026-06-20)

## Scope
- Audited tracked repository text files for directly identifying personal data, local machine paths, credentials, and common sensitive identifiers.
- Excluded binary image assets from text pattern scans.

## Checks performed
- Email address pattern scan.
- Secret/token pattern scan for common API keys and bearer-token-like prefixes.
- SSN and US-phone-number pattern scan.
- Local username path scan for `/home/<user>` and `/Users/<user>` references.
- Runtime log review for committed environment traces.

## Findings and remediation

### Personal local paths
Several source, documentation, harness, and scratch files contained absolute local paths with a personal username. These were replaced with generic example paths such as `/path/to/chatgpt-extension`, `/path/to/hermes-agent`, or home-directory-derived runtime paths.

### Runtime log artifact
`bridge-host.log` contained a traceback with personal local paths and was removed from version control. Runtime logs should stay untracked.

### Runtime cache lookup
`bridge-host.py` contained an absolute personal cache path. It now uses `Path.home()` so the lookup remains functional without embedding a personal username.

## Current result
No email addresses, common API-key/token formats, SSNs, US phone numbers, or personal local usernames were found by the final text scans.

## Follow-up recommendations
- Add log files and generated scratch outputs to `.gitignore` if this repository routinely produces them.
- Prefer `Path.home()`, environment variables, or documented placeholders in examples instead of checked-in absolute user paths.
- Re-run the checks before releases or public pushes, especially after adding logs, transcripts, screenshots, or generated reports.

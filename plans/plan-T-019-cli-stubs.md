# Implementation Plan: T-019 — Typer CLI entry + global options + stub commands

## Task Reference
- **Task ID:** T-019
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L

## Overview
Implement the full Typer CLI with all global options and stub commands. Stub commands print "not implemented" and exit 2. `serve` and `doctor` are declared but bodies deferred to T-020/T-021.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/cli.py` | Modify | Full CLI with global options and all commands |
| `tests/test_cli_stubs.py` | Create | Verify --help shows all commands, stubs exit 2 |

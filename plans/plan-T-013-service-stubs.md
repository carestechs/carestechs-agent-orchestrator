# Implementation Plan: T-013 — AI module service layer stubs

## Task Reference
- **Task ID:** T-013
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M

## Overview
Create `modules/ai/service.py` with function signatures for every operation the routes and CLI will call. All control-plane functions raise `NotImplementedYet`. The `IAIService` protocol in `contracts/ai.py` mirrors the public surface.

## Implementation Steps

### Step 1: Service functions
**File:** `src/app/modules/ai/service.py` — all functions raise `NotImplementedYet`.

### Step 2: IAIService protocol
**File:** `src/app/contracts/ai.py` — Protocol mirroring public service functions.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/service.py` | Modify | Service stubs |
| `src/app/contracts/ai.py` | Modify | IAIService protocol |

# GifMake Cleanup Plan

Date: 2026-02-12
Owner: Pending execution after context reset
Status: Planned

## Goal
Fix the reliability and UX issues identified in project review, then ship a cleaner, safer, more intuitive app without breaking existing workflows.

## Scope
This plan covers:
- Core flow bugs in `Auto Poster`, `Warmup`, and scheduler behavior
- Data integrity and dedupe issues
- Test suite and dependency cleanup
- GUI usability and visual redesign
- Scheduler integration into the GUI

## Principles
- Fix correctness and safety first, visuals second.
- Keep changes testable and staged.
- Avoid large rewrites in one pass; deliver in small checkpoints.
- Keep operator-facing behavior explicit (no hidden critical config).

## Workstreams

### 1) Critical Reliability Fixes
Priority: P0

1. Fix `AutoPosterTab.is_running` lifecycle.
- Reset state after analysis completion and on analysis failure.
- Ensure posting can start immediately after successful analysis.

2. Reinstate warmup safety gating.
- Implement real `should_post_today()` logic (ex: browse-only days before posting days).
- Align warmup UI text with actual behavior.

3. Fix campaign row remove indexing.
- Remove rows by object reference or stable ID, not stale positional index.
- Rebind/refresh row controls after remove.

4. Make stop actions responsive.
- Replace long blocking sleeps with interruptible wait loops that check stop flags.

5. Harden warmup browser lifecycle.
- Always stop AdsPower profile in `finally`.
- Guard against missing browser contexts/pages.

Acceptance criteria:
- Analyze -> Post flow works every time.
- Browse-only days are enforced when configured.
- Remove buttons always remove correct campaign.
- Stop request reacts within a few seconds.
- Warmup leaves no orphan AdsPower sessions.

---

### 2) Data Integrity and Dedupe
Priority: P0

1. Scheduler dedupe across queue/history.
- Prevent requeue of already successful uploads for same account+file fingerprint.
- Add DB constraints/indexes as needed.

2. Improve queue semantics.
- Distinguish pending, processing, done, failed, retried clearly.
- Avoid repeated enqueue loops from source rescans.

3. Preserve post attempt history.
- Replace destructive `INSERT OR REPLACE` behavior in post history with append-only attempts.
- Keep latest status queryable while retaining prior attempts.

Acceptance criteria:
- Same file is not requeued once successfully uploaded (unless explicitly forced).
- Post history includes all attempts, not just latest overwrite.

---

### 3) Test Suite and Quality Gates
Priority: P0

1. Repair stale tests.
- Update tests to current `gif_generator` API and helpers.
- Remove references to deleted internals.

2. Add targeted regression tests for reviewed bugs.
- Auto poster state reset
- Scheduler dedupe behavior
- History append behavior

3. Add runnable baseline test command(s).
- Document and verify a default local test command.

Acceptance criteria:
- `pytest tests -q` passes in a fresh setup.
- New regressions in reviewed bug areas are covered.

---

### 4) Dependencies and Config Hygiene
Priority: P1

1. Normalize dependencies.
- Expand root `requirements.txt` to include runtime dependencies used by current modules.
- Optionally split into `requirements-core.txt`, `requirements-automation.txt`, etc.

2. Startup dependency checks.
- Fail early with clear messages for missing optional modules.

3. Secret handling improvements.
- Add `config/api_keys.json` and similar sensitive runtime config files to `.gitignore`.
- Provide sample templates (`*.example`) and loader fallbacks.

Acceptance criteria:
- Fresh install from docs works.
- Sensitive files are not accidentally tracked.

---

### 5) GUI/UX Redesign and Flow Simplification
Priority: P1

1. Information architecture cleanup.
- Clear tabs: `Convert`, `Post`, `Warmup`, `Scheduler`, `Settings`.

2. Redesign `Auto Poster` into explicit 3-step workflow.
- Step 1: Campaign setup
- Step 2: Analyze
- Step 3: Review and Start
- Replace log-only review with a structured review table.

3. Make config explicit.
- Show API key and AdsPower status in a visible settings area.
- Remove hidden critical fields.

4. Implement true drag-and-drop in converter tab.
- Keep click-to-browse as fallback.

5. Visual polish pass.
- Consistent spacing, typography hierarchy, status chips, disabled/loading states.

Acceptance criteria:
- New user can complete key flow without guessing.
- No critical control is hidden.
- Drag-and-drop works as labeled.

---

### 6) Scheduler GUI Integration
Priority: P1

1. Add scheduler tab wired to existing backend.
- Start/stop/status
- Scan sources
- Queue list (pending/processing/failed)
- Error/history views

2. Expose schedule settings in GUI.
- Mode, active hours, posts/day, retries/backoff, source mappings.

Acceptance criteria:
- Full scheduler control is possible from GUI without shell commands.

---

## Recommended Execution Order

Phase 0: Baseline and branch setup (0.5 day)
- Snapshot current behavior and failing tests.
- Create issue checklist from this plan.

Phase 1: P0 reliability fixes (1-2 days)
- Workstream 1 complete.

Phase 2: P0 data integrity fixes (1-2 days)
- Workstream 2 complete.

Phase 3: P0 tests and dependency cleanup (1-2 days)
- Workstreams 3 and 4 complete enough for stable development.

Phase 4: UX redesign implementation (2-4 days)
- Workstream 5 complete.

Phase 5: Scheduler GUI integration (1-2 days)
- Workstream 6 complete.

Phase 6: Hardening and release prep (1 day)
- Final test pass, docs updates, packaging checks.

## Risk Notes
- Reddit/AdsPower automation is UI-fragile by nature; keep selectors isolated and tested.
- Scheduler changes touch data model; include migration strategy and backup notes.
- GUI refactor should be staged to avoid breaking existing operators.

## Definition of Done (Project)
- Critical bugs fixed and covered by tests.
- No duplicate scheduling regressions.
- Warmup gating behavior is real and documented.
- GUI flows are explicit and intuitive for core tasks.
- Scheduler fully manageable via GUI.
- Fresh setup docs are accurate and complete.

## Session Restart Handoff
When restarting context, begin with:
1. Execute Phase 1 (Workstream 1) only.
2. Run tests after each bug fix group.
3. Commit in small logical units:
   - `fix: auto-poster state lifecycle`
   - `fix: warmup posting gate and stop responsiveness`
   - `fix: campaign row removal stability`
   - `fix: warmup browser cleanup`


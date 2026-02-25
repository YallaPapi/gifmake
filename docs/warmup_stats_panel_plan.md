# Warmup Stats Panel — Implementation Plan

## Overview
Add a collapsible analytics panel at the top of the Warmup tab showing per-account stats and aggregate metrics. Two-tier design: summary bar + account table.

## Tier 1: Summary Bar (always visible, ~60px)
A horizontal row of 6 KPI pill badges (reusing existing `_make_pill()` pattern).

| Pill | Label | Color | Data Source |
|------|-------|-------|-------------|
| Active accounts | `X Active` | green | Count of accounts with warmup record, not banned |
| Done today | `X/Y Today` | teal | `get_warmed_today_ids()` count / total active |
| Banned | `X Banned` | red | Count from `data/warmup_bans.json` |
| Avg warmup day | `Day X` | blue | Average of `get_warmup_day()` across active accounts |
| Total comments | `X Cmt` | blue | Sum of `total_comments` from `account_warmup` table |
| Total joins | `X Join` | amber | Sum of `total_joins` from `account_warmup` table |

Right side: **Refresh** button + **collapse/expand toggle** (chevron).

## Tier 2: Account Table (collapsible, scrollable, ~200px)
A `CTkScrollableFrame` with compact rows (~32px each).

### Columns
| Column | Width | Content |
|--------|-------|---------|
| Status | 24px | Colored indicator: ✓ green (done today), ○ blue (pending), ✗ red (banned), — gray (not started) |
| Username | flex | Account username, truncated ~18 chars |
| Day | 36px | Warmup day number or `--` |
| Phase | 65px | Lurker/Light/Regular/Active/Established |
| Total C/J | 70px | Lifetime comments / joins |
| Last Run | 80px | Relative: "today", "yesterday", "3d ago", "never" |

### Sort Order
1. Currently running (green) — top
2. Active, not done today (blue) — needs attention
3. Done today (teal check) — completed
4. Not started (gray) — informational
5. Banned (red X) — bottom, hidden by default

### Phase Colors
- Lurker (1-3): gray `#94A3B8`
- Light (4-7): light blue `#60A5FA`
- Regular (8-14): purple `#A78BFA`
- Active (15-21): amber `#F59E0B`
- Established (22+): green `#22C55E`

## Layout Position
Insert between hero description (row 0) and Step 1 Multi-Account (row 1). Shift existing rows down by 1 if needed, or use a fractional approach.

## Collapsed by Default
- Summary bar always visible (~60px)
- Account table hidden until chevron clicked
- Banned accounts hidden with "Show N banned" toggle at bottom of table

## Data Sources
- `post_history.py`: `get_all_warmup_stats()` — bulk query for all accounts
- `post_history.py`: `get_warmed_today_ids()` — today's completed set
- `data/warmup_bans.json` — ban status per adspower_id
- `config/account_profiles.json` — username mapping (adspower_id → profile name)
- Phase calculated from `started_at` field: `(now - started_at).days + 1`

## Refresh Triggers
- After each warmup run completes (hook into `_on_run_all_complete`)
- After single account warmup completes
- Manual Refresh button click
- On tab switch (when user navigates to Warmup tab)
- NO polling/timers

## DB Changes
- Added `get_all_warmup_stats()` to `post_history.py` — returns all rows from `account_warmup`
- No schema changes needed for initial version

## Files to Modify
- `src/core/post_history.py` — add `get_all_warmup_stats()` ✅ DONE
- `src/gui/warmup_tab.py` — add stats panel widget code, shift grid rows

## Future: Per-Session Tracking (not in this PR)
- New `warmup_sessions` table to track per-session data (duration, comments, karma before/after)
- Daily delta tracking (today's comments vs lifetime)
- This requires `record_session()` call at end of `run_daily_warmup()`

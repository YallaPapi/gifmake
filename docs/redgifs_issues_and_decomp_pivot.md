# RedGIFs Uploader: Current Issues and Decomp Pivot (One Pager)

## Context
The RedGIFs posting flow in the GUI became unreliable across API mode and browser mode. The expected flow was: select account/profile in GUI, generate clips, then auto-publish with tags, description, and content type. In practice, uploads often stalled or failed with inconsistent API/UI behavior.

## What Was Failing
- Proxy/profile mismatch confusion:
  - GUI profile changes were not consistently reflected in the effective upload path.
  - Legacy API-path assumptions and stale proxy settings created misleading failures.
- API finalize failures:
  - Repeated `400 UploadFailed` on `POST /v2/gifs/submit` after successful S3 upload + processing wait.
  - This produced misleading "uploading" status with no successful publish.
- Browser automation instability:
  - Automation reached metadata but did not reliably satisfy required fields.
  - `Publish` remained disabled and timed out (`Publish button never became enabled`).
- UI state ambiguity:
  - Multiple `Edit`/`Continue` buttons; generic selectors hit wrong panels.
  - Tags and Content Type could invalidate each other during transitions.

## Root Causes Identified
- Wrong panel targeting:
  - Generic selectors opened non-target cards (for example Content Type instead of Tags).
- Timing/state gating:
  - Automation acted before target controls were fully available.
- Validation coupling:
  - `Tags` and `Content Type` are both hard requirements; one can become invalid while fixing the other.
- Weak success criteria:
  - Generic "published/success" text can produce false positives.

## Evidence Collected
- Per-action traces (screenshot + HTML + UI JSON after each step):
  - Required-field states at each transition.
  - `Publish` enable/disable lifecycle.
  - Cases where tags were valid but content type was still required.
- Network-level proof runs:
  - `POST https://api.redgifs.com/v2/gifs/submit` response captured.
  - HTTP status + response body recorded.
  - Watch link observed after publish.

## Pivot: Decomp-First Stabilization
We moved from heuristic click-flow patches to a decomp-first state-machine approach:
1. File accepted
2. Metadata ready
3. Content Type valid
4. Tags valid (>=3)
5. Publish enabled
6. Submit acknowledged
7. Watch URL confirmed

For each stage we enforce:
- Required UI invariants
- Deterministic selectors
- Retry/timeout budgets
- Artifact capture (PNG + HTML + structured UI dump + network events)

## Current Status (After Fixes)
- Browser mode now captures submit responses and no longer trusts text-only success.
- Success is returned only on hard signals:
  - successful submit response with gif id, or
  - resolved watch URL/link.
- Submit HTTP >= 400 now fails fast with response body surfaced in the error.

## Latest Proof Snapshot
- Browser uploader test result:
  - `success: true`
  - `url: https://www.redgifs.com/watch/miserlyuncommondolphin`
- Network probe result:
  - `POST https://api.redgifs.com/v2/gifs/submit -> 200`
  - response body contained gif id (example: `silvergaseoussnipe`)
  - watch link observed: `https://redgifs.com/watch/silvergaseoussnipe`

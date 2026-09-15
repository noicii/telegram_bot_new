# vnext-v3 Dashboard Design

## Goal
Reconstruct the V2 dashboard path without changing Bot V2 core features or downloader behavior. The dashboard must remain responsive and must not use a fixed 3-second refresh rule.

## Non-negotiable behavior
- Downloader implementation and behavior remain unchanged unless a separately approved optimization is proposed.
- Existing Bot V2 commands, callbacks, queueing, download/upload limits, HLS behavior, splitting, cleanup, and persistence remain available.
- No fixed 3-second dashboard timer.
- Dashboard renders the latest known state and coalesces bursts instead of replaying stale progress events.

## Dashboard architecture
The TelegramFloodGate remains the single pacing/coalescing authority for dashboard edits. Main must not impose a second independent 1.2-second dashboard throttle. Dashboard updates are keyed per chat/message and retain only the newest pending render. FloodWait increases the adaptive cooldown; successful updates gradually recover responsiveness.

The existing dashboard content is preserved, including download/upload counts, progress, HLS segment information, speed/ETA, upload parts, disk usage, completed/failed/cancelled totals, retry buttons, Refresh and Queue controls.

Queued tasks must be displayed as queued rather than under the active downloading section. Active downloading and uploading sections continue to show only their respective states.

## Scope
Initial vnext-v3 work is dashboard-focused. New modules may be introduced only where they provide a clear responsibility boundary. No unnecessary files or speculative refactors.

## Validation
Validate syntax/imports, dashboard state classification, coalescing behavior, and regression-sensitive paths. Do not claim production-ready until validation evidence is available.

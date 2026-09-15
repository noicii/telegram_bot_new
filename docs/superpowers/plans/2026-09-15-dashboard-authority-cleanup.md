# Dashboard Authority Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bot V2 dashboard updates responsive and single-authority without changing downloader behavior or V2 features.

**Architecture:** Keep TelegramFloodGate as the dashboard coalescing/rate-control authority. Remove Pipeline's Pyrogram monkey-patching layer so dashboard callbacks are not gated twice. Keep main.py responsible for rendering and commands in this pass.

**Tech Stack:** Python 3, asyncio, Pyrogram, existing Bot V2 Pipeline and TelegramFloodGate.

**Spec:** Existing approved dashboard design/spec on vnext-v3.

## Global Constraints
- Preserve all existing V2 features and command/callback behavior.
- Do not change downloader implementation or behavior/output.
- No fixed 3-second dashboard refresh rule.
- Dashboard updates remain coalesced and FloodWait-aware.
- Only vnext-v3 is modified; production bot-vnext remains untouched.
- Do not delete code unless its production path is proven unused.

---

### Task 1: Remove duplicate Pipeline dashboard interception

**Files:**
- Modify: `bot_vnext/app/pipeline.py`

**Interfaces:**
- Preserve Pipeline constructor and public lifecycle/task APIs.
- Preserve `Pipeline.telegram_gate` for upload/API throttling and dashboard callback scheduling.
- Remove only the Pyrogram monkey-patching/cache interception used to gate dashboard message edits.

- [ ] Remove `_install_dashboard_hooks()` call from `Pipeline.__init__`.
- [ ] Remove `_install_dashboard_hooks()` and its client monkey-patching implementation.
- [ ] Remove imports made unused by that removal.
- [ ] Keep progress/complete/failure callbacks publishing through `telegram_gate.publish_dashboard()`.
- [ ] Run syntax/static checks available in the repository environment.
- [ ] Commit as a focused dashboard cleanup commit.

### Task 2: Verify dashboard call paths

**Files:**
- Inspect: `bot_vnext/main.py`
- Inspect: `bot_vnext/app/core/telegram_gate.py`
- Inspect: `bot_vnext/app/pipeline.py`

- [ ] Confirm progress, completion and failure callbacks still enter the dashboard gate.
- [ ] Confirm manual `/status` and refresh continue using the same dashboard message state.
- [ ] Confirm ordinary Pyrogram calls are no longer intercepted by Pipeline.
- [ ] Confirm no downloader file was modified.
- [ ] Inspect branch diff for unintended changes.

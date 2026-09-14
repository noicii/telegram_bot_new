# AI RULES — READ BEFORE TOUCHING CODE

## 🚨 HARD RULE: UNDERSTAND FIRST, EDIT SECOND

This repository contains a live Telegram video downloader bot. **No AI agent may edit, delete, overwrite, refactor, rename, or deploy code until it has first understood the production architecture and current state.**

If you cannot explain the current production flow and identify the correct production files, **STOP. Do not make changes.**

## 1. Mandatory reading gate

Before any code change, read these documents in this order:

1. `AI_RULES.md`
2. `AGENTS.md`
3. `PROJECT_HANDOFF.md`
4. `ARCHITECTURE.md`
5. `BOT_STATUS.md`
6. `BOT_FEATURES.md`
7. `OPERATIONS.md`
8. `DEPLOYMENT.md`
9. `UPDATE_GUIDE.md`
10. `CHANGELOG.md`
11. `bot_vnext/PRODUCTION_ENTRYPOINT.md`
12. `bot_vnext/STORAGE_CLEANUP.md`

Then inspect the relevant production source files under `bot_vnext/` before editing them.

**Reading documentation is a prerequisite, not an optional recommendation.**

## 2. Production source of truth

The production bot starts at:

`bot_vnext/main.py`

The production application code is under:

`bot_vnext/app/`

Root-level files such as `bot.py`, `handlers.py`, `downloader.py`, `queue_worker.py`, and `database.py` are **legacy architecture** unless current documentation and the actual call path explicitly prove otherwise.

**Never edit legacy root code as a shortcut for a V2 problem.**

## 3. Mandatory architecture understanding

Before editing, the AI must establish from the repository itself:

- production entrypoint and launcher
- Telegram → handler → pipeline → download → upload flow
- download/upload worker counts
- current HLS segment concurrency
- downloader methods and fallback behavior
- download → upload handoff
- configured Telegram upload destination
- large-file splitting behavior
- thumbnail behavior
- dashboard/progress flow and refresh policy
- disk cleanup and active-file protection
- restart/interrupted-task behavior
- systemd and singleton enforcement
- clean deployment/update behavior
- secret/configuration boundaries

If any item is unknown, ambiguous, or contradictory: **STOP → inspect more code/docs → resolve it. Do not guess.**

## 4. Change-scope gate

For every requested change, first determine:

1. Exactly what the user requested.
2. Which production component owns that behavior.
3. Exact files that need changing.
4. Existing behavior that must remain unchanged.
5. Possible side effects/regressions.
6. How the change will be validated.

Make the **smallest safe change**.

Do not silently change:

- download speed
- upload speed
- worker counts
- concurrency
- queue semantics
- Telegram destination
- retry policy
- cleanup thresholds
- restart behavior
- systemd behavior
- deployment behavior
- public bot behavior

unless explicitly requested or strictly required by the requested fix.

## 5. High-risk change gate

Treat these as high-risk:

- `bot_vnext/main.py`
- `bot_vnext/app/pipeline.py`
- `bot_vnext/app/downloader/engine.py`
- `bot_vnext/app/queue/upload_manager.py`
- `bot_vnext/app/uploader/engine.py`
- `update.sh`
- `telegram-bot.service`
- authentication/authorization code
- storage cleanup code
- dependency changes

For high-risk changes, trace the call path before editing and perform targeted regression validation afterward.

**Architecture refactors require explicit user approval.** A perceived “better design” is not permission to replace working architecture.

## 6. Secrets and privacy

Never:

- commit `.env`
- print or expose `BOT_TOKEN`, `API_HASH`, API credentials, session credentials, or private keys
- copy secrets into documentation
- include secrets in logs, commits, issues, or generated files
- commit Telegram session/runtime credential files

If secret exposure is discovered, stop the change and tell the user.

## 7. Destructive-operation gate

Do not perform destructive operations merely to make a change easier.

Examples include deleting production code, wiping databases, deleting media, killing production processes, replacing systemd units, or changing permissions.

Use documented procedures when they are explicitly part of the normal deployment workflow. Otherwise, require explicit user approval before destructive operations.

## 8. No competing runtime

The canonical production launcher is systemd. Do not start competing bot instances using `nohup`, `screen`, `tmux`, ad-hoc background processes, or a second service.

Do not create another production entrypoint.

## 9. Evidence over assumptions

Documentation can become stale. Code can also contradict documentation. Therefore:

**Do not blindly trust either one. Trace the actual production call path and reconcile discrepancies before changing behavior.**

Never invent configuration values, file locations, worker counts, or runtime behavior.

### 9.1 Downloader diagnostic rule — generic fixes only

When using `DOWNLOADER_TESTING.md` / `tools/downloader_test.py`, every test URL is a **diagnostic sample**, not a target for a special-case fix.

AI agents must:

- diagnose the failure from evidence before editing downloader code;
- make fixes in the generic production downloader path so the same failure pattern can be handled across valid links;
- never hardcode a test URL, hostname, provider, CDN, episode, signed token, or one-off path merely to make the test pass;
- not treat one successful URL as proof that the downloader is globally fixed;
- validate other link/source patterns after the original failing case succeeds when practical;
- follow the user's evidence-first loop: **Attempt 1 → diagnose → generic fix → Attempt 2 → diagnose → generic fix → ... → SUCCESS**;
- run one bounded attempt at a time during troubleshooting rather than blindly looping until success.

If a proposed change only helps one specific link and does not address a reproducible generic failure pattern, **STOP and do not merge it as a downloader fix**.

## 10. Validation gate

After every meaningful production change:

1. Run syntax/static checks appropriate to changed files.
2. Run targeted tests/checks for the affected path.
3. Verify the canonical production entrypoint remains intact.
4. Verify exactly one production V2 bot process is active after deployment.
5. Verify the canonical systemd service is the launcher.
6. Check relevant logs for errors.
7. Update affected documentation.
8. Add a dated `CHANGELOG.md` entry for meaningful behavior changes.

Never declare success based only on “code looks correct.”

## 11. Deployment gate

Production deployment must use the documented clean deployment process:

```bash
cd ~/telegram_bot_new && bash update.sh
```

The updater intentionally refreshes tracked code while preserving `.env` and the local virtual environment and removing disposable runtime state. Do not invent an alternate production update procedure unless `update.sh` itself is the requested change.

## 12. User approval boundary

The AI may implement a clearly requested, well-understood change.

The AI must **stop and ask the user** before:

- changing architecture
- changing core worker/concurrency strategy
- changing authentication/authorization rules
- changing Telegram destination/security boundaries
- changing persistence/state policy
- changing deployment model
- introducing a new external service
- making a broad refactor unrelated to the requested fix

## 13. Final rule

When in doubt:

**STOP → READ → TRACE → UNDERSTAND → PLAN → EDIT → VALIDATE → DOCUMENT.**

Never:

**GUESS → EDIT → HOPE.**

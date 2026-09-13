# Repository AI Instructions

> **STOP before editing.** Read `AI_RULES.md` completely. These instructions are mandatory for AI-assisted changes in this repository.

## Pre-edit gate

An AI must understand the production architecture before changing anything. At minimum, read:

- `AI_RULES.md`
- `PROJECT_HANDOFF.md`
- `ARCHITECTURE.md`
- `BOT_STATUS.md`
- `BOT_FEATURES.md`
- `OPERATIONS.md`
- `DEPLOYMENT.md`
- `UPDATE_GUIDE.md`
- `CHANGELOG.md`
- `bot_vnext/PRODUCTION_ENTRYPOINT.md`
- `bot_vnext/STORAGE_CLEANUP.md`

Then inspect the relevant `bot_vnext/` source files.

Do **not** edit first and understand later.

## Production boundary

Production entrypoint: `bot_vnext/main.py`.

Production application code: `bot_vnext/app/`.

Root-level legacy modules are not the V2 production path. Do not modify them to solve V2 issues without explicit evidence and approval.

## Safety requirements

- Make the smallest change that satisfies the user's request.
- Preserve working behavior unless the user explicitly asks for it to change.
- Never guess architecture, configuration, destination, worker counts, concurrency, or runtime behavior.
- Never expose or commit secrets.
- Never create competing bot processes or alternate production launchers.
- Use the documented deployment/update process.
- Validate the changed path before declaring success.
- Update documentation and `CHANGELOG.md` for meaningful production behavior changes.

## If context is insufficient

**STOP and inspect the repository.** Do not make a speculative patch. If the required information cannot be established safely, ask the user rather than guessing.

### Golden rule

**READ → UNDERSTAND → TRACE → PLAN → EDIT → VALIDATE → DOCUMENT.**

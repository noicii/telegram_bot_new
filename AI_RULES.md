# AI RULES — READ BEFORE TOUCHING CODE

## 🚨 HARD RULE: UNDERSTAND FIRST, EDIT SECOND

This repository contains a live Telegram video downloader bot. **No AI agent may edit, delete, overwrite, refactor, rename, or deploy code until it has first understood the production architecture and current state.**

If you cannot explain the current production flow and identify the correct production files, **STOP. Do not make changes.**

## Mandatory reading order

Before any code change, read these documents in this order:

1. `PROJECT_HANDOFF.md`
2. `ARCHITECTURE.md`
3. `BOT_STATUS.md`
4. `BOT_FEATURES.md`
5. `OPERATIONS.md`
6. `DEPLOYMENT.md`
7. `UPDATE_GUIDE.md`
8. `CHANGELOG.md`
9. `bot_vnext/PRODUCTION_ENTRYPOINT.md`
10. `bot_vnext/STORAGE_CLEANUP.md`

Then inspect the relevant production source files under `bot_vnext/` before editing them.

## Production source of truth

The production bot starts at:

`bot_vnext/main.py`

The production pipeline is under:

`bot_vnext/app/`

Root-level files such as `bot.py`, `handlers.py`, `downloader.py`, `queue_worker.py`, and `database.py` are **legacy architecture** unless the current documentation explicitly says otherwise.

**Never edit legacy root code as a shortcut for a V2 problem.**

## Mandatory understanding checklist

Before editing, the AI must be able to answer these questions from the repository itself:

- What is the production entrypoint?
- How does Telegram input reach the pipeline?
- How many download workers and upload workers are configured?
- What is the current HLS segment concurrency?
- How does download completion hand off to upload?
- What Telegram destination does the uploader use?
- How are large files split and uploaded?
- How are thumbnails handled?
- How does progress reach the dashboard?
- How does disk cleanup protect active files?
- What happens to incomplete work after restart?
- How is the single systemd instance enforced?
- What does `update.sh` delete, and what does it preserve?
- Which secrets must never be committed?

If any answer is unknown, **do not guess and do not edit. Inspect the repository first.**

## Change-scope rules

- Change only what the user explicitly requests or what is strictly required to implement that request.
- Do not silently change download speed, upload speed, worker counts, concurrency, queue behavior, Telegram destination, cleanup policy, restart behavior, or deployment behavior.
- Do not replace working architecture with a new implementation without explicit approval.
- Do not modify `.env` or expose secrets.
- Do not commit tokens, API keys, session files, private credentials, or runtime secrets.
- Do not delete production code merely because it looks unused; verify its role first.
- Do not create a second production entrypoint.
- Do not start the bot manually with `nohup`, `screen`, `tmux`, or another competing process when systemd is the canonical service.

## Before writing code

The AI must first state internally/briefly in its work notes:

1. What the user wants changed.
2. Which production component owns that behavior.
3. Which exact files need changing.
4. What behavior must remain unchanged.
5. What validation will prove the change works.

Then make the smallest safe change.

## After writing code

Required before declaring success:

1. Run syntax/static validation appropriate to the changed files.
2. Verify the relevant production path.
3. Check that no duplicate bot process is running.
4. Confirm the canonical systemd service remains the production launcher.
5. Update the relevant documentation when behavior changes.
6. Add a dated entry to `CHANGELOG.md` for meaningful production changes.

## Deployment rule

Production deployment must follow the documented clean update process. The canonical update command is:

```bash
cd ~/telegram_bot_new && bash update.sh
```

Do not invent an alternate deployment procedure unless the documented updater is itself the subject of the requested change.

## Important: documentation is a safety gate

The documentation files are not optional background reading. They exist specifically so a future developer or AI cannot safely make a random change without first understanding the system.

**When in doubt: STOP → READ → TRACE → VERIFY → THEN EDIT.**

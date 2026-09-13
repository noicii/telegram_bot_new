# Production entrypoint

The production systemd service must execute `bot_vnext/main.py`.

The legacy root `bot.py` is not the production V2 entrypoint.

Current V2 runtime targets:
- 2 download workers
- 4 upload workers
- 16 HLS segments per video
- automatic large-file splitting before Telegram upload

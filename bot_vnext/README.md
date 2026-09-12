# Telegram Bot vNext

Clean rebuild. This tree is intentionally isolated from the current production bot until tests pass.

## Architecture

Telegram -> Crawl -> Task DB -> Download Pool (2) -> Upload Queue -> Upload Pool (4)

Download engine routing:
- direct HTTP/HTTPS -> aria2c when available, then aiohttp
- HLS/DASH -> FFmpeg, then browser extraction / yt-dlp
- supported video sites -> yt-dlp, then browser extraction
- JS-generated media -> Playwright extraction -> direct/FFmpeg

Cancellation is task-scoped and must terminate subprocesses, browser contexts and async network work without affecting other tasks.

No production files are replaced by this tree.

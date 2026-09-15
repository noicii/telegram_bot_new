# `/crawl` Command Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade only `/crawl <URL>` into a bounded multi-page episode crawler that discovers relevant nested/paginated episode pages while preserving the plain chat-URL flow, selection UI, queue semantics, and downloader behavior.

**Architecture:** Keep `crawler.py` as the crawler implementation boundary and preserve the existing `crawl_blog_episodes()` function as the direct/plain-URL behavior. Add a separate `/crawl` entry function, `crawl_website_episodes()`, that reuses the existing parsing, normalization, provider, resolution, and result-schema helpers but adds bounded same-host page traversal. Change only `/crawl` dispatch in `bot_vnext/main.py` to call the new function; plain URL messages continue calling the existing function. Add focused crawler and dispatch tests without touching downloader code.

**Tech Stack:** Python 3, `requests`, BeautifulSoup/lxml, `unittest`/pytest-compatible tests, existing crawler helpers and Telegram handler flow.

**Spec:** `docs/superpowers/specs/2026-09-15-crawl-command-upgrade-design.md`

## Global Constraints

- Upgrade only the website/episode crawling path used by `/crawl <URL>`.
- The direct plain-URL chat flow must retain its current behavior and must not consume the new multi-page crawl logic.
- Downloader behavior and output must remain unchanged.
- Stay on the source site's host for page traversal unless a link is an actual downloadable/provider candidate.
- Prefer episode/season/video/player/embed/watch/stream/archive/index and pagination semantics; do not recursively spider arbitrary navigation.
- Use a visited set based on normalized URLs and strict depth/page/time bounds.
- Child-page failures must not discard candidates already found elsewhere.
- Keep existing HTTP timeout behavior as the baseline.
- Preserve the existing result fields: `title`, `episode`, `url`, `source`, `resolution`, `source_url`.
- Preserve provider support, URL normalization, protected-domain handling, method selection, queue behavior, and selection UI.
- Do not modify `bot_vnext/app/downloader/engine.py` or `browser_hls.py`.

---

## File Map

- **Modify:** `crawler.py` — retain all existing direct-crawl behavior; add bounded website traversal helpers and `crawl_website_episodes()` for `/crawl` only.
- **Modify:** `bot_vnext/main.py` — import the new crawler function and route only `crawl_cmd()` to it; leave `text_url()` behavior unchanged.
- **Create:** `bot_vnext/tests/test_crawler_command.py` — unit tests for traversal, filtering, context, deduplication, failure isolation, limits, schema, and direct-path separation.
- **No downloader files:** downloader implementation remains untouched.

## Task 1: Lock Down the Entry-Point Separation

**Files:**
- Modify: `bot_vnext/main.py: imports, crawl_cmd(), text_url(), crawl_url()`
- Test: `bot_vnext/tests/test_crawler_command.py`

**Interfaces:**
- Existing direct path continues to call `crawl_blog_episodes(url)` through `crawl_url()`.
- New command path will call `crawl_website_episodes(url)`.
- Existing selection session/result contract remains unchanged.

- [ ] **Step 1: Write the failing dispatch tests**

```python
from unittest.mock import AsyncMock, patch


def test_plain_url_uses_existing_single_page_crawler():
    bot = object.__new__(V2Bot)
    bot.crawl_url = AsyncMock()
    message = FakeMessage("https://example.com/page")
    asyncio.run(bot.text_url(None, message))
    bot.crawl_url.assert_awaited_once_with(message, "https://example.com/page")


def test_crawl_command_uses_website_crawler(monkeypatch):
    # Patch the crawler used by crawl_cmd and assert that the command path
    # does not invoke crawl_blog_episodes.
    ...
```

The final test must exercise the actual `crawl_cmd()` implementation rather than merely testing a helper, and it must make the old `crawl_blog_episodes` path fail if invoked.

- [ ] **Step 2: Run the focused tests and verify the new command test fails**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py
```

Expected: the new `/crawl` dispatch test fails because `crawl_cmd()` currently delegates to `crawl_url()`, which uses `crawl_blog_episodes()`.

- [ ] **Step 3: Implement the smallest dispatch change**

Import `crawl_website_episodes` beside the existing crawler import and make `crawl_cmd()` invoke a command-specific crawl method. Keep `text_url()` exactly on the existing `crawl_url()` path. The command-specific method should preserve the current wait message, exception handling, empty-result message, session construction, method lookup, and `render_selection()` call; only the crawler function changes.

- [ ] **Step 4: Run the focused dispatch tests**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py -k 'plain_url or crawl_command'
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot_vnext/main.py bot_vnext/tests/test_crawler_command.py
git commit -m "refactor: isolate crawl command from direct URLs"
```

---

## Task 2: Add Bounded Same-Site Page Discovery

**Files:**
- Modify: `crawler.py` near the existing URL/candidate helpers and `crawl_blog_episodes()` implementation.
- Test: `bot_vnext/tests/test_crawler_command.py`

**Interfaces:**
- `crawl_website_episodes(raw_url, max_depth=2, max_pages=24, max_seconds=45)` returns `list[dict]` using the existing six-field result schema.
- Internal discovery helper accepts a page URL and parsed page and returns only normalized, same-host crawl targets that pass relevance rules.

- [ ] **Step 1: Write failing tests for relevant-link discovery**

```python
ROOT = "https://series.example/show"


def test_discovers_episode_and_pagination_links_but_not_navigation():
    html = """
    <a href="/episode-1">Episode 1</a>
    <a href="/season-1?page=2">Next</a>
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
    <a href="https://other.example/episode-9">Other host</a>
    """
    soup = BeautifulSoup(html, "lxml")
    links = _discover_crawl_links(soup, ROOT, {"https://series.example"})
    assert "https://series.example/episode-1" in links
    assert "https://series.example/season-1?page=2" in links
    assert "https://series.example/about" not in links
    assert "https://other.example/episode-9" not in links


def test_page_limits_prevent_unbounded_traversal(monkeypatch):
    pages = {ROOT: make_page_with_links("/episode-1", "/episode-2", "/episode-3")}
    monkeypatch.setattr("crawler._fetch_html", lambda url, timeout=25: pages.get(url, ""))
    results = crawl_website_episodes(ROOT, max_pages=2)
    assert fetch_count <= 2
```

- [ ] **Step 2: Run tests to verify discovery is absent**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py -k 'discovers_episode or page_limits'
```

Expected: FAIL because the bounded traversal helpers do not yet exist.

- [ ] **Step 3: Implement normalized same-host discovery**

Add explicit constants/defaults for the command crawler bounds. Use `_normalize_url()` for the visited key. Resolve relative links with `urljoin()`. Compare hosts using `_host()`. Reject non-http URLs, different hosts, already visited URLs, and links whose text/URL lacks episode/video/player/embed/watch/stream/season/archive/index/pagination semantics. Treat `next`, `page`, `paged`, `older`, and numeric pagination query/path patterns as pagination signals. Do not enqueue provider/media candidates for page traversal when they are cross-host; they remain extraction candidates only.

- [ ] **Step 4: Implement breadth-first bounded traversal**

Start with the normalized root URL at depth 0. Fetch at most `max_pages` HTML pages, never enqueue a normalized URL twice, and stop when `time.monotonic() - started >= max_seconds`. A failed fetch simply marks that page unavailable and continues with the remaining queue. Parse each successful page with BeautifulSoup and collect its candidate links plus newly discovered same-host crawl targets.

- [ ] **Step 5: Run the focused discovery tests**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py -k 'discovers_episode or page_limits'
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add crawler.py bot_vnext/tests/test_crawler_command.py
git commit -m "feat: add bounded same-site crawl traversal"
```

---

## Task 3: Extract Nested Episode Candidates With Context and Global Deduplication

**Files:**
- Modify: `crawler.py`
- Test: `bot_vnext/tests/test_crawler_command.py`

**Interfaces:**
- Each fetched page contributes media/provider candidates through the existing `_extract_candidates()` and `_looks_like_video_candidate()` logic.
- `crawl_website_episodes()` returns the same dictionaries consumed by `V2Bot.render_selection()` and `enqueue_selected()`.

- [ ] **Step 1: Write failing nested-page/context tests**

```python
def test_nested_episode_page_candidate_inherits_episode_context(monkeypatch):
    root = "https://series.example/show"
    episode = "https://series.example/episode-7"
    pages = {
        root: '<a href="/episode-7">Episode 7</a>',
        episode: '<h1>Episode 7</h1><iframe src="https://stream.example/embed/abc"></iframe>',
    }
    monkeypatch.setattr("crawler._fetch_html", lambda url, timeout=25: pages.get(url))
    items = crawl_website_episodes(root, max_pages=4)
    assert len(items) == 1
    assert items[0]["episode"] == "Episode 7"
    assert items[0]["source_url"] == episode


def test_duplicate_media_urls_are_emitted_once(monkeypatch):
    # Two pages expose the same media URL with tracking parameters that
    # _normalize_url() removes.
    ...
    assert len([x for x in items if x["url"].startswith("https://stream.example/video")]) == 1


def test_result_schema_is_unchanged(monkeypatch):
    items = crawl_website_episodes(ROOT)
    assert set(items[0]) == {"title", "episode", "url", "source", "resolution", "source_url"}
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py -k 'nested_episode or duplicate_media or result_schema'
```

Expected: FAIL because traversal does not yet assemble child-page candidates into the final result set.

- [ ] **Step 3: Implement per-page context propagation**

For each crawl page, derive context from page title, `h1`/headings, relevant link text, and the page URL. When a child page was reached from a link carrying `Episode N`, retain that episode as the fallback context for candidates extracted from the child page. Prefer an explicit episode detected in the child candidate/page text, then child-page context, then parent link context, then URL-derived episode, then `Unknown Episode`.

- [ ] **Step 4: Build final candidates using existing provider/resolution logic**

For every extracted candidate, reuse `_provider_name()`, `_detect_resolution()`, and `_lookup_page_resolution()` exactly as the existing crawler does. Do not rewrite downloader-facing URLs. Preserve `source_url` as the page where the media candidate was found so downstream `Referer` metadata remains meaningful.

- [ ] **Step 5: Globally deduplicate and sort**

Normalize every final media URL with `_normalize_url()`. Use one global `seen_media` set across all pages. Preserve the existing episode-number/resolution/provider/URL sort order so selection behavior remains stable.

- [ ] **Step 6: Run the focused extraction tests**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py -k 'nested_episode or duplicate_media or result_schema'
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add crawler.py bot_vnext/tests/test_crawler_command.py
git commit -m "feat: extract nested crawl candidates with context"
```

---

## Task 4: Add Failure Isolation, Filtering, and Regression Coverage

**Files:**
- Modify: `crawler.py` only if tests expose a concrete traversal/filtering defect.
- Modify: `bot_vnext/tests/test_crawler_command.py`
- Test: `bot_vnext/tests/test_core_safety.py` as the full-suite regression target; do not change its existing safety assertions.

**Interfaces:**
- A failed child request never removes already collected root/other-page results.
- Unrelated same-host pages are not crawled merely because they are linked.
- Existing `crawl_blog_episodes()` behavior remains directly testable and untouched.

- [ ] **Step 1: Write failure-isolation and direct-crawler regression tests**

```python
def test_child_page_failure_does_not_discard_root_candidates(monkeypatch):
    pages = {
        ROOT: '<h2>Episode 1</h2><iframe src="https://stream.example/e1"></iframe>'
                 '<a href="/episode-2">Episode 2</a>',
        "https://series.example/episode-2": None,
    }
    monkeypatch.setattr("crawler._fetch_html", lambda url, timeout=25: pages.get(url))
    items = crawl_website_episodes(ROOT)
    assert any(item["episode"] == "Episode 1" for item in items)


def test_unrelated_same_site_links_are_not_fetched(monkeypatch):
    fetched = []
    html = '<a href="/about">About</a><a href="/episode-1">Episode 1</a>'
    ...
    assert "https://series.example/about" not in fetched


def test_direct_crawler_stays_single_page(monkeypatch):
    fetched = []
    monkeypatch.setattr("crawler._fetch_html", lambda url, timeout=25: fetched.append(url) or '<a href="/episode-2">Episode 2</a>')
    crawl_blog_episodes(ROOT)
    assert fetched == [ROOT]
```

- [ ] **Step 2: Run the regression tests and verify any missing isolation behavior**

Run:

```bash
pytest -q bot_vnext/tests/test_crawler_command.py
```

Expected: all crawler tests pass after concrete fixes.

- [ ] **Step 3: Run the existing safety suite**

Run:

```bash
pytest -q bot_vnext/tests/test_core_safety.py
```

Expected: PASS with no modifications to downloader/core safety behavior.

- [ ] **Step 4: Run the complete available test suite**

Run:

```bash
pytest -q
```

Expected: PASS, or if the repository has no root pytest configuration, run the discovered `bot_vnext/tests` suite explicitly and record that limitation rather than claiming a full-suite pass.

- [ ] **Step 5: Inspect the final diff for protected-area changes**

Verify that `bot_vnext/app/downloader/engine.py` and `bot_vnext/app/downloader/browser_hls.py` have no changes and that the only production behavior changes are `/crawl` dispatch plus the new bounded crawler.

- [ ] **Step 6: Commit the completed regression coverage**

```bash
git add crawler.py bot_vnext/main.py bot_vnext/tests/test_crawler_command.py
 git commit -m "test: harden crawl command traversal"
```

---

## Final Verification Checklist

- [ ] `/crawl <URL>` uses `crawl_website_episodes()` and the existing selection UI.
- [ ] Plain pasted `http://`/`https://` URLs still use the existing `crawl_url()` → `crawl_blog_episodes()` behavior.
- [ ] Root-page media/provider candidates are still extracted.
- [ ] Relevant same-site episode/season/player/pagination pages are traversed only within depth/page/time bounds.
- [ ] Different-host links are not crawled as pages.
- [ ] Provider/media links can still be emitted as candidates without being treated as traversal targets.
- [ ] Duplicate page URLs and duplicate media URLs are suppressed.
- [ ] Episode context propagates from parent index/link context to child-page candidates.
- [ ] Child fetch/parser failures are isolated.
- [ ] Result keys remain exactly `title`, `episode`, `url`, `source`, `resolution`, `source_url`.
- [ ] Downloader code is unchanged.
- [ ] Existing core safety tests pass.
- [ ] No claim of production success is made until the available tests and, where possible, a real `/crawl` smoke test have been run.

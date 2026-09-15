# `/crawl` Command Upgrade Design

## Scope

Upgrade only the website/episode crawling path used by the `/crawl <URL>` command.

The direct plain-URL chat flow must retain its current behavior and must not consume the new multi-page crawl logic. Downloader behavior and output must remain unchanged.

## Current flow

`bot_vnext/main.py` registers `/crawl` and passes the supplied URL to the existing crawl handler. The handler currently invokes `crawl_blog_episodes(url)` and then feeds its existing result objects into the current episode/source/resolution selection UI.

The current crawler extracts candidates from the supplied HTML using links, embeds, media tags, scripts and metadata, performs URL normalization/deduplication, detects episode/resolution/provider information, and returns the existing result schema.

## Proposed flow

`/crawl URL`
1. Fetch the root page.
2. Extract direct media/provider/player candidates exactly as the current crawler does.
3. Discover only relevant same-site child pages: episode/season/video/player/archive/index links and pagination links.
4. Crawl those relevant pages within strict safety bounds (depth/page count/time), with a visited-URL set.
5. Extract media/provider/player candidates from each successfully fetched page.
6. Preserve episode context from headings, link text, page title and URL when assigning candidates.
7. Normalize and globally deduplicate candidates.
8. Detect provider and resolution without changing downloader-facing URLs.
9. Return the same result object fields currently expected by the selection flow: `title`, `episode`, `url`, `source`, `resolution`, `source_url`.
10. Continue through the existing `/crawl` selection UI and existing enqueue/download pipeline.

## Discovery rules

- Stay on the source site's host for page traversal unless a link is an actual downloadable/provider candidate.
- Prefer links whose URL/text indicates episode, season, video, player, embed, watch, stream, or pagination semantics.
- Do not recursively spider arbitrary site navigation.
- Use a visited set based on normalized URLs.
- Apply bounded depth and page limits so large sites cannot cause unbounded crawling.
- Child-page failures must not discard candidates already found elsewhere.

## Separation requirement

The implementation must keep the two user entry behaviors conceptually separate:

- Plain URL message: existing direct-link behavior remains unchanged.
- `/crawl URL`: upgraded multi-page website crawler.

If the current code shares a helper between these paths, the upgraded traversal behavior must be isolated behind the `/crawl` path rather than globally changing the shared direct URL behavior.

## Compatibility requirements

- Do not modify downloader behavior.
- Do not change HLS/download/upload behavior.
- Do not change the existing selection UI contract.
- Do not remove provider support currently present.
- Preserve existing URL normalization and protected-domain handling unless a change is required to support the bounded `/crawl` discovery layer.
- Preserve existing method selection and queue behavior.

## Reliability

- Keep existing HTTP timeout behavior as the baseline.
- Handle malformed pages, failed child requests, redirects and parser errors independently.
- Deduplicate both page traversal and final media results.
- Avoid fetching the same child page more than once per crawl.

## Testing goals

Add focused tests for:

1. episode-link discovery from a root page;
2. pagination discovery;
3. nested episode/player page extraction;
4. duplicate page and media URL suppression;
5. episode-context propagation;
6. unrelated same-site navigation exclusion;
7. child-page failure isolation;
8. unchanged result schema;
9. direct URL path not invoking the new multi-page traversal behavior.

## Success criteria

For representative series pages where episodes are exposed through index/pagination/nested pages, `/crawl` should discover substantially more valid episode media candidates while avoiding unrelated site pages and duplicate results. Existing downstream selection and downloading must continue to receive the same schema and semantics.

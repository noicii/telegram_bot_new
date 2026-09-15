from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

if "pyrogram" not in sys.modules:
    pyrogram = types.ModuleType("pyrogram")
    pyrogram_types = types.ModuleType("pyrogram.types")
    pyrogram_types.InlineKeyboardButton = type("InlineKeyboardButton", (), {})
    pyrogram_types.InlineKeyboardMarkup = type("InlineKeyboardMarkup", (), {})
    pyrogram.types = pyrogram_types
    sys.modules["pyrogram"] = pyrogram
    sys.modules["pyrogram.types"] = pyrogram_types

import crawler
from app import crawler_website
from app.crawler_website import _discover_crawl_links, crawl_website_episodes
from crawler import crawl_blog_episodes

ROOT = "https://series.example/show"


class CrawlerCommandTests(unittest.TestCase):
    def test_plain_url_stays_on_existing_crawl_url_path(self):
        tree = ast.parse(Path("bot_vnext/main.py").read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "text_url")
        calls = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "crawl_url"
        ]
        self.assertTrue(calls)

    def test_crawl_command_uses_website_crawler_path(self):
        tree = ast.parse(Path("bot_vnext/main.py").read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "crawl_cmd")
        calls = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertTrue(any(call.func.attr == "crawl_website_url" for call in calls))
        self.assertFalse(any(call.func.attr == "crawl_url" for call in calls))

    def test_discovers_episode_and_pagination_links_but_not_navigation(self):
        html = """
        <a href="/episode-1">Episode 1</a>
        <a href="/season-1?page=2">Next</a>
        <a href="/about">About</a>
        <a href="/contact">Contact</a>
        <a href="https://other.example/episode-9">Other host</a>
        """
        soup = BeautifulSoup(html, "lxml")
        links = _discover_crawl_links(soup, ROOT, {crawler._host(ROOT)})
        self.assertIn("https://series.example/episode-1", links)
        self.assertIn("https://series.example/season-1?page=2", links)
        self.assertNotIn("https://series.example/about", links)
        self.assertNotIn("https://other.example/episode-9", links)

    def test_page_limit_prevents_unbounded_traversal(self):
        pages = {
            ROOT: '<a href="/episode-1">Episode 1</a><a href="/episode-2">Episode 2</a><a href="/episode-3">Episode 3</a>',
            "https://series.example/episode-1": '<iframe src="https://stream.example/embed/1"></iframe>',
            "https://series.example/episode-2": '<iframe src="https://stream.example/embed/2"></iframe>',
        }
        fetched = []

        def fake_fetch(url, timeout=25):
            fetched.append(url)
            return pages.get(url)

        with patch.object(crawler_website, "_fetch_html", side_effect=fake_fetch):
            crawl_website_episodes(ROOT, max_pages=2)
        self.assertLessEqual(len(fetched), 2)

    def test_depth_limit_prevents_deeper_page_fetches(self):
        episode = "https://series.example/episode-1"
        nested = "https://series.example/episode-2"
        pages = {
            ROOT: '<a href="/episode-1">Episode 1</a>',
            episode: '<a href="/episode-2">Episode 2</a>',
            nested: '<iframe src="https://stream.example/embed/2"></iframe>',
        }
        fetched = []

        def fake_fetch(url, timeout=25):
            fetched.append(url)
            return pages.get(url)

        with patch.object(crawler_website, "_fetch_html", side_effect=fake_fetch):
            crawl_website_episodes(ROOT, max_depth=1, max_pages=10)
        self.assertIn(ROOT, fetched)
        self.assertIn(episode, fetched)
        self.assertNotIn(nested, fetched)

    def test_nested_episode_page_propagates_episode_context(self):
        episode = "https://series.example/episode-7"
        pages = {
            ROOT: '<a href="/episode-7">Episode 7</a>',
            episode: '<h1>Episode 7</h1><iframe src="https://stream.example/embed/abc"></iframe>',
        }
        with patch.object(crawler_website, "_fetch_html", side_effect=lambda url, timeout=25: pages.get(url)):
            items = crawl_website_episodes(ROOT, max_pages=4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["episode"], "Episode 7")
        self.assertEqual(items[0]["source_url"], episode)

    def test_duplicate_media_urls_are_emitted_once(self):
        episode_1 = "https://series.example/episode-1"
        episode_2 = "https://series.example/episode-2"
        pages = {
            ROOT: '<a href="/episode-1">Episode 1</a><a href="/episode-2">Episode 2</a>',
            episode_1: '<h1>Episode 1</h1><iframe src="https://stream.example/embed/video?utm_source=a"></iframe>',
            episode_2: '<h1>Episode 2</h1><iframe src="https://stream.example/embed/video?utm_source=b"></iframe>',
        }
        with patch.object(crawler_website, "_fetch_html", side_effect=lambda url, timeout=25: pages.get(url)):
            items = crawl_website_episodes(ROOT, max_pages=4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://stream.example/embed/video")

    def test_result_schema_is_unchanged(self):
        pages = {ROOT: '<h2>Episode 1</h2><iframe src="https://stream.example/embed/abc"></iframe>'}
        with patch.object(crawler_website, "_fetch_html", side_effect=lambda url, timeout=25: pages.get(url)):
            items = crawl_website_episodes(ROOT)
        self.assertEqual(set(items[0]), {"title", "episode", "url", "source", "resolution", "source_url"})

    def test_child_page_failure_does_not_discard_root_candidates(self):
        episode = "https://series.example/episode-2"
        pages = {
            ROOT: '<h2>Episode 1</h2><iframe src="https://stream.example/e1"></iframe><a href="/episode-2">Episode 2</a>',
            episode: None,
        }
        with patch.object(crawler_website, "_fetch_html", side_effect=lambda url, timeout=25: pages.get(url)):
            items = crawl_website_episodes(ROOT)
        self.assertTrue(any(item["episode"] == "Episode 1" for item in items))

    def test_unrelated_same_site_links_are_not_fetched(self):
        fetched = []
        pages = {
            ROOT: '<a href="/about">About</a><a href="/episode-1">Episode 1</a>',
            "https://series.example/episode-1": '<iframe src="https://stream.example/e1"></iframe>',
        }

        def fake_fetch(url, timeout=25):
            fetched.append(url)
            return pages.get(url)

        with patch.object(crawler_website, "_fetch_html", side_effect=fake_fetch):
            crawl_website_episodes(ROOT)
        self.assertNotIn("https://series.example/about", fetched)

    def test_direct_crawler_stays_single_page(self):
        fetched = []

        def fake_fetch(url, timeout=25):
            fetched.append(url)
            return '<a href="/episode-2">Episode 2</a>'

        with patch.object(crawler, "_fetch_html", side_effect=fake_fetch):
            crawl_blog_episodes(ROOT)
        self.assertEqual(fetched, [ROOT])


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
CRAWLER = ROOT / "crawler.py"

# Crawl UI: show only selection mark + episode + resolution.
# Provider/site/domain stays internal and is never rendered in the episode button.
s = MAIN.read_text()
old = '''    def _button_label(self, item, index, selected):\n        mark = "☑️" if index in selected else "⬜"\n        return f"{mark} {item.get('episode') or 'Episode ?'} • {item.get('resolution') or 'Unknown'} • {urlparse(item.get('url') or '').netloc or 'Unknown'}"\n'''
new = '''    def _button_label(self, item, index, selected):\n        mark = "☑️" if index in selected else "⬜"\n        episode = str(item.get("episode") or "").strip()\n        resolution = str(item.get("resolution") or "").strip()\n        if not episode or episode.lower() == "unknown episode":\n            episode = "Episode ?"\n        if not resolution or resolution.lower() == "unknown":\n            resolution = "Quality ?"\n        return f"{mark} {episode} • {resolution}"\n'''
if old not in s:
    raise SystemExit("main.py button label block not found")
s = s.replace(old, new, 1)
MAIN.write_text(s)

# Improve episode extraction for common E03 / E3 naming used by provider links.
s = CRAWLER.read_text()
old = '''            r"\\bS\\d{1,2}E(\\d{1,4})\\b",\n            r"\\bEpisode[\\s._-]*(\\d{1,4})\\b",\n            r"\\bEp[\\s._-]*(\\d{1,4})\\b",\n'''
new = '''            r"\\bS\\d{1,2}E(\\d{1,4})\\b",\n            r"\\bEpisode[\\s._-]*(\\d{1,4})\\b",\n            r"\\bEp[\\s._-]*(\\d{1,4})\\b",\n            r"(?<![A-Za-z0-9])E[\\s._-]*(\\d{1,4})(?![A-Za-z0-9])",\n'''
if old not in s:
    raise SystemExit("crawler.py episode pattern block not found")
s = s.replace(old, new, 1)
CRAWLER.write_text(s)

print("CLEAN CRAWL UI APPLIED")
print("Episode buttons: Episode + resolution only")
print("Website/provider/domain metadata: hidden from UI")
print("Episode detection: E03/E3 patterns added")

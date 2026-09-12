from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
CRAWLER = ROOT / "crawler.py"

# Keep the provider/site visible in the selection button so the owner can
# distinguish multiple hosts from the same crawl. Remove only noisy URL
# metadata from the displayed label.
s = MAIN.read_text()
old = '''    def _button_label(self, item, index, selected):
        mark = "☑️" if index in selected else "⬜"
        return f"{mark} {item.get('episode') or 'Episode ?'} • {item.get('resolution') or 'Unknown'} • {urlparse(item.get('url') or '').netloc or 'Unknown'}"
'''
new = '''    def _button_label(self, item, index, selected):
        mark = "☑️" if index in selected else "⬜"
        episode = str(item.get("episode") or "").strip()
        resolution = str(item.get("resolution") or "").strip()
        provider = str(item.get("source") or "").strip()
        if not episode or episode.lower() == "unknown episode":
            episode = "Episode ?"
        if not resolution or resolution.lower() == "unknown":
            resolution = "Quality ?"
        if not provider or provider.lower() == "unknown":
            provider = "Unknown site"
        return f"{mark} {episode} • {resolution} • {provider}"
'''
if old not in s:
    raise SystemExit("main.py button label block not found")
s = s.replace(old, new, 1)
MAIN.write_text(s)

# Improve episode extraction for common E03 / E3 naming used by provider links.
s = CRAWLER.read_text()
old = '''            r"\\bS\\d{1,2}E(\\d{1,4})\\b",
            r"\\bEpisode[\\s._-]*(\\d{1,4})\\b",
            r"\\bEp[\\s._-]*(\\d{1,4})\\b",
'''
new = '''            r"\\bS\\d{1,2}E(\\d{1,4})\\b",
            r"\\bEpisode[\\s._-]*(\\d{1,4})\\b",
            r"\\bEp[\\s._-]*(\\d{1,4})\\b",
            r"(?<![A-Za-z0-9])E[\\s._-]*(\\d{1,4})(?![A-Za-z0-9])",
'''
if old not in s:
    raise SystemExit("crawler.py episode pattern block not found")
s = s.replace(old, new, 1)
CRAWLER.write_text(s)

print("CRAWL UI METADATA FIX APPLIED")
print("Episode buttons: Episode + resolution + provider/site")
print("Provider/site remains visible for multi-host crawls")
print("Episode detection: E03/E3 patterns added")

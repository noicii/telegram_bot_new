from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
PIPELINE = ROOT / "bot_vnext" / "app" / "pipeline.py"

# Upload pool: 6 independent workers.
p = PIPELINE.read_text()
p = p.replace('UploadManager(client, workers=4,', 'UploadManager(client, workers=6,', 1)
PIPELINE.write_text(p)

# Pyrogram: up to 16 concurrent media transmissions.
m = MAIN.read_text()
if "max_concurrent_transmissions=" in m:
    m = re.sub(r"max_concurrent_transmissions=\d+", "max_concurrent_transmissions=16", m, count=1)
else:
    patterns = [
        (r'(Client\([^\n]*BOT_TOKEN[^\n]*)(\))', r'\1, max_concurrent_transmissions=16\2'),
        (r'(Client\([^\n]*bot_token[^\n]*)(\))', r'\1, max_concurrent_transmissions=16\2'),
    ]
    for pattern, repl in patterns:
        m, n = re.subn(pattern, repl, m, count=1)
        if n:
            break
# Keep UI text truthful.
m = m.replace('uploads=4', 'uploads=6')
m = m.replace('Uploads: **4** simultaneous', 'Uploads: **6** simultaneous')
m = m.replace('active_u}/4', 'active_u}/6')
m = m.replace('u.get(\'queued\', 0)', 'u.get(\'queued\', 0)')
MAIN.write_text(m)

print("ULTRA UPLOAD 6x16 APPLIED")
print("Upload workers: 4 -> 6")
print("Pyrogram transmissions: 8 -> 16")
print("Download settings unchanged")

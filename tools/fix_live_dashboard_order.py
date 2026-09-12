from pathlib import Path

p = Path("bot_vnext/main.py")
s = p.read_text()

old = '''        else:\n            for row in rows[:10]:\n                kind = "⬇️" if row.get("task_type") == "download" else "⬆️"\n                status = str(row.get("status") or "queued").upper()\n                pct = float(row.get("progress") or 0)\n                method = (row.get("metadata") or {}).get("download_method") or "auto"\n                title = str(row.get("title") or row.get("url") or row.get("id"))[:45]\n                lines.append(f"{kind} **{status}** • **{pct:.1f}%** • {method_label(method)}")\n                lines.append(f"🎬 {title}")\n                if row.get("speed"): lines.append(f"⚡ {float(row.get('speed') or 0)/1048576:.2f} MB/s")\n                lines.append("")\n'''

new = '''        else:\n            # Keep task cards in creation order. Progress updates change updated_at,\n            # so the dashboard must never reorder videos while they are downloading.\n            def _task_order(row):\n                created = str(row.get("created_at") or "")\n                return (created, str(row.get("id") or ""))\n\n            rows = sorted(rows, key=_task_order)\n            for index, row in enumerate(rows[:10], 1):\n                kind = "⬇️" if row.get("task_type") == "download" else "⬆️"\n                status = str(row.get("status") or "queued").upper()\n                pct = float(row.get("progress") or 0)\n                method = (row.get("metadata") or {}).get("download_method") or "auto"\n                title = str(row.get("title") or row.get("url") or row.get("id"))[:55]\n                lines.append(f"**#{index}** {kind} **{status}** • **{pct:.1f}%** • {method_label(method)}")\n                lines.append(f"🎬 **{title}**")\n                if row.get("speed"): lines.append(f"⚡ {float(row.get('speed') or 0)/1048576:.2f} MB/s")\n                lines.append("")\n'''

if old not in s:
    raise SystemExit("dashboard block not found; no changes made")

p.write_text(s.replace(old, new, 1))
print("LIVE DASHBOARD ORDER FIX APPLIED")

"""Telegram entrypoint for Bot V2 with a single-message live command center."""
from __future__ import annotations
import asyncio
import logging
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent
if str(V2_ROOT) not in sys.path: sys.path.insert(0, str(V2_ROOT))
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH
from crawler import crawl_blog_episodes
from utils import sanitize_filename
from app.pipeline import Pipeline
from app.downloader.method_store import get_method, set_method, get_default_method, set_default_method, next_method, method_label, METHODS
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("bot_vnext")
PAGE_SIZE = 8
COMMANDS = [("start","🚀 Start Bot V2"),("status","📊 Live download/upload progress"),("queue","📋 Running & queued tasks"),("cancel","🛑 Cancel a download/upload task"),("retry","🔁 Retry a failed task"),("clear","🧹 Clear completed/failed/cancelled tasks"),("crawl","🔎 Crawl URL & select episodes"),("settings","⚙️ Bot settings & controls"),("method","🎯 Choose download method"),("health","🩺 Check bot & engine health")]
def owner_only(message): return bool(OWNER_ID and message.from_user and message.from_user.id == OWNER_ID)
def callback_owner(query): return bool(OWNER_ID and query.from_user and query.from_user.id == OWNER_ID)
def fmt_bytes(v):
    try: v=max(0.0,float(v or 0))
    except (TypeError,ValueError): v=0.0
    return f"{v/(1024**3):.2f} GB" if v>=1024**3 else f"{v/(1024**2):.0f} MB"
def fmt_speed(v):
    try: v=max(0.0,float(v or 0))
    except (TypeError,ValueError): v=0.0
    return f"{v/(1024**2):.1f} MB/s"
def fmt_eta(v):
    try: s=max(0,int(v or 0))
    except (TypeError,ValueError): s=0
    if not s: return "—"
    h,s=divmod(s,3600); m,s=divmod(s,60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
def bar(p,width=20):
    try: p=max(0.0,min(100.0,float(p or 0)))
    except (TypeError,ValueError): p=0.0
    n=max(0,min(width,int(round(p/100*width))))
    return "█"*n+"░"*(width-n)
class V2Bot:
    def __init__(self,client):
        self.client=client; self.pipeline=None; self.sessions={}; self.progress_cache={}; self.dashboard_messages={}; self.dashboard_locks={}; self.dashboard_last_edit={}
    def lock(self,chat_id): return self.dashboard_locks.setdefault(chat_id,asyncio.Lock())
    async def start(self):
        self.pipeline=Pipeline(self.client,DOWNLOAD_DIR,on_progress=self.on_progress,on_complete=self.on_complete,on_failed=self.on_failed); await self.pipeline.start()
        try: await self.client.set_bot_commands([BotCommand(c,d) for c,d in COMMANDS])
        except Exception: logger.exception("could not set bot command menu")
    async def stop(self):
        if self.pipeline: await self.pipeline.stop(); self.pipeline=None
    async def start_cmd(self,client,message):
        if owner_only(message): await message.reply_text("🎬 **BOT V2 READY**\n\n🔎 `/crawl <URL>` — crawl & select episodes\n📊 `/status` — live status\n📋 `/queue` — task list\n🎯 `/method` — choose download method\n⚙️ `/settings` — all controls\n🩺 `/health` — system health\n\n⚡ Downloads: **2** simultaneous\n⚡ Uploads: **4** simultaneous\n🛑 Independent cancellation: **ON**")
    async def status_cmd(self,client,message):
        if owner_only(message): await self.show_dashboard(message.chat.id,message)
    async def show_dashboard(self,chat_id,source=None):
        if not self.pipeline:
            if source: await source.reply_text("❌ V2 pipeline is not running.")
            return
        mid=self.dashboard_messages.get(chat_id)
        if mid and await self.render_dashboard(chat_id,mid,True): return
        self.dashboard_messages.pop(chat_id,None)
        try: msg=await source.reply_text("📊 **LIVE DOWNLOAD / UPLOAD**\n\n⏳ Loading live status…") if source else await self.client.send_message(chat_id,"📊 **LIVE DOWNLOAD / UPLOAD**\n\n⏳ Loading live status…")
        except Exception: return
        self.dashboard_messages[chat_id]=msg.id; await self.render_dashboard(chat_id,msg.id,True)
    async def queue_cmd(self,client,message):
        if owner_only(message): await self.send_queue(message)
    async def send_queue(self,message):
        if not self.pipeline: await message.reply_text("❌ V2 pipeline is offline."); return
        rows=await self.pipeline.db.get_tasks(limit=25); active=[r for r in rows if r.get("status") in {"queued","downloading","uploading"}]
        if not active:
            await message.reply_text("📋 **QUEUE EMPTY**\n\nNo queued or running tasks.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Live Status",callback_data="v2:status")]])); return
        lines=["📋 **V2 QUEUE**",""]; buttons=[]
        for i,r in enumerate(active[:15],1):
            kind="⬆️" if r.get("status")=="uploading" else "⬇️"; title=str(r.get("title") or r.get("url") or r.get("id"))[:42]; pct=float(r.get("progress") or 0); method=(r.get("metadata") or {}).get("download_method") or "auto"
            lines.append(f"{kind} **{i}. {str(r.get('status','?')).upper()}** • {pct:.0f}% • {method_label(method)}\n`{title}`\n`{r.get('id')}`"); buttons.append([InlineKeyboardButton(f"🛑 Cancel {i}",callback_data=f"v2:cancel:{r.get('id')}")])
        buttons += [[InlineKeyboardButton("📊 Live Status",callback_data="v2:status"),InlineKeyboardButton("🔄 Refresh",callback_data="v2:queue")],[InlineKeyboardButton("🧹 Clear Done",callback_data="v2:clear")]]
        await message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons))
    async def cancel_cmd(self,client,message):
        if not owner_only(message): return
        p=(message.text or "").split()
        if len(p)<2: await message.reply_text("Usage: `/cancel TASK_ID`"); return
        if not self.pipeline: await message.reply_text("❌ V2 pipeline is offline."); return
        await message.reply_text("🛑 Task cancelled independently." if await self.pipeline.cancel(p[-1]) else "⚠️ Task is not currently active.")
    async def retry_cmd(self,client,message):
        if not owner_only(message): return
        p=(message.text or "").split(maxsplit=1)
        if len(p)!=2: await message.reply_text("Usage: `/retry TASK_ID`"); return
        if not self.pipeline: await message.reply_text("❌ V2 pipeline is offline."); return
        m=await get_default_method(); ok=await self.pipeline.retry(p[1].strip(),method=m); await message.reply_text(f"🔁 Task re-queued with **{method_label(m)}** (max 3 attempts)." if ok else "⚠️ Retry is available only for failed/cancelled tasks.")
    async def clear_cmd(self,client,message):
        if not owner_only(message): return
        if not self.pipeline: await message.reply_text("❌ V2 pipeline is offline."); return
        await message.reply_text(f"🧹 Cleared **{await self.pipeline.db.clear_finished()}** completed/failed/cancelled task(s).")
    async def crawl_cmd(self,client,message):
        if not owner_only(message): return
        p=(message.text or "").split(maxsplit=1)
        if len(p)!=2: await message.reply_text("Usage: `/crawl https://example.com/episode-page`"); return
        await self.crawl_url(message,p[1].strip())
    async def text_url(self,client,message):
        if not owner_only(message): return
        t=(message.text or "").strip()
        if not t or t.startswith("/"): return
        if t.startswith(("http://","https://")): await self.crawl_url(message,t)
        else: await message.reply_text("🔗 Send a valid http/https URL or use `/crawl <URL>`.")
    async def crawl_url(self,message,url):
        wait=await message.reply_text("🔎 Crawling… please wait")
        try: items=await asyncio.to_thread(crawl_blog_episodes,url)
        except Exception as exc: logger.exception("crawl failed"); await wait.edit_text(f"❌ Crawl failed\n`{str(exc)[:700]}`"); return
        if not items: await wait.edit_text("❌ No downloadable episode links found."); return
        series=self._series_name(items); saved=await get_method(items[0].get("url") or url); self.sessions[message.from_user.id]={"items":items,"page":0,"selected":set(),"series":series,"source_url":url,"message_id":wait.id,"method":saved}; await self.render_selection(wait,self.sessions[message.from_user.id])
    def _series_name(self,items):
        title=str(items[0].get("title") or "Series"); ep=str(items[0].get("episode") or ""); res=str(items[0].get("resolution") or "")
        for x in (f" - {ep}",f" - {res}"):
            if x in title: title=title.split(x,1)[0]
        return title.strip(" -") or "Series"
    def _button_label(self,item,index,selected): return f"{'☑️' if index in selected else '⬜'} {item.get('episode') or 'Episode ?'} • {item.get('resolution') or 'Unknown'} • {urlparse(item.get('url') or '').netloc or 'Unknown'}"
    async def render_selection(self,message,s):
        items,page,selected=s["items"],s["page"],s["selected"]; pages=max(1,(len(items)+PAGE_SIZE-1)//PAGE_SIZE); start=page*PAGE_SIZE; end=min(len(items),start+PAGE_SIZE)
        lines=[f"🎬 **{s['series']}**",f"📦 **{len(items)} Options**",""]+[self._button_label(items[i],i,selected) for i in range(start,end)]+["",f"📄 Page {page+1}/{pages} • ☑️ {len(selected)}/{len(items)}",f"🎯 **Download method: {method_label(s.get('method','auto'))}**"]
        buttons=[[InlineKeyboardButton(self._button_label(items[i],i,selected),callback_data=f"v2:t:{i}")] for i in range(start,end)]; nav=[]
        if page>0: nav.append(InlineKeyboardButton("◀️ Previous",callback_data="v2:p:-1"))
        if page+1<pages: nav.append(InlineKeyboardButton("Next ▶️",callback_data="v2:p:1"))
        if nav: buttons.append(nav)
        buttons += [[InlineKeyboardButton("☑️ Select All",callback_data="v2:all"),InlineKeyboardButton("❌ Clear",callback_data="v2:selclear")],[InlineKeyboardButton(f"🎯 Change: {method_label(s.get('method','auto'))}",callback_data="v2:method")],[InlineKeyboardButton("🚀 Download Selected",callback_data="v2:download")],[InlineKeyboardButton("❌ Cancel Selection",callback_data="v2:close")]]
        await message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons))
    async def method_cmd(self,client,message):
        if owner_only(message): await self.send_method_menu(message)
    async def send_method_menu(self,message):
        cur=await get_default_method(); await message.reply_text(f"🎯 **DOWNLOAD METHOD**\n\nCurrent default: **{method_label(cur)}**",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(("✅ " if m==cur else "")+method_label(m),callback_data=f"v2:m:{m}")] for m in METHODS]))
    async def settings_cmd(self,client,message):
        if owner_only(message): await self.send_settings(message)
    async def send_settings(self,message):
        m=await get_default_method(); await message.reply_text(f"⚙️ **BOT V2 SETTINGS**\n\n🎯 Download method: **{method_label(m)}**\n⬇️ Download workers: **2**\n⬆️ Upload workers: **4**\n🛑 Independent cancellation: **ON**\n💾 Persistent SQLite queue: **ON**\n🖼️ Auto thumbnail: configured\n🍪 Cookies: auto-detected",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 Download Method",callback_data="v2:methodmenu")],[InlineKeyboardButton("📊 Live Status",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")],[InlineKeyboardButton("🩺 Health Check",callback_data="v2:health")],[InlineKeyboardButton("🧹 Clear Finished",callback_data="v2:clear")]]))
    async def health_cmd(self,client,message):
        if owner_only(message): await self.send_health(message)
    async def send_health(self,message):
        checks=[("Pipeline",bool(self.pipeline and self.pipeline._started)),("SQLite DB",Path(ROOT/"bot_vnext.db").exists()),("FFmpeg",shutil.which("ffmpeg") is not None),("aria2c",shutil.which("aria2c") is not None)]; chromium=Path.home()/".cache"/"ms-playwright"; checks.append(("Playwright cache",chromium.exists() and any(chromium.glob("chromium*")))); checks += [("Download workers",bool(self.pipeline and len(self.pipeline.download.running_tasks)<=2)),("Upload workers",bool(self.pipeline and len(self.pipeline.upload.running_tasks)<=4))]; lines=["🩺 **V2 HEALTH CHECK**",""]+[f"{'✅' if ok else '❌'} {name}" for name,ok in checks]
        if self.pipeline: lines += ["",f"⬇️ Active downloads: {self.pipeline.download.active_workers()}/2",f"⬆️ Active uploads: {self.pipeline.upload.active_workers()}/4"]
        await message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Recheck",callback_data="v2:health")]]))
    async def callback(self,client,query):
        if not callback_owner(query): await query.answer("Not allowed",show_alert=True); return
        data=query.data or ""
        if data=="v2:status": await query.answer(); self.dashboard_messages[query.message.chat.id]=query.message.id; await self.render_dashboard(query.message.chat.id,query.message.id,True); return
        if data=="v2:queue": await query.answer(); await self.send_queue(query.message); return
        if data=="v2:settings": await query.answer(); await self.send_settings(query.message); return
        if data in {"v2:health","v2:healthmenu"}: await query.answer(); await self.send_health(query.message); return
        if data=="v2:methodmenu" and not self.sessions.get(query.from_user.id): await query.answer(); await self.send_method_menu(query.message); return
        if data.startswith("v2:m:"):
            m=data.rsplit(":",1)[1]
            if m not in METHODS: await query.answer("Invalid method",show_alert=True); return
            await set_default_method(m); await query.answer(f"Default: {method_label(m)}"); await self.send_method_menu(query.message); return
        if data=="v2:clear":
            if self.pipeline: await query.answer(f"Cleared {await self.pipeline.db.clear_finished()} task(s)"); await self.send_queue(query.message)
            else: await query.answer("Pipeline offline",show_alert=True)
            return
        if data.startswith("v2:cancel:"):
            tid=data.split(":",2)[2]; ok=bool(self.pipeline and await self.pipeline.cancel(tid)); await query.answer("Cancelled" if ok else "Not active",show_alert=not ok); return
        s=self.sessions.get(query.from_user.id)
        if not s: await query.answer("Selection expired. Send /crawl again.",show_alert=True); return
        if data=="v2:method": s["method"]=next_method(s.get("method","auto")); await query.answer(f"Method: {method_label(s['method'])}"); await self.render_selection(query.message,s); return
        if data.startswith("v2:t:"):
            i=int(data.rsplit(":",1)[1]); s["selected"].discard(i) if i in s["selected"] else s["selected"].add(i); await query.answer(); await self.render_selection(query.message,s); return
        if data=="v2:all": s["selected"]=set(range(len(s["items"]))); await query.answer("All selected"); await self.render_selection(query.message,s); return
        if data=="v2:selclear": s["selected"].clear(); await query.answer("Selection cleared"); await self.render_selection(query.message,s); return
        if data.startswith("v2:p:"):
            d=int(data.rsplit(":",1)[1]); pages=max(1,(len(s["items"])+PAGE_SIZE-1)//PAGE_SIZE); s["page"]=max(0,min(pages-1,s["page"]+d)); await query.answer(); await self.render_selection(query.message,s); return
        if data=="v2:close": self.sessions.pop(query.from_user.id,None); await query.answer("Selection closed"); await query.message.edit_text("❌ Selection cancelled."); return
        if data=="v2:download":
            selected=sorted(s["selected"])
            if not selected: await query.answer("Select at least one item",show_alert=True); return
            await query.answer("Queued"); await self.enqueue_selected(query.message,s,selected)
    async def enqueue_selected(self,message,s,selected):
        if not self.pipeline: await message.edit_text("❌ V2 pipeline is offline."); return
        method=s.get("method",await get_default_method()); dash=await message.edit_text(f"🚀 **Queued {len(selected)} task(s)**\n🎬 {s['series']}\n🎯 Method: {method_label(method)}\n\n⏳ Starting live dashboard…"); self.dashboard_messages[message.chat.id]=dash.id
        for i in selected:
            item=s["items"][i]; tid=uuid.uuid4().hex; filename=sanitize_filename(item.get("title") or f"{tid}.mp4"); filename += ".mp4" if not Path(filename).suffix else ""; await set_method(item.get("url") or "",method)
            md={"source_url":item.get("source_url"),"provider":item.get("source"),"resolution":item.get("resolution"),"cookiefile":str(COOKIES_PATH) if COOKIES_PATH.is_file() else None,"download_method":method,"headers":{"Referer":item.get("source_url")} if item.get("source_url") else {}}; md={k:v for k,v in md.items() if v}
            payload={"task_type":"download","url":item["url"],"filename":filename,"chat_id":message.chat.id,"caption":item.get("title") or s["series"],"thumbnail":str(THUMB_PATH) if THUMB_PATH.is_file() else None,"mode":"video","title":item.get("title"),"provider":item.get("source"),"resolution":item.get("resolution"),"metadata":md}
            try: await self.pipeline.submit(tid,payload)
            except Exception: logger.exception("queue submit failed for %s",tid)
        await self.render_dashboard(message.chat.id,dash.id,True)
    async def render_dashboard(self,chat_id,message_id,force=False):
        if not self.pipeline or not message_id: return False
        async with self.lock(chat_id):
            now=asyncio.get_running_loop().time()
            if not force and now-self.dashboard_last_edit.get(chat_id,0)<1.2: return True
            rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); counts=await self.pipeline.db.counts(); d,u=counts.get("download",{}),counts.get("upload",{}); ad,au=self.pipeline.download.active_workers(),self.pipeline.upload.active_workers(); qd,qu=d.get("queued",0),u.get("queued",0)
            lines=["📊 **LIVE DOWNLOAD / UPLOAD**","",f"⬇️ Downloads: **{ad}/2 active** • {qd} queued",f"⬆️ Uploads: **{au}/4 active** • {qu} queued",""]
            downs=[r for r in rows if r.get("status") in {"queued","downloading"}]; ups=[r for r in rows if r.get("status")=="uploading"]
            if downs:
                lines.append("📥 **DOWNLOADING**")
                for n,r in enumerate(downs[:8],1):
                    live=self.progress_cache.get(str(r.get("id")),{}); pct=float(live.get("percent",r.get("progress") or 0) or 0); cur=live.get("current",0); total=live.get("total",0); sp=live.get("speed",r.get("speed",0)); eta=live.get("eta",r.get("eta",0)); det=live.get("details") or {}; title=str(r.get("title") or r.get("url") or r.get("id"))[:48]; res=str(r.get("resolution") or "").strip(); lines += [f"{n}️⃣ **{title}{(' • '+res) if res else ''}**",f"{bar(pct)} **{pct:.0f}%**",f"📦 {fmt_bytes(cur)} / {fmt_bytes(total)} • ⚡ {fmt_speed(sp)} • ETA {fmt_eta(eta)}"]
                    if det.get("hls_total"): lines.append(f"🧩 HLS: {int(det.get('hls_completed') or 0)} / {int(det.get('hls_total') or 0)} segments"+(f" • 🔁 {int(det.get('hls_retries') or 0)} retries" if det.get("hls_retries") else ""))
                    lines.append("")
            else: lines.append("📥 **DOWNLOADING**\nNo active downloads.\n")
            if ups:
                lines.append("📤 **UPLOADING**")
                for n,r in enumerate(ups[:8],1):
                    live=self.progress_cache.get(str(r.get("id")),{}); pct=float(live.get("percent",r.get("progress") or 0) or 0); cur=live.get("current",0); total=live.get("total",0); sp=live.get("speed",r.get("speed",0)); eta=live.get("eta",r.get("eta",0)); det=live.get("details") or {}; title=str(r.get("title") or r.get("url") or r.get("id"))[:48]; lines += [f"{n}️⃣ **{title}**",f"{bar(pct)} **{pct:.0f}%**",f"📦 {fmt_bytes(cur)} / {fmt_bytes(total)} • ⚡ {fmt_speed(sp)} • ETA {fmt_eta(eta)}"]
                    if int(det.get("parts") or 1)>1: lines.append(f"📦 Part {int(det.get('part') or 1)}/{int(det.get('parts') or 1)}")
                    lines.append("")
            else: lines.append("📤 **UPLOADING**\nNo active uploads.\n")
            disk=shutil.disk_usage(DOWNLOAD_DIR); used=disk.total-disk.free; dp=used*100/disk.total if disk.total else 0; icon="🔴" if dp>=90 else "🟠" if dp>=80 else "🟢"; lines.append(f"{icon} **Disk:** {used/(1024**3):.1f}/{disk.total/(1024**3):.1f} GB used • {disk.free/(1024**3):.1f} GB free ({dp:.0f}%)")
            lines.append(f"\n✅ Done: {d.get('completed',0)+u.get('completed',0)} • ❌ Failed: {d.get('failed',0)+u.get('failed',0)} • 🛑 Cancelled: {d.get('cancelled',0)+u.get('cancelled',0)}")
            try:
                msg=await self.client.get_messages(chat_id,message_id); await msg.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])); self.dashboard_last_edit[chat_id]=now; return True
            except Exception as exc: logger.debug("dashboard edit failed: %s",exc); return False
    async def on_progress(self,kind,task_id,percent=None,current=None,total=None,speed=None,eta=None,details=None):
        self.progress_cache[str(task_id)]={"percent":percent,"current":current or 0,"total":total or 0,"speed":speed or 0,"eta":eta or 0,"details":details or {},"kind":kind}
        try:
            row=await self.pipeline.db.get_task(str(task_id)) if self.pipeline else None
            if row and row.get("chat_id") and self.dashboard_messages.get(int(row["chat_id"])): await self.render_dashboard(int(row["chat_id"]),self.dashboard_messages[int(row["chat_id"])])
        except Exception: logger.debug("live dashboard update failed",exc_info=True)
    async def on_complete(self,task_id):
        self.progress_cache.pop(str(task_id),None)
        try:
            row=await self.pipeline.db.get_task(str(task_id)) if self.pipeline else None
            if row and row.get("chat_id"): await self.render_dashboard(int(row["chat_id"]),self.dashboard_messages.get(int(row["chat_id"])),True)
        except Exception: pass
    async def on_failed(self,task_id,error):
        self.progress_cache.pop(str(task_id),None); logger.error("task %s failed: %s",task_id,error)
        try:
            row=await self.pipeline.db.get_task(str(task_id)) if self.pipeline else None
            if row and row.get("chat_id"): await self.render_dashboard(int(row["chat_id"]),self.dashboard_messages.get(int(row["chat_id"])),True)
        except Exception: pass
async def run():
    if not BOT_TOKEN or not API_ID or not API_HASH or not OWNER_ID: raise RuntimeError("API_ID, API_HASH, BOT_TOKEN and OWNER_ID must be configured")
    app=Client("telegram_bot_v2",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN,workdir=str(ROOT/"data")); bot=V2Bot(app)
    app.add_handler(MessageHandler(bot.start_cmd,filters.command("start"))); app.add_handler(MessageHandler(bot.status_cmd,filters.command("status"))); app.add_handler(MessageHandler(bot.queue_cmd,filters.command("queue"))); app.add_handler(MessageHandler(bot.cancel_cmd,filters.command("cancel"))); app.add_handler(MessageHandler(bot.retry_cmd,filters.command("retry"))); app.add_handler(MessageHandler(bot.clear_cmd,filters.command("clear"))); app.add_handler(MessageHandler(bot.crawl_cmd,filters.command("crawl"))); app.add_handler(MessageHandler(bot.settings_cmd,filters.command("settings"))); app.add_handler(MessageHandler(bot.method_cmd,filters.command("method"))); app.add_handler(MessageHandler(bot.health_cmd,filters.command("health"))); app.add_handler(MessageHandler(bot.text_url,filters.text & ~filters.command([c for c,_ in COMMANDS]))); app.add_handler(CallbackQueryHandler(bot.callback))
    async with app:
        await bot.start()
        try: await asyncio.Event().wait()
        finally: await bot.stop()
if __name__=="__main__": asyncio.run(run())

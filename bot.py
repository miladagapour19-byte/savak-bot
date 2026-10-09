import asyncio
import os
import json
import random
import time
import subprocess
from datetime import datetime

from splusthon import SoroushClient, events
from splusthon.sessions import StringSession

# ═══════════════════════════════════════════════════════
#  ⚡ هسته اصلی
# ═══════════════════════════════════════════════════════

PHONE_NUMBER = "+989057271421"
SESSION_FILE = os.path.expanduser("~/savak_session.txt")
TEXTS_FILE   = os.path.expanduser("~/texts_config.json")
NET_FILE     = os.path.expanduser("~/net_stats.json")
JUDGE_FILE   = os.path.expanduser("~/judge_config.json")

BOOT_TIME    = datetime.now()
START_TS     = time.time()

SLOTS      = tuple(range(1, 11))
LIST_SLOTS = (1, 2, 3)

# ─── آفیشو ───
OFF_MIN_SECONDS = 180   # ۳ دقیقه کامل
OFF_MSG_SINGLE  = "به دلیل ۳ مین آفی با این شخص به هیچ کلی ادامه نمیدم"
OFF_MSG_MULTI   = "کس ننتی"
OFF_MSG_DELETED = "به دلیل ۳ مین آفی با ساید مقابل به هیچ کلی ادامه نمیدیم و هر چتی از ساید ما و ساید مقابل بیاد فاقده"

# ─── Judge ───
JUDGE_KEYWORDS = [
    ("داور", 99),
    ("ریپ بزن", 85), ("ریپلای کن", 85),
    ("@all", 70),
    ("زمان فایت", 80), ("فایت تموم", 80), ("فایت تمام", 80),
]

SILENT_MODE = True   # تأییدیه‌های ادمین → سیو مسیج، تو گپ هیچی نمیاد

net_stats = {
    "start_time": START_TS,
    "sent_bytes": 0,
    "recv_bytes": 0,
    "ping_samples": [],
    "running": False,
    "task": None,
}

# ────────────────────────────────────────────────────────
#  سشن
# ────────────────────────────────────────────────────────

def load_session():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s and len(s) > 50:
                print("✅ session found")
                return StringSession(s)
        except Exception as e:
            print(f"⚠️ session err: {e}")
    print("🆕 new session")
    return StringSession()

def save_session(cl=None):
    try:
        c = cl or client
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(c.session.save())
        print("💾 session saved")
        return True
    except Exception as e:
        print(f"⚠️ save err: {e}")
        return False

client = SoroushClient(load_session())

def try_wakelock():
    try:
        subprocess.run(["termux-wake-lock"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("🔒 wakelock فعال شد")
    except Exception:
        pass

# ────────────────────────────────────────────────────────
#  ذخیره‌سازی
# ────────────────────────────────────────────────────────

def load_texts():
    default = {}
    for s in SLOTS:
        default[f"spam{s}"]  = []
        default[f"reply{s}"] = []
    if not os.path.exists(TEXTS_FILE):
        return default
    try:
        with open(TEXTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return default

def save_texts(d):
    with open(TEXTS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

texts = load_texts()

saved_messages = {s: None for s in SLOTS}
forward_tasks  = {s: {} for s in SLOTS}
spam_tasks     = {s: {} for s in SLOTS}
spam_configs   = {s: {} for s in SLOTS}
reply_tasks    = {s: {} for s in SLOTS}
reply_configs  = {s: {} for s in SLOTS}
stats          = {s: {"spam": 0, "forward": 0, "reply": 0, "total": 0} for s in SLOTS}

user_cache    = {}
last_msg_time = {}
last_msg_id   = {}
off_locked    = {}
edit_targets  = {}
user_edit_counts = {}
voice_evidence_log = {}

# آفیشو: chat_id -> list of {"user_id": int, "marker_msg_id": int, "replied": bool}
off_trackers = {}

# Judge
judge_cache = {}   # chat_id -> [user_id_str, ...]
judge_mode  = {"enabled": False, "reply_text": "حاضر"}

def load_json_file(fn):
    if not os.path.exists(fn): return {}
    try:
        with open(fn, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_json_file(fn, data):
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_judge_config():
    global judge_mode
    if os.path.exists(JUDGE_FILE):
        try:
            with open(JUDGE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            judge_mode["enabled"]    = bool(d.get("enabled", False))
            judge_mode["reply_text"] = d.get("reply_text", "حاضر")
        except Exception:
            pass

def save_judge_config():
    try:
        with open(JUDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(judge_mode, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

load_judge_config()

def slot_text(kind, slot):
    return texts.get(f"{kind}{slot}", [])

def save_slot_text(kind, slot, values):
    texts[f"{kind}{slot}"] = values
    save_texts(texts)

def msg_link(chat_id, msg_id):
    cid = str(chat_id)
    if cid.startswith("-100"): cid = cid[4:]
    elif cid.startswith("-"): cid = cid[1:]
    return f"https://t.me/c/{cid}/{msg_id}"

def is_private(event):
    try: return event.is_private
    except Exception: return event.chat_id > 0

# ────────────────────────────────────────────────────────
#  پاسخ ادمین (سایلنت)
# ────────────────────────────────────────────────────────

async def admin_reply(event, text):
    """تأییدیه ادمین → سایلنت، میره سیو مسیج."""
    if not SILENT_MODE:
        try:
            await event.reply(text); return
        except Exception:
            pass
    try:
        await client.send_message("me", text)
    except Exception:
        pass

async def admin_reply_err(event, text):
    """خطا هم میره سیو مسیج."""
    try:
        await client.send_message("me", f"⚠️ {text}")
    except Exception:
        pass

# ────────────────────────────────────────────────────────
#  لیست‌ها
# ────────────────────────────────────────────────────────

FILES = {
    "friend":   {s: os.path.expanduser(f"~/friend_users{s}.json")   for s in LIST_SLOTS},
    "antigirl": {s: os.path.expanduser(f"~/antigirl_users{s}.json") for s in LIST_SLOTS},
    "enemy":    {s: os.path.expanduser(f"~/enemy_users{s}.json")    for s in LIST_SLOTS},
}
TITLES = {"friend": "دوست", "antigirl": "ضد دختر", "enemy": "بدخواه"}

users_data = {k: {s: load_json_file(FILES[k][s]) for s in LIST_SLOTS} for k in FILES}
indexes    = {k: {s: {} for s in LIST_SLOTS} for k in FILES}

ENEMY_INSULTS = [
    "کسه ننت","کس ننت","کسننت","کسه ناموست","کس ناموست","کسناموست","کسه مادرت",
    "کس مادرت","کسمادرت","کیرم تو کسمادرت","کیر تو کسمادرت","کیرم تو کسه مادرت",
    "کیرم تو کس مادرت","کیرم تو ناموست","کیر تو ناموست","کیرم تو کسناموست",
    "کیرم تو ننت","کیر تو ننت","مادر جندتو گاییدم","مادر کسکشتو گاییدم",
    "مادر سگتو گاییدم","مادرتو گاییدم","ننتو گاییدم","ناموستو گاییدم",
    "مادرت رو گاییدم","ننت رو میگام","ناموست رو میگام","کیر تو قلب مادرت",
    "کیرم تو قلب ناموست","کیرم بناموست","کیر به ناموست","کیرم دهن مادرت",
    "کیرم دهن ناموست","شاش تو ننت","شاشیدم رو صورت ناموست","کیر به مادرت",
    "کیر به ننت","کیر به ناموست","کسخارومادرت","خارناموستو گاییدم",
    "خارمادرتو میگام","کیر تو وجوده ناموست","مادرتو میکنم","ننتو میکنم",
    "ناموستو میکنم","مادرتو بگام","ننتو بگام","ناموستو بگام",
    "ننتی","مادرتی","دالگتی","کسننتی","ناموستی","کسمادرتی","خاندانت رو گاییدم",
    "خاندانتو میگام","کسه ناموستی مگر؟","کسه مادرتی مگر؟","کسننتی مگر؟",
    "ممنگتی مگر؟","دالگتی مگر؟","مادرتی مگر؟","ننتی مگر؟","تف توی کسو کونه مادرت",
    "تف تو کسو کونه ننت","کیر به همه کست","کیرم تو زنده مردت","مادر هرزترو گاییدم",
    "گوه تو ناموست","گوه تو مادرت","عزیزاتو گاییدم","عزیز ترین شخص زندگیت رو گاییدم",
]

FRIEND_WORDS = [
    "خان","جنگی","مشتی","سوتون","یدونه","سلطان","اشتباه کرد","ولشکن","بامرام",
    "جیگر","عشق","داش","بزرگی کن","با مرام","با وقار","لوتی","لوتی منش","سرور",
    "نوکرته","ببخشش","مشتی ای","نایاب","تک","خاکی","تودلی","سالار","پهلوون",
    "کاردرست","اقا","توپر","با وجود","تک پر","عقاب","شیر","پرچمدار","آس","گنگ",
]

ANTIGIRL_WORDS = {
    1: ["جنده ساکت","مغزمو خوردی ساکت شو جنده","مگه نمیگم ساکت؟","بو تن ماهی گپو برداشت",
        "لفتو بزن گپو بوی تن ماهی گرفت","اه اه چندش بزن بیرون","ریدی به گپ با کست",
        "با بوی کست خفه مون کردی","د برو بیرون پتیاره","تو کستو با مسواک هم تمیز نمیتونی کنی",
        "کیرم تو PH لول کست","انقد عرق سگی به خوردت دادن","بوی کست کشتمون گمشو بیرون دیگه",
        "حال به هم زن جای جق زدن","برو بیرون دخترک سکسچتر","سکس چت پیوی این فرد ریپلای شده",
        "چک خورو لگد خور پسرا بزن بیرون","خیلی حال بهم زنی","فش خور پسرا","سیکتیر لگد خور"],
    2: ["یه نود بده","یه نود بده اذیت نکن","یه نوده نودو رد کن بیاد","ردش کن نودو",
        "چندشه بو گندو","گپو بو تن ماهی برداشت","سیکتیر بیرون ریدی به گپ با بوی کست",
        "سکس چت رایگان میکنی","سگ تورو رایگان هم نمیکنه","کسه زشت","عوق",
        "سیکتیر اون ور کسه بد بو","حال به هم زن","سیکتیر جاکش","دخترکه سکسچتر",
        "شنیدم سر کیر دعوا میکنی","جنده سر کیر دعوا نکن","یه دو دقیقه ول کن کیرو خایمو",
        "نمیدم دستت خایه مو","کست بد بود","چقد کسه بد بویی داری",
        "برا گاییدنت باید رو سرت چهار تا گونی کشید","عرق زیر بقلمو بریزم تو کست",
        "کسه بد بو","واقعا عوقم گرفت","چندش","بد بو","کسه بیریخت",
        "حالمون از کست بهم خورد","برو بیرون کسه زشت","برو کستو با چاغو ببر",
        "کست چرا انقد بیریخته؟","جنده خانوم هیس حالمونو بهم زدی"],
    3: ["Sag Pesara","kir Khor","Khaeyh Khor","Kost Chera Enghad Zeshteh",
        "Ridam To Koset","koset khafamon kard","Khayeh Khor","Kir Khor Pesara",
        "Chak Khor","Fosh Khor","Sag Hame Kare","Dastam Gir Kard To Koset",
        "Kost Cheghad Bo medeh","Zir Dast Pesara","Zir Khab Pesara",
        "Kheli Koset Bo Mede","Hal B Ham Zan","Chendeshh","Kos khol","Sekter Biron",
        "Kose Bad Bo","Kore Khar","Boroooo Birooon","Jende","Jendeh Khanom",
        "Kam Khayeh Bokhor","Goz khor","Boz Maje","Sage Zesht",
        "Koseto Nemekhoram","Kos Zesht","Koset Shabeh Gorbeh Mordeh Hast",
        "Khele Koset Zeshte","birooon borooo","Koset khafamon Kard"],
}

def get_friend_reply(slot, uid):
    uid = str(uid)
    idx = indexes["friend"][slot].get(uid, 0)
    r = FRIEND_WORDS[idx % len(FRIEND_WORDS)]
    indexes["friend"][slot][uid] = (idx + 1) % len(FRIEND_WORDS)
    return r

def get_antigirl_reply(slot, uid):
    uid = str(uid)
    pool = ANTIGIRL_WORDS[slot]
    idx = indexes["antigirl"][slot].get(uid, 0)
    r = pool[idx % len(pool)]
    indexes["antigirl"][slot][uid] = (idx + 1) % len(pool)
    return r

def get_enemy_two_insults():
    return random.sample(ENEMY_INSULTS, 2)

async def fetch_user(user_id):
    if user_id in user_cache:
        return user_cache[user_id]
    try:
        u = await client.get_entity(user_id)
        uname = f"@{u.username}" if getattr(u, "username", None) else "ندارد"
        info = {"name": getattr(u, "first_name", None) or str(user_id),
                "id": user_id, "username": uname}
    except Exception:
        info = {"name": str(user_id), "id": user_id, "username": "ندارد"}
    user_cache[user_id] = info
    return info

async def get_replied_target(event):
    if not event.message.reply_to_msg_id: return None
    try:
        m = await client.get_messages(event.chat_id, ids=event.message.reply_to_msg_id)
        if not m or not m.sender_id: return None
        return m
    except Exception as e:
        print(f"target err: {e}")
        return None

async def add_list_user(event, slot, kind, me):
    if is_private(event): return
    tgt = await get_replied_target(event)
    if tgt is None:
        await admin_reply_err(event, "روی پیام شخص ریپلای کن."); return
    if tgt.sender_id == me.id:
        await admin_reply_err(event, "خودت رو نمی‌تونی اضافه کنی."); return
    uid = str(tgt.sender_id)
    info = await fetch_user(tgt.sender_id)
    users = users_data[kind][slot]
    title = TITLES[kind]
    if uid in users:
        await admin_reply_err(event, f"قبلاً در {title} {slot} بود."); return
    users[uid] = {"chat_id": event.chat_id, "name": info["name"],
                  "username": info["username"], "added_at": datetime.now().isoformat()}
    indexes[kind][slot][uid] = 0
    save_json_file(FILES[kind][slot], users)
    await admin_reply(event, f"✅ به {title} {slot} اضافه شد → {info['name']}")

async def remove_all(event, kind):
    if is_private(event): return
    tgt = await get_replied_target(event)
    if tgt is None:
        await admin_reply_err(event, "روی پیام شخص ریپلای کن."); return
    uid = str(tgt.sender_id)
    removed = []
    for slot in LIST_SLOTS:
        if uid in users_data[kind][slot]:
            del users_data[kind][slot][uid]
            indexes[kind][slot].pop(uid, None)
            save_json_file(FILES[kind][slot], users_data[kind][slot])
            removed.append(str(slot))
    title = TITLES[kind]
    if removed:
        await admin_reply(event, f"🗑 از {title} اسلات {', '.join(removed)} حذف شد.")
    else:
        await admin_reply_err(event, f"تو هیچ اسلات {title} نبود.")

async def show_user_lists(event):
    tgt = await get_replied_target(event)
    if tgt is None:
        await admin_reply_err(event, "روی پیام شخص ریپلای کن."); return
    uid = str(tgt.sender_id)
    info = await fetch_user(tgt.sender_id)
    found = []
    for kind in ("friend", "antigirl", "enemy"):
        for slot in LIST_SLOTS:
            if uid in users_data[kind][slot]:
                found.append(f"{TITLES[kind]} {slot}")
    replied_text = tgt.text or "[مدیا]"
    link = msg_link(event.chat_id, tgt.id)
    if not found:
        await admin_reply(event, f"هیچ لیستی نیست.\n{info['name']}\n{uid}\n{info['username']}\nمتن: {replied_text}\n{link}")
        return
    await admin_reply(event, f"{info['name']}\n{uid}\n{info['username']}\nلیست‌ها: {', '.join(found)}\n"
                      f"متن: {replied_text}\nلینک: {link}")

# ────────────────────────────────────────────────────────
#  ⏰ آفیشو جدید (auto watcher)
# ────────────────────────────────────────────────────────

async def offadd(event):
    """اضافه کردن ترکر آف با ریپلای."""
    tgt = await get_replied_target(event)
    if tgt is None:
        await admin_reply_err(event, "روی پیام طرف ریپلای کن."); return
    if not tgt.sender_id:
        await admin_reply_err(event, "فرستنده مشخص نیست."); return
    cid = event.chat_id
    uid = tgt.sender_id
    off_trackers.setdefault(cid, [])
    # اگه قبلاً هست، آپدیت کن
    for t in off_trackers[cid]:
        if t["user_id"] == uid:
            t["marker_msg_id"] = tgt.id
            t["replied"] = False
            await admin_reply(event, f"♻️ آف {uid} آپدیت شد.")
            return
    off_trackers[cid].append({
        "user_id": uid,
        "marker_msg_id": tgt.id,
        "replied": False,
        "set_at": datetime.utcnow().isoformat(),
    })
    off_locked[cid] = False   # reset lock
    await admin_reply(event, f"✅ آف {uid} تنظیم شد. ({len(off_trackers[cid])} نفر)")

async def off_watcher():
    """هر ۱۰ ثانیه چک می‌کنه آف‌ها رو."""
    while True:
        try:
            await asyncio.sleep(10)
            now = datetime.utcnow()
            for chat_id in list(off_trackers.keys()):
                trackers = off_trackers.get(chat_id) or []
                if not trackers: continue

                # وضعیت هر ترکر
                statuses = []
                for t in trackers:
                    uid = t["user_id"]
                    last_t = last_msg_time.get((chat_id, uid))
                    if last_t is None:
                        statuses.append(("no", t)); continue
                    delta = (now - last_t).total_seconds()
                    if delta >= OFF_MIN_SECONDS:
                        statuses.append(("off", t))
                    else:
                        statuses.append(("on", t))

                off_list = [t for s, t in statuses if s == "off"]
                if not off_list: continue

                all_off = all(s == "off" for s, _ in statuses) and len(trackers) > 1

                for t in off_list:
                    if t.get("replied"): continue
                    uid = t["user_id"]
                    target_msg_id = last_msg_id.get((chat_id, uid))
                    if not target_msg_id:
                        # fallback: fetch
                        try:
                            msgs = await client.get_messages(chat_id, limit=50)
                            for m in msgs:
                                if m.sender_id == uid:
                                    target_msg_id = m.id; break
                        except Exception:
                            pass
                    if not target_msg_id:
                        t["replied"] = True; continue

                    # اگه تکست مرجع پاک شده؟
                    marker_deleted = False
                    try:
                        mm = await client.get_messages(chat_id, ids=t["marker_msg_id"])
                        if mm is None:
                            marker_deleted = True
                    except Exception:
                        marker_deleted = True

                    # انتخاب متن
                    if marker_deleted:
                        txt = OFF_MSG_DELETED
                    elif all_off:
                        txt = OFF_MSG_MULTI
                    else:
                        txt = OFF_MSG_SINGLE

                    try:
                        await client.send_message(chat_id, txt, reply_to=target_msg_id)
                        net_stats["sent_bytes"] += len(txt.encode("utf-8"))
                        t["replied"] = True
                    except Exception as e:
                        print(f"off watcher send err: {e}")

                # اگه همه replied شدن → قفل چت
                if all(t.get("replied") for t in trackers):
                    off_locked[chat_id] = True
                    # متوقف کردن حلقه‌ها
                    for s in SLOTS:
                        for tm in (reply_tasks[s], forward_tasks[s], spam_tasks[s]):
                            task = tm.get(chat_id)
                            if task and not task.done():
                                task.cancel()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"off_watcher err: {e}")

# ────────────────────────────────────────────────────────
#  🧠 ایده ۳۹: تحلیل تاکتیکی
# ────────────────────────────────────────────────────────

async def tactical_analysis(event):
    text = (event.message.text or "").strip()
    parts = text.split()
    target_uid = None
    target_name = "طرف"

    if event.message.reply_to_msg_id:
        try:
            m = await client.get_messages(event.chat_id, ids=event.message.reply_to_msg_id)
            if m and m.sender_id:
                target_uid = m.sender_id
        except Exception:
            pass
    elif len(parts) >= 2 and parts[1].startswith("@"):
        try:
            ent = await client.get_entity(parts[1][1:])
            target_uid = ent.id
            target_name = getattr(ent, "first_name", str(ent.id)) or str(ent.id)
        except Exception as e:
            await admin_reply_err(event, f"پیدا نشد: {e}"); return

    if target_uid is None:
        await admin_reply_err(event, "روی پیام طرف ریپلای کن یا @username بده."); return

    info = await fetch_user(target_uid)
    target_name = info["name"]

    try:
        msgs = await client.get_messages(event.chat_id, limit=300)
    except Exception as e:
        await admin_reply_err(event, f"خطا در خواندن پیام‌ها: {e}"); return

    user_msgs = [m for m in msgs if m.sender_id == target_uid]
    if not user_msgs:
        await admin_reply_err(event, "پیامی از این شخص پیدا نشد."); return

    # الگوی ساعت‌ها
    hour_count = {}
    for m in user_msgs:
        if getattr(m, "date", None):
            h = m.date.hour
            hour_count[h] = hour_count.get(h, 0) + 1
    top_hours = sorted(hour_count.items(), key=lambda x: -x[1])[:3]

    # میانگین طول
    texts_arr = [m.text for m in user_msgs if m.text]
    avg_len = sum(len(t) for t in texts_arr) / len(texts_arr) if texts_arr else 0

    # کلمات پرتکرار
    from collections import Counter
    wc = Counter()
    for t in texts_arr:
        for w in t.split():
            w = w.strip(".,!?؟،:;…")
            if len(w) > 2:
                wc[w] += 1
    top_words = wc.most_common(5)

    # شمارش فحش
    insult_count = 0
    for t in texts_arr:
        for ins in ["کیر","کس ","جنده","ننت","ناموس","خار","گایید"]:
            insult_count += t.count(ins)

    edit_count = user_edit_counts.get(target_uid, 0)
    voice_count = sum(1 for m in user_msgs if getattr(m, "voice", None))

    lines = [f"🧠 تحلیل تاکتیکی {info['username']}"]
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("الگوی فعالیت:")
    if top_hours:
        for h, c in top_hours:
            lines.append(f"▸ ساعت {h:02d}:00 → {c} پیام")
    else:
        lines.append("▸ داده کافی نیست")
    lines.append("")
    lines.append("آمار:")
    lines.append(f"▸ میانگین طول متن: {avg_len:.0f} کاراکتر")
    lines.append(f"▸ تعداد ادیت: {edit_count}")
    lines.append(f"▸ تعداد ویس: {voice_count}")
    lines.append(f"▸ فحش تخمینی: {insult_count}")
    if top_words:
        lines.append(f"▸ کلمات پرتکرار: {', '.join(w for w,_ in top_words)}")

    lines.append("")
    lines.append("ضعف‌ها:")
    if avg_len > 100:
        lines.append("▸ کپشن بلند → می‌شه میوتش کرد")
    if edit_count > 3:
        lines.append("▸ ادیت زیاد → می‌شه مدرک گرفت")
    if insult_count > 20:
        lines.append("▸ فحش زیاد → ریپورت سریع جواب می‌ده")
    if top_hours:
        peak = top_hours[0][0]
        off_hours = [(h, c) for h, c in hour_count.items() if c <= 1]
        lines.append(f"▸ پیک فعالیت ساعت {peak:02d} → خارج از این ساعت ضعیفه")

    lines.append("")
    lines.append("توصیه تاکتیکی:")
    if top_hours:
        peak = top_hours[0][0]
        lines.append(f"▸ ساعت {peak:02d} به بعد بهش حمله کن")
    lines.append("▸ کپشن‌ها و ویس‌هاش رو ذخیره کن")
    lines.append("▸ قبل از آف ۵ ساعت صبر نکن")

    try:
        await client.send_message("me", "\n".join(lines))
    except Exception:
        pass

# ────────────────────────────────────────────────────────
#  🎙 ایده ۴۰: Voice Evidence
# ────────────────────────────────────────────────────────

async def handle_voice_evidence(event):
    """ویس دشمن خودکار ذخیره میشه."""
    try:
        if is_private(event): return
        if not (getattr(event.message, "voice", None) or getattr(event.message, "audio", None)):
            return
        sid = event.sender_id
        if not sid: return
        sid_s = str(sid)
        is_enemy = any(sid_s in users_data["enemy"][slot] for slot in LIST_SLOTS)
        if not is_enemy: return

        info = await fetch_user(sid)
        # فوروارد به سیو مسیج
        try:
            await client.forward_messages("me", event.message.id, event.chat_id)
        except Exception:
            pass
        # گزارش
        notif = (f"🎙 Voice Evidence ذخیره شد\n"
                 f"👤 {info['name']} - {info['username']}\n"
                 f"🆔 {sid}\n"
                 f"📎 لینک: {msg_link(event.chat_id, event.message.id)}")
        try:
            await client.send_message("me", notif)
        except Exception:
            pass
        voice_evidence_log.setdefault(event.chat_id, []).append({
            "uid": sid, "msg_id": event.message.id,
            "saved_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        print(f"voice evidence err: {e}")

# ────────────────────────────────────────────────────────
#  ⚖️ ایده ۴: Judge Assist
# ────────────────────────────────────────────────────────

def detect_judge(text):
    if not text: return (False, 0, None)
    for kw, conf in JUDGE_KEYWORDS:
        if kw in text:
            return (True, conf, kw)
    return (False, 0, None)

async def handle_judge_detect(event):
    try:
        if is_private(event): return
        text = event.message.text or ""
        if not text: return
        is_j, conf, sig = detect_judge(text)
        if not is_j: return

        sid_s = str(event.sender_id) if event.sender_id else ""
        judge_cache.setdefault(event.chat_id, [])
        if sid_s and sid_s not in judge_cache[event.chat_id]:
            judge_cache[event.chat_id].append(sid_s)

        info = await fetch_user(event.sender_id) if event.sender_id else {"name":"?","username":"?"}
        notif = (f"⚠️ احتمال داور\n"
                 f"━━━━━━━━━━━━━━━\n"
                 f"👤 {info['name']} - {info['username']}\n"
                 f"💬 پیام: {text[:120]}\n"
                 f"📊 احتمال: {conf}%\n"
                 f"🎯 سیگنال: {sig}\n"
                 f"🔗 {msg_link(event.chat_id, event.message.id)}")
        try:
            await client.send_message("me", notif)
        except Exception:
            pass

        if judge_mode["enabled"]:
            try:
                await event.reply(judge_mode["reply_text"])
            except Exception:
                pass
    except Exception as e:
        print(f"judge detector err: {e}")

# ────────────────────────────────────────────────────────
#  🚨 ریپورت
# ────────────────────────────────────────────────────────

async def report_target(event):
    text = (event.message.text or "").strip()
    parts = text.split(maxsplit=2)
    target_entity = None
    reason = "spam"

    if event.message.reply_to_msg_id:
        try:
            tgt = await client.get_messages(event.chat_id, ids=event.message.reply_to_msg_id)
            if tgt and tgt.sender_id:
                target_entity = tgt.sender_id
        except Exception: pass

    if len(parts) >= 2 and parts[1].startswith("@"):
        try:
            ent = await client.get_entity(parts[1][1:])
            target_entity = ent.id
        except Exception as e:
            await admin_reply_err(event, f"یوزرنیم پیدا نشد: {e}"); return
        if len(parts) >= 3: reason = parts[2]
    elif len(parts) >= 2 and not parts[1].startswith("@"):
        reason = parts[1]

    if target_entity is None:
        await admin_reply_err(event, "روی پیام طرف ریپلای کن یا @username بده.")
        return

    try:
        messages = await client.get_messages(event.chat_id, limit=100)
        target_msgs = [m for m in messages if m.sender_id == target_entity]
        msgs_ids = [m.id for m in target_msgs[:50]] if target_msgs else (
            [event.message.reply_to_msg_id] if event.message.reply_to_msg_id else [])
    except Exception:
        msgs_ids = [event.message.reply_to_msg_id] if event.message.reply_to_msg_id else []

    if not msgs_ids:
        await admin_reply_err(event, "پیامی برای ریپورت پیدا نشد."); return

    reasons = ["spam", "violence", "child", "copyright", "other"]
    sent = 0
    try:
        from splusthon import functions
        for rid in reasons:
            try:
                await client(functions.messages.ReportRequest(
                    peer=event.chat_id, id=msgs_ids[:20], reason=rid))
                sent += 1
                await asyncio.sleep(0.05)
            except Exception: pass
        for mid in msgs_ids[:20]:
            for rid in reasons:
                try:
                    await client(functions.messages.ReportRequest(
                        peer=event.chat_id, id=[mid], reason=rid))
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception: pass
    except Exception: pass

    try:
        for mid in msgs_ids[:20]:
            try:
                await client.report_spam(event.chat_id, [mid])
                sent += 1
                await asyncio.sleep(0.05)
            except Exception: pass
    except Exception: pass

    await admin_reply(event, f"✅ ریپورت انجام شد → {sent} ارسال روی {target_entity}")

# ────────────────────────────────────────────────────────
#  📶 پینگ و نت
# ────────────────────────────────────────────────────────

def human_bytes(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024: return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"

async def ping_test(event):
    try:
        t0 = time.time()
        await client.get_me()
        lat = (time.time() - t0) * 1000
        net_stats["ping_samples"].append(lat)
        up = time.time() - net_stats["start_time"]
        uptime = f"{int(up//3600):02d}:{int((up%3600)//60):02d}:{int(up%60):02d}"
        net_stats["sent_bytes"] += 512
        net_stats["recv_bytes"] += 1024
        used = net_stats["sent_bytes"] + net_stats["recv_bytes"]
        rate = used / max(up, 1)
        await admin_reply(event,
            f"📶 پینگ: `{lat:.0f} ms`\n"
            f"🟢 {'خوب' if lat < 500 else 'متوسط' if lat < 1500 else 'ضعیف'}\n"
            f"⏳ آپتایم: `{uptime}`\n"
            f"📥 دریافت: `{human_bytes(net_stats['recv_bytes'])}`\n"
            f"📤 ارسال: `{human_bytes(net_stats['sent_bytes'])}`\n"
            f"📊 مصرف کل: `{human_bytes(used)}`\n"
            f"⚡ نرخ: `{human_bytes(rate)}/s`")
    except Exception as e:
        await admin_reply_err(event, f"خطا در پینگ: {e}")

async def netstat_handler(event):
    up = time.time() - net_stats["start_time"]
    uptime = f"{int(up//3600):02d}:{int((up%3600)//60):02d}:{int(up%60):02d}"
    used = net_stats["sent_bytes"] + net_stats["recv_bytes"]
    rate = used / max(up, 1)
    samples = net_stats["ping_samples"]
    avg_ping = sum(samples) / len(samples) if samples else 0
    await admin_reply(event,
        f"📊 آمار شبکه\n"
        f"⏳ آپتایم: `{uptime}`\n"
        f"📥 دریافت: `{human_bytes(net_stats['recv_bytes'])}`\n"
        f"📤 ارسال: `{human_bytes(net_stats['sent_bytes'])}`\n"
        f"💾 کل: `{human_bytes(used)}`\n"
        f"⚡ نرخ: `{human_bytes(rate)}/s`\n"
        f"📶 میانگین پینگ: `{avg_ping:.0f} ms`")

# ────────────────────────────────────────────────────────
#  🗑 پاکسازی
# ────────────────────────────────────────────────────────

async def clearmine_handler(event):
    if is_private(event):
        await admin_reply_err(event, "فقط توی گپ."); return
    parts = (event.message.text or "").split()
    limit = 100
    if len(parts) >= 2:
        try: limit = int(parts[1])
        except Exception: pass
    try:
        me = await client.get_me()
        msgs = await client.get_messages(event.chat_id, limit=limit)
        deleted = 0
        for m in msgs:
            if m.sender_id == me.id:
                try:
                    await client.delete_messages(event.chat_id, [m.id])
                    deleted += 1
                    await asyncio.sleep(0.15)
                except Exception: pass
        await admin_reply(event, f"🗑 {deleted} پیام پاک شد.")
    except Exception as e:
        await admin_reply_err(event, f"خطا: {e}")

# ────────────────────────────────────────────────────────
#  هندلرها
# ────────────────────────────────────────────────────────

@client.on(events.MessageEdited)
async def edit_tracker(event):
    try:
        if event.sender_id:
            user_edit_counts[event.sender_id] = user_edit_counts.get(event.sender_id, 0) + 1
        cid = event.chat_id
        sid = str(event.sender_id)
        if is_private(event): return
        if cid not in edit_targets or sid not in edit_targets[cid]: return
        msg_id = event.message.id
        link = msg_link(cid, msg_id)
        try:
            await event.reply("🚫 به دلیل ادیت تکست با این شخص به هیچ کلی ادامه نمیدیم.",
                              reply_to=msg_id)
        except Exception: pass
        text = (f"📝 ادیت تکست شناسایی شد\n"
                f"👤 `{sid}`\n"
                f"📄 متن جدید: {event.message.text or '[مدیا]'}\n"
                f"🔗 {link}\n")
        try: await client.send_message("me", text)
        except Exception: pass
    except Exception as e:
        print(f"edit_tracker err: {e}")

@client.on(events.NewMessage)
async def track_last_msg(event):
    try:
        if event.sender_id:
            last_msg_time[(event.chat_id, event.sender_id)] = datetime.utcnow()
            last_msg_id[(event.chat_id, event.sender_id)] = event.message.id
            if event.sender_id not in user_cache:
                await fetch_user(event.sender_id)
    except Exception: pass

@client.on(events.NewMessage)
async def voice_and_judge_watcher(event):
    await handle_voice_evidence(event)
    await handle_judge_detect(event)

def build_help_text():
    now = datetime.now()
    days = ["دوشنبه","سه‌شنبه","چهارشنبه","پنج‌شنبه","جمعه","شنبه","یکشنبه"]
    wd = days[now.weekday()]
    boot = BOOT_TIME.strftime("%Y/%m/%d - %H:%M:%S")
    cur = now.strftime("%Y/%m/%d - %H:%M:%S")
    return (
        "⚡ **سیستم مدیریت هوشمند سلف** ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 **۱. تنظیم متون**\n"
        "`setspam1` تا `setspam10` [متن1] | [متن2]\n"
        "`setreply1` تا `setreply10` [متن1] | [متن2]\n"
        "💾 `setfor1` تا `setfor10` (ریپلای)\n\n"
        "🚀 **۲. اسپم / ریپلای / فوروارد**\n"
        "💥 `start[1-10] [تعداد/0] [زمان]`\n"
        "🔁 `reply[1-10] [تعداد/0] [زمان]` (ریپلای)\n"
        "🔄 `for[1-10] [تعداد] [زمان]`\n\n"
        "😈 **۳. بدخواه و ریپورت**\n"
        "`enemy1` (ریپلای) | `r

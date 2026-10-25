"""
APK שהבעלים שלח לבוט טלגרם → release "apks" בריפו הזה → דיווח לאתר.

מופעל מ-.github/workflows/telegram-apk.yml. מתחבר כבוט ב-MTProto (Telethon),
מוריד את הקובץ מההודעה (עד 2GB), מחשב SHA-256, מעלה ל-release ומדווח
ל-kosher-tech.com/api/telegram-apk?action=result. הבעלים מקבל תשובה בטלגרם.

⚠️ הריפו ציבורי והלוג גלוי: לא להדפיס שמות קבצים, מזהים או טוקנים.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import StringSession

# סודות חסרים — עוצרים עם הודעה ברורה (בלי לגלות ערכים)
_missing = [k for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_APK_BOT_TOKEN")
            if not os.environ.get(k, "").strip()]
if _missing:
    print("חסרים Secrets בריפו (Settings → Secrets and variables → Actions → Repository secrets): "
          + ", ".join(_missing))
    sys.exit(1)

API_ID = int(os.environ["TELEGRAM_API_ID"].strip())
API_HASH = os.environ["TELEGRAM_API_HASH"].strip()
BOT_TOKEN = os.environ["TELEGRAM_APK_BOT_TOKEN"].strip()
GH_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["REPO"]
RESULT_URL = os.environ["RESULT_URL"]
PENDING_URL = RESULT_URL.split("?")[0] + "?action=pending"
TAG = "apks"
SESSION_FILE = ".tg-session"
MAX_WAIT = 600   # טלגרם ביקש להמתין עד 10 דקות — ממתינים; יותר — התור נשאר לריצה הבאה

GH = "https://api.github.com"
GH_HEADERS = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}

# אותה נגזרת כמו ב-functions/api/telegram-apk.js (resultSecret)
RESULT_TOKEN = hmac.new(BOT_TOKEN.encode(), b"kt-telegram-result", hashlib.sha256).hexdigest()
SESSION_KEY = hashlib.sha256(hmac.new(BOT_TOKEN.encode(), b"kt-telegram-session", hashlib.sha256).digest()).digest()


def report(item_id, status, **extra):
    """מעדכן את השורה בפאנל. כשל בדיווח לא מפיל את הריצה — רק נרשם."""
    try:
        r = requests.post(RESULT_URL, json={"id": item_id, "status": status, **extra},
                          headers={"X-KT-Token": RESULT_TOKEN}, timeout=30)
        print(f"report {status}: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"report {status} failed: {type(e).__name__}")


def tell(item, text):
    """תשובה בטלגרם — לאן שהקובץ נשלח (קבוצה או צ'אט פרטי)."""
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": item["notify_chat_id"], "text": text,
                            "reply_to_message_id": item["notify_message_id"],
                            "allow_sending_without_reply": True}, timeout=30)
    except requests.RequestException:
        pass


def pending():
    """כל מה שבתור. אתר בגרסה ישנה (בלי ?action=pending) — רק הקובץ שהפעיל את הריצה."""
    try:
        r = requests.get(PENDING_URL, headers={"X-KT-Token": RESULT_TOKEN}, timeout=30)
        if r.status_code == 200:
            return r.json().get("items") or []
        print(f"pending: HTTP {r.status_code} — falling back to the triggering item")
    except requests.RequestException as e:
        print(f"pending failed: {type(e).__name__} — falling back to the triggering item")
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            p = json.load(f).get("client_payload") or {}
        if p.get("id") and p.get("chat_id") and p.get("message_id"):
            return [{"id": p["id"], "chat_id": p["chat_id"], "message_id": p["message_id"],
                     "notify_chat_id": p.get("notify_chat_id") or p["chat_id"],
                     "notify_message_id": p.get("notify_message_id") or p["message_id"]}]
    except (OSError, ValueError, KeyError):
        pass
    return []


def load_session():
    try:
        with open(SESSION_FILE, "rb") as f:
            blob = f.read()
        return AESGCM(SESSION_KEY).decrypt(blob[:12], blob[12:], None).decode()
    except Exception:  # noqa: BLE001 — אין / ישן / מפתח אחר: מתחברים מחדש
        return ""


def save_session(text):
    nonce = os.urandom(12)
    with open(SESSION_FILE, "wb") as f:
        f.write(nonce + AESGCM(SESSION_KEY).encrypt(nonce, text.encode(), None))


def release_id():
    r = requests.get(f"{GH}/repos/{REPO}/releases/tags/{TAG}", headers=GH_HEADERS, timeout=30)
    if r.status_code == 200:
        return r.json()["id"]
    if r.status_code != 404:
        raise RuntimeError(f"GitHub release lookup {r.status_code}")
    r = requests.post(f"{GH}/repos/{REPO}/releases", headers=GH_HEADERS, timeout=30, json={
        "tag_name": TAG, "name": "אפליקציות החנות", "body": "קבצי APK של חנות כשר-טק. מנוהל אוטומטית."})
    r.raise_for_status()
    return r.json()["id"]


def upload(path, file_name):
    """מעלה ל-release כקובץ אחד (GitHub מקבל עד 2GB) ומחזיר כתובת ציבורית קבועה."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file_name) or "app.apk"
    asset = f"{int(time.time() * 1000)}-tg{uuid.uuid4().hex[:8]}-{safe}"
    rid = release_id()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        r = requests.post(
            f"https://uploads.github.com/repos/{REPO}/releases/{rid}/assets",
            params={"name": asset},
            headers={**GH_HEADERS, "Content-Type": "application/vnd.android.package-archive",
                     "Content-Length": str(size)},
            data=f, timeout=3600)
    if r.status_code >= 300:
        raise RuntimeError(f"העלאה ל-GitHub נכשלה ({r.status_code})")
    return f"https://github.com/{REPO}/releases/download/{TAG}/{asset}"


async def connect():
    """מתחבר כבוט, עם ההתחברות השמורה אם יש. None = טלגרם ביקש להמתין יותר מדי."""
    client = TelegramClient(StringSession(load_session()), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        for attempt in (1, 2):
            try:
                await client.sign_in(bot_token=BOT_TOKEN)
                break
            except errors.FloodWaitError as e:
                if attempt == 2 or e.seconds > MAX_WAIT:
                    print(f"telegram asked to wait {e.seconds}s — queue kept for the next run")
                    await client.disconnect()
                    return None
                print(f"telegram asked to wait {e.seconds}s — waiting")
                await asyncio.sleep(e.seconds + 2)
        print("signed in (new session)")
    else:
        print("signed in (saved session)")
    save_session(client.session.save())
    return client


async def download(client, item, path):
    """מוריד את הקובץ מההודעה בצ'אט הפרטי של הבוט. מחזיר את שם הקובץ."""
    # בצ'אט פרטי מספרי ההודעות הם של הבוט עצמו — אפשר לבקש לפי מספר בלבד
    res = await client(functions.messages.GetMessagesRequest(id=[types.InputMessageID(int(item["message_id"]))]))
    msg = next((m for m in res.messages if m and getattr(m, "media", None)), None)
    if msg is None or msg.file is None:
        raise RuntimeError("ההודעה לא נמצאה או שאין בה קובץ")
    last = [0]

    def progress(done, size):
        pct = int(done * 100 / size) if size else 0
        if pct >= last[0] + 25:
            last[0] = pct
            print(f"download {pct}%")

    await client.download_media(msg, file=path, progress_callback=progress)
    return msg.file.name or "app.apk"


def finish(path, name):
    with open(path, "rb") as f:
        if f.read(2) != b"PK":
            raise RuntimeError("הקובץ אינו APK תקין")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return upload(path, name), h.hexdigest(), os.path.getsize(path)


async def run(items):
    client = await connect()
    if client is None:
        return
    path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "telegram-apk.bin")
    try:
        for n, item in enumerate(items, 1):
            print(f"item {n}/{len(items)}")
            report(item["id"], "downloading")
            try:
                name = await download(client, item, path)
                url, sha, size = finish(path, name)
                report(item["id"], "ready", apk_url=url, apk_sha256=sha, file_size=size)
                tell(item, f"✓ {name} מוכן בפאנל: חנות האפליקציות ← \"ממתינים מטלגרם\" ← הוספה לקטלוג.")
            except errors.FloodWaitError as e:
                # טלגרם האט אותנו באמצע — זה וכל השאר חוזרים לתור לריצה הבאה
                print(f"telegram asked to wait {e.seconds}s mid-run — rest stays queued")
                report(item["id"], "queued")
                break
            except Exception as e:  # noqa: BLE001 — כל כשל מדווח לפאנל ולשולח, וממשיכים לבא
                err = str(e) or type(e).__name__
                print(f"failed: {type(e).__name__}")
                report(item["id"], "failed", error=err)
                tell(item, f"✗ ההורדה נכשלה: {err}")
            finally:
                if os.path.exists(path):
                    os.remove(path)
    finally:
        await client.disconnect()


def main():
    items = pending()
    print(f"queued: {len(items)}")
    if items:
        asyncio.run(run(items))


if __name__ == "__main__":
    main()

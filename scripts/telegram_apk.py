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
import os
import re
import sys
import time
import uuid

import requests
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN = os.environ["TELEGRAM_APK_BOT_TOKEN"]
GH_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["REPO"]
INBOX_ID = os.environ["INBOX_ID"]
CHAT_ID = int(os.environ["CHAT_ID"])
MESSAGE_ID = int(os.environ["MESSAGE_ID"])
RESULT_URL = os.environ["RESULT_URL"]
TAG = "apks"

GH = "https://api.github.com"
GH_HEADERS = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}

# אותה נגזרת כמו ב-functions/api/telegram-apk.js (resultSecret)
RESULT_TOKEN = hmac.new(BOT_TOKEN.encode(), b"kt-telegram-result", hashlib.sha256).hexdigest()


def report(status, **extra):
    """מעדכן את השורה בפאנל. כשל בדיווח לא מפיל את הריצה — רק נרשם."""
    try:
        r = requests.post(RESULT_URL, json={"id": INBOX_ID, "status": status, **extra},
                          headers={"X-KT-Token": RESULT_TOKEN}, timeout=30)
        print(f"report {status}: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"report {status} failed: {type(e).__name__}")


def tell_owner(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text, "reply_to_message_id": MESSAGE_ID,
                            "allow_sending_without_reply": True}, timeout=30)
    except requests.RequestException:
        pass


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


async def fetch(path):
    """מוריד את הקובץ מההודעה. מחזיר את שם הקובץ המקורי."""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    try:
        # שיחה פרטית עם הבוט (השרת מקבל רק כזו): מספרי ההודעות הם של הבוט
        # עצמו, ולכן אפשר לבקש לפי מספר בלי לדעת מראש מי הצד השני.
        res = await client(functions.messages.GetMessagesRequest(id=[types.InputMessageID(MESSAGE_ID)]))
        msgs = res.messages
        msg = next((m for m in msgs if m and getattr(m, "media", None)), None)
        if msg is None or msg.file is None:
            raise RuntimeError("ההודעה לא נמצאה או שאין בה קובץ")
        name = msg.file.name or "app.apk"
        total = msg.file.size or 0
        last = [0]

        def progress(done, size):
            pct = int(done * 100 / size) if size else 0
            if pct >= last[0] + 20:
                last[0] = pct
                print(f"download {pct}%")

        await client.download_media(msg, file=path, progress_callback=progress)
        print(f"downloaded {os.path.getsize(path) / 1048576:.1f}MB of {total / 1048576:.1f}MB")
        return name
    finally:
        await client.disconnect()


def main():
    report("downloading")
    path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "telegram-apk.bin")
    try:
        name = asyncio.run(fetch(path))
        with open(path, "rb") as f:
            if f.read(2) != b"PK":
                raise RuntimeError("הקובץ אינו APK תקין")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        size = os.path.getsize(path)
        url = upload(path, name)
        report("ready", apk_url=url, apk_sha256=sha, file_size=size)
        tell_owner(f"✓ {name} מוכן בפאנל: חנות האפליקציות ← \"ממתינים מטלגרם\" ← הוספה לקטלוג.")
    except Exception as e:  # noqa: BLE001 — כל כשל מדווח לבעלים ולפאנל
        err = str(e) or type(e).__name__
        print(f"failed: {type(e).__name__}")
        report("failed", error=err)
        tell_owner(f"✗ ההורדה נכשלה: {err}")
        sys.exit(1)
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    main()

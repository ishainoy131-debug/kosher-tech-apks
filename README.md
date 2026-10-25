# kosher-tech-apks

אחסון ציבורי לקבצי ה-APK של חנות האפליקציות של כשר-טק (release בתגית `apks`).
המכשירים מורידים מכאן בלי טוקן. **אין כאן קוד של האתר או של האפליקציה.**

מה כן יש כאן:
- `.github/workflows/telegram-apk.yml` + `scripts/telegram_apk.py` — קובץ APK שהבעלים שולח
  לבוט הטלגרם של החנות מורד מכאן (MTProto, עד 2GB), מועלה ל-release `apks`, ומופיע בפאנל
  ב"ממתינים מטלגרם". מופעל רק מהשרת של kosher-tech.com (`repository_dispatch`).
  Secrets: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_APK_BOT_TOKEN`.

⚠️ הריפו ציבורי וגם הלוגים של הריצות: לא להדפיס בהם שמות קבצים, מזהים או טוקנים.

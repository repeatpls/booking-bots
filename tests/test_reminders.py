# -*- coding: utf-8 -*-
"""Нагадування за 24 години і за годину."""
import json, os, shutil, sys, tempfile
from datetime import datetime, timedelta
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [777]
c["reminders"] = {"enabled": True, "before": [1440, 60]}
c["payment"] = {"mode": "link", "deposit": {"type": "fixed", "amount": 900},
                "card": "0000 1111 2222 3333"}
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t"})
bot.init_db()

U = 88
BASE = bot.now().replace(second=0, microsecond=0)
SLOT = (BASE + timedelta(days=3)).replace(hour=14, minute=0)

with bot.db() as conn:
    conn.execute("INSERT INTO bookings(id, chat_id, username, service, slot, name, phone,"
                 " status, created, master_name, deposit, paid, minutes)"
                 " VALUES(1,?,?,?,?,?,?,'new',?,?,?,0,240)",
                 (U, "petro", "Середнє тату", SLOT.isoformat(timespec="minutes"),
                  "Петро", "+380670000000", BASE.isoformat(timespec="seconds"), "Остап", 900))

def at(dt):
    bot.now = lambda: dt
    bot._last_tick = 0.0
def texts(chat=U):
    return [p["text"] for m, p in S if m == "sendMessage" and p.get("chat_id") == chat]

print("— зарано")
at(SLOT - timedelta(days=2))
ok(bot.reminders_tick(force=True) == 0, "за дві доби нічого не шлеться")

print("— за 24 години")
S.clear(); at(SLOT - timedelta(hours=24))
ok(bot.reminders_tick(force=True) == 1, "нагадування надіслано")
t = texts()[0]
ok("завтра" in t, "сказано, що завтра")
ok("Середнє тату" in t and "Остап" in t, "видно послугу й майстра")
ok("900 ₴" in t.replace(" ", " ") and "не надійшла" in t, "нагадано про передоплату")
ok("<code>0000 1111 2222 3333</code>" in t, "картка для копіювання")
ds = [b["callback_data"] for r in [p for m, p in S if m == "sendMessage"][0]["reply_markup"]["inline_keyboard"] for b in r if b.get("callback_data")]
ok("willcome:1" in ds and "unbook:1" in ds, "кнопки «Буду» і «Скасувати»")

print("— двічі не шлеться")
S.clear(); at(SLOT - timedelta(hours=23))
ok(bot.reminders_tick(force=True) == 0, "повторно те саме нагадування не йде")

print("— за годину")
S.clear(); at(SLOT - timedelta(minutes=60))
ok(bot.reminders_tick(force=True) == 1, "друге нагадування надіслано")
ok("за 1 годину" in texts()[0], "сказано, що за годину")

print("— після оплати передоплату не згадує")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET paid=1 WHERE id=1")
    conn.execute("DELETE FROM reminders WHERE booking_id=1 AND minutes=60")
S.clear(); at(SLOT - timedelta(minutes=60))
bot.reminders_tick(force=True)
ok("не надійшла" not in texts()[0], "оплаченому клієнту про гроші не пишуть")

print("— запізніле не шлеться")
with bot.db() as conn:
    conn.execute("DELETE FROM reminders")
S.clear(); at(SLOT - timedelta(hours=10))
n = bot.reminders_tick(force=True)
ok(n == 0, "після довгого простою добове нагадування не шлеться навздогін")
ok(bot.reminder_sent(1, 1440), "але позначається як закрите, щоб не випливло пізніше")

print("— скасований запис не нагадує")
with bot.db() as conn:
    conn.execute("DELETE FROM reminders")
    conn.execute("UPDATE bookings SET status='cancelled' WHERE id=1")
S.clear(); at(SLOT - timedelta(hours=24))
ok(bot.reminders_tick(force=True) == 0, "скасованому нагадування не йде")

print("— вимкнені нагадування")
W2 = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W2)
shutil.copy(os.path.join(SRC, "slots.py"), W2)
c2 = dict(c); c2.pop("reminders")
json.dump(c2, open(W2 + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
import subprocess
probe = ("import sys\nsys.argv=[sys.argv[0],%r];sys.path.insert(0,%r)\nimport bot\n"
         "print('MINS', bot.reminder_minutes())\n") % (W2, W2)
o = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True)
ok("MINS []" in o.stdout, "без блоку reminders нагадування вимкнені")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

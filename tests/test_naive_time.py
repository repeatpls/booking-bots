# -*- coding: utf-8 -*-
"""Записи, збережені без часової зони (зроблені на Windows), не ламають бота."""
import json, os, shutil, sys, tempfile
from datetime import timedelta
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
c["reminders"] = {"enabled": True, "before": [1440, 60]}
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t"})
bot.init_db()

ok(bot.tz() is not None, "часова зона в цьому середовищі є")

# запис, збережений БЕЗ зони — рівно те, що приїхало з Windows
slot_naive = (bot.now() + timedelta(hours=24)).replace(
    tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")
ok("+" not in slot_naive, "рядок справді без зони: %s" % slot_naive)

with bot.db() as conn:
    conn.execute("INSERT INTO bookings(id, chat_id, service, slot, name, phone,"
                 " status, created, deposit, paid, minutes)"
                 " VALUES(1,9,'Середнє тату',?, 'Петро','+380670000000','new',?,0,0,240)",
                 (slot_naive, bot.now().isoformat(timespec="seconds")))

print("— нагадування")
try:
    n = bot.reminders_tick(force=True)
    crashed = False
except TypeError as e:
    crashed = True
    print("     ", e)
ok(not crashed, "reminders_tick не падає на записі без зони")
ok(n == 1, "нагадування все одно надіслано (%s)" % n)

print("— зайняті слоти")
try:
    busy = bot.occupied("")
    crashed = False
except TypeError:
    crashed = True
ok(not crashed, "occupied не падає")
ok(len(busy) > 0, "слот порахований як зайнятий (%d клітинок)" % len(busy))

print("— мішанина: запис із зоною поруч")
slot_aware = (bot.now() + timedelta(hours=48)).replace(
    second=0, microsecond=0).isoformat(timespec="minutes")
with bot.db() as conn:
    conn.execute("INSERT INTO bookings(id, chat_id, service, slot, name, phone,"
                 " status, created, deposit, paid, minutes)"
                 " VALUES(2,9,'Маленьке тату',?, 'Іван','+380670000001','new',?,0,0,120)",
                 (slot_aware, bot.now().isoformat(timespec="seconds")))
try:
    bot.reminders_tick(force=True)
    bot.occupied("")
    crashed = False
except TypeError:
    crashed = True
ok(not crashed, "старі й нові записи в одній базі уживаються")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

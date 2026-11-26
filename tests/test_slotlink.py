# -*- coding: utf-8 -*-
"""Дотик по вільній годині в календарі: посилання → послуга → запис."""
import json, os, shutil, sys, tempfile
from datetime import datetime, timedelta

W = tempfile.mkdtemp()
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"
cfg["admin_ids"] = [777]
json.dump(cfg, open(os.path.join(W, "config.json"), "w", encoding="utf-8"))

sys.path.insert(0, W)
import bot

SENT = []
bot.call = lambda method, **p: (SENT.append((method, p)), {"username": "test_bot"})[1]
bot.init_db()

USER = 4242
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

def texts():
    return [p.get("text", "") for m, p in SENT
            if m in ("sendMessage", "editMessageText")]
def last_text():
    t = texts()
    return t[-1] if t else ""
def buttons():
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText"):
            rm = p.get("reply_markup") or {}
            return [b["callback_data"] for row in rm.get("inline_keyboard", [])
                    for b in row if "callback_data" in b]
    return []
def labels():
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText"):
            rm = p.get("reply_markup") or {}
            return [b["text"] for row in rm.get("inline_keyboard", []) for b in row]
    return []
def msg(text=None, contact=None):
    bot.handle_message({"chat": {"id": USER, "type": "private"},
                        "from": {"id": USER, "username": "petro"},
                        "message_id": 1, "text": text, "contact": contact})
def cb(data):
    bot.handle_callback({"id": "1", "data": data,
                         "message": {"chat": {"id": USER}, "message_id": 1}})

# найближчий день, коли працює перший майстер, і година з його вільних
M0 = bot.masters()[0]
SID = [s for s in bot.offered_services() if bot.master_does(M0, s["id"])][0]["id"]
day = (bot.now() + timedelta(days=2)).date()
for _ in range(14):
    times = bot.free_times(day.isoformat(), M0["id"], SID)
    if times:
        break
    day += timedelta(days=1)
SLOT = times[0][0]
CODE = "t-0-" + SLOT[:10].replace("-", "") + SLOT[11:13] + SLOT[14:16]

print("— розбір посилання")
m, slot = bot.parse_slot_link(CODE)
ok(m and m["id"] == M0["id"] and slot == SLOT, "майстер і час прочитані")
ok(bot.parse_slot_link("t-0-20261127") == (None, None), "короткий код відкинуто")
ok(bot.parse_slot_link("t-99-202611271130") == (None, None),
   "неіснуючий майстер відкинуто")
ok(bot.parse_slot_link("x-0-202611271130") == (None, None), "чужий префікс відкинуто")
ok(bot.parse_slot_link("t-0-202602301130") == (None, None),
   "неіснуюча дата відкинута")
ok(bot.parse_slot_link("") == (None, None), "порожнє відкинуто")

print("— перехід із календаря")
SENT.clear()
msg("/start " + CODE)
ok(bot.pretty_slot(SLOT) in last_text(), "бот назвав обрану годину")
ok(M0["name"] in last_text(), "і майстра")
wsvc = [b for b in buttons() if b.startswith("wsvc:")]
ok(len(wsvc) > 0, "запропоновано послуги (%d)" % len(wsvc))
others = [s["id"] for s in bot.offered_services()
          if not bot.master_does(M0, s["id"])]
ok(not any(("wsvc:" + o) in wsvc for o in others),
   "чужих послуг цього майстра в списку немає")
ok(any("—" in l for l in labels()), "видно ціни саме цього майстра")
step, d = bot.get_state(USER)
ok(step == "wslot" and d.get("slot_wanted") == SLOT, "година запам'ятана")

print("— звичайний /start посилання не ламає")
SENT.clear()
msg("/start")
ok("act:book" in buttons(), "просте /start і далі відкриває меню")
SENT.clear()
msg("/start t-77-202611271130")
ok("act:book" in buttons(), "зіпсоване посилання просто відкриває меню")

print("— вибір послуги займає саме цю годину")
SENT.clear()
msg("/start " + CODE)
cb("wsvc:" + SID)
step, d = bot.get_state(USER)
ok(step == "name" and d.get("slot") == SLOT, "час зафіксовано, питаємо ім'я")
ok(d.get("master_id") == M0["id"], "майстер той самий")
ok(d.get("price"), "ціна підставлена")
ok("ім'я" in last_text().lower(), "бот спитав ім'я")

print("— запис доходить до кінця")
msg("Петро")
msg(contact={"phone_number": "+380991112233"})
row = bot.active_booking(USER)
ok(row is not None, "запис створено")
ok(row and row["slot"] == SLOT, "збережена саме обрана година")
ok(row and row["master_id"] == M0["id"], "і саме той майстер")

print("— зайняту годину вдруге не віддамо")
SENT.clear()
USER = 4343
msg("/start " + CODE)
cb("wsvc:" + SID)
step, d = bot.get_state(USER)
ok(step != "name", "другого клієнта на ту саму годину не пустило")
ok(any("зайняли" in t for t in texts()), "людині пояснили, що сталося")
ok(any(b.startswith("time:") for b in buttons()), "одразу показано інші години")
ok(not any(b == "time:" + SLOT for b in buttons()),
   "зайнятої години серед них немає")

print("— довга послуга, яка не вміщається, теж не проходить")
with bot.db() as conn:
    conn.execute("DELETE FROM bookings")
long_sid = None
for s in bot.offered_services():
    if bot.master_does(M0, s["id"]) and bot.duration_of(s["id"]) > bot.step_minutes():
        long_sid = s["id"]
        break
if long_sid:
    days, w_start, w_end, _ = bot.schedule_of(M0)
    late = day.isoformat() + "T" + w_end
    late_dt = datetime.fromisoformat(late) - timedelta(minutes=bot.step_minutes())
    LATE = late_dt.isoformat(timespec="minutes")
    code2 = "t-0-" + LATE[:10].replace("-", "") + LATE[11:13] + LATE[14:16]
    SENT.clear()
    USER = 4444
    msg("/start " + code2)
    cb("wsvc:" + long_sid)
    step, d = bot.get_state(USER)
    ok(step != "name", "остання година дня під довгу послугу не віддана")
    ok(any("закоротка" in t or "зайняли" in t for t in texts()),
       "причину пояснено")
else:
    ok(True, "у цього конфіга немає довгих послуг — перевіряти нічого")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

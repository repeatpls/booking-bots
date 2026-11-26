# -*- coding: utf-8 -*-
"""Повторні клієнти: впізнавання, знижка, запис на іншу людину."""
import json, os, shutil, sys, tempfile
from datetime import timedelta

W = tempfile.mkdtemp(); SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"
cfg["admin_ids"] = [777]
cfg["loyalty"] = {"repeat_discount": 10}
json.dump(cfg, open(os.path.join(W, "config.json"), "w", encoding="utf-8"))
sys.path.insert(0, W)
import bot

S = []
bot.call = lambda m, **p: (S.append((m, p)), {"message_id": len(S) + 100})[1]
bot.init_db()

R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

USER = [5001]
def msg(text=None, contact=None):
    bot.handle_message({"chat": {"id": USER[0], "type": "private"},
                        "from": {"id": USER[0], "username": "petro"},
                        "message_id": 1, "text": text, "contact": contact})
def cb(data):
    bot.handle_callback({"id": "1", "data": data, "from": {"username": "petro"},
                         "message": {"chat": {"id": USER[0]}, "message_id": 1}})
def newest():
    with bot.db() as conn:
        return conn.execute("SELECT * FROM bookings ORDER BY id DESC"
                            " LIMIT 1").fetchone()
def texts():
    return [p.get("text", "") for m, p in S
            if m in ("sendMessage", "editMessageText")
            and p.get("chat_id") == USER[0]]
def last():
    t = texts(); return t[-1] if t else ""
def buttons():
    for m, p in reversed(S):
        if m in ("sendMessage", "editMessageText") and p.get("chat_id") == USER[0]:
            rm = p.get("reply_markup") or {}
            return [b.get("callback_data") for row in rm.get("inline_keyboard", [])
                    for b in row]
    return []

print("— рахунок знижки")
ok(bot.discount_price("700 ₴", 10) == bot.money(630), "700 → 630")
ok(bot.discount_price("від 6 500 ₴", 10) == "від " + bot.money(5850),
   "«від» зберігається: %s" % bot.discount_price("від 6 500 ₴", 10))
ok(bot.discount_price("безкоштовно", 10) is None, "на безкоштовне знижки немає")
ok(bot.discount_price("700 ₴", 0) is None, "вимкнена знижка нічого не рахує")
ok(bot.norm_phone("+380 67 123-45-67") == bot.norm_phone("0671234567"),
   "різні написання того самого номера збігаються")

print("— перший запис: нічого не питаємо зайвого")
M = bot.masters()[0]
SID = [s for s in bot.offered_services() if bot.master_does(M, s["id"])][0]["id"]
day = bot.open_days(M["id"], SID)[0][0]
slot = bot.free_times(day, M["id"], SID)[0][0]
S.clear()
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot)
ok("ім'я" in last().lower(), "у нового клієнта просто питаємо імʼя")
ok(not [b for b in buttons() if b and b.startswith("who:")],
   "вибору «себе чи іншу» новому не показуємо")
msg("Петро Коваль"); msg("+380671112233")
first = [t for t in texts() if "Записали" in t]
ok(first, "перший запис пройшов")
ok(first and "−10" not in first[0], "на першому записі знижки немає")

print("— привітання при поверненні")
S.clear()
msg("/start")
t = last()
ok("З поверненням" in t and "Петро" in t, "бот упізнав і привітався")
ok("знижка 10%" in t, "сказав про знижку")
ok(bot.visit_count(USER[0]) == 1, "візит порахований")

print("— повторний запис на себе")
S.clear()
slot2 = bot.free_times(day, M["id"], SID)[1][0]
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot2)
ok("Кого записуємо" in last(), "спитали, кого записуємо")
ok("who:self" in buttons() and "who:other" in buttons(), "є обидві кнопки")
cb("who:self")
ok("Ваші дані" in last() and "Петро Коваль" in last() and "+380671112233" in last(),
   "показали збережені дані")
ok("data:ok" in buttons() and "data:edit" in buttons(), "є звірка і правка")
cb("data:ok")
done = [t for t in texts() if "Записали" in t]
ok(done, "запис пройшов без повторних питань")
ok(done and "−10%" in done[-1], "показана знижка")
row = newest()
ok(row and row["discount"] == 10, "знижка збережена в базі")
full = bot.price_of(SID, M)
ok(row and bot.parse_money(row["price"]) == round(bot.parse_money(full) * 0.9),
   "ціна порахована: %s замість %s" % (row["price"] if row else "?", full))

print("— «змінити дані» повертає до звичайних питань")
S.clear()
bot.clear_state(USER[0])
slot3 = bot.free_times(day, M["id"], SID)[2][0]
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot3); cb("who:self"); cb("data:edit")
ok("ім'я" in last().lower(), "питаємо імʼя наново")
msg("Петро К."); msg("+380671112233")
row = newest()
ok(row and row["name"] == "Петро К.", "нове імʼя збережено")
ok(row and row["discount"] == 10, "знижка для свого лишилась")

print("— запис на іншу людину, якої немає в базі")
S.clear()
bot.clear_state(USER[0])
slot4 = bot.free_times(day, M["id"], SID)[3][0]
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot4); cb("who:other")
ok("звати" in last(), "питаємо імʼя тієї людини")
msg("Андрій Новий"); msg("+380509998877")
with bot.db() as conn:
    row = conn.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
ok(row["name"] == "Андрій Новий", "записали іншу людину")
ok(row["discount"] == 0, "новій людині знижки немає")
ok(bot.parse_money(row["price"]) == bot.parse_money(full), "ціна повна")

print("— запис на іншу людину, яка вже в базі")
S.clear()
bot.clear_state(USER[0])
slot5 = bot.free_times(day, M["id"], SID)[4][0]
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot5); cb("who:other")
msg("Андрій Новий"); msg("0509998877")
with bot.db() as conn:
    row = conn.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
ok(row["discount"] == 10, "своєму по номеру знижка нарахована")
ok(any("базі" in t for t in texts()), "клієнту пояснили, за що знижка")

print("— знижку можна вимкнути")
bot.CFG["loyalty"] = {"repeat_discount": 0}
S.clear()
bot.clear_state(USER[0])
slot6 = bot.free_times(day, M["id"], SID)[5][0]
cb("act:book"); cb("svc:" + SID); cb("mst:" + M["id"]); cb("day:" + day)
cb("time:" + slot6); cb("who:self"); cb("data:ok")
with bot.db() as conn:
    row = conn.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
ok(row["discount"] == 0, "з нульовою знижкою ціна повна")
ok(bot.parse_money(row["price"]) == bot.parse_money(full), "і сума не змінилась")
bot.CFG["loyalty"] = {"repeat_discount": 10}

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

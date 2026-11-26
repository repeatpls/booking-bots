# -*- coding: utf-8 -*-
"""Прогін сценарію запису без мережі: виклики Telegram підмінені заглушкою."""
import json, os, shutil, sys, tempfile

WORK = tempfile.mkdtemp()
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
shutil.copy(os.path.join(SRC, "bot.py"), WORK)
shutil.copy(os.path.join(SRC, "slots.py"), WORK)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"
cfg["admin_ids"] = [777]
json.dump(cfg, open(os.path.join(WORK, "config.json"), "w", encoding="utf-8"))

sys.path.insert(0, WORK)
import bot

SENT = []
def fake_call(method, **p):
    SENT.append((method, p))
    return {"username": "test_bot"}
bot.call = fake_call
bot.init_db()

USER = 12345
def last_text(chat=None):
    chat = USER if chat is None else chat
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat:
            return p.get("text", "")
    return ""
def last_buttons():
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText"):
            rm = p.get("reply_markup") or {}
            return [b["callback_data"] for row in rm.get("inline_keyboard", [])
                    for b in row if "callback_data" in b]
    return []

def msg(text=None, contact=None):
    bot.handle_message({"chat": {"id": USER}, "from": {"username": "petro"},
                        "text": text, "contact": contact})
def cb(data):
    bot.handle_callback({"id": "1", "data": data,
                         "message": {"chat": {"id": USER}, "message_id": 1}})

ok = lambda cond, label: print(("  OK   " if cond else "  ПАДІННЯ ") + label) or cond

results = []

# 1. старт
msg("/start")
results.append(ok(cfg["business"] in last_text(), "/start показує меню"))
results.append(ok("act:book" in last_buttons(), "у меню є кнопка запису"))

# 2. послуги
cb("act:book")
results.append(ok(any(b.startswith("svc:") for b in last_buttons()), "список послуг"))

# 3. вибір послуги -> (майстер, якщо він є) -> календар
cb("svc:strizhka")
masters = [b for b in last_buttons() if b.startswith("mst:")]
if masters:
    results.append(ok(True, "спершу пропонується майстер (%d)" % len(masters)))
    cb(masters[0])

days = [b for b in last_buttons() if b.startswith("day:")]
if not days:
    # місяць міг відкритись без вільних днів — гортаємо вперед
    for _ in range(3):
        nxt = [b for b in last_buttons() if b.startswith("cal:")]
        if not nxt:
            break
        cb(nxt[-1])
        days = [b for b in last_buttons() if b.startswith("day:")]
        if days:
            break
results.append(ok(len(days) > 0, "календар показує вільні дні (%d)" % len(days)))

# 4. вибір дня -> час
cb(days[0])
times = [b for b in last_buttons() if b.startswith("time:")]
results.append(ok(len(times) > 0, "пропонуються вільні години (%d)" % len(times)))

# 5. вибір часу -> просить ім'я
chosen = times[0]
cb(chosen)
results.append(ok("ім'я" in last_text().lower(), "після часу просить ім'я"))

# 6. ім'я -> просить телефон
msg("Петро")
results.append(ok("номер" in last_text().lower(), "після імені просить телефон"))

# 7. крива відповідь на телефон
msg("абвгд")
results.append(ok("не номер" in last_text().lower(), "невалідний номер відхиляється"))

# 8. нормальний номер -> запис
msg("+380671234567")
results.append(ok("Записали" in last_text(), "запис підтверджено"))

with bot.db() as _c:
    rows = _c.execute("SELECT * FROM bookings").fetchall()
results.append(ok(len(rows) == 1, "заявка в базі"))
results.append(ok(rows[0]["name"] == "Петро" and rows[0]["phone"] == "+380671234567",
                  "ім'я та телефон збережені правильно"))
results.append(ok(any(p.get("chat_id") == 777 for m, p in SENT if m == "sendMessage"),
                  "адміну надіслано сповіщення"))

# 9. зайнятий слот більше не пропонується
slot_iso = chosen[5:]
mid = masters[0][4:] if masters else ""
busy = bot.occupied(mid)
results.append(ok(slot_iso in busy or any(slot_iso in str(x) for x in busy),
                  "слот позначено зайнятим"))
cb("act:book")
if masters:
    cb(masters[0])
cb(days[0])
results.append(ok(chosen not in last_buttons(), "зайнятий час зник зі списку"))

# 10. скасування звільняє слот
msg("/skasuvaty")
results.append(ok("скасовано" in last_text(), "скасування працює"))
busy = bot.occupied(mid)
results.append(ok(not (slot_iso in busy or any(slot_iso in str(x) for x in busy)),
                  "слот звільнився після скасування"))

# 11. запитання адміну
cb("act:ask"); msg("Чи працюєте в неділю?")
results.append(ok(any("Чи працюєте" in p.get("text", "") and p.get("chat_id") == 777
                      for m, p in SENT if m == "sendMessage"),
                  "запитання доходить адміну"))

print("\n%d з %d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)

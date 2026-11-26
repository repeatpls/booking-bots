# -*- coding: utf-8 -*-
"""Відгуки після візиту і повернення сплячих клієнтів."""
import json, os, shutil, sys, tempfile
from datetime import timedelta
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
cfg["feedback"] = {"enabled": True, "after_hours": 3,
                   "link": "https://maps.google.com/?cid=1", "link_label": "Google Maps"}
cfg["winback"] = {"enabled": True, "after_days": 90, "cooldown_days": 90,
                  "text": "давно вас не бачили."}
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t", "message_id": 1})
bot.multipart = lambda m, f, **p: {"message_id": 1}
bot.init_db()
bot.time.sleep = lambda *_a: None

def texts(chat):
    return [p.get("text", "") for m, p in S
            if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat]
def buttons(chat):
    out = []
    for m, p in S:
        if p.get("chat_id") != chat: continue
        for r in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            out += r
    return out

def make(chat, name, days_ago, minutes=60, status="new"):
    t = bot.now() - timedelta(days=days_ago)
    with bot.db() as conn:
        cur = conn.execute(
            "INSERT INTO bookings(chat_id, username, service, slot, name, phone,"
            " status, created, minutes) VALUES(?,?,?,?,?,?,?,?,?)",
            (chat, "u", "Стрижка", t.isoformat(timespec="minutes"), name,
             "+380670000000", status, t.isoformat(timespec="seconds"), minutes))
        return cur.lastrowid

print("— питаємо оцінку після візиту")
b1 = make(101, "Петро", 1)
S.clear()
ok(bot.feedback_tick(force=True) == 1, "одне питання надіслано")
ok(any("Оцініть" in t for t in texts(101)), "клієнта спитали")
stars = [b for b in buttons(101) if b.get("callback_data", "").startswith("fb:")]
ok(len(stars) == 5, "п'ять зірок (%d)" % len(stars))

print("— двічі не питаємо")
S.clear()
ok(bot.feedback_tick(force=True) == 0, "повторно не питає")

print("— одразу після візиту ще рано")
b2 = make(102, "Іван", 0)
with bot.db() as conn:
    conn.execute("UPDATE bookings SET slot=? WHERE id=?",
                 ((bot.now() - timedelta(minutes=30)).isoformat(timespec="minutes"), b2))
S.clear()
bot.feedback_tick(force=True)
ok(not texts(102), "поки візит щойно закінчився — мовчимо")

print("— висока оцінка веде на відгук")
S.clear()
bot.handle_callback({"id": "1", "data": "fb:%d:5" % b1,
    "message": {"message_id": 7, "chat": {"id": 101}}, "from": {"id": 101}})
ok(bot.booking_of(b1)["rating"] == 5, "оцінку збережено")
url = [b for b in buttons(101) if b.get("url")]
ok(url and "maps.google" in url[0]["url"], "є кнопка на Google Maps")
ok(not any("що було не так" in t for t in texts(101)), "у задоволеного не питаємо про проблеми")

print("— без посилання все одно дякуємо")
bot.CFG["feedback"]["link"] = ""
b3 = make(103, "Олег", 1)
S.clear()
bot.handle_callback({"id": "1", "data": "fb:%d:4" % b3,
    "message": {"message_id": 7, "chat": {"id": 103}}, "from": {"id": 103}})
ok(any("Дякуємо" in t for t in texts(103)), "подяка є")
ok(not [b for b in buttons(103) if b.get("url")], "зайвої кнопки нема")
bot.CFG["feedback"]["link"] = "https://maps.google.com/?cid=1"

print("— низька оцінка йде приватно власнику")
b4 = make(104, "Ігор", 1)
S.clear()
bot.handle_callback({"id": "1", "data": "fb:%d:2" % b4,
    "message": {"message_id": 7, "chat": {"id": 104}}, "from": {"id": 104}})
ok(any("що було не так" in t for t in texts(104)), "спитали, що не так")
ok(bot.get_state(104)[0] == "fb_text", "чекаємо на текст")
ok(not [b for b in buttons(104) if b.get("url")], "незадоволеного нікуди не ведемо")

S.clear()
bot.handle_message({"chat": {"id": 104, "type": "private"}, "message_id": 9,
                    "from": {"id": 104, "username": "ihor"},
                    "text": "Чекав 40 хвилин"})
ok(bot.booking_of(b4)["rating_text"] == "Чекав 40 хвилин", "скаргу збережено")
admin = "\n".join(texts(777))
ok("Чекав 40 хвилин" in admin and "Низька оцінка" in admin, "власник отримав скаргу")
ok(any("передали власнику" in t for t in texts(104)), "клієнту відповіли")
ok(bot.get_state(104)[0] != "fb_text", "стан знято")

print("— сплячі клієнти")
make(201, "Давній", 200)
make(202, "Теж давній", 120)
make(203, "Нещодавній", 10)
b_future = make(204, "Має запис", 150)
with bot.db() as conn:
    conn.execute("INSERT INTO bookings(chat_id, service, slot, name, phone, status,"
                 " created) VALUES(204,'Стрижка',?, 'Має запис','+3','new',?)",
                 ((bot.now() + timedelta(days=3)).isoformat(timespec="minutes"),
                  bot.now().isoformat(timespec="seconds")))
ids = sorted(r["chat_id"] for r in bot.sleeping_clients())
ok(201 in ids and 202 in ids, "давні у списку")
ok(203 not in ids, "нещодавнього не чіпаємо")
ok(204 not in ids, "того, хто вже записаний, не чіпаємо")

S.clear()
n = bot.winback_send()
ok(n >= 2, "розіслано %d" % n)
ok(any("давно вас не бачили" in t for t in texts(201)), "текст дійшов")
ok(any("Давній," in t for t in texts(201)), "звернулись на ім'я")
bl = [b.get("callback_data") for b in buttons(201)]
ok("act:book" in bl and "nomail" in bl, "є кнопки запису і відписки")

print("— вдруге тій самій людині не пишемо")
ok(not any(r["chat_id"] == 201 for r in bot.sleeping_clients()),
   "після розсилки людина випала зі списку")

print("— відписка працює")
S.clear()
bot.handle_callback({"id": "1", "data": "nomail",
    "message": {"message_id": 7, "chat": {"id": 202}}, "from": {"id": 202}})
ok(bot.opted_out(202), "позначено відписку")
with bot.db() as conn:
    conn.execute("DELETE FROM winback WHERE chat_id=202")
ok(not any(r["chat_id"] == 202 for r in bot.sleeping_clients()),
   "відписаному більше не пишемо навіть після паузи")
b5 = make(202, "Теж давній", 1)
S.clear()
bot.feedback_tick(force=True)
ok(not texts(202), "і оцінку в нього теж не питаємо")

print("— вимкнені функції нічого не роблять")
bot.CFG["winback"]["enabled"] = False
ok(bot.sleeping_clients() == [] and bot.winback_send() == 0, "winback вимкнено")
bot.CFG["feedback"]["enabled"] = False
ok(bot.feedback_tick(force=True) == 0, "feedback вимкнено")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

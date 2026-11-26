# -*- coding: utf-8 -*-
"""Жодне натискання не лишає відкритого файлу бази.

Саме ця течія дескрипторів валила бота на сервері: він відповідав
нормально, а через кілька годин переставав відповідати взагалі.
"""
import json, os, shutil, sys, tempfile, threading
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t", "message_id": 1})
bot.multipart = lambda m, f, **p: {"message_id": 1}
bot.init_db()


def open_db_files():
    """Скільки файлів бази відкрито просто зараз."""
    d = "/proc/self/fd"
    n = 0
    for name in os.listdir(d):
        try:
            if "zayavky.db" in os.readlink(os.path.join(d, name)):
                n += 1
        except OSError:
            pass
    return n


ok(open_db_files() == 0, "на старті база не тримається відкритою")

print("— 300 звернень до бази")
for i in range(300):
    bot.set_state(1000 + i % 7, "menu", {"n": i})
    bot.get_state(1000 + i % 7)
    bot.active_booking(1000 + i % 7)
leak = open_db_files()
ok(leak == 0, "після 300 звернень відкритих файлів бази: %d (треба 0)" % leak)

print("— 200 натискань кнопок через повний обробник")
def cb(chat_id, data):
    return {"callback_query": {"id": "x", "data": data,
            "message": {"message_id": 5, "chat": {"id": chat_id}},
            "from": {"id": chat_id, "username": "u"}}}

for i in range(200):
    for d in ("act:menu", "act:prices", "act:book", "act:mybooking"):
        bot.handle_update(cb(2000 + i % 5, d))
leak = open_db_files()
ok(leak == 0, "після 800 натискань відкритих файлів бази: %d (треба 0)" % leak)

print("— запис і відмова не лишають відкритої транзакції")
try:
    with bot.db() as conn:
        conn.execute("INSERT INTO state(chat_id, step, data) VALUES(1,'x','{}')")
        raise RuntimeError("умисна помилка посеред запису")
except RuntimeError:
    pass
ok(open_db_files() == 0, "після помилки посеред запису файл теж закрито")
with bot.db() as conn:
    row = conn.execute("SELECT COUNT(*) c FROM state WHERE chat_id=1").fetchone()
ok(row["c"] == 0, "невдалий запис відкотився, смітя в базі не лишилось")

print("— паралельні записи не падають на «database is locked»")
errors = []
def hammer(k):
    try:
        for i in range(60):
            bot.set_state(5000 + k, "menu", {"i": i})
            bot.get_state(5000 + k)
    except Exception as e:
        errors.append(repr(e))
ths = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
for t in ths: t.start()
for t in ths: t.join()
ok(not errors, "8 потоків по 60 записів без помилок" + ("" if not errors else ": " + errors[0]))
ok(open_db_files() == 0, "після паралельних записів відкритих файлів бази: 0")

print("— падіння обробника не виглядає як зависання")
S.clear()
orig = bot.screen_prices
bot.screen_prices = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("тест"))
bot.handle_update(cb(7777, "act:prices"))
bot.screen_prices = orig
texts = [p.get("text", "") for m, p in S if m == "sendMessage" and p.get("chat_id") == 7777]
ok(any("Не вдалося виконати" in t for t in texts),
   "клієнт отримав зрозуміле повідомлення, а не тишу")
ok(any(m == "answerCallbackQuery" for m, p in S), "кружечок на кнопці знято")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

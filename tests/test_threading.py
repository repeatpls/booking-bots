# -*- coding: utf-8 -*-
"""Повільна відправка не блокує бот, натискання одного клієнта не плутаються."""
import json, os, shutil, sys, tempfile, threading, time
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot

SENT = []
SLOW = {"sendMediaGroup", "sendPhoto"}
def fake_call(m, **p):
    if m in SLOW:
        time.sleep(1.2)                      # вдаємо повільне вивантаження
    SENT.append((m, time.time()))
    return {"username": "t"}
bot.call = fake_call
bot.multipart = lambda method, fields, files: (time.sleep(1.2) or
                                               SENT.append((method, time.time())) or
                                               [{"photo": [{"file_id": "F"}]}])
bot.init_db()

def upd_cb(chat, data):
    return {"update_id": 1, "callback_query": {"id": "1", "data": data,
            "message": {"chat": {"id": chat}, "message_id": 1}}}

print("— різні користувачі обробляються одночасно")
orig_card = bot.screen_master_card
bot.screen_master_card = lambda chat_id, message_id, mid: time.sleep(1.0)
start = time.time()
ths = [threading.Thread(target=bot.handle_update, args=(upd_cb(100 + i, "mcard:majster1"),))
       for i in range(3)]
for t in ths: t.start()
for t in ths: t.join()
took = time.time() - start
bot.screen_master_card = orig_card
ok(took < 1.8, "три клієнти за %.1f с замість 3.0 — працюють паралельно" % took)

print("— натискання одного клієнта не перемішуються")
order = []
orig = bot.screen_master_card
def traced(chat_id, message_id, mid):
    order.append(("in", mid)); time.sleep(0.3); order.append(("out", mid))
bot.screen_master_card = traced
ths = [threading.Thread(target=bot.handle_update, args=(upd_cb(500, "mcard:majster%d" % i),))
       for i in (1, 2, 3)]
for t in ths: t.start()
for t in ths: t.join()
bot.screen_master_card = orig
pairs = [order[i:i+2] for i in range(0, len(order), 2)]
ok(all(a[0] == "in" and b[0] == "out" and a[1] == b[1] for a, b in pairs),
   "обробка одного користувача строго послідовна")

print("— оновлення без чату не валить обробник")
try:
    bot.handle_update({"update_id": 9, "pre_checkout_query": {"id": "p1"}})
    crashed = False
except Exception:
    crashed = True
ok(not crashed, "pre_checkout_query обробився")
ok(any(m == "answerPreCheckoutQuery" for m, _ in SENT), "відповідь на передперевірку надіслано")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

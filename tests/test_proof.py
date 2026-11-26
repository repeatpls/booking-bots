# -*- coding: utf-8 -*-
"""Вихід із запитання і підтвердження оплати скріном."""
import json, os, shutil, sys, tempfile
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
cfg["payment"] = {"mode": "link", "card": "0000 0000 0000 0000",
                  "card_holder": "Тест",
                  "deposit": {"type": "percent", "value": 30, "round_to": 50}}
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t", "message_id": 1})
bot.multipart = lambda m, f, **p: {"message_id": 1}
bot.init_db()

USER = 42
def cb(data):
    bot.handle_callback({"id": "1", "data": data,
        "message": {"message_id": 5, "chat": {"id": USER}},
        "from": {"id": USER, "username": "petro"}})
def texts(chat=USER):
    return [p.get("text", "") for m, p in S
            if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat]
def buttons(chat=USER):
    out = []
    for m, p in S:
        if p.get("chat_id") != chat:
            continue
        for r in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            out += [(b.get("text", ""), b.get("callback_data", "")) for b in r]
    return out

print("— вихід із запитання")
S.clear(); cb("act:ask")
ok(any("запитання" in t for t in texts()), "екран запитання показано")
back = [b for b in buttons() if b[1] == "act:menu"]
ok(back, "є кнопка виходу: %s" % (back[0][0] if back else "немає"))
ok(bot.get_state(USER)[0] == "question", "бот справді чекає на запитання")

S.clear(); cb("act:menu")
ok(bot.get_state(USER)[0] != "question", "вихід скасував очікування запитання")
S.clear()
bot.handle_message({"chat": {"id": USER}, "message_id": 9,
                    "from": {"id": USER}, "text": "просто текст"})
sent_admin = [p for m, p in S if p.get("chat_id") == 777]
ok(not sent_admin, "після виходу текст уже не летить адміну як запитання")

print("— вихід із кроку імені")
S.clear(); bot.ask_name(USER)
ok([b for b in buttons() if b[1] == "act:menu"], "на кроці імені теж є вихід")

print("— реквізити одразу просять квитанцію")
S.clear()
d = {"service_name": "Середнє тату", "price": 6000, "slot": "2030-02-01T12:00",
     "name": "Петро", "phone": "+380670000000", "master_name": "Остап"}
with bot.db() as conn:
    conn.execute("INSERT INTO bookings(id, chat_id, service, slot, name, phone,"
                 " status, created, deposit, paid) VALUES(1,42,'Середнє тату',"
                 "'2030-02-01T12:00','Петро','+380670000000','new',?,1800,0)",
                 (bot.now().isoformat(timespec="seconds"),))
bot.ask_payment(USER, 1, d)
t = "\n".join(texts())
ok("квитанцію" in t or "квитанці" in t, "на екрані оплати просять квитанцію")
ok("скрін" in t, "і скрін теж названо")
ok("0000 0000 0000 0000" in t, "номер картки на місці")

print("— те саме на екрані «Мій запис»")
S.clear(); bot.screen_my_booking(USER, None)
t = "\n".join(texts())
ok("скрін" in t and "квитанц" in t, "у «Мій запис» прохання теж є")

print("— клієнт надсилає скрін")
S.clear()
bot.handle_message({"chat": {"id": USER}, "message_id": 77,
                    "from": {"id": USER, "username": "petro"},
                    "photo": [{"file_id": "AAA"}]})
ok(any("Отримали" in t for t in texts()), "клієнту відповіли, що отримали")
copied = [p for m, p in S if m == "copyMessage"]
ok(copied and copied[0]["chat_id"] == 777, "скрін переслано адміну")
ok(copied and copied[0]["message_id"] == 77, "переслано саме те повідомлення")
admin_t = "\n".join(texts(777))
ok("#1" in admin_t and "Петро" in admin_t, "адмін бачить, до якого запису скрін")
admin_b = [b[1] for b in buttons(777)]
ok("payok:1" in admin_b, "у адміна одразу кнопка підтвердження")

print("— квитанція файлом теж приймається")
S.clear()
bot.handle_message({"chat": {"id": USER}, "message_id": 78,
                    "from": {"id": USER}, "document": {"file_id": "BBB"}})
ok([p for m, p in S if m == "copyMessage"], "документ переслано так само")

print("— скрін без запису не губиться мовчки")
S.clear()
bot.handle_message({"chat": {"id": 999}, "message_id": 80,
                    "from": {"id": 999}, "photo": [{"file_id": "CCC"}]})
ok(any("нема до чого" in t for t in texts(999)), "бот пояснив, що робити")
ok(not [p for m, p in S if m == "copyMessage"], "адміну зайвого не слали")

print("— повторний скрін після оплати")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET paid=1 WHERE id=1")
S.clear()
bot.handle_message({"chat": {"id": USER}, "message_id": 81,
                    "from": {"id": USER}, "photo": [{"file_id": "DDD"}]})
ok(any("уже оплачено" in t for t in texts()), "сказали, що вже все оплачено")
ok(not [p for m, p in S if m == "copyMessage"], "адміна вдруге не турбували")


print("— ціна числом у конфігурації не валить бота")
ok(bot.parse_money(600) == 600, "число приймається як є")
ok(bot.parse_money("600 ₴") == 600, "рядок із символом валюти теж")
ok(bot.parse_money("від 9 000 ₴") == 9000, "рядок із пробілом і словом")
ok(bot.parse_money(None) == 0 and bot.parse_money("") == 0, "порожнє — нуль")
ok(bot.parse_money(1799.6) == 1799, "дробове число не ламає")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

# -*- coding: utf-8 -*-
"""База, створена старою версією бота, не ламає «Мій запис».

На сервері база живе з перших днів: тоді не було ні майстрів, ні цін,
ні передоплати. Якщо міграція щось пропустить, sqlite3.Row кидає
IndexError саме на екрані запису — і це виглядає як зависання.
"""
import json, os, shutil, sqlite3, sys, tempfile
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
c["payment"] = {"mode": "card", "card": "0000 0000 0000 0000",
                "card_holder": "Тест", "percent": 30}
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)

# --- база ПЕРШОЇ версії бота: лише те, що було на самому початку ---
old = sqlite3.connect(os.path.join(W, "zayavky.db"))
old.execute("""CREATE TABLE bookings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, username TEXT,
    service TEXT, slot TEXT,
    name TEXT, phone TEXT, comment TEXT,
    status TEXT DEFAULT 'new',
    created TEXT)""")
old.execute("CREATE TABLE state(chat_id INTEGER PRIMARY KEY, step TEXT, data TEXT)")
old.execute("INSERT INTO bookings(chat_id, username, service, slot, name, phone,"
            " status, created) VALUES(42,'u','Стрижка','2030-01-15T12:00',"
            "'Петро','+380670000000','new','2026-11-01T10:00:00')")
old.commit(); old.close()

sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t", "message_id": 1})
bot.multipart = lambda m, f, **p: {"message_id": 1}

print("— міграція старої бази")
try:
    bot.init_db()
    ok(True, "init_db пережив стару схему")
except Exception as e:
    ok(False, "init_db упав: %r" % e)

with bot.db() as conn:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(bookings)")}
for need in ("master_id", "master_name", "price", "minutes", "deposit", "paid"):
    ok(need in cols, "колонку %s додано міграцією" % need)

print("— старий запис читається")
row = bot.active_booking(42)
ok(row is not None, "старий запис знайдено")

print("— «Мій запис» на старому записі")
S.clear()
bot.handle_update({"callback_query": {"id": "1", "data": "act:mybooking",
    "message": {"message_id": 7, "chat": {"id": 42}},
    "from": {"id": 42, "username": "u"}}})
texts = [p.get("text", "") for m, p in S
         if m in ("sendMessage", "editMessageText") and p.get("chat_id") == 42]
ok(texts, "екран взагалі показано")
ok(not any("Не вдалося виконати" in t for t in texts),
   "обробник не впав" + ("" if not any("Не вдалося" in t for t in texts) else ""))
ok(any("Стрижка" in t for t in texts), "видно послугу")
ok(any("Петро" in t for t in texts), "видно ім'я")

print("— меню зі старим записом")
S.clear()
bot.handle_update({"callback_query": {"id": "2", "data": "act:menu",
    "message": {"message_id": 7, "chat": {"id": 42}},
    "from": {"id": 42, "username": "u"}}})
btns = []
for m, p in S:
    for r in (p.get("reply_markup") or {}).get("inline_keyboard", []):
        btns += [b.get("callback_data", "") for b in r]
ok("act:mybooking" in btns, "кнопка «Мій запис» у меню є")

print("— скасування старого запису")
S.clear()
bot.handle_update({"callback_query": {"id": "3", "data": "unbook:%d" % row["id"],
    "message": {"message_id": 7, "chat": {"id": 42}},
    "from": {"id": 42, "username": "u"}}})
texts = [p.get("text", "") for m, p in S
         if m in ("sendMessage", "editMessageText") and p.get("chat_id") == 42]
ok(not any("Не вдалося виконати" in t for t in texts), "скасування не впало")
ok(bot.active_booking(42) is None, "запис справді скасовано")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

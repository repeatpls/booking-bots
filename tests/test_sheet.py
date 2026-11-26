# -*- coding: utf-8 -*-
"""Вигрузка записів у Google Таблицю: черга, порядок, нічого не губиться."""
import json, os, shutil, sys, tempfile, time
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
cfg["sheet_webhook"] = "https://script.google.com/macros/s/TEST/exec"
cfg["sheet_secret"] = "tajemnycja"
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t", "message_id": 1})
bot.multipart = lambda m, f, **p: {"message_id": 1}
bot.init_db()

POSTED, FAIL = [], {"on": False}
def fake_post(body):
    if FAIL["on"]:
        raise OSError("таблиця недоступна")
    POSTED.append(json.loads(body))
    return True
bot.sheet_post = fake_post

def queued():
    with bot.db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]

d = {"service_id": "strizhka", "service_name": "Стрижка", "price": "600 ₴",
     "slot": "2030-03-01T12:00", "name": "Петро", "phone": "+380670000000",
     "master_id": "majster1", "master_name": "Остап"}

print("— новий запис іде в таблицю")
bid = bot.confirm(42, d, "petro")
time.sleep(0.3)
ok(len(POSTED) == 1, "надіслано один рядок (%d)" % len(POSTED))
p = POSTED[0] if POSTED else {}
ok(p.get("event") == "Новий запис", "подія названа")
ok(p.get("name") == "Петро" and p.get("phone") == "+380670000000", "клієнт на місці")
ok(p.get("master") == "Остап" and p.get("service") == "Стрижка", "послуга і майстер")
ok(p.get("secret") == "tajemnycja", "ключ доступу переданий")
ok(p.get("price") == "600 ₴", "вартість послуги як у прайсі: %s" % p.get("price"))
ok(p.get("slot_date") == "01.03.2030", "дата візиту окремо: %s" % p.get("slot_date"))
ok(p.get("slot_time") == "12:00", "час візиту окремо: %s" % p.get("slot_time"))
import re as _re
ok(_re.match(r"^\d{2}\.\d{2}\.\d{4}$", p.get("created_date") or ""),
   "дата створення у форматі 22.11.2026: %s" % p.get("created_date"))
ok(_re.match(r"^\d{2}:\d{2}$", p.get("created_time") or ""),
   "час створення у форматі 16:01: %s" % p.get("created_time"))
ok("T" not in str(p.get("created_date")), "машинного рядка з T у таблиці нема")
ok(p.get("created") and "T" not in p["created"] and "о" in p["created"],
   "старе поле теж заповнене по-людськи: %s" % p.get("created"))
ok(p.get("when") == "01.03.2030 о 12:00", "старе поле «Коли»: %s" % p.get("when"))
ok(queued() == 0, "черга порожня")

print("— таблиця лежить: запис не губиться")
FAIL["on"] = True
POSTED.clear()
bot.mark_paid(bid, by_admin=True)
time.sleep(0.3)
ok(not POSTED, "нічого не надіслано, таблиця ж лежить")
ok(queued() == 1, "рядок чекає в черзі")

print("— таблиця повернулась: черга дотискається")
FAIL["on"] = False
sent = bot.sheet_flush()
ok(sent == 1, "відправлено з черги: %d" % sent)
ok(queued() == 0, "черга спорожніла")
ok(POSTED and POSTED[0]["event"] == "Оплата", "подія оплати дійшла")
ok(POSTED and POSTED[0]["paid"] == 1, "видно, що оплачено")
ok(POSTED and _re.match(r"^\d{2}\.\d{2}\.\d{4}$", POSTED[0]["paid_date"] or ""),
   "дата оплати теж розділена: %s" % POSTED[0]["paid_date"])
ok(POSTED and _re.match(r"^\d{2}:\d{2}$", POSTED[0]["paid_time"] or ""),
   "і час оплати: %s" % POSTED[0]["paid_time"])

print("— порядок подій зберігається")
FAIL["on"] = True; POSTED.clear()
b2 = bot.confirm(43, dict(d, name="Іван"), "ivan")
bot.handle_message({"chat": {"id": 43, "type": "private"}, "message_id": 5,
                    "from": {"id": 43}, "text": "/skasuvaty"})
time.sleep(0.3)
ok(queued() == 2, "обидві події в черзі (%d)" % queued())
FAIL["on"] = False
bot.sheet_flush()
ok([x["event"] for x in POSTED] == ["Новий запис", "Скасування"],
   "спершу запис, потім скасування: %s" % [x["event"] for x in POSTED])
ok(POSTED[-1]["status"] == "cancelled", "статус у таблиці оновлено")

print("— без налаштованої таблиці бот нічого не робить")
bot.CFG["sheet_webhook"] = ""
POSTED.clear()
bot.confirm(44, dict(d, name="Оля"), "olia")
time.sleep(0.2)
ok(not POSTED and queued() == 0, "ні відправки, ні черги")
bot.CFG["sheet_webhook"] = "https://script.google.com/macros/s/TEST/exec"

print("— збій таблиці не валить запис клієнта")
FAIL["on"] = True
S.clear()
b3 = bot.confirm(45, dict(d, name="Тарас"), "taras")
time.sleep(0.3)
ok(b3 is not None, "клієнта записали попри несправну таблицю")
ok(any("Записали" in p.get("text", "") for m, p in S if p.get("chat_id") == 45),
   "клієнт отримав підтвердження як завжди")
FAIL["on"] = False


print("— команда перевірки зв'язку")
bot.CFG["admin_ids"] = [777]
S.clear(); POSTED.clear(); FAIL["on"] = False
bot.handle_message({"chat": {"id": 777, "type": "private"}, "message_id": 1,
                    "from": {"id": 777}, "text": "/tablycja"})
ok(POSTED and POSTED[-1]["event"] == "Перевірка", "пробний рядок надіслано")
ok(POSTED and _re.match(r"^\d{2}\.\d{2}\.\d{4}$", POSTED[-1]["created_date"]),
   "у пробному рядку теж людська дата")
ok(any("відповіла" in p.get("text", "") for m, p in S if p.get("chat_id") == 777),
   "адміну сказали, що зв'язок є")

print("— перевірка показує справжню помилку")
FAIL["on"] = True
S.clear()
bot.handle_message({"chat": {"id": 777, "type": "private"}, "message_id": 2,
                    "from": {"id": 777}, "text": "/tablycja"})
t = "\n".join(p.get("text", "") for m, p in S if p.get("chat_id") == 777)
ok("не відповіла" in t, "збій названо прямо")
ok("Тільки я" in t, "підказано найчастішу причину")
FAIL["on"] = False

print("— без налаштувань команда це й каже")
bot.CFG["sheet_webhook"] = ""
S.clear()
bot.handle_message({"chat": {"id": 777, "type": "private"}, "message_id": 3,
                    "from": {"id": 777}, "text": "/tablycja"})
ok(any("не підключена" in p.get("text", "") for m, p in S if p.get("chat_id") == 777),
   "сказано, що таблиці нема в конфігу")
bot.CFG["sheet_webhook"] = "https://script.google.com/macros/s/TEST/exec"

print("— не адмін команду не бачить")
S.clear()
bot.handle_message({"chat": {"id": 99, "type": "private"}, "message_id": 4,
                    "from": {"id": 99}, "text": "/tablycja"})
ok(not any("Таблиця" in p.get("text", "") for m, p in S if p.get("chat_id") == 99),
   "звичайному клієнту нічого про таблицю не кажуть")


print("— стовпець «До оплати»")
class Row(dict):
    def __getitem__(self, k): return self.get(k)
ok(bot.due_amount(Row(price="600 ₴", deposit=200)) == 400, "600 мінус 200 = 400")
ok(bot.due_amount(Row(price="від 9 000 ₴", deposit=1800)) == 7200,
   "ціна словами теж рахується: %s" % bot.due_amount(Row(price="від 9 000 ₴", deposit=1800)))
ok(bot.due_amount(Row(price="600 ₴", deposit=0)) == 600, "без передоплати — вся сума")
ok(bot.due_amount(Row(price="600 ₴", deposit=600)) == 0, "оплачено повністю — нуль")
ok(bot.due_amount(Row(price="600 ₴", deposit=900)) == 0, "переплата не дає мінуса")
ok(bot.due_amount(Row(price="", deposit=200)) == "", "без ціни — порожньо, а не вигаданий нуль")
ok(bot.due_amount(Row(price=600, deposit=200)) == 400, "ціна числом теж")

POSTED.clear(); FAIL["on"] = False
b9 = bot.confirm(60, dict(d, name="Назар"), "nazar")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET deposit=200 WHERE id=?", (b9,))
line = bot.sheet_payload(bot.booking_of(b9), "Перевірка")
ok(line["due"] == 400, "у рядку таблиці до оплати 400: %s" % line["due"])
ok(line["deposit"] == 200 and line["price"] == "600 ₴",
   "вартість і передоплата поруч")
ok(list(line).index("price") < list(line).index("deposit") < list(line).index("due"),
   "порядок полів: вартість, передоплата, до оплати")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

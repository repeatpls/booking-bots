# -*- coding: utf-8 -*-
"""Передоплата: режим посилання, режим Telegram Payments, вимкнено."""
import json, os, shutil, subprocess, sys, tempfile
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

def boot(payment):
    W = tempfile.mkdtemp()
    shutil.copy(os.path.join(SRC, "bot.py"), W)
    shutil.copy(os.path.join(SRC, "slots.py"), W)
    c = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
    c["token"] = "000:TEST"; c["admin_ids"] = [777]
    if payment is not None: c["payment"] = payment
    json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
    return W

def session(W):
    for m in [m for m in list(sys.modules) if m == "bot"]: del sys.modules[m]
    sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
    import bot
    S = []
    bot.call = lambda m, **p: (S.append((m, p)) or ({"username": "t"} if m != "sendInvoice" else {"message_id": 5}))
    bot.init_db()
    return bot, S

U = 300
def book(bot, S, svc="serednie", mid="majster1"):
    def cb(d): bot.handle_callback({"id":"1","data":d,"message":{"chat":{"id":U},"message_id":1}})
    def msg(t): bot.handle_message({"chat":{"id":U},"from":{"username":"p"},"text":t})
    cb("act:book"); cb("svc:" + svc); cb("mst:" + mid)
    days = [b["callback_data"][4:] for r in S[-1][1]["reply_markup"]["inline_keyboard"] for b in r if b["callback_data"].startswith("day:")]
    cb("day:" + days[0])
    times = [b["callback_data"][5:] for r in S[-1][1]["reply_markup"]["inline_keyboard"] for b in r if b["callback_data"].startswith("time:")]
    cb("time:" + times[0]); msg("Петро"); msg("+380670000000")

# ---------------------------------------------------------------- посилання
print("— режим «посилання», передоплата відсотком")
W = boot({"mode": "link", "deposit": {"type": "percent", "value": 30, "round_to": 50},
          "link": "https://send.monobank.ua/jar/demo",
          "note": "Час тримається за вами після передоплати."})
bot, S = session(W)
book(bot, S)
texts = [p["text"] for m, p in S if m == "sendMessage" and p.get("chat_id") == U]
pay = [t for t in texts if "Передоплата" in t]
ok(pay, "показано крок оплати")
base = bot.parse_money(bot.price_of("serednie", bot.master_by_id("majster1")))
want = int(round(base * 0.30 / 50) * 50)
ok(bot.money(want).replace("\u00a0", " ") in pay[0].replace("\u00a0", " "),
   "30 відсотків від %d = %d грн" % (base, want))
kb = [p for m, p in S if m == "sendMessage" and p.get("chat_id") == U and p.get("reply_markup")][-1]
urls = [b for r in kb["reply_markup"]["inline_keyboard"] for b in r if "url" in b]
ok(urls and "send.monobank.ua" in urls[0]["url"], "перша кнопка веде одразу на оплату")
ok(bot.money(want).replace("\u00a0", " ") in urls[0]["text"].replace("\u00a0", " "),
   "на кнопці стоїть сума (%s)" % urls[0]["text"])
with bot.db() as _c:
    row = _c.execute("SELECT * FROM bookings").fetchone()
ok(row["deposit"] == want and row["paid"] == 0,
   "сума %d ₴ у базі, оплата поки не стоїть" % want)

print("\n— оплата на картку, без посилання")
Wc = boot({"mode": "link", "deposit": {"type": "fixed", "amount": 700},
           "card": "0000 1111 2222 3333", "card_holder": "Імʼя Прізвище"})
botc, Sc = session(Wc)
book(botc, Sc)
t = [p["text"] for m, p in Sc if m == "sendMessage" and "Передоплата" in p.get("text", "")][-1]
ok("<code>0000 1111 2222 3333</code>" in t, "номер картки подано як текст для копіювання")
ok("Імʼя Прізвище" in t, "отримувача видно")
ok("скопіювати" in t, "пояснення, що номер копіюється дотиком")
kbc = [p for m, p in Sc if m == "sendMessage" and p.get("reply_markup")][-1]
ok(not [b for r in kbc["reply_markup"]["inline_keyboard"] for b in r if "url" in b],
   "кнопки прямої оплати нема, бо посилання нема")
ok(any(b.get("callback_data","").startswith("paid:") for r in kbc["reply_markup"]["inline_keyboard"] for b in r),
   "кнопка «Я оплатив» на місці")

print("\n— клієнт тисне «Я оплатив»")
S.clear()
bot.handle_callback({"id":"1","data":"paid:1","message":{"chat":{"id":U},"message_id":9}})
adm = [p for m, p in S if m == "sendMessage" and p.get("chat_id") == 777]
ok(adm, "адміну пішло повідомлення")
ds = [b["callback_data"] for r in adm[0]["reply_markup"]["inline_keyboard"] for b in r]
ok("payok:1" in ds and "payno:1" in ds, "у адміна кнопки підтвердити і відхилити")
ok(bot.booking_of(1)["paid"] == 0, "без адміна оплата не зараховується")

print("\n— адмін підтверджує")
S.clear()
bot.handle_callback({"id":"1","data":"payok:1","message":{"chat":{"id":777},"message_id":9}})
ok(bot.booking_of(1)["paid"] == 1, "оплату зараховано")
ok(any("прийнято" in p.get("text", "") and "підтверджено" in p.get("text", "")
       for m, p in S if p.get("chat_id") == U),
   "клієнту повідомили, що оплату прийнято і запис підтверджено")

print("\n— чужий не може підтвердити оплату")
bot2, S2 = session(boot({"mode": "link", "deposit": {"type": "fixed", "amount": 500},
                         "link": "https://x"}))
book(bot2, S2)
bot2.handle_callback({"id":"1","data":"payok:1","message":{"chat":{"id":999},"message_id":1}})
ok(bot2.booking_of(1)["paid"] == 0, "сторонній натиск не зараховує оплату")

# ---------------------------------------------------------------- рахунок
print("\n— режим Telegram Payments")
bot3, S3 = session(boot({"mode": "invoice", "provider_token": "PROV:TEST",
                         "currency": "UAH", "deposit": {"type": "fixed", "amount": 800}}))
book(bot3, S3)
inv = [p for m, p in S3 if m == "sendInvoice"]
ok(inv, "надіслано рахунок")
ok(inv[0]["prices"][0]["amount"] == 80000, "сума в копійках: 800 ₴ -> 80000")
ok(inv[0]["payload"] == "booking:1", "у рахунку номер запису")
ok(inv[0]["provider_token"] == "PROV:TEST", "токен провайдера з конфігу")

print("\n— оплата прийшла від Telegram")
S3.clear()
bot3.handle_message({"chat": {"id": U}, "from": {"username": "p"},
                     "successful_payment": {"invoice_payload": "booking:1",
                                            "total_amount": 80000, "currency": "UAH"}})
ok(bot3.booking_of(1)["paid"] == 1, "запис позначено оплаченим автоматично")

print("\n— без токена провайдера режим вимикається")
bot4, _ = session(boot({"mode": "invoice", "deposit": {"type": "fixed", "amount": 500}}))
ok(bot4.pay_mode() == "off", "invoice без токена не вмикається")

print("\n— вимкнена оплата нічого не ламає")
bot5, S5 = session(boot(None))
book(bot5, S5)
ok(not [p for m, p in S5 if m == "sendInvoice"], "рахунків нема")
ok(not [p["text"] for m, p in S5 if m == "sendMessage" and "Передоплата" in p.get("text","")],
   "кроку оплати нема")
ok(bot5.booking_of(1) is not None, "запис усе одно створено")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

# -*- coding: utf-8 -*-
"""Робоча група: підключення, сповіщення, оплата і відповіді клієнту."""
import json, os, shutil, sys, tempfile
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
cfg["payment"] = {"mode": "link", "card": "0000 0000 0000 0000",
                  "deposit": {"type": "percent", "value": 30, "round_to": 50}}
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot

S = []
MEMBER = {"status": "administrator"}
def fake_call(m, **p):
    S.append((m, p))
    if m == "getChatMember":
        return dict(MEMBER)
    return {"username": "t", "message_id": 1000 + len(S)}
bot.call = fake_call
bot.multipart = lambda m, f, **p: {"message_id": 1}
bot.init_db()

GROUP, USER, BOSS, MASTER = -1001234, 42, 777, 555

def texts(chat):
    return [p.get("text", "") for m, p in S
            if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat]
def buttons(chat):
    out = []
    for m, p in S:
        if p.get("chat_id") != chat:
            continue
        for r in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            out += r
    return out
def gmsg(text, frm=BOSS, reply_to=None, mid=1):
    m = {"chat": {"id": GROUP, "type": "supergroup"}, "message_id": mid,
         "from": {"id": frm, "first_name": "Остап", "username": "ostap"},
         "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    bot.handle_message(m)

print("— підключення групи")
S.clear(); gmsg("/grupa")
ok(bot.group_id() == GROUP, "групу записано")
ok(any("підключено" in t for t in texts(GROUP)), "група отримала інструкцію")

print("— чужа людина групу не підключить")
bot.set_setting("group_id", 0)
MEMBER["status"] = "member"
S.clear(); gmsg("/grupa", frm=999)
ok(bot.group_id() == 0, "звичайний учасник не підключив групу")
ok(any("адміністратор" in t for t in texts(GROUP)), "йому це пояснили")
MEMBER["status"] = "administrator"
gmsg("/grupa")
ok(bot.group_id() == GROUP, "адміністратор підключив")

print("— повторне підключення не повторює інструкцію")
S.clear(); gmsg("/grupa")
t = "\n".join(texts(GROUP))
ok("вже підключено" in t, "сказали коротко, що вже підключено")
ok("Оплату підтверджуйте" not in t, "довгої інструкції вдруге нема")
ok(len(t.strip().splitlines()) <= 3, "не більше двох рядків із порожнім між ними")
ok(bot.group_id() == GROUP, "підключення збереглось")

S.clear(); gmsg("/start@lids_bot")
ok("вже підключено" in "\n".join(texts(GROUP)), "те саме для /start з іменем бота")

print("— новий запис летить і в групу, і адміну")
S.clear()
d = {"service_id": "strizhka", "service_name": "Стрижка", "price": 600,
     "slot": "2030-03-01T12:00", "name": "Петро", "phone": "+380670000000",
     "master_id": "majster1", "master_name": "Остап"}
bid = bot.confirm(USER, d, "petro")
ok(any("Стрижка" in t for t in texts(GROUP)), "група бачить новий запис")
ok(any("Стрижка" in t for t in texts(BOSS)), "адмін теж бачить")

print("— клієнт надіслав скрін, підтверджуємо з групи")
S.clear()
with bot.db() as conn:
    conn.execute("UPDATE bookings SET deposit=200 WHERE id=?", (bid,))
bot.handle_message({"chat": {"id": USER, "type": "private"}, "message_id": 90,
                    "from": {"id": USER, "username": "petro"},
                    "photo": [{"file_id": "AAA"}]})
copied = [p["chat_id"] for m, p in S if m == "copyMessage"]
ok(GROUP in copied, "скрін переслано в групу")
gb = []
for m, p in S:
    if p.get("chat_id") == GROUP:
        for r in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            gb += [b.get("callback_data") for b in r]
ok("payok:%d" % bid in gb, "у групі є кнопка підтвердження")

S.clear()
bot.handle_callback({"id": "1", "data": "payok:%d" % bid,
    "message": {"message_id": 7, "chat": {"id": GROUP}},
    "from": {"id": MASTER, "first_name": "Остап", "username": "ostap"}})
with bot.db() as conn:
    paid = conn.execute("SELECT paid FROM bookings WHERE id=?", (bid,)).fetchone()["paid"]
ok(paid == 1, "оплату зараховано")
ct = "\n".join(texts(USER))
ok("прийнято" in ct and "підтверджено" in ct,
   "клієнту написали, що оплату прийнято і запис підтверджено")
gt = "\n".join(texts(GROUP))
ok("Остап" in gt, "у групі видно, хто саме підтвердив")

print("— стороння людина кнопку не натисне")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET paid=0 WHERE id=?", (bid,))
S.clear()
bot.handle_callback({"id": "1", "data": "payok:%d" % bid,
    "message": {"message_id": 7, "chat": {"id": 31337}},
    "from": {"id": 31337}})
with bot.db() as conn:
    paid = conn.execute("SELECT paid FROM bookings WHERE id=?", (bid,)).fetchone()["paid"]
ok(paid == 0, "з чужого чату оплату не підтвердити")

print("— запитання клієнта і відповідь просто з групи")
S.clear()
bot.set_state(USER, "question", {})
bot.handle_message({"chat": {"id": USER, "type": "private"}, "message_id": 95,
                    "from": {"id": USER, "username": "petro"},
                    "text": "Чи працюєте в неділю?"})
gq = [(p.get("text"), mid) for mid, (m, p) in enumerate(S)
      if p.get("chat_id") == GROUP]
ok(any("Чи працюєте" in (t or "") for t, _ in gq), "запитання прийшло в групу")
qmid = [p for m, p in S if p.get("chat_id") == GROUP][0]
sent_id = [r for r in S if r[1].get("chat_id") == GROUP]
# повідомлення в групі отримало номер — відповідаємо на нього
with bot.db() as conn:
    row = conn.execute("SELECT message_id FROM threads WHERE chat_id=?",
                       (GROUP,)).fetchone()
ok(row is not None, "повідомлення прив'язано до клієнта")

S.clear()
gmsg("Так, у неділю з 10 до 16", frm=MASTER, reply_to=row["message_id"], mid=120)
ok(any("з 10 до 16" in t for t in texts(USER)), "відповідь дійшла клієнту")
# підтвердження тепер не окремим повідомленням, а в самому запитанні
ge = [p for m, p in S if m == "editMessageText" and p.get("chat_id") == GROUP]
ok(ge and "Відповів" in ge[0]["text"], "у групі запитання помічене як закрите")
ok(ge and bot.ASK_HINT not in ge[0]["text"], "підказка «відповідайте» прибрана")

print("— відповідь не на те повідомлення нікуди не йде")
S.clear()
gmsg("просто балачка в групі", frm=MASTER, reply_to=999999, mid=121)
ok(not texts(USER), "клієнту нічого не полетіло")

print("— звичайна балачка в групі ігнорується")
S.clear()
gmsg("хлопці, хто сьогодні закриває?", frm=MASTER, mid=122)
ok(not S, "бот мовчить, коли до нього не звертаються")

print("— відключення групи")
S.clear(); gmsg("/vidkluchyty")
ok(bot.group_id() == 0, "групу відключено")
S.clear()
bot.notify("тест")
ok(not texts(GROUP), "у відключену групу більше не шлемо")
ok(texts(BOSS), "адмін і далі отримує")


print("— кнопки підтвердження зникають усюди, а не лише там, де натиснули")
bot.set_setting("group_id", GROUP)
bot.CFG["admin_ids"] = [BOSS]
S.clear()
d2 = {"service_id": "strizhka", "service_name": "Стрижка", "price": 600,
      "slot": "2030-04-01T12:00", "name": "Орест", "phone": "+380670000001",
      "master_id": "majster1", "master_name": "Остап"}
b2 = bot.confirm(50, d2, "orest")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET deposit=200, paid=0 WHERE id=?", (b2,))
S.clear()
bot.handle_callback({"id": "1", "data": "paid:%d" % b2,
    "message": {"message_id": 5, "chat": {"id": 50}},
    "from": {"id": 50, "username": "orest"}})
with bot.db() as conn:
    copies = conn.execute("SELECT chat_id FROM pay_msgs WHERE booking_id=?",
                          (b2,)).fetchall()
places = sorted(c["chat_id"] for c in copies)
ok(places == sorted([GROUP, BOSS]),
   "кнопки висять у двох місцях — групі й адміна: %s" % places)

S.clear()
bot.handle_callback({"id": "2", "data": "payok:%d" % b2,
    "message": {"message_id": 70, "chat": {"id": BOSS}},
    "from": {"id": BOSS, "first_name": "Дмитро"}})
edits = [(p.get("chat_id"), p.get("text")) for m, p in S if m == "editMessageText"]
ok(len(edits) == 2, "перемальовано обидві копії (%d)" % len(edits))
ok(all("reply_markup" not in p for m, p in S if m == "editMessageText"),
   "кнопок у перемальованих повідомленнях нема")
ok(any(c == GROUP for c, _t in edits), "у групі кнопки теж зникли")
ok(all("Дмитро" in (t or "") for _c, t in edits), "скрізь видно, хто підтвердив")
with bot.db() as conn:
    left = conn.execute("SELECT COUNT(*) c FROM pay_msgs WHERE booking_id=?",
                        (b2,)).fetchone()["c"]
ok(left == 0, "записи про кнопки прибрано")

print("— повторне натискання вже нічого не робить")
S.clear()
bot.handle_callback({"id": "3", "data": "payok:%d" % b2,
    "message": {"message_id": 70, "chat": {"id": GROUP}},
    "from": {"id": MASTER, "first_name": "Остап"}})
paid_msgs = [p for m, p in S if p.get("chat_id") == 50 and m == "sendMessage"]
ok(not paid_msgs, "клієнту вдруге не писали про оплату")

print("— відмова теж закриває кнопки всюди")
S.clear()
b3 = bot.confirm(51, dict(d2, name="Богдан", slot="2030-04-02T12:00"), "bohdan")
with bot.db() as conn:
    conn.execute("UPDATE bookings SET deposit=200, paid=0 WHERE id=?", (b3,))
bot.handle_callback({"id": "4", "data": "paid:%d" % b3,
    "message": {"message_id": 6, "chat": {"id": 51}}, "from": {"id": 51}})
S.clear()
bot.handle_callback({"id": "5", "data": "payno:%d" % b3,
    "message": {"message_id": 80, "chat": {"id": GROUP}},
    "from": {"id": MASTER, "first_name": "Остап"}})
edits = [p.get("chat_id") for m, p in S if m == "editMessageText"]
ok(len(edits) == 2, "обидві копії закрито й після відмови (%d)" % len(edits))
ok(any("не бачить надходження" in (p.get("text") or "")
       for m, p in S if m == "editMessageText"), "написано, що саме сталось")
ok(any("квитанцію" in (p.get("text") or "")
       for m, p in S if p.get("chat_id") == 51), "клієнта попросили квитанцію")


print("— кнопка розкладу")
bot.CFG["miniapp_url"] = "https://botlids.duckdns.org/lezo"
bot.CFG["masters"] = [{"id": "ostap", "name": "Остап", "tg_id": 501}]
bot.set_setting("group_id", GROUP)
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 1,
                    "from": {"id": BOSS}, "text": "/rozklad"})
wb = [b for b in buttons(BOSS) if b.get("web_app")]
ok(wb and wb[0]["web_app"]["url"].endswith("/lezo"), "власнику дали кнопку сторінки")

S.clear()
bot.handle_message({"chat": {"id": 501, "type": "private"}, "message_id": 2,
                    "from": {"id": 501}, "text": "/rozklad"})
wb = [b for b in buttons(501) if b.get("web_app")]
ok(wb and "v=free" in wb[0]["web_app"]["url"],
   "майстру в особистому чаті дають лише вільні години")
ok(any("робочій групі" in t for t in texts(501)),
   "майстру сказано, де дивитись повний розклад")

S.clear()
bot.handle_message({"chat": {"id": 42, "type": "private"}, "message_id": 3,
                    "from": {"id": 42}, "text": "/rozklad"})
wb = [b for b in buttons(42) if b.get("web_app")]
ok(wb and "v=free" in wb[0]["web_app"]["url"], "клієнту дали вільні години")
ok(not any("v=free" not in b["web_app"]["url"] for b in wb),
   "повного розкладу клієнту не дали")
ok(any("власнику закладу" in t for t in texts(42)), "клієнту пояснили")

S.clear()
bot.handle_message({"chat": {"id": 42, "type": "private"}, "message_id": 31,
                    "from": {"id": 42}, "text": "/vilni"})
wb = [b for b in buttons(42) if b.get("web_app")]
ok(wb and "v=free" in wb[0]["web_app"]["url"], "команда /vilni відкриває календар")

S.clear()
bot.handle_message({"chat": {"id": 42, "type": "private"}, "message_id": 32,
                    "from": {"id": 42}, "text": "/start"})
wb = [b for b in buttons(42) if b.get("web_app")]
ok(wb and "v=free" in wb[0]["web_app"]["url"],
   "у меню клієнта є кнопка вільних годин")

S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 33,
                    "from": {"id": BOSS}, "text": "/start"})
wb = [b for b in buttons(BOSS) if b.get("web_app")]
ok(wb and "v=free" in wb[0]["web_app"]["url"],
   "у меню власника ця кнопка теж є")

print("— кнопка в групі")
bot.CFG["miniapp_link"] = "https://t.me/lids_for_business_bot/rozklad"
S.clear(); gmsg("/rozklad", frm=MASTER, mid=200)
gw = [b for b in buttons(GROUP) if b.get("url")]
ok(gw, "у групі кнопка-посилання працює")
gt = "\n".join(texts(GROUP))
ok("майстер своєю колонкою" in gt, "пояснено, що саме в розкладі")
ok("телефон" in gt, "сказано про картку запису")
ok("Оновлюється сам" in gt, "сказано, що оновлюється саме")
ok([p for m, p in S if m == "pinChatMessage"],
   "бот сам закріпив повідомлення, просити користувача не треба")

print("— команда з іменем бота в приватному чаті")
bot.CFG["miniapp_url"] = "https://botlids.duckdns.org/lezo"
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 20,
                    "from": {"id": BOSS}, "text": "/rozklad@lids_for_business_bot"})
ok([b for b in buttons(BOSS) if b.get("web_app")],
   "команда з «@імʼя_бота» теж спрацювала")

print("— команди в списку по «/»")
S.clear()
bot.publish_commands()
sets = [p for m, p in S if m == "setMyCommands"]
ok(len(sets) == 3, "три набори команд: клієнтам, групам, адміністраторам (%d)" % len(sets))
priv = [p for p in sets if not p.get("scope")]
ok(priv and all(c["command"] != "rozklad" for c in priv[0]["commands"]),
   "клієнтам розклад не показуємо")
grp = [p for p in sets if (p.get("scope") or {}).get("type") == "all_group_chats"]
ok(grp, "є набір саме для груп")
names = [c["command"] for c in grp[0]["commands"]] if grp else []
ok("rozklad" in names, "у групах видно /rozklad")
ok("grupa" in names and "vidkluchyty" in names, "і керування групою")
ok(all(c.get("description") for c in grp[0]["commands"]),
   "у кожної команди є опис")

print("— кнопка біля поля вводу — список команд")
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 10,
                    "from": {"id": BOSS}, "text": "/start"})
mb = [p for m, p in S if m == "setChatMenuButton"]
ok(mb and mb[0]["menu_button"]["type"] == "commands",
   "власнику лишили список команд")
own = [p for m, p in S if m == "setMyCommands"
       and (p.get("scope") or {}).get("chat_id") == BOSS]
ok(own, "власнику надіслано його власний список")
names = [c["command"] for c in own[0]["commands"]] if own else []
ok("rozklad" in names, "у списку власника є розклад")
ok("vilni" in names, "і вільні години")

S.clear()
bot.handle_message({"chat": {"id": 42, "type": "private"}, "message_id": 11,
                    "from": {"id": 42}, "text": "/start"})
mb = [p for m, p in S if m == "setChatMenuButton"]
ok(mb and mb[0]["menu_button"]["type"] == "commands",
   "клієнту теж список команд")
ok(not [p for m, p in S if m == "setMyCommands"
        and (p.get("scope") or {}).get("chat_id") == 42],
   "клієнту власницьких команд не шлемо")

print("— у групі посилання, а не кнопка-застосунок")
bot.CFG["miniapp_link"] = "https://t.me/lids_for_business_bot/rozklad"
S.clear(); gmsg("/rozklad", frm=MASTER, mid=201)
ok([p for m, p in S if m == "pinChatMessage"], "бот закріпив кнопку вгорі чату")
gb = buttons(GROUP)
ok(gb and gb[-1].get("url", "").startswith("https://t.me/"),
   "у групі саме посилання t.me")
ok(not any(b.get("web_app") for b in gb),
   "кнопки web_app у групі немає — Telegram її відхиляє")

print("— без посилання бот пояснює, що робити")
bot.CFG["miniapp_link"] = ""
S.clear(); gmsg("/rozklad", frm=MASTER, mid=202)
ok(any("/newapp" in t for t in texts(GROUP)), "сказано, що створити в BotFather")
bot.CFG["miniapp_link"] = "https://t.me/lids_for_business_bot/rozklad"

print("— у приватному чаті лишається кнопка-застосунок")
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 21,
                    "from": {"id": BOSS}, "text": "/rozklad"})
ok([b for b in buttons(BOSS) if b.get("web_app")], "у приваті web_app працює")

print("— без адреси кнопки немає")
bot.CFG["miniapp_url"] = ""
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 4,
                    "from": {"id": BOSS}, "text": "/rozklad"})
ok(not [b for b in buttons(BOSS) if b.get("web_app")], "кнопки нема")
ok(any("miniapp_url" in t for t in texts(BOSS)), "сказано, чого бракує")

print("— http адресу не беремо")
bot.CFG["miniapp_url"] = "http://botlids.duckdns.org/lezo"
ok(bot.miniapp_url() == "", "незахищену адресу відкинуто — Telegram її не відкриє")
bot.CFG["miniapp_url"] = "https://botlids.duckdns.org/lezo"

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

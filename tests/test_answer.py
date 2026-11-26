# -*- coding: utf-8 -*-
"""Відповідь на запитання клієнта: з групи і з особистого чату власника."""
import json, os, shutil, sys, tempfile

W = tempfile.mkdtemp(); SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"
BOSS, SECOND, GROUP, CLIENT = 777, 888, -100500, 4242
cfg["admin_ids"] = [BOSS, SECOND]
json.dump(cfg, open(os.path.join(W, "config.json"), "w", encoding="utf-8"))
sys.path.insert(0, W)
import bot

S = []
MID = [1000]
def fake(method, **p):
    S.append((method, p))
    if method == "sendMessage":
        MID[0] += 1
        return {"message_id": MID[0], "chat": {"id": p["chat_id"]}}
    return {"ok": True}
bot.call = fake
bot.init_db()
bot.set_setting("group_id", GROUP)

R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)
def to(chat):
    return [(p.get("text", ""), p) for m, p in S
            if m == "sendMessage" and p.get("chat_id") == chat]
def edits(chat):
    return [p for m, p in S
            if m == "editMessageText" and p.get("chat_id") == chat]

print("— запитання розходиться по команді з одним ключем")
bot.set_state(CLIENT, "question", {})
bot.handle_message({"chat": {"id": CLIENT, "type": "private"}, "message_id": 1,
                    "from": {"id": CLIENT, "username": "petro"},
                    "text": "Чи можна з дитиною?"})
mids = {}
for m, p in S:
    if m == "sendMessage" and "Запитання" in p.get("text", ""):
        mids[p["chat_id"]] = None
ok(GROUP in mids and BOSS in mids and SECOND in mids,
   "запитання прийшло в групу і обом адмінам")
sent = [(p["chat_id"], mid) for (m, p), mid in
        zip([x for x in S if x[0] == "sendMessage"],
            range(1001, 1001 + len(S)))]
with bot.db() as conn:
    rows = conn.execute("SELECT chat_id, message_id, tkey, client_chat"
                        " FROM threads").fetchall()
keys = {r["tkey"] for r in rows}
ok(len(rows) == 3, "запамʼятали всі три копії (%d)" % len(rows))
ok(len(keys) == 1 and list(keys)[0], "у копій один спільний ключ")
ok({r["client_chat"] for r in rows} == {CLIENT}, "усі ведуть до клієнта")
boss_mid = [r["message_id"] for r in rows if r["chat_id"] == BOSS][0]
group_mid = [r["message_id"] for r in rows if r["chat_id"] == GROUP][0]
second_mid = [r["message_id"] for r in rows if r["chat_id"] == SECOND][0]

print("— власник відповідає в особистому чаті з ботом")
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 50,
                    "from": {"id": BOSS, "first_name": "Віталій",
                             "username": "vitaliy"},
                    "text": "Так, з дитиною можна.",
                    "reply_to_message": {"message_id": boss_mid}})
cl = to(CLIENT)
ok(cl and "з дитиною можна" in cl[0][0], "клієнт отримав відповідь")
ok(not [t for t, _ in to(BOSS) if "Надіслано" in t],
   "окремого службового повідомлення більше немає")
ge = edits(GROUP)
ok(ge and ge[0]["message_id"] == group_mid, "у групі переписано саме те запитання")
ok(ge and bot.ASK_HINT not in ge[0]["text"], "підказка «відповідайте» зникла")
ok(ge and "Відповів" in ge[0]["text"], "на її місці позначка про відповідь")
ok(ge and "Віталій" in ge[0]["text"] and "@vitaliy" in ge[0]["text"],
   "видно, хто саме відповів")
ok(ge and "Чи можна з дитиною?" in ge[0]["text"], "сам текст запитання лишився")
be = edits(BOSS)
ok(be and be[0]["message_id"] == boss_mid, "у того, хто відповів, теж переписано")
se = edits(SECOND)
ok(se and se[0]["message_id"] == second_mid, "і в другого адміна")

print("— те саме з групи")
S.clear()
bot.handle_group({"chat": {"id": GROUP, "type": "supergroup"},
                  "message_id": 51, "from": {"id": SECOND, "first_name": "Оля"},
                  "text": "Беремо з 5 років.",
                  "reply_to_message": {"message_id": group_mid}})
cl = to(CLIENT)
ok(cl and "з 5 років" in cl[0][0], "клієнт отримав відповідь із групи")
be = edits(BOSS)
ok(be and be[0]["message_id"] == boss_mid, "власнику запитання переписано")
ok(be and "Оля" in be[0]["text"], "підписано іменем того, хто відповів")
ok(be and "Відповів" in be[0]["text"] and bot.ASK_HINT not in be[0]["text"],
   "друга відповідь теж прибрала підказку")

print("— чужі відповіді нічого не ламають")
S.clear()
bot.handle_message({"chat": {"id": BOSS, "type": "private"}, "message_id": 52,
                    "from": {"id": BOSS}, "text": "просто текст",
                    "reply_to_message": {"message_id": 999999}})
ok(not to(CLIENT), "відповідь не на запитання клієнту не йде")
S.clear()
bot.handle_message({"chat": {"id": CLIENT, "type": "private"}, "message_id": 53,
                    "from": {"id": CLIENT}, "text": "ехо",
                    "reply_to_message": {"message_id": boss_mid}})
ok(not [t for t, _ in to(CLIENT) if "Відповідь від" in t],
   "не-адмін не може відповідати від імені закладу")

print("— імʼя, схоже на адресу, не стає посиланням")
mark = bot.who_answered({"first_name": "di.ma", "username": "flame4k"})
ok("di.\u2060ma" in mark, "крапку в імені знешкоджено")
ok(mark.count("<a ") == 1 and "t.me/flame4k" in mark,
   "посилання рівно одне — на нік")

print("— якщо переписати не вдалося, позначка приходить окремо")
S.clear()
bot.set_state(CLIENT, "question", {})
bot.handle_message({"chat": {"id": CLIENT, "type": "private"}, "message_id": 70,
                    "from": {"id": CLIENT, "username": "petro"},
                    "text": "А парковка є?"})
with bot.db() as conn:
    row = conn.execute("SELECT chat_id, message_id FROM threads WHERE chat_id=?"
                       " ORDER BY message_id DESC LIMIT 1", (GROUP,)).fetchone()
old_call = bot.call
def no_edit(method, **p):
    if method == "editMessageText":
        S.append((method, p))
        return None          # Telegram відмовив: повідомлення застаре
    return old_call(method, **p)
bot.call = no_edit
S.clear()
bot.handle_group({"chat": {"id": GROUP, "type": "supergroup"},
                  "message_id": 71, "from": {"id": SECOND, "first_name": "Оля"},
                  "text": "Є, за рогом.",
                  "reply_to_message": {"message_id": row["message_id"]}})
bot.call = old_call
ok([t for t, _ in to(CLIENT) if "за рогом" in t], "клієнт усе одно отримав відповідь")
fallback = [p for t, p in to(GROUP) if "Відповів" in t]
ok(fallback, "позначка прийшла окремим повідомленням")
ok(fallback and fallback[0].get("reply_to_message_id") == row["message_id"],
   "і причеплена до запитання")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

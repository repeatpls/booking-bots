# -*- coding: utf-8 -*-
"""Кнопка «Мій запис і оплата»: зʼявляється після бронювання, показує стан."""
import json, os, shutil, subprocess, sys, tempfile
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "templates/tattoo.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [777]
c["payment"] = {"mode": "link", "deposit": {"type": "fixed", "amount": 900},
                "card": "0000 1111 2222 3333", "note": "Передоплата тримає час."}
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S = []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t"})
bot.init_db()

U = 420
def cb(d): bot.handle_callback({"id":"1","data":d,"message":{"chat":{"id":U},"message_id":1}})
def msg(t): bot.handle_message({"chat":{"id":U},"from":{"username":"p"},"text":t})
def last(chat=U):
    for m, p in reversed(S):
        if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat:
            return p
    return {}
def datas(chat=U):
    return [b.get("callback_data") for r in (last(chat).get("reply_markup") or {}).get("inline_keyboard", []) for b in r]

print("— до бронювання кнопки нема")
msg("/start")
ok("act:mybooking" not in datas(), "у чистому меню кнопки «Мій запис» нема")

print("\n— бронюємо")
cb("act:book"); cb("svc:serednie"); cb("mst:majster1")
days = [d[4:] for d in datas() if d.startswith("day:")]
cb("day:" + days[0])
times = [d[5:] for d in datas() if d.startswith("time:")]
cb("time:" + times[0]); msg("Петро"); msg("+380670000000")

print("\n— після бронювання кнопка зʼявилась")
S.clear(); msg("/start")
ok("act:mybooking" in datas(), "кнопка в меню є")
lbl = [b["text"] for r in (last().get("reply_markup") or {}).get("inline_keyboard", []) for b in r
       if b.get("callback_data") == "act:mybooking"][0]
ok(lbl == "Мій запис і оплата" and len(lbl) <= 34, "напис короткий: «%s»" % lbl)

print("\n— екран показує запис і несплачену передоплату")
S.clear(); cb("act:mybooking")
t = last()["text"]
mname = bot.master_by_id("majster1")["name"]
sname = bot.service_by_id("serednie")["name"]
ok(sname in t and mname in t, "видно послугу «%s» і майстра «%s»" % (sname, mname))
ok("900 ₴" in t.replace(" ", " ") and "не оплачена" in t, "видно суму й те, що не сплачено")
ok("<code>0000 1111 2222 3333</code>" in t, "номер картки для копіювання")
ds = datas()
ok(any(d and d.startswith("paid:") for d in ds), "кнопка «Я оплатив»")
ok(any(d and d.startswith("unbook:") for d in ds), "кнопка скасування")

print("\n— після підтвердження оплати стан змінюється")
bot.mark_paid(1, by_admin=True)
S.clear(); cb("act:mybooking")
t = last()["text"]
ok("оплачено" in t and "не оплачена" not in t, "тепер написано «оплачено»")
ok(not any(d and d.startswith("paid:") for d in datas()), "кнопки оплати більше нема")
ok("0000 1111" not in t, "реквізити вже не показуються")

print("\n— після скасування кнопка зникає")
cb("unbook:1")
S.clear(); msg("/start")
ok("act:mybooking" not in datas(), "кнопки в меню знову нема")
S.clear(); cb("act:mybooking")
ok("Активних записів нема" in last()["text"], "прямий виклик каже, що записів нема")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

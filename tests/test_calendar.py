# -*- coding: utf-8 -*-
"""Календар вибору дати."""
import json, os, shutil, sys, tempfile
from datetime import datetime, timedelta

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
WORK = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), WORK)
shutil.copy(os.path.join(SRC, "slots.py"), WORK)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
json.dump(cfg, open(os.path.join(WORK, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], WORK]; sys.path.insert(0, WORK)
import bot

SENT = []
bot.call = lambda m, **p: (SENT.append((m, p)) or {"username": "t"})
bot.init_db()
U = 700
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)
def last():
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText") and p.get("chat_id") == U:
            return p
    return {}
def grid(): return (last().get("reply_markup") or {}).get("inline_keyboard", [])
def datas(): return [b["callback_data"] for r in grid() for b in r]
def cb(data):
    bot.handle_callback({"id": "1", "data": data, "message": {"chat": {"id": U}, "message_id": 1}})
def msg(t=None):
    bot.handle_message({"chat": {"id": U}, "from": {"username": "p"}, "text": t})

print("— вигляд календаря")
cb("act:book"); cb("svc:strizhka"); cb("mst:oleh")
g = grid()
ok(len(g[0]) == 3, "шапка з трьох кнопок")
nav = g[0]
ok(nav[1]["callback_data"] == "nop" and any(mn in nav[1]["text"] for mn in bot.MONTHS_NOM),
   "по центру повна назва місяця (%s)" % nav[1]["text"])
ok(any(ms in nav[0]["text"] for ms in bot.MONTHS_SHORT) or nav[0]["text"] == " ",
   "ліва кнопка несе назву попереднього місяця (%s)" % nav[0]["text"])
ok(any(ms in nav[2]["text"] for ms in bot.MONTHS_SHORT) or nav[2]["text"] == " ",
   "права кнопка несе назву наступного місяця (%s)" % nav[2]["text"])
ok([b["text"] for b in g[1]] == ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"], "рядок днів тижня")
ok(all(len(r) == 7 for r in g[2:-1]), "сітка по сім клітинок у рядку")
ok(g[-1][0]["callback_data"].startswith("svc:"), "внизу кнопка «Назад»")
navtext = g[0][1]["text"]
ok(navtext == bot.MONTHS_NOM[bot.now().month - 1],
   "на кнопці лише назва місяця, без року (%s)" % navtext)
ok(any(mn in last()["text"] for mn in bot.MONTHS_NOM),
   "повна назва місяця — у тексті над сіткою")

print("\n— клікаються тільки робочі дні майстра")
days = [d[4:] for d in datas() if d.startswith("day:")]
ok(days, "є доступні дні (%d)" % len(days))
ok(all(datetime.fromisoformat(x).weekday() in (0,1,2,3,4) for x in days),
   "в Олега клікаються лише Пн—Пт")
today = bot.now().date()
ok(all(datetime.fromisoformat(x).date() >= today for x in days), "минулих днів нема")

print("\n— гортання місяців")
cur = g[0][1]["text"]
nxt = [b["callback_data"] for b in g[0] if b["callback_data"].startswith("cal:")]
ok(nxt, "є куди гортати")
cb(nxt[-1])
ok(grid()[0][1]["text"] != cur, "місяць перемкнувся на %s" % grid()[0][1]["text"])
back = [b["callback_data"] for b in grid()[0] if b["callback_data"].startswith("cal:")]
cb(back[0])
ok(grid()[0][1]["text"] == cur, "повернення на попередній місяць")

print("\n— за горизонт і в минуле не пускає")
far = bot.shift_month(today.year, today.month, 9)
cb("cal:%04d-%02d" % far)
ok(grid()[0][1]["text"] not in ("%s %d" % (bot.MONTHS_NOM[far[1]-1], far[0]), bot.MONTHS_NOM[far[1]-1]),
   "стрибок на девʼять місяців уперед обрізано")
past = bot.shift_month(today.year, today.month, -3)
cb("cal:%04d-%02d" % past)
ok(grid()[0][1]["text"] == cur, "стрибок у минуле повертає на поточний місяць")

print("\n— порожні клітинки нічого не ламають")
before = len(SENT)
cb("nop")
ok(len(SENT) - before <= 1, "натискання на «·» не малює нового екрана")

print("\n— вибір дня веде до часу й запис доходить до кінця")
cb("day:" + days[0])
times = [d for d in datas() if d.startswith("time:")]
ok(times, "після дня зʼявились години (%d)" % len(times))
cb(times[0]); msg("Петро"); msg("+380670000000")
ok("Записали" in last()["text"], "запис завершено")

print("\n— зайнятий день зникає, якщо вільних годин не лишилось")
cb("act:book"); cb("svc:strizhka"); cb("mst:iryna")
idays = [d[4:] for d in datas() if d.startswith("day:")]
ok(idays and all(datetime.fromisoformat(x).weekday() in (5,6) for x in idays),
   "в Ірини клікаються лише Сб—Нд (%d)" % len(idays))

print("\n— повернення лишається в тому самому місяці")
cb("act:book"); cb("svc:strizhka"); cb("mst:oleh")
start_month = grid()[0][1]["text"]
nxt = [b["callback_data"] for b in grid()[0] if b["callback_data"].startswith("cal:")][-1]
cb(nxt)
other_month = grid()[0][1]["text"]
ok(other_month != start_month, "перегорнули на %s" % other_month)
odays = [d[4:] for d in datas() if d.startswith("day:")]
ok(odays, "у цьому місяці є вільні дні")
cb("day:" + odays[0])
ok(any(d.startswith("time:") for d in datas()), "відкрились години")
backs = [d for d in datas() if d.startswith("mst:") or d.startswith("svc:")]
cb(backs[-1])
ok(grid()[0][1]["text"] == other_month,
   "назад повернуло в %s, а не в поточний місяць" % grid()[0][1]["text"])

print("\n— новий запис починається з поточного місяця")
cb("act:book"); cb("svc:boroda"); cb("mst:oleh")
ok(grid()[0][1]["text"] == bot.MONTHS_NOM[bot.now().month - 1],
   "свіжий сценарій відкриває поточний місяць")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

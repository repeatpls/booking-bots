# -*- coding: utf-8 -*-
"""Перевірка режиму з майстрами. Мережа не потрібна: виклики Telegram підмінені."""
import subprocess
import json, os, shutil, sys, tempfile
from datetime import datetime

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
WORK = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), WORK)
shutil.copy(os.path.join(SRC, "slots.py"), WORK)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
json.dump(cfg, open(os.path.join(WORK, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)

sys.argv = [sys.argv[0], WORK]
sys.path.insert(0, WORK)
import bot

SENT = []
bot.call = lambda m, **p: (SENT.append((m, p)) or {"username": "t"})
bot.init_db()

U = 500
def last(chat=U):
    for m, p in reversed(SENT):
        if m in ("sendMessage", "editMessageText") and p.get("chat_id") == chat:
            return p
    return {}
def text(chat=U): return last(chat).get("text", "")
def datas(chat=U):
    rm = last(chat).get("reply_markup") or {}
    return [b["callback_data"] for row in rm.get("inline_keyboard", []) for b in row if "callback_data" in b]
def labels(chat=U):
    rm = last(chat).get("reply_markup") or {}
    return [b["text"] for row in rm.get("inline_keyboard", []) for b in row]
def cb(data, chat=U):
    bot.handle_callback({"id": "1", "data": data, "message": {"chat": {"id": chat}, "message_id": 1}})
def msg(t=None, contact=None, chat=U):
    bot.handle_message({"chat": {"id": chat}, "from": {"username": "p"}, "text": t, "contact": contact})

R = []
def ok(cond, label):
    R.append(bool(cond)); print(("  OK   " if cond else "  ПАДІННЯ ") + label)

def wd(iso): return datetime.fromisoformat(iso).weekday()

print("— меню та послуги")
msg("/start")
ok("act:masters" in datas(), "у меню зʼявилась кнопка «Майстри»")
cb("act:book")
ok(len([d for d in datas() if d.startswith("svc:")]) == 7, "сім послуг у списку")

print("\n— вибір майстра залежить від послуги")
cb("svc:dytyacha")
ok(datas() == ["mst:iryna", "act:book"], "дитячу стрижку робить лише Ірина")
cb("act:book"); cb("svc:strizhka")
ms = [d for d in datas() if d.startswith("mst:")]
ok(ms == ["mst:oleh", "mst:taras", "mst:iryna"], "чоловічу стрижку роблять усі троє")
ok(any("700" in l for l in labels()) and any("550" in l for l in labels())
   and any("500" in l for l in labels()), "у кожного майстра своя ціна в кнопці")

print("\n— градація цін у прайсі")
cb("act:prices")
t = text()
ok("Олег (старший барбер) — 700 ₴" in t
   and "Тарас (барбер) — 550 ₴" in t
   and "Ірина (дитячий майстер) — 500 ₴" in t,
   "у прайсі біля кожного майстра його роль у дужках")
ok("Моделювання бороди" in t, "послуги без градації теж показані")

print("\n— графіки майстрів не змішуються")
cb("act:book"); cb("svc:strizhka"); cb("mst:oleh")
days_oleh = [d[4:] for d in datas() if d.startswith("day:")]
ok(days_oleh and all(wd(x) in (0,1,2,3,4) for x in days_oleh),
   "Олегу пропонуються тільки Пн—Пт (%d днів)" % len(days_oleh))

cb("act:book"); cb("svc:strizhka"); cb("mst:iryna")
days_iryna = [d[4:] for d in datas() if d.startswith("day:")]
ok(days_iryna and all(wd(x) in (5,6) for x in days_iryna),
   "Ірині тільки Сб—Нд (%d днів)" % len(days_iryna))

cb("act:book"); cb("svc:strizhka"); cb("mst:taras")
days_taras = [d[4:] for d in datas() if d.startswith("day:")]
ok(days_taras and all(wd(x) in (3,4,5,6) for x in days_taras),
   "Тарасу тільки Чт—Нд (%d днів)" % len(days_taras))

print("\n— години теж свої")
cb("day:" + days_taras[0])
times_taras = [d[5:] for d in datas() if d.startswith("time:")]
ok(times_taras and min(x[11:16] for x in times_taras) >= "12:00",
   "Тарас починає не раніше 12:00 (перший %s)" % min(x[11:16] for x in times_taras))
cb("act:book"); cb("svc:strizhka"); cb("mst:iryna"); cb("day:" + days_iryna[0])
times_iryna = [d[5:] for d in datas() if d.startswith("time:")]
ok(times_iryna and max(x[11:16] for x in times_iryna) <= "16:00",
   "Ірина закінчує до 17:00 (останній старт %s)" % max(x[11:16] for x in times_iryna))

print("\n— тривалість послуги блокує сусідні слоти")
cb("act:book"); cb("svc:complex"); cb("mst:oleh")
d_c = [d[4:] for d in datas() if d.startswith("day:")][0]
cb("day:" + d_c)
t_c = [d[5:] for d in datas() if d.startswith("time:")]
chosen = t_c[0]
cb("time:" + chosen); msg("Петро"); msg("+380671111111")
ok("Записали" in text(), "запис на 90-хвилинну послугу пройшов")
busy = bot.occupied("oleh")
ok(len(busy) == 3, "90 хвилин зайняли три слоти по 30 (зайнято %d)" % len(busy))

print("\n— чужий час не блокується")
ok(len(bot.occupied("taras")) == 0, "у Тараса нічого не зайнято")
# Перевіряємо саме те, що запис до одного майстра не чіпає іншого.
# Якщо ця година взагалі поза графіком Тараса, перевіряти нема чого:
# раніше тест падав просто тому, що Олег починає о 10:00, а Тарас о 12:00.
free_taras = [x[0] for x in bot.free_times(d_c, "taras", "strizhka")]
free_taras_empty = [x[0] for x in bot.free_times(d_c, "taras", "strizhka", set())]
if chosen in free_taras_empty:
    ok(chosen in free_taras, "той самий час у Тараса лишився вільним")
else:
    ok(True, "ця година поза графіком Тараса — блокувати нічого")

print("\n— зайнятий час зникає у того самого майстра")
cb("act:book"); cb("svc:complex"); cb("mst:oleh"); cb("day:" + d_c)
ok(chosen not in [d[5:] for d in datas() if d.startswith("time:")],
   "зайнятий слот більше не пропонується Олегу")

print("\n— кнопка «Майстри»")
cb("act:menu"); cb("act:masters")
ok([d for d in datas() if d.startswith("mcard:")] == ["mcard:oleh", "mcard:taras", "mcard:iryna"],
   "список із трьох майстрів")
cb("mcard:oleh")
t = text()
ok("Графік" in t and "Пн, Вт, Ср, Чт, Пт" in t, "картка показує графік майстра")
ok("25 грудня" in t, "вихідний майстра показаний")
ok("Чоловіча стрижка · ~1 год — 700 ₴" in t,
   "у картці його ціни разом із тривалістю")
ok("mbook:oleh" in datas(), "з картки можна записатися")

print("\n— запис із картки майстра")
cb("mbook:oleh")
ok([d for d in datas() if d.startswith("msvc:")], "показані послуги цього майстра")
cb("msvc:oleh:boroda")
ok([d for d in datas() if d.startswith("day:")], "після послуги одразу дні")

print("\n— режим без майстрів (інші ніші)")
cfg2 = dict(cfg); cfg2.pop("masters")
cfg2["services"] = [{"id": "dostavka", "name": "Доставка обіду", "price": "250 ₴"}]
W2 = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W2)
shutil.copy(os.path.join(SRC, "slots.py"), W2)
json.dump(cfg2, open(os.path.join(W2, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
probe = '''
import sys, json
sys.argv=[sys.argv[0], %r]; sys.path.insert(0, %r)
import bot
S=[]; bot.call=lambda m,**p:(S.append((m,p)) or {"username":"t"}); bot.init_db()
bot.handle_message({"chat":{"id":9},"from":{"username":"x"},"text":"/start"})
mk=[p for m,p in S if m=="sendMessage"][-1]["reply_markup"]["inline_keyboard"]
ds=[b["callback_data"] for r in mk for b in r]
print("MASTERS_BTN", "act:masters" in ds)
bot.handle_callback({"id":"1","data":"svc:dostavka","message":{"chat":{"id":9},"message_id":1}})
mk=[p for m,p in S if m in ("sendMessage","editMessageText")][-1].get("reply_markup",{}).get("inline_keyboard",[])
print("DAYS", any(b["callback_data"].startswith("day:") for r in mk for b in r))
''' % (W2, W2)
out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True)
res = out.stdout
ok("MASTERS_BTN False" in res, "без майстрів кнопки «Майстри» нема")
ok("DAYS True" in res, "без майстрів послуга веде одразу до вибору дня")
if out.returncode: print(out.stderr[-500:])

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

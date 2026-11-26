# -*- coding: utf-8 -*-
import json, os, shutil, sys, tempfile
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
shutil.copy("/home/claude/covers/barbershop.jpg", W + "/cover.jpg")

sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S, UP = [], []
bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t"})
def fake_up(chat_id, path, caption, kb):
    UP.append(path)
    S.append(("sendPhoto", {"chat_id": chat_id, "caption": caption, "reply_markup": kb, "_file": path}))
    return {"photo": [{"file_id": "FID_cover"}]}
bot.upload_photo = fake_up
bot.init_db()

def msg(t): bot.handle_message({"chat": {"id": 8}, "from": {"username": "x"}, "text": t})
def cb(d, photo=False):
    m = {"chat": {"id": 8}, "message_id": 1}
    if photo: m["photo"] = [{"file_id": "x"}]
    bot.handle_callback({"id": "1", "data": d, "message": m})
def photos(): return [p for m, p in S if m == "sendPhoto"]

print("— /start")
msg("/start")
ph = photos()
ok(len(ph) == 1, "старт прийшов картинкою")
ok("Барбершоп ЛЕЗО" in ph[0]["caption"], "привітання стало підписом")
ds = [b["callback_data"] for r in ph[0]["reply_markup"]["inline_keyboard"] for b in r]
ok("act:book" in ds and "act:masters" in ds, "кнопки меню під картинкою")
ok(len(UP) == 1, "файл вивантажено один раз")

print("\n— повернення в меню без картинки")
S.clear()
cb("act:menu")
ok(not photos(), "кнопка «Назад» не шле обкладинку вдруге")
ok([p for m, p in S if m in ("sendMessage", "editMessageText")], "меню перемальовано текстом")

print("\n— другий /start бере file_id з кешу")
S.clear(); UP.clear()
msg("/start")
ok(len(UP) == 0, "файл удруге не вивантажується")
ok(any(str(p.get("photo", "")).startswith("FID_") for m, p in S if m == "sendPhoto"),
   "картинка пішла за збереженим номером")

print("\n— без cover.jpg нічого не ламається")
W2 = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W2)
shutil.copy(os.path.join(SRC, "slots.py"), W2)
json.dump(c, open(W2 + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
import subprocess
probe = ("import sys\nsys.argv=[sys.argv[0],%r];sys.path.insert(0,%r)\nimport bot\n"
         "S=[];bot.call=lambda m,**p:(S.append((m,p)) or {'username':'t'});bot.init_db()\n"
         "bot.handle_message({'chat':{'id':8},'from':{'username':'x'},'text':'/start'})\n"
         "print('PHOTO', any(m=='sendPhoto' for m,_ in S))\n"
         "print('TEXT', any(m=='sendMessage' for m,_ in S))\n") % (W2, W2)
o = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True)
ok("PHOTO False" in o.stdout and "TEXT True" in o.stdout, "без обкладинки старт іде текстом")
if o.returncode: print(o.stderr[-300:])

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

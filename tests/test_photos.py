# -*- coding: utf-8 -*-
"""Фото майстрів: стрічка карток, кеш file_id, відкіт без фото."""
import subprocess
import json, os, shutil, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
WORK = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), WORK)
shutil.copy(os.path.join(SRC, "slots.py"), WORK)
cfg = json.load(open(os.path.join(SRC, "config.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
json.dump(cfg, open(os.path.join(WORK, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)

# кладемо «фото» тільки двом майстрам із трьох
os.makedirs(os.path.join(WORK, "photos"))
for mid in ("oleh", "taras"):
    open(os.path.join(WORK, "photos", mid + ".jpg"), "wb").write(b"\xff\xd8\xff" + os.urandom(64))

sys.argv = [sys.argv[0], WORK]
sys.path.insert(0, WORK)
import bot

SENT, UPLOADS = [], []
bot.call = lambda m, **p: (SENT.append((m, p)) or {"username": "t"})
def fake_upload(chat_id, path, caption, keyboard):
    UPLOADS.append(path)
    SENT.append(("sendPhoto", {"chat_id": chat_id, "caption": caption,
                               "reply_markup": keyboard, "_file": path}))
    return {"photo": [{"file_id": "FID_" + os.path.basename(path)}]}
bot.upload_photo = fake_upload
bot.init_db()

U = 600
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)
def cb(data, photo=False):
    m = {"chat": {"id": U}, "message_id": 1}
    if photo: m["photo"] = [{"file_id": "x"}]
    bot.handle_callback({"id": "1", "data": data, "message": m})
def photos_sent(): return [p for m, p in SENT if m == "sendPhoto"]

print("— список майстрів: одне повідомлення з кнопками")
SENT.clear()
cb("act:masters")
ok(not photos_sent(), "у списку фото не надсилаються")
msgs = [p for m, p in SENT if m in ("sendMessage", "editMessageText")]
ok(len(msgs) == 1, "список — рівно одне повідомлення (%d)" % len(msgs))
ds = [b["callback_data"] for r in msgs[0]["reply_markup"]["inline_keyboard"] for b in r]
ok(ds == ["mcard:oleh", "mcard:taras", "mcard:iryna", "act:menu"],
   "кнопка на кожного майстра плюс «Назад»")

print("\n— картка майстра")
SENT.clear()
cb("mcard:oleh")
ph = photos_sent()
ok(len(ph) == 1, "картка Олега пішла з фото")
cap = ph[0].get("caption", "")
ok("Графік" in cap and "Чоловіча стрижка · ~1 год — 700 ₴" in cap,
   "у підписі графік, ціни й тривалість")
SENT.clear()
cb("mcard:iryna")
ok(not photos_sent(), "картка Ірини без фото — звичайний текст")
ok("Ірина" in [p for m, p in SENT if m in ("sendMessage", "editMessageText")][-1]["text"],
   "текст картки на місці")

print("\n— натискання з повідомлення-фото не ламає екран")
SENT.clear()
cb("act:menu", photo=True)
kinds = [m for m, p in SENT if m in ("sendMessage", "editMessageText")]
ok("editMessageText" not in kinds and "sendMessage" in kinds,
   "з фото бот шле нове повідомлення, а не редагує")

print("\n— запис із картки й далі працює")
SENT.clear()
cb("mbook:oleh", photo=True)
last = [p for m, p in SENT if m in ("sendMessage", "editMessageText")][-1]
ds = [b["callback_data"] for r in last["reply_markup"]["inline_keyboard"] for b in r]
ok(any(d.startswith("msvc:oleh:") for d in ds), "список послуг майстра відкрився")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

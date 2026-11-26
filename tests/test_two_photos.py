# -*- coding: utf-8 -*-
"""Дві картинки в картці майстра: портрет і аркуш робіт."""
import json, os, shutil, sys, tempfile
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
c = json.load(open("/mnt/user-data/uploads/bot lids/clients/kontur/config.json", encoding="utf-8"))
c["token"] = "000:TEST"; c["admin_ids"] = [1]
for i, m in enumerate(c["masters"], 1):
    m.pop("photo", None)
    m["photos"] = ["photos/majster%d.jpg" % i, "photos/majster%d_roboty.jpg" % i]
# третьому майстру аркуша не кладемо — має лишитись одне фото
c["masters"][2]["photos"] = ["photos/majster3.jpg"]
json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)

os.makedirs(W + "/photos")
for i in (1, 2, 3):
    open(W + "/photos/majster%d.jpg" % i, "wb").write(b"\xff\xd8\xff" + os.urandom(32))
for i in (1, 2):
    open(W + "/photos/majster%d_roboty.jpg" % i, "wb").write(b"\xff\xd8\xff" + os.urandom(32))

sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
S, UP = [], []
def fake_call(m, **p):
    S.append((m, p))
    if m == "sendMediaGroup":
        return [{"photo": [{"file_id": "FID_%d" % i}]} for i in range(len(p.get("media", [])))]
    return {"username": "t"}
bot.call = fake_call
def up(chat_id, path, caption, kb):
    UP.append(os.path.basename(path))
    S.append(("sendPhoto", {"chat_id": chat_id, "caption": caption, "reply_markup": kb, "_f": os.path.basename(path)}))
    return {"photo": [{"file_id": "FID_" + os.path.basename(path)}]}
bot.upload_photo = up

def fake_multipart(method, fields, files):
    import json as _j
    for name, (fname, blob) in files.items():
        UP.append(fname)
    S.append((method, {"chat_id": fields.get("chat_id"),
                       "media": _j.loads(fields["media"]) if "media" in fields else None}))
    n = len(_j.loads(fields["media"])) if "media" in fields else 1
    return [{"photo": [{"file_id": "FID_%d" % i}]} for i in range(n)]
bot.multipart = fake_multipart
bot.init_db()
def cb(d):
    bot.handle_callback({"id":"1","data":d,"message":{"chat":{"id":4},"message_id":1}})
def photos(): return [p for m, p in S if m == "sendPhoto"]

print("— майстер із двома фото: альбом одним повідомленням")
S.clear(); UP.clear()
cb("mcard:majster1")
albums = [p for m, p in S if m == "sendMediaGroup"]
ok(len(albums) == 1, "рівно один альбом (%d)" % len(albums))
media = albums[0]["media"]
ok(len(media) == 2, "в альбомі дві картинки (%d)" % len(media))
ok(UP == ["majster1.jpg", "majster1_roboty.jpg"], "спершу портрет, потім аркуш робіт")
ok("Остап" in media[0]["caption"] and "Графік" in media[0]["caption"],
   "опис із графіком — підписом альбому")
ok("caption" not in media[1], "на другій картинці підпису нема")
btns = [p for m, p in S if m == "sendMessage" and p.get("reply_markup")]
ok(len(btns) == 1, "кнопки окремим повідомленням")
ds = [b["callback_data"] for r in btns[0]["reply_markup"]["inline_keyboard"] for b in r]
ok("mbook:majster1" in ds and "act:masters" in ds, "обидві кнопки на місці")

print("\n— майстер з одним фото")
S.clear(); UP.clear()
cb("mcard:majster3")
ph = photos()
ok(len(ph) == 1, "одна картинка (%d)" % len(ph))
last = [p for m, p in S if m in ("sendMessage", "editMessageText", "sendPhoto")][-1]
ds = [b["callback_data"] for r in (last.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
ok("mbook:majster3" in ds, "кнопки все одно на місці")

print("\n— повторний показ бере file_id з кешу")
S.clear(); UP.clear()
cb("mcard:majster1")
ok(UP == [], "жоден файл не вивантажується вдруге")
al = [p for m, p in S if m == "sendMediaGroup"]
ok(al and len(al[0]["media"]) == 2, "альбом усе одно з двох картинок")
ok(all(str(x["media"]).startswith("FID_") for x in al[0]["media"]),
   "обидві пішли за збереженими номерами")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)

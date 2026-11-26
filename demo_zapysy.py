# -*- coding: utf-8 -*-
"""Демо-записи на 26–30 листопада для всіх ботів.

Запускати на сервері:  python3 /opt/botlids/demo_zapysy.py

Скрипт спершу прибирає всі записи цих п'яти днів, потім ставить нові.
Нагадування й опитування по них одразу позначаються надісланими: клієнти
вигадані, і бот не повинен стукати в неіснуючі чати.
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "demo_zapysy.json")
CLIENTS = os.path.join(HERE, "clients")


def columns(conn, table):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def put(name, rows, a, b):
    db = os.path.join(CLIENTS, name, "zayavky.db")
    if not os.path.isfile(db):
        print("  %-8s бази немає, пропускаю" % name)
        return
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        have = columns(conn, "bookings")
        old = conn.execute(
            "SELECT id FROM bookings WHERE slot >= ? AND slot < ?",
            (a, b)).fetchall()
        ids = [r["id"] for r in old]
        if ids:
            marks = ",".join("?" * len(ids))
            conn.execute("DELETE FROM reminders WHERE booking_id IN (%s)" % marks, ids)
            conn.execute("DELETE FROM bookings WHERE id IN (%s)" % marks, ids)

        before = []
        try:
            cfg = json.load(open(os.path.join(CLIENTS, name, "config.json"),
                                 encoding="utf-8"))
            before = [int(x) for x in (cfg.get("reminders") or {}).get("before", [])]
        except Exception:
            pass

        added = 0
        for r in rows:
            r = dict(r)
            if "asked_at" in have:          # щоб бот не питав вигаданих про враження
                r["asked_at"] = r["created"]
            keys = [k for k in r if k in have]
            conn.execute(
                "INSERT INTO bookings(%s) VALUES(%s)"
                % (",".join(keys), ",".join("?" * len(keys))),
                [r[k] for k in keys])
            bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for m in before:                # щоб не полетіли нагадування
                conn.execute("INSERT OR IGNORE INTO reminders(booking_id, minutes,"
                             " sent_at) VALUES(?,?,?)", (bid, m, r["created"]))
            added += 1
        conn.commit()
        print("  %-8s прибрано %d, додано %d" % (name, len(ids), added))
    finally:
        conn.close()


def main():
    if not os.path.isfile(DATA):
        print("Немає файла %s — поклади його поруч зі скриптом." % DATA)
        return 1
    payload = json.load(open(DATA, encoding="utf-8"))
    a, b = payload["days"][0], payload["days"][1] + "T23:59"
    print("Демо-записи з %s по %s" % (a, payload["days"][1]))
    for name, rows in payload["clients"].items():
        put(name, rows, a, b)
    print("Готово. Перезапускати бота не треба — він читає базу на льоту.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

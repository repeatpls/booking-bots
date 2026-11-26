#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускає ботів усіх клієнтів одразу і стежить, щоб вони не падали.

Структура:
    bot.py
    run_all.py
    clients/
        lezo/config.json       <- Барбершоп ЛЕЗО
        bronza/config.json     <- Салон Бронза
        ...

Запуск:   python run_all.py
Зупинка:  Ctrl+C — зупинить усіх разом.

Кожен клієнт працює окремим процесом. Якщо в одного щось зламається,
решта продовжать працювати, а впалого цей скрипт підніме сам.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS_DIR = os.path.join(HERE, "clients")
BOT = os.path.join(HERE, "bot.py")
RESTART_DELAY = 10          # секунд перед перезапуском
MAX_FAST_FAILS = 5          # скільки разів поспіль пробувати, якщо падає одразу


def log(msg):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def find_clients():
    if not os.path.isdir(CLIENTS_DIR):
        sys.exit("Нема теки clients/. Створіть її та покладіть усередину\n"
                 "теку на кожного клієнта з файлом config.json.")
    out = []
    for name in sorted(os.listdir(CLIENTS_DIR)):
        path = os.path.join(CLIENTS_DIR, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "config.json")):
            out.append((name, path))
    return out


def start(path):
    return subprocess.Popen([sys.executable, BOT, path])


def main():
    clients = find_clients()
    if not clients:
        sys.exit("У теці clients/ нема жодного клієнта з config.json.")

    log("Клієнтів знайдено: %d — %s"
        % (len(clients), ", ".join(n for n, _ in clients)))

    procs = {}      # name -> (Popen, path, fails, last_start)
    for name, path in clients:
        procs[name] = [start(path), path, 0, time.time()]
        log("запущено %s" % name)

    try:
        while True:
            time.sleep(3)
            for name, rec in procs.items():
                proc, path, fails, started = rec
                if proc.poll() is None:
                    if time.time() - started > 60 and fails:
                        rec[2] = 0      # протримався хвилину — лічильник збиваємо
                    continue

                lived = time.time() - started
                if lived < 15:
                    rec[2] = fails + 1
                    if rec[2] >= MAX_FAST_FAILS:
                        log("%s падає одразу після старту %d разів — "
                            "більше не піднімаю. Перевірте його config.json"
                            % (name, rec[2]))
                        continue
                log("%s зупинився (код %s), перезапуск через %d с"
                    % (name, proc.returncode, RESTART_DELAY))
                time.sleep(RESTART_DELAY)
                rec[0] = start(path)
                rec[3] = time.time()
                log("%s піднято" % name)
    except KeyboardInterrupt:
        log("Зупиняю всіх...")
        for name, rec in procs.items():
            if rec[0].poll() is None:
                rec[0].send_signal(signal.SIGTERM)
        for name, rec in procs.items():
            try:
                rec[0].wait(timeout=10)
            except Exception:
                rec[0].kill()
        log("Готово.")


if __name__ == "__main__":
    main()

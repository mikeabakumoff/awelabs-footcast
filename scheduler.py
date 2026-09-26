import sys
import os


if sys.platform == "win32":
    os.system("chcp 65001 > nul")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import time
import logging


if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import requests
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scheduler.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

BANGKOK_OFFSET = timedelta(hours=7)
RUN_HOUR   = 9
RUN_MINUTE = 0

SCRIPTS = [
    ("collect_data.py",     "Новые результаты матчей"),
    ("collect_xg.py",       "xG данные (Understat)"),
    ("collect_injuries.py", "Травмы и таблицы (API-Football)"),
    ("collect_events.py",   "Голы и минуты (API-Football)"),
    ("build_features.py",   "Обновление признаков"),
    ("train_models.py",     "Переобучение моделей"),
    ("collect_lineups_predictions.py", "Составы и прогнозы (API-Football)"),
    ("clear_cache.py",      "Очистка кеша"),
    ("run_bot.py",          "Прогнозы + Telegram"),
]


def load_env():
    env = {}
    try:
        with open("env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


_env = load_env()
TELEGRAM_TOKEN = _env.get("TELEGRAM_TOKEN", "")
OWNER_CHAT_ID  = _env.get("OWNER_CHAT_ID", "")


def notify(text):
    if not TELEGRAM_TOKEN or not OWNER_CHAT_ID:
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        requests.post(url, json={
            "chat_id": OWNER_CHAT_ID, "text": text, "parse_mode": "HTML"
        }, timeout=10)
    except Exception:
        pass


def bangkok_now():
    return datetime.now(timezone.utc) + BANGKOK_OFFSET


def seconds_until_next_run():
    now    = bangkok_now()
    target = now.replace(hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_script(script, description):
    if not Path(script).exists():
        log.warning("  " + script + " не найден - пропускаю")
        return True
    log.info(">> " + description)
    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        elapsed = round(time.time() - start, 1)
        if result.returncode == 0:
            log.info("  OK: " + description + " за " + str(elapsed) + "с")
            return True
        err = (result.stderr or result.stdout or "")[-400:]
        log.error("  FAIL: " + description)
        if err:
            log.error("  " + err)
        return False
    except subprocess.TimeoutExpired:
        log.error("  FAIL: " + description + " - таймаут")
        return False
    except Exception as e:
        log.error("  FAIL: " + description + " - " + str(e))
        return False


def run_chain():
    now_str = bangkok_now().strftime("%d.%m.%Y %H:%M")
    log.info("=" * 55)
    log.info("АВТОЗАПУСК - " + now_str + " Bangkok")
    log.info("=" * 55)
    notify("<b>Запуск бота</b> - " + now_str + " Bangkok")
    results = {}
    for script, description in SCRIPTS:
        results[script] = run_script(script, description)
        time.sleep(2)
    success = sum(1 for v in results.values() if v)
    log.info("Цепочка завершена: " + str(success) + "/" + str(len(SCRIPTS)) + " успешно")
    failed = [desc for s, desc in SCRIPTS if not results.get(s, True)]
    if failed:
        notify("<b>Ошибки:</b> " + ", ".join(failed) + "\nПроверьте scheduler.log")


def main():
    log.info("=" * 55)
    log.info("ПЛАНИРОВЩИК ЗАПУЩЕН")
    log.info("  Запуск каждый день в 09:00 Bangkok (UTC+7)")
    log.info("  Сейчас: " + bangkok_now().strftime("%d.%m.%Y %H:%M") + " Bangkok")
    log.info("=" * 55)
    notify(
        "<b>Планировщик запущен</b>\n"
        "Запуск ежедневно в 09:00 Bangkok\n"
        "Сейчас: " + bangkok_now().strftime("%d.%m.%Y %H:%M")
    )
    while True:
        wait_sec = seconds_until_next_run()
        wait_h   = int(wait_sec // 3600)
        wait_m   = int((wait_sec % 3600) // 60)
        log.info("Следующий запуск через " + str(wait_h) + "ч " + str(wait_m) + "м")
        time.sleep(wait_sec)
        run_chain()


if __name__ == "__main__":
    main()

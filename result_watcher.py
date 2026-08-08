"""
result_watcher.py - Запускает проверку результатов каждые 30 минут
Запускать: python result_watcher.py (оставить работать в фоне)
Или через Task Scheduler: запустить один раз при старте ПК
"""
import time
import subprocess
import sys
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("result_watcher.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

BANGKOK = timedelta(hours=7)
CHECK_INTERVAL = 30 * 60  # каждые 30 минут


def bangkok_now():
    return datetime.now(timezone.utc) + BANGKOK


def run_check():
    try:
        result = subprocess.run(
            [sys.executable, "check_results.py"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode == 0:
            log.info("check_results.py: OK")
        else:
            log.error("check_results.py: FAIL")
            if result.stderr:
                log.error(result.stderr[-300:])
    except Exception as e:
        log.error(f"check_results error: {e}")


def main():
    log.info("=" * 50)
    log.info("RESULT WATCHER ЗАПУЩЕН")
    log.info(f"Проверка каждые 30 минут")
    log.info(f"Сейчас Bangkok: {bangkok_now().strftime('%d.%m.%Y %H:%M')}")
    log.info("=" * 50)

    while True:
        log.info(f"Запускаю проверку результатов...")
        run_check()
        log.info(f"Следующая проверка через 30 минут")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

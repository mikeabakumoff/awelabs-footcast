import sys
import os
import time
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path


if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("setup.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

BANGKOK_OFFSET = timedelta(hours=7)

STEPS = [
    ("collect_data.py",     "Сборка данных клубных лиг (football-data.co.uk)"),
    ("collect_xg.py",       "Сборка xG данных (Understat)"),
    ("collect_injuries.py", "Травмы и таблицы (API-Football)"),
    ("collect_events.py",   "Голы и минуты (API-Football)"),
    ("build_features.py",   "Построение признаков"),
    ("collect_ucl.py",      "Сборка данных ЛЧ/ЛЕ + UEFA коэффициенты"),
    ("train_models.py",     "Обучение моделей"),
    ("collect_lineups_predictions.py", "Составы и прогнозы (API-Football)"),
    ("clear_cache.py",      "Очистка кеша агентов"),
]


STEP_TIMEOUTS = {
    "build_features.py": 2400,
    "collect_ucl.py":    600,
    "train_models.py":   1800,
}

def run_step(script, description):
    if not Path(script).exists():
        log.warning(f"  {script} не найден - пропускаю")
        return True
    log.info(f">> {description}")
    start = time.time()
    timeout = STEP_TIMEOUTS.get(script, 1200)
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
        elapsed = round(time.time() - start, 1)
        if result.returncode == 0:
            log.info(f"  OK: {description} за {elapsed}с")
            return True
        else:
            err = (result.stderr or result.stdout or "")[-500:]
            log.error(f"  FAIL: {description}")
            if err:
                log.error(f"  {err}")
            return False
    except subprocess.TimeoutExpired:
        log.error(f"  FAIL: {description} - таймаут (>{timeout//60} мин)")
        return False
    except Exception as e:
        log.error(f"  FAIL: {description} - {e}")
        return False


def main():
    now = (datetime.now(timezone.utc) + BANGKOK_OFFSET).strftime("%d.%m.%Y %H:%M")
    log.info("=" * 55)
    log.info(f"ПОЛНАЯ НАСТРОЙКА/ОБНОВЛЕНИЕ - {now} Bangkok")
    log.info("=" * 55)

    results = {}
    for script, description in STEPS:
        ok = run_step(script, description)
        results[script] = ok
        time.sleep(2)

    success = sum(1 for v in results.values() if v)
    log.info("=" * 55)
    log.info(f"Готово: {success}/{len(STEPS)} шагов успешно")

    failed = [desc for s, desc in STEPS if not results.get(s, True)]
    if failed:
        log.warning("Не выполнено: " + ", ".join(failed))
        log.warning("Проверьте setup.log")
    else:
        log.info("Все шаги выполнены успешно!")
        log.info("Теперь запустите: python run_bot.py")


if __name__ == "__main__":
    main()

"""
worker.py — scheduler en background que dispara run_sync periódicamente.

Se arranca desde web.py al iniciar la app Flask (ver create_app / start_worker).
Usa APScheduler en modo background (un thread aparte, no bloquea el servidor web).
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from .gmail_sync import run_sync, SYNC_INTERVAL_MINUTES
from .cleanup import purge_old_delivered
from .mqtt_publish import publish_all_packages

_scheduler = None


def start_worker(db_path: str, gmail_search_fn, gmail_get_thread_fn):
    """
    Arranca el scheduler en background. Idempotente: si ya está corriendo,
    no lo vuelve a arrancar (evita duplicar el job si Flask recarga el
    módulo en modo debug).
    """
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    def _job():
        try:
            run_sync(db_path, gmail_search_fn, gmail_get_thread_fn)
        except RuntimeError as e:
            # Típicamente falta el token.json (OAuth no autorizado todavía).
            print(f"[worker] Escaneo omitido: {e}")
        purge_old_delivered(db_path)
        publish_all_packages(db_path)

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=_job,
        trigger="interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="gmail_sync_job",
        next_run_time=None,  # no disparar inmediatamente al arrancar; el primer
                              # escaneo ya se hace a demanda o lo dispara el usuario
    )
    _scheduler.start()
    print(f"[worker] Scheduler arrancado, intervalo={SYNC_INTERVAL_MINUTES}min")
    return _scheduler


def stop_worker():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

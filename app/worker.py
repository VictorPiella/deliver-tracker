"""
worker.py — scheduler en background que dispara run_sync periódicamente.

Se arranca desde web.py al iniciar la app Flask (ver create_app / start_worker).
Usa APScheduler en modo background (un thread aparte, no bloquea el servidor web).
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from .gmail_sync import run_sync, SYNC_INTERVAL_MINUTES

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

    if gmail_search_fn is None:
        print("[worker] Gmail no conectado (sin gmail_search_fn) — worker no arrancado.")
        return None

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=lambda: run_sync(db_path, gmail_search_fn, gmail_get_thread_fn),
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

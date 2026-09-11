"""
worker.py — scheduler en background que dispara run_sync periódicamente.

Se arranca desde web.py al iniciar la app Flask (ver create_app / start_worker).
Usa APScheduler en modo background (un thread aparte, no bloquea el servidor web).
"""
from datetime import timezone

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

    _scheduler = BackgroundScheduler(daemon=True, timezone=timezone.utc)
    _scheduler.add_job(
        func=_job,
        trigger="interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="gmail_sync_job",
        # OJO: no pasar next_run_time=None aquí. En APScheduler eso no significa
        # "usa el default", significa "job en pausa" — el job se creaba parado y
        # la sincronización horaria no llegaba a dispararse nunca. Omitiéndolo,
        # el trigger de intervalo ya arranca en now + SYNC_INTERVAL_MINUTES, que
        # es justo el comportamiento que se buscaba (no escanear al arrancar).
        max_instances=1,      # si un escaneo se alarga, no solapar con el siguiente
        coalesce=True,        # tras un parón, un único escaneo de recuperación, no N
        misfire_grace_time=300,
    )
    _scheduler.start()
    job = _scheduler.get_job("gmail_sync_job")
    print(f"[worker] Scheduler arrancado, intervalo={SYNC_INTERVAL_MINUTES}min, "
          f"primer escaneo={job.next_run_time}")
    return _scheduler


def stop_worker():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

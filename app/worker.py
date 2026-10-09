"""
worker.py — scheduler en background que dispara run_sync periódicamente.

Se arranca desde web.py al iniciar la app Flask (ver create_app / start_worker).
Usa APScheduler en modo background (un thread aparte, no bloquea el servidor web).
"""
import threading
from datetime import timezone

from apscheduler.schedulers.background import BackgroundScheduler
from .gmail_sync import run_sync, SYNC_INTERVAL_MINUTES
from .backup import backup_if_due
from .cleanup import purge_old_delivered, vaciar_papelera_vieja
from .mqtt_publish import publish_all_packages
from .timeutils import utcnow

_scheduler = None

# Estado del escaneo en curso. El escaneo se lanza en un hilo aparte para que
# el boton "Escanear" conteste al instante: tras un paron largo la ventana
# adaptativa puede pedir un ano de correo, y eso tarda mucho mas que los 120s
# de timeout de gunicorn — la peticion moria y, como el marcador de ultimo
# escaneo se pone al final, el reintento fallaba igual una y otra vez.
_sync_lock = threading.Lock()
_sync_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_summary": None,
    "last_error": None,
}


def sync_state() -> dict:
    return dict(_sync_state)


def trigger_sync_now(db_path: str, gmail_search_fn, gmail_get_thread_fn, log=print) -> bool:
    """
    Lanza un escaneo en segundo plano. Devuelve False si ya habia uno en marcha
    (el candado tambien impide que el worker horario y el boton se pisen).
    """
    if not _sync_lock.acquire(blocking=False):
        return False

    def _ejecutar():
        _sync_state.update(running=True, started_at=utcnow(),
                           finished_at=None, last_error=None)
        try:
            resumen = run_sync(db_path, gmail_search_fn, gmail_get_thread_fn, log=log)
            purge_old_delivered(db_path, log=log)
            # Y despues el borrado definitivo de lo que lleva demasiado en la
            # papelera. En este orden a proposito: lo que se archiva hoy empieza
            # a contar hoy, no se archiva y se borra en la misma pasada.
            vaciar_papelera_vieja(db_path, log=log)
            publish_all_packages(db_path, log=log)
            # Como mucho una copia al día, justo después de que la base cambie.
            backup_if_due(db_path, log=log)
            _sync_state["last_summary"] = resumen
        except Exception as e:
            # Tipicamente falta token.json, o el token ha caducado.
            _sync_state["last_error"] = str(e)
            log(f"[sync] Escaneo fallido: {e}")
        finally:
            _sync_state.update(running=False, finished_at=utcnow())
            _sync_lock.release()

    hilo = threading.Thread(target=_ejecutar, name="sync", daemon=True)
    hilo.start()
    _sync_state["thread"] = hilo
    return True


def wait_for_sync(timeout: float = 30.0) -> bool:
    """Espera a que acabe el escaneo en curso. Pensado para los tests."""
    hilo = _sync_state.get("thread")
    if hilo is None:
        return True
    hilo.join(timeout)
    return not hilo.is_alive()


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
        # Mismo camino que el botón del panel: comparten candado, así el
        # escaneo horario y uno lanzado a mano no se pisan.
        if not trigger_sync_now(db_path, gmail_search_fn, gmail_get_thread_fn):
            print("[worker] Ya hay un escaneo en marcha, se omite este ciclo")

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

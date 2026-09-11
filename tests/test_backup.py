"""
Copias de seguridad de la base.

La base es la única copia duradera del histórico: los emails de origen puede que
ya no existan. Sin copias, un fichero corrupto se lleva todo por delante.
"""
import os
from datetime import timedelta

import pytest

from app.backup import LAST_BACKUP_KEY, backup_dir, backup_if_due, make_backup
from app.models import Package, get_session, set_setting
from app.sync import ingest_event
from app.timeutils import utcnow

from conftest import EVENT_DATE


@pytest.fixture(autouse=True)
def backups_activos(monkeypatch):
    import app.backup as backup
    monkeypatch.setattr(backup, "BACKUP_ENABLED", True)


def sembrar(db_path):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "amazon", "order_id": "408-1-1", "package_id": "ENVIO1",
        "status": "shipped", "status_label_raw": "Enviado", "title": "Algo",
        "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
    })
    s.close()


class TestMakeBackup:
    def test_crea_una_copia_utilizable(self, db_path):
        sembrar(db_path)
        destino = make_backup(db_path, log=lambda *a: None)

        assert destino is not None and os.path.exists(destino)
        # La copia tiene que poder abrirse y traer los mismos datos.
        copia = get_session(destino)
        assert copia.query(Package).one().external_package_id == "ENVIO1"
        copia.close()

    def test_va_al_subdirectorio_backups(self, db_path):
        sembrar(db_path)
        destino = make_backup(db_path, log=lambda *a: None)
        assert os.path.dirname(destino) == backup_dir(db_path)

    def test_repetirla_el_mismo_dia_no_falla(self, db_path):
        # VACUUM INTO revienta si el fichero destino ya existe.
        sembrar(db_path)
        assert make_backup(db_path, log=lambda *a: None) is not None
        assert make_backup(db_path, log=lambda *a: None) is not None

    def test_conserva_solo_las_ultimas(self, db_path):
        sembrar(db_path)
        make_backup(db_path, log=lambda *a: None)

        # Copias antiguas simuladas.
        d = backup_dir(db_path)
        for dia in range(1, 12):
            open(os.path.join(d, f"packages-2026010{dia:02d}.db"), "w").close()

        make_backup(db_path, keep=3, log=lambda *a: None)
        quedan = [f for f in os.listdir(d) if f.startswith("packages-")]
        assert len(quedan) == 3

    def test_un_fallo_no_propaga_excepcion(self, db_path):
        # Mejor perder una copia que tumbar el escaneo entero. Se fuerza el
        # fallo poniendo un FICHERO donde deberia ir el directorio de copias.
        sembrar(db_path)
        with open(backup_dir(db_path), "w") as f:
            f.write("no soy un directorio")

        avisos = []
        assert make_backup(db_path, log=avisos.append) is None
        assert any("omitida" in a for a in avisos)


class TestBackupIfDue:
    def test_la_primera_vez_hace_copia(self, db_path):
        sembrar(db_path)
        assert backup_if_due(db_path, log=lambda *a: None) is not None

    def test_no_repite_en_el_mismo_dia(self, db_path):
        sembrar(db_path)
        assert backup_if_due(db_path, log=lambda *a: None) is not None
        assert backup_if_due(db_path, log=lambda *a: None) is None

    def test_al_dia_siguiente_vuelve_a_copiar(self, db_path):
        sembrar(db_path)
        backup_if_due(db_path, log=lambda *a: None)

        s = get_session(db_path)
        set_setting(s, LAST_BACKUP_KEY, (utcnow() - timedelta(days=2)).isoformat())
        s.commit()
        s.close()

        assert backup_if_due(db_path, log=lambda *a: None) is not None

    def test_se_puede_desactivar(self, db_path, monkeypatch):
        import app.backup as backup
        monkeypatch.setattr(backup, "BACKUP_ENABLED", False)
        sembrar(db_path)
        assert backup.backup_if_due(db_path, log=lambda *a: None) is None

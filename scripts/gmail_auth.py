"""
scripts/gmail_auth.py — autorización OAuth inicial de Gmail.

Ejecutar UNA VEZ en local (no dentro del container Docker: necesita abrir un
navegador, y el container en Unraid es headless).

Uso (desde la raíz del repo, con las dependencias instaladas):
    python -m scripts.gmail_auth

Requiere credentials.json (descargado desde Google Cloud Console, tipo de
credencial "OAuth client ID" / "Desktop app") en ./data/credentials.json
(o la ruta indicada por GMAIL_CREDENTIALS_PATH). Genera token.json en el
mismo directorio. Copia ambos ficheros al volumen /data del container en
producción (Unraid) — ese es el volumen que ya monta docker-compose.yml.
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

from app.gmail_oauth import SCOPES, CREDENTIALS_PATH, TOKEN_PATH


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        raise SystemExit(
            f"No se encontró {CREDENTIALS_PATH}.\n"
            "Descárgalo desde Google Cloud Console (OAuth client ID, tipo 'Desktop app') "
            "y colócalo en esa ruta antes de continuar."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    print(f"Autorización completa. Token guardado en {TOKEN_PATH}")


if __name__ == "__main__":
    main()

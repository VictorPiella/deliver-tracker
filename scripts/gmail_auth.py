"""
scripts/gmail_auth.py — autorización OAuth inicial de Gmail.

Se ejecuta UNA VEZ. Genera data/token.json; a partir de ahí la app sólo refresca
el access token, lo que no necesita navegador y por tanto funciona headless en
Unraid.

Dos formas de ejecutarlo, ambas válidas:

  A) Dentro de Docker (recomendado en Windows — no necesitas Python en el host):

       docker compose run --rm -p 8080:8080 deliver-tracker python -m scripts.gmail_auth

     El script levanta el servidor de redirección dentro del container escuchando
     en 0.0.0.0:8080, pero anuncia a Google la URL http://localhost:8080/. Como
     ese puerto está publicado, el navegador de Windows completa el círculo.

  B) En el host directamente:

       py -m venv .venv
       .venv\\Scripts\\pip install -r requirements.txt
       .venv\\Scripts\\python -m scripts.gmail_auth

Requiere credentials.json (Google Cloud Console -> OAuth client ID, tipo
"Desktop app") en ./data/credentials.json, o donde apunte GMAIL_CREDENTIALS_PATH.
"""
import argparse
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from app.gmail_oauth import SCOPES, CREDENTIALS_PATH, TOKEN_PATH

DEFAULT_PORT = 8080


def running_in_container() -> bool:
    """
    Detecta Docker para decidir si abrir el navegador y a qué interfaz atarse.
    /.dockerenv lo crea el propio Docker; la variable permite forzarlo a mano.
    """
    if os.environ.get("RUNNING_IN_DOCKER", "").lower() in ("1", "true", "yes"):
        return True
    return os.path.exists("/.dockerenv")


def main():
    parser = argparse.ArgumentParser(description="Autorización OAuth inicial de Gmail.")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("OAUTH_PORT", DEFAULT_PORT)),
        help=f"Puerto del servidor de redirección (por defecto {DEFAULT_PORT}). "
             "Dentro de Docker debe coincidir con el puerto publicado.",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="No intentar abrir el navegador; sólo imprimir la URL. "
             "Se activa solo cuando se detecta Docker.",
    )
    args = parser.parse_args()

    if not os.path.exists(CREDENTIALS_PATH):
        sys.exit(
            f"No se encontró {CREDENTIALS_PATH}.\n\n"
            "Descárgalo desde Google Cloud Console (APIs y servicios -> Credenciales ->\n"
            "Crear credenciales -> ID de cliente de OAuth -> tipo 'Aplicación de escritorio')\n"
            "y guárdalo en esa ruta antes de continuar."
        )

    in_docker = running_in_container()
    open_browser = not (args.no_browser or in_docker)

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)

    print()
    print("=" * 72)
    if in_docker:
        print("Ejecutando dentro de Docker: el navegador no se abre solo.")
        print(f"Asegúrate de que el puerto {args.port} está publicado")
        print(f"(docker compose run --rm -p {args.port}:{args.port} ...).")
    print("Se abrirá/imprimirá una URL de Google. Autoriza con la cuenta de Gmail")
    print("que recibe los emails de Amazon/AliExpress.")
    print("=" * 72)
    print()

    creds = flow.run_local_server(
        # host se usa para construir el redirect_uri que ve Google; bind_addr es
        # la interfaz donde escucha de verdad. Dentro del container tienen que
        # ser distintos: Google (y el navegador) hablan de "localhost" desde el
        # punto de vista de Windows, pero el servidor debe aceptar la conexión
        # que llega reenviada desde fuera del container.
        host="localhost",
        bind_addr="0.0.0.0" if in_docker else None,
        port=args.port,
        open_browser=open_browser,
        access_type="offline",   # imprescindible para obtener refresh_token
        prompt="consent",        # fuerza a que Google lo devuelva aunque ya hubiera consentido
        authorization_prompt_message="Abre esta URL en tu navegador:\n\n    {url}\n",
        success_message="Listo. Ya puedes cerrar esta pestaña y volver a la terminal.",
    )

    if not creds.refresh_token:
        sys.exit(
            "Google no devolvió refresh_token. Sin él la app dejaría de funcionar\n"
            "en cuanto caducara el access token (una hora). Revoca el acceso en\n"
            "https://myaccount.google.com/permissions y vuelve a ejecutar esto."
        )

    os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print()
    print(f"Autorización completa. Token guardado en {TOKEN_PATH}")
    print()
    print("Siguientes pasos:")
    print("  1. Pon USE_MOCK_GMAIL=false en tu .env")
    print("  2. docker compose up -d --build")
    print("  3. Para Unraid, copia data/credentials.json y data/token.json a")
    print("     /mnt/user/appdata/deliver-tracker/")


if __name__ == "__main__":
    main()

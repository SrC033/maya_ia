""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import webview  # pywebview

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
HTML_PATH = Path(r"C:\Users\PC\Documents\MYA_IA\templates\modulos\main.html")
PUERTO_POR_DEFECTO = 8765
HOST = "127.0.0.1"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_URL_SESION = "https://api.openai.com/v1/realtime/sessions"
VIDA_LLAVE_SEGUNDOS = 60


# --------------------------------------------------------------------------
# Puente entre JavaScript y Python (para pantalla completa nativa)
# --------------------------------------------------------------------------
class Puente:
    """API que el JS del WebView puede llamar vía pywebview.api.*"""

    def __init__(self):
        # No guardamos la ventana aquí para evitar recursión/inpección
        # de pywebview. Usamos webview.windows[0] cuando la necesitemos.
        self._completo = False

    def _ventana(self):
        """Devuelve la primera ventana de pywebview, o None."""
        try:
            return webview.windows[0] if webview.windows else None
        except Exception:
            return None

    def alternar_pantalla_completa(self):
        """Alterna entre pantalla completa y ventana. Devuelve el estado nuevo."""
        v = self._ventana()
        if v is None:
            return False
        try:
            v.toggle_fullscreen()
            self._completo = not self._completo
            print(f"[MIA] Pantalla completa: {'sí' if self._completo else 'no'}")
            return self._completo
        except Exception as e:
            print("[MIA] No pude alternar pantalla completa:", e)
            return self._completo

    def poner_pantalla_completa(self, valor: bool):
        """Fuerza el estado (por si lo llamás desde afuera)."""
        v = self._ventana()
        if v is None:
            return False
        valor = bool(valor)
        if valor != self._completo:
            try:
                v.toggle_fullscreen()
                self._completo = valor
                print(f"[MIA] Pantalla completa forzada a: {'sí' if valor else 'no'}")
            except Exception as e:
                print("[MIA] No pude fijar pantalla completa:", e)
                return False
        return True

    def esta_en_pantalla_completa(self):
        """Devuelve el estado actual guardado."""
        return self._completo


# --------------------------------------------------------------------------
# Funciones de red
# --------------------------------------------------------------------------
def puerto_libre(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, puerto))
            return True
        except OSError:
            return False


def buscar_puerto_libre(inicial: int, max_intentos: int = 20) -> int:
    for i in range(max_intentos):
        if puerto_libre(inicial + i):
            return inicial + i
    raise RuntimeError(f"No encontré puerto libre entre {inicial} y {inicial+max_intentos-1}")


# --------------------------------------------------------------------------
# Manejador HTTP
# --------------------------------------------------------------------------
class ManejadorMIA(BaseHTTPRequestHandler):
    server_version = "MIA/1.0"

    def _json(self, codigo: int, datos: dict) -> None:
        cuerpo = json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _bytes(self, codigo: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, formato, *args):
        return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        ruta = urlparse(self.path).path

        if ruta in ("/api/salud", "/api/health"):
            self._json(200, {"servidor": True, "hora": time.time()})
            return

        if ruta in ("/", "/index.html", "/main.html",
                    "/mia-stand.html", "/MIA%20%E2%80%94%20MyndTeks.html"):
            self._servir_html()
            return

        base = HTML_PATH.parent.resolve()
        pedido = (base / ruta.lstrip("/")).resolve()
        try:
            pedido.relative_to(base)
        except ValueError:
            self._bytes(403, b"prohibido", "text/plain; charset=utf-8")
            return
        if pedido.is_file():
            self._servir_archivo(pedido)
        else:
            self._bytes(404, b"no encontrado", "text/plain; charset=utf-8")

    def _servir_html(self):
        if not HTML_PATH.is_file():
            msg = (
                f"No encontré el archivo:\n  {HTML_PATH}\n\n"
                "Revisá que exista, o pasá la ruta con --html."
            ).encode("utf-8")
            self._bytes(500, msg, "text/plain; charset=utf-8")
            print("[MIA] Falta el HTML:", HTML_PATH)
            return
        self._servir_archivo(HTML_PATH)

    def _servir_archivo(self, ruta: Path):
        tipos = {
            ".html": "text/html; charset=utf-8",
            ".htm":  "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg":  "image/svg+xml",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".ico":  "image/x-icon",
            ".woff": "font/woff",
            ".woff2":"font/woff2",
        }
        tipo = tipos.get(ruta.suffix.lower(), "application/octet-stream")
        self._bytes(200, ruta.read_bytes(), tipo)

    def do_POST(self):
        ruta = urlparse(self.path).path
        if ruta == "/api/sesion":
            self._crear_sesion()
            return
        self._json(404, {"error": "ruta desconocida"})

    def _crear_sesion(self):
        if not OPENAI_API_KEY:
            self._json(503, {
                "error": "sin_llave",
                "detalle": ("El servidor no tiene OPENAI_API_KEY configurada. "
                            "Pegá la llave a mano en la pantalla de arranque.")
            })
            return

        try:
            largo = int(self.headers.get("Content-Length") or 0)
            crudo = self.rfile.read(largo) if largo else b"{}"
            pedido = json.loads(crudo.decode("utf-8") or "{}")
        except Exception as e:
            self._json(400, {"error": "json_invalido", "detalle": str(e)})
            return

        sesion = pedido.get("session") or {}
        cuerpo_openai = {
            "model": sesion.get("model") or "gpt-realtime",
            "voice": (sesion.get("audio", {})
                            .get("output", {})
                            .get("voice", "marin")),
        }
        if "instructions" in sesion:
            cuerpo_openai["instructions"] = sesion["instructions"]

        datos = json.dumps(cuerpo_openai).encode("utf-8")
        req = urllib.request.Request(
            OPENAI_URL_SESION,
            data=datos,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
                "OpenAI-Beta": "realtime=v1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                respuesta = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "replace")[:300]
            print(f"[MIA] OpenAI respondió {e.code}: {detalle}")
            self._json(e.code, {"error": "openai", "detalle": detalle})
            return
        except Exception as e:
            print("[MIA] No pude hablar con OpenAI:", e)
            self._json(502, {"error": "sin_conexion", "detalle": str(e)})
            return

        llave = (respuesta.get("client_secret", {}) or {}).get("value") \
                or respuesta.get("value")
        if not llave:
            self._json(502, {"error": "respuesta_sin_llave", "detalle": respuesta})
            return

        self._json(200, {"value": llave, "expira_en": VIDA_LLAVE_SEGUNDOS})


# --------------------------------------------------------------------------
# Arranque con PyWebview
# --------------------------------------------------------------------------
def main() -> int:
    global HTML_PATH

    p = argparse.ArgumentParser(description="Servidor local de MIA en escritorio")
    p.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO,
                   help=f"puerto (por defecto {PUERTO_POR_DEFECTO})")
    p.add_argument("--html", type=Path, default=HTML_PATH,
                   help="ruta al archivo main.html")
    p.add_argument("--fullscreen", action="store_true",
                   help="arrancar directamente en pantalla completa")
    args = p.parse_args()

    HTML_PATH = args.html

    if not HTML_PATH.is_file():
        print(f"[MIA] ATENCIÓN: no existe {HTML_PATH}")
        print("      La ventana se abrirá, pero el servidor devolverá 500 al pedir la página.")
    else:
        print(f"[MIA] Sirviendo: {HTML_PATH}")

    if OPENAI_API_KEY:
        print("[MIA] OPENAI_API_KEY detectada: /api/sesion entregará llaves temporales.")
    else:
        print("[MIA] Sin OPENAI_API_KEY: la llave se pega en el HTML o en la pantalla de arranque.")

    puerto = args.puerto
    if not puerto_libre(puerto):
        try:
            puerto = buscar_puerto_libre(puerto + 1)
            print(f"[MIA] El puerto {args.puerto} estaba ocupado, uso el {puerto}.")
        except RuntimeError as e:
            print(f"[MIA] {e}")
            return 1

    url = f"http://localhost:{puerto}/"

    try:
        servidor = ThreadingHTTPServer((HOST, puerto), ManejadorMIA)
    except OSError as e:
        print(f"[MIA] No pude abrir el puerto {puerto}: {e}")
        return 1

    # Servidor HTTP en segundo plano
    hilo_servidor = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo_servidor.start()
    print(f"[MIA] Servidor corriendo en fondo en {url}")

    # Puente para el JS (F11 → toggle_fullscreen)
    puente = Puente()

    ventana = webview.create_window(
        title="MIA — MyndTeks",
        url=url,
        width=1200,
        height=800,
        min_size=(800, 600),
        js_api=puente,
        fullscreen=args.fullscreen
    )
    puente._completo = args.fullscreen

    # Marca que pywebview ya está listo en el log cuando carga la página
    try:
        ventana.events.loaded += lambda: print("[MIA] Ventana lista. F11 = pantalla completa.")
    except Exception:
        pass

    # Arranca la interfaz gráfica (bloquea hasta cerrar la ventana)
    webview.start()

    # Al cerrar la ventana, apagamos el servidor limpiamente
    print("\n[MIA] Cerrando servidor…")
    servidor.shutdown()
    servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
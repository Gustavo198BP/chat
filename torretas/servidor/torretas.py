#!/usr/bin/env python3
"""Monitoreo de torretas: servidor.

Lee por USB lo que manda el receptor (una línea JSON por mensaje de cada
nodo), guarda en SQLite cada cambio de estado de las máquinas y sirve la
página con el estado actual y la línea de tiempo.

    python torretas.py                  # con el receptor conectado por USB
    python torretas.py --puerto COM5    # si no encuentra el receptor solo
    python torretas.py --simular        # 14 máquinas de prueba, sin hardware
"""

import argparse
import csv
import heapq
import io
import itertools
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

AQUI = os.path.dirname(os.path.abspath(__file__))

NOMBRES = {
    "PRODUCCION": "Producción",
    "MANTENIMIENTO": "Mantenimiento",
    "FALLA": "Falla",
    "APAGADA": "Apagada",
    "SIN_SENAL": "Sin señal",
}

CONFIG_BASE = {
    "puerto_serial": "auto",
    "http_host": "0.0.0.0",
    "http_puerto": 8080,
    "base_de_datos": "torretas.db",
    "segundos_sin_senal": 60,
    "canales": ["verde", "amarillo", "rojo", "extra"],
    "prioridad": [["rojo", "FALLA"], ["amarillo", "MANTENIMIENTO"], ["verde", "PRODUCCION"]],
    "turnos": [],
}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS nodos (
    mac TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    arranque INTEGER,
    seq INTEGER,
    canales TEXT,
    estado TEXT,
    desde REAL,              -- cuándo empezó el estado actual
    ultimo REAL,             -- último mensaje recibido
    rssi INTEGER,
    perdidos INTEGER,
    sin_senal INTEGER NOT NULL DEFAULT 0,
    id_sin_senal INTEGER     -- evento SIN_SENAL abierto
);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY,
    mac TEXT NOT NULL,
    ts REAL NOT NULL,        -- cuándo empezó este estado
    estado TEXT NOT NULL,
    canales TEXT,
    origen TEXT NOT NULL     -- evento, latido o sin_senal
);
CREATE INDEX IF NOT EXISTS eventos_mac_ts ON eventos (mac, ts);
"""


def cargar_config(ruta):
    config = dict(CONFIG_BASE)
    with open(ruta, encoding="utf-8") as f:
        config.update(json.load(f))
    return config


def clave_natural(texto):
    """Para que "Máquina 2" quede antes que "Máquina 10"."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", texto)]


def calcular_tramos(puntos, desde, hasta):
    """[(ts, estado), ...] en orden -> [[inicio, fin, estado], ...] recortados a [desde, hasta]."""
    tramos = []
    for i, (ts, estado) in enumerate(puntos):
        siguiente = puntos[i + 1][0] if i + 1 < len(puntos) else hasta
        inicio, fin = max(ts, desde), min(siguiente, hasta)
        if fin <= inicio:
            continue
        if tramos and tramos[-1][2] == estado:
            tramos[-1][1] = fin
        else:
            tramos.append([inicio, fin, estado])
    return tramos


def resumir(tramos):
    segundos = {estado: 0.0 for estado in NOMBRES}
    veces = {estado: 0 for estado in NOMBRES}
    for inicio, fin, estado in tramos:
        segundos[estado] = segundos.get(estado, 0.0) + fin - inicio
        veces[estado] = veces.get(estado, 0) + 1
    return {"segundos": segundos, "veces": veces}


class Monitor:
    """Convierte los mensajes de los nodos en un historial de estados por máquina."""

    def __init__(self, ruta_db, config):
        self.limite_sin_senal = config["segundos_sin_senal"]
        canales = config["canales"]
        self.prioridad = [(canales.index(canal), estado) for canal, estado in config["prioridad"]]
        self.lock = threading.Lock()
        self.db = sqlite3.connect(ruta_db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(ESQUEMA)

    def derivar(self, canales):
        for indice, estado in self.prioridad:
            if indice < len(canales) and canales[indice]:
                return estado
        return "APAGADA"

    def mensaje(self, m, ahora):
        """Procesa un mensaje del receptor (ya convertido a dict) recibido en `ahora`."""
        tipo = m.get("tipo")
        if tipo not in ("evento", "latido"):
            return
        try:
            mac = str(m["mac"]).upper()
            arranque = int(m["arranque"])
            seq = int(m["seq"])
            ts = ahora - int(m["edad_ms"]) / 1000
            canales = [int(c) for c in m["canales"]]
        except (KeyError, TypeError, ValueError):
            return
        estado = self.derivar(canales)
        texto_canales = ",".join(map(str, canales))

        with self.lock, self.db:
            n = self.db.execute("SELECT * FROM nodos WHERE mac=?", (mac,)).fetchone()
            if n is None:
                self.db.execute("INSERT INTO nodos (mac, nombre) VALUES (?, ?)", (mac, mac))
                n = self.db.execute("SELECT * FROM nodos WHERE mac=?", (mac,)).fetchone()
            mismo_arranque = n["arranque"] == arranque

            # "continuo" = sabemos todo lo que pasó en el nodo hasta este mensaje.
            # El nodo guarda los eventos que no ha podido entregar, así que si
            # llega el que sigue, no nos perdimos nada aunque haya habido un hueco.
            cambio = False
            if tipo == "evento":
                continuo = mismo_arranque and seq <= n["seq"] + 1
                if not (mismo_arranque and seq <= n["seq"]):  # si no es repetido
                    self._insertar(mac, ts, estado, texto_canales, "evento")
                    cambio = True
            else:
                continuo = mismo_arranque and seq == n["seq"]
                if not continuo:
                    # Faltan eventos o el nodo se reinició: lo único seguro es el estado actual.
                    self._insertar(mac, ts, estado, texto_canales, "latido")
                    cambio = True

            if n["sin_senal"] and continuo:
                # El nodo siguió registrando todo el tiempo: el hueco no fue real.
                self.db.execute("DELETE FROM eventos WHERE id=?", (n["id_sin_senal"],))

            self.db.execute(
                "UPDATE nodos SET ultimo=?, rssi=?, perdidos=?, sin_senal=0, id_sin_senal=NULL WHERE mac=?",
                (ahora, m.get("rssi"), m.get("perdidos"), mac),
            )
            if cambio:
                self.db.execute(
                    "UPDATE nodos SET arranque=?, seq=?, canales=?, estado=?, desde=? WHERE mac=?",
                    (arranque, seq, texto_canales, estado, ts, mac),
                )

    def _insertar(self, mac, ts, estado, canales, origen):
        cursor = self.db.execute(
            "INSERT INTO eventos (mac, ts, estado, canales, origen) VALUES (?, ?, ?, ?, ?)",
            (mac, ts, estado, canales, origen),
        )
        return cursor.lastrowid

    def revisar(self, ahora):
        """Marca SIN_SENAL, desde el último mensaje, a los nodos que dejaron de hablar."""
        with self.lock, self.db:
            callados = self.db.execute(
                "SELECT mac, ultimo FROM nodos WHERE sin_senal=0 AND ultimo < ?",
                (ahora - self.limite_sin_senal,),
            ).fetchall()
            for n in callados:
                id_evento = self._insertar(n["mac"], n["ultimo"], "SIN_SENAL", None, "sin_senal")
                self.db.execute(
                    "UPDATE nodos SET sin_senal=1, id_sin_senal=? WHERE mac=?", (id_evento, n["mac"])
                )

    def renombrar(self, mac, nombre, crear=False):
        with self.lock, self.db:
            if crear:
                self.db.execute(
                    "INSERT INTO nodos (mac, nombre) VALUES (?, ?) "
                    "ON CONFLICT(mac) DO UPDATE SET nombre=excluded.nombre",
                    (mac.upper(), nombre),
                )
                return True
            cursor = self.db.execute("UPDATE nodos SET nombre=? WHERE mac=?", (nombre, mac.upper()))
            return cursor.rowcount > 0

    def estado(self):
        with self.lock:
            filas = self.db.execute("SELECT * FROM nodos").fetchall()
        maquinas = []
        for n in filas:
            sin_senal = bool(n["sin_senal"]) or n["estado"] is None
            maquinas.append({
                "mac": n["mac"],
                "nombre": n["nombre"],
                "estado": "SIN_SENAL" if sin_senal else n["estado"],
                "desde": n["ultimo"] if sin_senal else n["desde"],
                "canales": [int(c) for c in n["canales"].split(",")] if n["canales"] else [],
                "ultimo": n["ultimo"],
                "rssi": n["rssi"],
                "perdidos": n["perdidos"],
            })
        maquinas.sort(key=lambda m: clave_natural(m["nombre"]))
        return maquinas

    def linea(self, desde, hasta, ahora):
        """Tramos de estado de cada máquina entre desde y hasta (sin pasar de ahora)."""
        hasta = min(hasta, ahora)
        with self.lock:
            nodos = self.db.execute("SELECT mac, nombre FROM nodos").fetchall()
            puntos = {}
            for n in nodos:
                previo = self.db.execute(
                    "SELECT ts, estado FROM eventos WHERE mac=? AND ts<=? ORDER BY ts DESC, id DESC LIMIT 1",
                    (n["mac"], desde),
                ).fetchall()
                resto = self.db.execute(
                    "SELECT ts, estado FROM eventos WHERE mac=? AND ts>? AND ts<? ORDER BY ts, id",
                    (n["mac"], desde, hasta),
                ).fetchall()
                puntos[n["mac"]] = [(r["ts"], r["estado"]) for r in previo + resto]
        maquinas = []
        for n in nodos:
            tramos = calcular_tramos(puntos[n["mac"]], desde, hasta)
            maquinas.append({"mac": n["mac"], "nombre": n["nombre"], "tramos": tramos, "resumen": resumir(tramos)})
        maquinas.sort(key=lambda m: clave_natural(m["nombre"]))
        return {"desde": desde, "hasta": hasta, "maquinas": maquinas}


def tramos_csv(linea):
    def celda(texto):  # que Excel no tome un nombre como fórmula
        return "'" + texto if texto[:1] in ("=", "+", "-", "@") else texto

    def fecha(ts):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["maquina", "estado", "inicio", "fin", "minutos"])
    for m in linea["maquinas"]:
        for inicio, fin, estado in m["tramos"]:
            escritor.writerow(
                [celda(m["nombre"]), NOMBRES.get(estado, estado), fecha(inicio), fecha(fin), f"{(fin - inicio) / 60:.1f}"]
            )
    return "﻿" + salida.getvalue()  # el BOM es para que Excel lea bien los acentos


class Pagina(BaseHTTPRequestHandler):
    monitor = None
    config = None

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(AQUI, "web", "index.html"), "rb") as f:
                self._responder(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/api/estado":
            self._json({"ahora": time.time(), "maquinas": self.monitor.estado()})
        elif url.path == "/api/config":
            self._json({"turnos": self.config["turnos"]})
        elif url.path in ("/api/linea", "/api/tramos.csv"):
            q = parse_qs(url.query)
            try:
                desde, hasta = float(q["desde"][0]), float(q["hasta"][0])
            except (KeyError, ValueError):
                desde = hasta = math.nan
            if not (math.isfinite(desde) and math.isfinite(hasta)):
                self._responder(400, "faltan desde y hasta".encode(), "text/plain; charset=utf-8")
                return
            linea = self.monitor.linea(desde, hasta, time.time())
            if url.path == "/api/linea":
                self._json(linea)
            else:
                nombre = time.strftime("torretas_%Y-%m-%d_%H%M.csv", time.localtime(desde))
                self._responder(200, tramos_csv(linea).encode("utf-8"), "text/csv; charset=utf-8",
                                {"Content-Disposition": f'attachment; filename="{nombre}"'})
        else:
            self._responder(404, b"no existe", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/nombre":
            self._responder(404, b"no existe", "text/plain; charset=utf-8")
            return
        try:
            largo = min(int(self.headers.get("Content-Length", 0)), 10000)
            datos = json.loads(self.rfile.read(largo))
            mac, nombre = str(datos["mac"]), str(datos["nombre"]).strip()[:40]
        except (ValueError, KeyError, TypeError):
            nombre = ""
        if not nombre:
            self._responder(400, "falta el nombre".encode(), "text/plain; charset=utf-8")
        elif not self.monitor.renombrar(mac, nombre):
            self._responder(404, "no conozco esa máquina".encode(), "text/plain; charset=utf-8")
        else:
            self._json({"ok": True})

    def _json(self, datos):
        self._responder(200, json.dumps(datos).encode("utf-8"), "application/json")

    def _responder(self, codigo, cuerpo, tipo, encabezados=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        for clave, valor in (encabezados or {}).items():
            self.send_header(clave, valor)
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, formato, *args):
        pass


# --- Receptor por USB ---------------------------------------------------------

# Chips USB-serie de las placas ESP32: CP210x, CH340/CH9102, FTDI y el USB propio de Espressif.
CHIPS_USB = (0x10C4, 0x1A86, 0x0403, 0x303A)


def buscar_puerto(list_ports):
    for puerto in list_ports.comports():
        if puerto.vid in CHIPS_USB:
            return puerto.device
    return None


def procesar_linea(monitor, linea, ver):
    texto = linea.decode("utf-8", "replace").strip()
    if not texto.startswith("{"):
        return  # mensajes de arranque del ESP32
    try:
        m = json.loads(texto)
    except ValueError:
        return
    if not isinstance(m, dict):
        return
    if ver:
        print(texto)
    if m.get("tipo") == "receptor":
        return
    try:
        monitor.mensaje(m, time.time())
    except sqlite3.Error as e:
        print(f"No pude guardar un mensaje: {e}")


def leer_receptor(monitor, puerto, ver):
    import serial
    from serial.tools import list_ports

    avisado = False
    while True:
        nombre = puerto if puerto and puerto != "auto" else buscar_puerto(list_ports)
        if not nombre:
            if not avisado:
                print("No encuentro el receptor por USB; sigo buscando...")
                avisado = True
            time.sleep(3)
            continue
        conexion = serial.Serial()
        conexion.port = nombre
        conexion.baudrate = 115200
        conexion.timeout = 1
        conexion.dtr = False  # que abrir el puerto no reinicie el ESP32
        conexion.rts = False
        try:
            conexion.open()
        except serial.SerialException as e:
            print(f"No pude abrir {nombre}: {e}")
            time.sleep(3)
            continue
        print(f"Leyendo el receptor en {nombre}")
        avisado = False
        resto = b""
        try:
            while True:
                resto += conexion.read(conexion.in_waiting or 1)
                *lineas, resto = resto.split(b"\n")
                if len(resto) > 4096:
                    resto = b""
                for linea in lineas:
                    procesar_linea(monitor, linea, ver)
        except serial.SerialException as e:
            print(f"Se desconectó el receptor ({e}); reintentando")
            conexion.close()
            time.sleep(3)


def vigilar(monitor):
    while True:
        monitor.revisar(time.time())
        time.sleep(5)


# --- Simulador ------------------------------------------------------------------

class Simulador:
    """Genera los mismos mensajes que mandaría el receptor, para probar sin hardware."""

    MINUTOS = {"PRODUCCION": 45, "FALLA": 7, "MANTENIMIENTO": 18, "APAGADA": 12}
    LATIDO = 15

    def __init__(self, monitor, maquinas=14, horas=10, semilla=None):
        self.monitor = monitor
        self.azar = random.Random(semilla)
        self.cola = []
        self.orden = itertools.count()
        ahora = time.time()
        inicio = ahora - horas * 3600
        for i in range(maquinas):
            nodo = {"mac": f"02:00:00:00:00:{i + 1:02X}", "apagado": False}
            monitor.renombrar(nodo["mac"], f"Máquina {i + 1}", crear=True)
            self._encender(nodo, inicio, self.azar.choice(["PRODUCCION"] * 4 + ["MANTENIMIENTO", "APAGADA"]))
            if self.azar.random() < 0.25:  # algunas máquinas se quedan sin corriente un rato
                corte = self.azar.uniform(inicio + 3600, ahora - 1800)
                self._programar(corte, self._apagar, nodo)
        self._programar(inicio, self._revisar, None)

    def _programar(self, t, funcion, dato):
        heapq.heappush(self.cola, (t, next(self.orden), funcion, dato))

    def _mensaje(self, nodo, tipo, edad):
        return {
            "mac": nodo["mac"], "rssi": self.azar.randint(-78, -45), "tipo": tipo,
            "arranque": nodo["arranque"], "seq": nodo["seq"], "edad_ms": int(edad * 1000),
            "canales": list(nodo["canales"]), "activo_s": 0, "perdidos": 0, "en_cola": 0,
        }

    def _poner(self, nodo, estado, t):
        nodo["estado"], nodo["desde"] = estado, t
        rojo = 2 if estado == "FALLA" and self.azar.random() < 0.5 else 1
        nodo["canales"] = {
            "PRODUCCION": [1, 0, 0, 0], "MANTENIMIENTO": [0, 1, 0, 0],
            "FALLA": [0, 0, rojo, 0], "APAGADA": [0, 0, 0, 0],
        }[estado]
        nodo["seq"] += 1
        nodo["version"] = nodo.get("version", 0) + 1  # invalida cambios ya programados
        # El nodo confirma el cambio y lo manda ~1.5 s después, con la hora real.
        retraso = self.azar.uniform(1.5, 2.2)
        nodo["entregado"] = t + retraso
        self._programar(t + retraso, self._entregar, self._mensaje(nodo, "evento", retraso))
        minutos = self.MINUTOS[estado]
        duracion = max(60, self.azar.expovariate(1 / (minutos * 60)))
        self._programar(t + duracion, self._cambiar, (nodo, nodo["version"]))

    def _encender(self, nodo, t, estado):
        nodo.update(apagado=False, arranque=self.azar.getrandbits(32), seq=0)
        self._poner(nodo, estado, t)
        self._programar(t + self.LATIDO, self._latido, (nodo, nodo["arranque"]))

    def _cambiar(self, dato, t):
        nodo, version = dato
        if nodo["apagado"] or version != nodo["version"]:
            return
        if nodo["estado"] == "PRODUCCION":
            siguiente = self.azar.choices(["FALLA", "MANTENIMIENTO", "APAGADA"], [0.55, 0.3, 0.15])[0]
        else:
            siguiente = "PRODUCCION"
        self._poner(nodo, siguiente, t)

    def _latido(self, dato, t):
        nodo, arranque = dato
        if nodo["apagado"] or arranque != nodo["arranque"]:
            return
        if t >= nodo["entregado"]:  # como el nodo real: sin latidos con un evento pendiente
            self._entregar(self._mensaje(nodo, "latido", t - nodo["desde"]), t)
        self._programar(t + self.LATIDO, self._latido, dato)

    def _apagar(self, nodo, t):
        nodo["apagado"] = True
        self._programar(t + self.azar.uniform(8, 35) * 60, self._regresar, nodo)

    def _regresar(self, nodo, t):
        self._encender(nodo, t, "MANTENIMIENTO")

    def _entregar(self, mensaje, t):
        self.monitor.mensaje(mensaje, t)

    def _revisar(self, _, t):
        self.monitor.revisar(t)
        self._programar(t + 5, self._revisar, None)

    def ponerse_al_dia(self):
        """Procesa de golpe todo lo que ya debió pasar."""
        while self.cola and self.cola[0][0] <= time.time():
            t, _, funcion, dato = heapq.heappop(self.cola)
            funcion(dato, t)

    def correr(self):
        """Sigue generando en tiempo real."""
        while True:
            t, _, funcion, dato = heapq.heappop(self.cola)
            espera = t - time.time()
            if espera > 0:
                time.sleep(espera)
            funcion(dato, t)


def main():
    parser = argparse.ArgumentParser(description="Monitoreo de torretas")
    parser.add_argument("--config", default=os.path.join(AQUI, "config.json"))
    parser.add_argument("--puerto", help="puerto del receptor, p. ej. COM5 (si no, el de config.json)")
    parser.add_argument("--simular", action="store_true", help="14 máquinas de prueba, sin hardware")
    parser.add_argument("--ver", action="store_true", help="muestra cada mensaje que llega del receptor")
    args = parser.parse_args()
    config = cargar_config(args.config)

    if args.simular:
        ruta_db = os.path.join(AQUI, "torretas-simulacion.db")
        for sufijo in ("", "-wal", "-shm"):
            if os.path.exists(ruta_db + sufijo):
                os.remove(ruta_db + sufijo)
    else:
        ruta_db = os.path.join(AQUI, config["base_de_datos"])
    monitor = Monitor(ruta_db, config)

    if args.simular:
        print("Generando 10 horas de historial de prueba...")
        simulador = Simulador(monitor)
        simulador.ponerse_al_dia()
        threading.Thread(target=simulador.correr, daemon=True).start()
    else:
        try:
            import serial  # noqa: F401
        except ImportError:
            print("Falta pyserial. Instálalo con:  py -m pip install pyserial")
            return
        puerto = args.puerto or config["puerto_serial"]
        threading.Thread(target=leer_receptor, args=(monitor, puerto, args.ver), daemon=True).start()
        threading.Thread(target=vigilar, args=(monitor,), daemon=True).start()

    Pagina.monitor = monitor
    Pagina.config = config
    servidor = ThreadingHTTPServer((config["http_host"], config["http_puerto"]), Pagina)
    puerto_http = config["http_puerto"]
    print(f"Página: http://localhost:{puerto_http}  (desde otra PC: http://<IP de esta PC>:{puerto_http})")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Pruebas del servidor:  python -m unittest test_torretas"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import torretas

CONFIG = dict(torretas.CONFIG_BASE, turnos=[{"nombre": "Turno 1", "inicio": "06:00", "fin": "14:00"}])
MAC = "AA:BB:CC:00:00:01"
VERDE, AMARILLO, ROJO, NADA = [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0]


def mensaje(tipo, seq, edad_s, canales, arranque=7, mac=MAC):
    return {"mac": mac, "tipo": tipo, "arranque": arranque, "seq": seq,
            "edad_ms": int(edad_s * 1000), "canales": canales, "rssi": -60}


class PruebasMonitor(unittest.TestCase):
    def setUp(self):
        self.m = torretas.Monitor(":memory:", CONFIG)
        self.addCleanup(self.m.db.close)

    def tramos(self, desde, hasta, ahora=None):
        linea = self.m.linea(desde, hasta, ahora if ahora is not None else hasta)
        return [(round(i), round(f), e) for i, f, e in linea["maquinas"][0]["tramos"]]

    def test_prioridad(self):
        self.assertEqual(self.m.derivar([1, 0, 1, 0]), "FALLA")
        self.assertEqual(self.m.derivar([1, 2, 0, 0]), "MANTENIMIENTO")  # parpadeando también cuenta
        self.assertEqual(self.m.derivar([1, 0, 0, 1]), "PRODUCCION")  # el canal extra no cambia el estado
        self.assertEqual(self.m.derivar(NADA), "APAGADA")

    def test_evento_se_fecha_cuando_paso(self):
        self.m.mensaje(mensaje("evento", 1, 2.0, VERDE), 1000)
        estado = self.m.estado()[0]
        self.assertEqual(estado["estado"], "PRODUCCION")
        self.assertAlmostEqual(estado["desde"], 998)
        self.assertEqual(estado["nombre"], MAC)

    def test_evento_repetido_no_se_duplica(self):
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.mensaje(mensaje("evento", 2, 0, ROJO), 1100)
        self.m.mensaje(mensaje("evento", 2, 5, ROJO), 1105)  # el nodo lo reenvió
        self.assertEqual(self.tramos(1000, 1200), [(1000, 1100, "PRODUCCION"), (1100, 1200, "FALLA")])

    def test_sin_senal_se_marca_desde_el_ultimo_mensaje(self):
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.revisar(1059)
        self.assertEqual(self.m.estado()[0]["estado"], "PRODUCCION")
        self.m.revisar(1061)
        self.assertEqual(self.m.estado()[0]["estado"], "SIN_SENAL")
        # el último mensaje fue en 1000, así que desde ahí no se sabe nada
        self.assertEqual(self.tramos(1000, 1500), [(1000, 1500, "SIN_SENAL")])

    def test_hueco_sin_eventos_perdidos_se_borra(self):
        # El receptor dejó de oír al nodo, pero el nodo no tuvo cambios: el latido lo confirma.
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.revisar(1100)
        self.m.mensaje(mensaje("latido", 1, 300, VERDE), 1300)
        self.assertEqual(self.m.estado()[0]["estado"], "PRODUCCION")
        self.assertEqual(self.tramos(1000, 1400), [(1000, 1400, "PRODUCCION")])

    def test_eventos_guardados_llenan_el_hueco(self):
        # Mientras no había señal pasaron cosas; el nodo las guardó y llegan después con su hora.
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.revisar(1100)
        self.m.mensaje(mensaje("evento", 2, 100, ROJO), 1300)  # pasó en 1200
        self.m.mensaje(mensaje("evento", 3, 50, AMARILLO), 1300)  # pasó en 1250
        self.assertEqual(
            self.tramos(1000, 1400),
            [(1000, 1200, "PRODUCCION"), (1200, 1250, "FALLA"), (1250, 1400, "MANTENIMIENTO")],
        )

    def test_reinicio_deja_el_hueco(self):
        # Se fue la corriente: el nodo regresa con otro número de arranque y no sabe qué pasó.
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.revisar(1100)
        self.m.mensaje(mensaje("evento", 1, 2, AMARILLO, arranque=99), 1600)
        self.assertEqual(
            self.tramos(1000, 1700),
            [(1000, 1598, "SIN_SENAL"), (1598, 1700, "MANTENIMIENTO")],
        )

    def test_latido_con_eventos_perdidos_resincroniza(self):
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.m.mensaje(mensaje("latido", 3, 40, ROJO), 1100)  # se perdieron los eventos 2 y 3
        estado = self.m.estado()[0]
        self.assertEqual(estado["estado"], "FALLA")
        self.assertAlmostEqual(estado["desde"], 1060)
        self.m.mensaje(mensaje("latido", 3, 55, ROJO), 1115)  # ya está al día: no agrega nada
        self.assertEqual(self.tramos(1000, 1200), [(1000, 1060, "PRODUCCION"), (1060, 1200, "FALLA")])

    def test_mensajes_raros_se_ignoran(self):
        self.m.mensaje({"tipo": "evento", "mac": MAC}, 1000)
        self.m.mensaje({"tipo": "receptor", "mac": MAC}, 1000)
        self.m.mensaje(mensaje("evento", "x", 0, VERDE), 1000)
        self.assertEqual(self.m.estado(), [])

    def test_linea_no_pasa_de_ahora(self):
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.assertEqual(self.tramos(900, 5000, ahora=1500), [(1000, 1500, "PRODUCCION")])

    def test_renombrar(self):
        self.m.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        self.assertTrue(self.m.renombrar(MAC.lower(), "H1"))
        self.assertFalse(self.m.renombrar("00:00:00:00:00:00", "otra"))
        self.assertEqual(self.m.estado()[0]["nombre"], "H1")

    def test_orden_natural(self):
        for i in (10, 2, 1):
            mac = f"AA:00:00:00:00:{i:02X}"
            self.m.mensaje(mensaje("evento", 1, 0, VERDE, mac=mac), 1000)
            self.m.renombrar(mac, f"Máquina {i}")
        self.assertEqual([m["nombre"] for m in self.m.estado()], ["Máquina 1", "Máquina 2", "Máquina 10"])


class PruebasFunciones(unittest.TestCase):
    def test_tramos_se_unen_y_recortan(self):
        puntos = [(0, "PRODUCCION"), (50, "PRODUCCION"), (100, "FALLA"), (100, "MANTENIMIENTO"), (150, "PRODUCCION")]
        self.assertEqual(
            torretas.calcular_tramos(puntos, 25, 200),
            [[25, 100, "PRODUCCION"], [100, 150, "MANTENIMIENTO"], [150, 200, "PRODUCCION"]],
        )

    def test_resumen(self):
        r = torretas.resumir([[0, 60, "PRODUCCION"], [60, 90, "FALLA"], [90, 150, "PRODUCCION"]])
        self.assertEqual(r["segundos"]["PRODUCCION"], 120)
        self.assertEqual(r["veces"]["FALLA"], 1)

    def test_csv(self):
        linea = {"maquinas": [{"nombre": "=H1", "tramos": [[0, 90, "FALLA"]]}]}
        texto = torretas.tramos_csv(linea)
        self.assertTrue(texto.startswith("﻿maquina,estado"))
        self.assertIn("'=H1,Falla,", texto)
        self.assertIn(",1.5", texto)


class PruebasSimulador(unittest.TestCase):
    def test_historial_completo_y_sin_huecos(self):
        m = torretas.Monitor(":memory:", CONFIG)
        self.addCleanup(m.db.close)
        sim = torretas.Simulador(m, maquinas=14, horas=10, semilla=3)
        sim.ponerse_al_dia()
        maquinas = m.estado()
        self.assertEqual(len(maquinas), 14)
        ahora = max(x["ultimo"] for x in maquinas)
        desde = ahora - 9 * 3600
        linea = m.linea(desde, ahora, ahora)
        vistos = set()
        for maquina in linea["maquinas"]:
            tramos = maquina["tramos"]
            self.assertAlmostEqual(tramos[0][0], desde)
            for a, b in zip(tramos, tramos[1:]):
                self.assertAlmostEqual(a[1], b[0])  # sin huecos ni encimados
                self.assertNotEqual(a[2], b[2])
            vistos.update(t[2] for t in tramos)
        self.assertEqual(vistos, set(torretas.NOMBRES))


class PruebasPagina(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.monitor = torretas.Monitor(":memory:", CONFIG)
        cls.monitor.mensaje(mensaje("evento", 1, 0, VERDE), 1000)
        torretas.Pagina.monitor = cls.monitor
        torretas.Pagina.config = CONFIG
        cls.servidor = ThreadingHTTPServer(("127.0.0.1", 0), torretas.Pagina)
        cls.base = f"http://127.0.0.1:{cls.servidor.server_address[1]}"
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.monitor.db.close()

    def pedir(self, ruta, datos=None):
        cuerpo = json.dumps(datos).encode() if datos is not None else None
        with urllib.request.urlopen(urllib.request.Request(self.base + ruta, data=cuerpo)) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def codigo(self, ruta, datos=None):
        try:
            return self.pedir(ruta, datos)[0]
        except urllib.error.HTTPError as e:
            return e.code

    def test_pagina(self):
        _, tipo, cuerpo = self.pedir("/")
        self.assertIn("text/html", tipo)
        self.assertIn(b"<title>", cuerpo)

    def test_api(self):
        estado = json.loads(self.pedir("/api/estado")[2])
        self.assertEqual(estado["maquinas"][0]["estado"], "PRODUCCION")
        self.assertEqual(json.loads(self.pedir("/api/config")[2])["turnos"][0]["nombre"], "Turno 1")
        linea = json.loads(self.pedir("/api/linea?desde=900&hasta=1100")[2])
        self.assertEqual(linea["maquinas"][0]["tramos"], [[1000, 1100, "PRODUCCION"]])
        _, tipo, csv = self.pedir("/api/tramos.csv?desde=900&hasta=1100")
        self.assertIn("text/csv", tipo)
        self.assertIn("Producción", csv.decode("utf-8"))

    def test_errores(self):
        self.assertEqual(self.codigo("/api/linea"), 400)
        self.assertEqual(self.codigo("/api/linea?desde=nan&hasta=1"), 400)
        self.assertEqual(self.codigo("/nada"), 404)
        self.assertEqual(self.codigo("/api/nombre", {"mac": MAC, "nombre": "  "}), 400)
        self.assertEqual(self.codigo("/api/nombre", {"mac": "00:00:00:00:00:00", "nombre": "x"}), 404)

    def test_renombrar(self):
        self.assertEqual(self.codigo("/api/nombre", {"mac": MAC, "nombre": "H2"}), 200)
        self.assertEqual(self.monitor.estado()[0]["nombre"], "H2")


if __name__ == "__main__":
    unittest.main()

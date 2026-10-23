# Monitoreo de torretas

Registra la hora exacta en que cada máquina entra y sale de producción (verde),
mantenimiento (amarillo) y falla (rojo), leyendo los cables de su torreta. Cada
máquina lleva un nodo (ESP32 + módulo de optoacopladores) que avisa por radio
(ESP-NOW) a un receptor conectado por USB a una PC; la PC guarda el historial y
sirve una página con el estado de todas las máquinas.

El plan completo (material, conexiones e instalación) está en el documento
"Plan: monitoreo de torretas en 14 máquinas".

| Carpeta | Qué es |
|---|---|
| `firmware/nodo/` | Programa del ESP32 que va en cada máquina |
| `firmware/receptor/` | Programa del ESP32 que va conectado por USB a la PC |
| `firmware/pruebas/` | Pruebas en PC de la detección de encendido, apagado y parpadeo |
| `servidor/` | Programa de la PC: guarda el historial y sirve la página |

## Conexión del nodo

| Torreta / módulo PC817 | Módulo PC817 / ESP32 |
|---|---|
| Cable verde → entrada 1 | Salida 1 → GPIO 32 |
| Cable amarillo → entrada 2 | Salida 2 → GPIO 33 |
| Cable rojo → entrada 3 | Salida 3 → GPIO 25 |
| (opcional, p. ej. puerta) → entrada 4 | Salida 4 → GPIO 26 |
| Común de la torreta → común de entradas | VCC → **3V3** (nunca 5 V) · GND → GND |

Si el común de la torreta es 0 V, el común de entradas del módulo es su
terminal negativa (G/GND); si el común es +24 V, es su terminal positiva. Mide
antes con el multímetro.

## Cargar los ESP32

1. Instala el Arduino IDE 2.
2. En *Preferencias → URLs adicionales del gestor de tarjetas* agrega
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json` y en el
   *Gestor de tarjetas* instala **esp32** de Espressif.
3. Abre `firmware/receptor/receptor.ino`, elige la tarjeta **ESP32 Dev Module** y
   el puerto, y súbelo al ESP32 del receptor.
4. Abre `firmware/nodo/nodo.ino` y súbelo a cada ESP32 de las máquinas. Todos
   llevan el mismo programa: cada máquina se identifica por su MAC y el nombre se
   le pone en la página.

Los ajustes (pines, canal de radio, tiempos de detección) están en
`firmware/nodo/config.h`. Si cambias `CANAL_RADIO`, cámbialo también al inicio
de `receptor.ino`. Si los colores salen al revés (prendido cuando está
apagado), cambia `ACTIVO_EN_BAJO` a `0`.

En el *Monitor serie* a 115200 baudios el nodo muestra su MAC, cuándo encuentra
al receptor y cada cambio de la torreta. El LED de la placa queda fijo cuando
está conectado al receptor y parpadea cuando no lo encuentra.

## Programa de la PC

Requiere Python 3.9 o más nuevo. En Windows, doble clic en
`servidor/iniciar.bat`, o:

    cd servidor
    py -m pip install -r requirements.txt
    py torretas.py

La página queda en http://localhost:8080 y, desde otra PC o celular de la misma
red, en `http://<IP de esta PC>:8080` (Windows pedirá permiso en el firewall).

Para verla sin hardware, `simular.bat` o `py torretas.py --simular` genera 14
máquinas de prueba con 10 horas de historial, en una base de datos aparte.

`servidor/config.json`:

| Clave | Qué es |
|---|---|
| `puerto_serial` | Puerto del receptor (`"auto"` lo busca solo, o p. ej. `"COM5"`) |
| `http_host`, `http_puerto` | Dónde se sirve la página |
| `base_de_datos` | Archivo SQLite con el historial |
| `segundos_sin_senal` | Tiempo sin mensajes para marcar una máquina "sin señal" |
| `prioridad` | Qué color manda cuando hay varios prendidos y qué estado es |
| `turnos` | Nombre, inicio y fin de cada turno (los que trae son de ejemplo) |

Además de la página: `/api/estado` (estado actual en JSON),
`/api/linea?desde=<epoch>&hasta=<epoch>` (tramos y resumen por máquina) y
`/api/tramos.csv?desde=…&hasta=…` (lo mismo para Excel).

## Pruebas

    cd servidor && python -m unittest test_torretas
    cd firmware/pruebas && g++ -std=c++11 -Wall -o prueba prueba_torreta.cpp && ./prueba

// Ajustes del nodo. Todos los nodos llevan el mismo programa; cada máquina se
// identifica por la dirección MAC de su ESP32 y se le pone nombre en la página.
#pragma once

// Pines del ESP32 conectados a las salidas del módulo de optoacopladores.
#define PIN_VERDE 32
#define PIN_AMARILLO 33
#define PIN_ROJO 25
#define PIN_EXTRA 26  // cuarto canal opcional (p. ej. la puerta); sin conectar no estorba

// Los módulos PC817 normales ponen su salida en LOW cuando el foco está prendido.
#define ACTIVO_EN_BAJO 1

// LED de la placa: fijo = conectado al receptor, parpadeando = sin receptor.
// -1 para no usarlo.
#define PIN_LED 2

// Canal de radio. Debe ser el mismo en el receptor.
#define CANAL_RADIO 1

// Cada cuánto manda "sigo vivo" cuando no hay cambios.
#define LATIDO_MS 15000

// Detección de la torreta (ver torreta.h).
#define REBOTE_MS 30
#define MAX_FASE_MS 1300   // lo más que dura prendido o apagado un foco que parpadea
#define CONFIRMAR_MS 1500  // debe ser mayor que MAX_FASE_MS
#define AGRUPAR_MS 400

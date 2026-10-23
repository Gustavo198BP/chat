// Formato de los mensajes por radio entre los nodos y el receptor.
// Este archivo debe ser idéntico en nodo/ y en receptor/.
#pragma once

#include <stdint.h>

namespace protocolo {

const uint32_t MAGICO = 0x54525254;  // "TRRT"
const uint8_t VERSION = 1;
const int CANALES = 4;  // verde, amarillo, rojo y uno extra

enum Tipo : uint8_t {
  EVENTO = 1,  // cambió la torreta (se reintenta hasta que el receptor lo recibe)
  LATIDO = 2,  // "sigo vivo", con el estado actual
  FARO = 3,    // el receptor anuncia su dirección a los nodos
};

struct __attribute__((packed)) Mensaje {
  uint32_t magico;
  uint8_t version;
  uint8_t tipo;
  uint32_t arranque;  // número al azar de cada encendido del nodo
  uint32_t seq;       // número del evento; en un latido, el del último evento
  uint32_t edad_ms;   // evento: hace cuánto pasó; latido: hace cuánto empezó el estado actual
  uint8_t canales[CANALES];  // 0 apagado, 1 encendido, 2 parpadeando
  uint32_t activo_s;  // segundos desde que encendió el nodo
  uint16_t perdidos;  // eventos descartados porque la cola se llenó
  uint8_t en_cola;    // eventos esperando envío
};

struct __attribute__((packed)) Faro {
  uint32_t magico;
  uint8_t version;
  uint8_t tipo;
};

}  // namespace protocolo

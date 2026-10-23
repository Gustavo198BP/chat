// Nodo: va en cada máquina. Lee los focos de la torreta y manda cada cambio al
// receptor por ESP-NOW. Si el receptor no contesta, guarda los eventos y los
// reintenta; cada evento lleva cuánto tiempo hace que pasó, así que la hora
// registrada es la del cambio aunque llegue tarde.
#include <WiFi.h>
#include <esp_now.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#if __has_include(<esp_random.h>)
#include <esp_random.h>
#endif

#include "compat.h"
#include "config.h"
#include "protocolo.h"
#include "torreta.h"

static const uint8_t PINES[protocolo::CANALES] = {PIN_VERDE, PIN_AMARILLO, PIN_ROJO, PIN_EXTRA};
static const torreta::Ajustes AJUSTES = {REBOTE_MS, MAX_FASE_MS, CONFIRMAR_MS, AGRUPAR_MS};
static const int FALLOS_PARA_CAMBIAR = 5;  // envíos fallidos seguidos antes de aceptar otro receptor

struct Evento {
  uint32_t seq;
  uint64_t t;
  uint8_t canales[protocolo::CANALES];
};

static torreta::Torreta<protocolo::CANALES> luces(AJUSTES);
static torreta::Cola<Evento, 64> cola;
static uint32_t arranque, ultimoSeq;
static uint64_t inicioEstado;
static uint8_t estadoActual[protocolo::CANALES];
static uint16_t perdidos;

// El callback de recepción anota la dirección que anunció el receptor; loop() la usa.
static portMUX_TYPE candado = portMUX_INITIALIZER_UNLOCKED;
static uint8_t macAnunciada[6];
static bool hayAnuncio = false;
static uint8_t receptor[6];
static bool hayReceptor = false;

enum Resultado : uint8_t { PENDIENTE, EXITO, FALLO };
static volatile uint8_t resultado = PENDIENTE;
static bool enVuelo = false;
static bool vueloEsEvento = false;
static uint32_t seqEnVuelo;
static uint64_t tEnvio, proximoIntento, ultimoEnvio;
static uint32_t espera = 200;
static int fallosSeguidos = 0;

static uint64_t ahoraMs() { return esp_timer_get_time() / 1000; }

static void alEnviar(InfoEnvio, esp_now_send_status_t estado) {
  resultado = estado == ESP_NOW_SEND_SUCCESS ? EXITO : FALLO;
}

static void alRecibir(InfoRecepcion info, const uint8_t *datos, int largo) {
  protocolo::Faro faro;
  if (largo != (int)sizeof faro) return;
  memcpy(&faro, datos, sizeof faro);
  if (faro.magico != protocolo::MAGICO || faro.version != protocolo::VERSION ||
      faro.tipo != protocolo::FARO)
    return;
  portENTER_CRITICAL(&candado);
  memcpy(macAnunciada, macOrigen(info), 6);
  hayAnuncio = true;
  portEXIT_CRITICAL(&candado);
}

static void leerEntradas(bool crudos[]) {
  for (int i = 0; i < protocolo::CANALES; i++) {
    crudos[i] = digitalRead(PINES[i]) == (ACTIVO_EN_BAJO ? LOW : HIGH);
  }
}

static void registrarEvento(uint64_t t) {
  Evento e;
  e.seq = ++ultimoSeq;
  e.t = t;
  for (int i = 0; i < protocolo::CANALES; i++) e.canales[i] = luces.clase(i);
  if (!cola.meter(e) && perdidos < UINT16_MAX) perdidos++;
  inicioEstado = t;
  memcpy(estadoActual, e.canales, sizeof estadoActual);
  Serial.printf("Evento %lu: verde=%u amarillo=%u rojo=%u extra=%u (0 apagado, 1 prendido, 2 parpadeo)\n",
                (unsigned long)e.seq, (unsigned)e.canales[0], (unsigned)e.canales[1],
                (unsigned)e.canales[2], (unsigned)e.canales[3]);
}

static void atenderAnuncio() {
  uint8_t mac[6];
  bool hay;
  portENTER_CRITICAL(&candado);
  hay = hayAnuncio;
  hayAnuncio = false;
  memcpy(mac, macAnunciada, 6);
  portEXIT_CRITICAL(&candado);
  if (!hay || enVuelo) return;  // si llega mientras se envía algo, el siguiente faro sirve
  // Sólo se cambia de receptor si el actual dejó de contestar.
  if (hayReceptor && (memcmp(mac, receptor, 6) == 0 || fallosSeguidos < FALLOS_PARA_CAMBIAR)) return;

  if (hayReceptor) esp_now_del_peer(receptor);
  esp_now_peer_info_t par = {};
  memcpy(par.peer_addr, mac, 6);
  par.channel = 0;  // el canal actual
  par.ifidx = WIFI_IF_STA;
  par.encrypt = false;
  if (esp_now_add_peer(&par) != ESP_OK) return;
  memcpy(receptor, mac, 6);
  hayReceptor = true;
  fallosSeguidos = 0;
  espera = 200;
  proximoIntento = 0;
  Serial.printf("Receptor encontrado: %02X:%02X:%02X:%02X:%02X:%02X\n", mac[0], mac[1], mac[2], mac[3],
                mac[4], mac[5]);
}

static void anotarFallo(uint64_t ahora) {
  fallosSeguidos++;
  proximoIntento = ahora + espera;
  espera = espera >= 2500 ? 5000 : espera * 2;
}

static void enviar(uint8_t tipo, uint32_t seq, uint64_t t, const uint8_t canales[], uint64_t ahora) {
  protocolo::Mensaje m;
  memset(&m, 0, sizeof m);
  m.magico = protocolo::MAGICO;
  m.version = protocolo::VERSION;
  m.tipo = tipo;
  m.arranque = arranque;
  m.seq = seq;
  uint64_t edad = ahora - t;
  m.edad_ms = edad > UINT32_MAX ? UINT32_MAX : (uint32_t)edad;
  memcpy(m.canales, canales, protocolo::CANALES);
  m.activo_s = (uint32_t)(ahora / 1000);
  m.perdidos = perdidos;
  m.en_cola = cola.tamano() > 255 ? 255 : (uint8_t)cola.tamano();

  ultimoEnvio = ahora;
  resultado = PENDIENTE;
  if (esp_now_send(receptor, (const uint8_t *)&m, sizeof m) != ESP_OK) {
    anotarFallo(ahora);
    return;
  }
  enVuelo = true;
  vueloEsEvento = tipo == protocolo::EVENTO;
  seqEnVuelo = seq;
  tEnvio = ahora;
}

static void atenderEnvios(uint64_t ahora) {
  if (enVuelo) {
    uint8_t r = resultado;
    if (r == PENDIENTE && ahora - tEnvio < 1000) return;
    enVuelo = false;
    if (r == EXITO) {
      fallosSeguidos = 0;
      espera = 200;
      // Si la cola se llenó durante el envío, el evento enviado ya no está al frente.
      if (vueloEsEvento && !cola.vacia() && cola.frente().seq == seqEnVuelo) cola.sacar();
    } else {
      anotarFallo(ahora);
    }
  }
  if (!hayReceptor || ahora < proximoIntento) return;

  if (!cola.vacia()) {
    const Evento &e = cola.frente();
    enviar(protocolo::EVENTO, e.seq, e.t, e.canales, ahora);
  } else if (ahora - ultimoEnvio >= LATIDO_MS) {
    enviar(protocolo::LATIDO, ultimoSeq, inicioEstado, estadoActual, ahora);
  }
}

static void actualizarLed(uint64_t ahora) {
#if PIN_LED >= 0
  bool conectado = hayReceptor && fallosSeguidos < 3;
  digitalWrite(PIN_LED, conectado || (ahora / 250) % 2 ? HIGH : LOW);
#else
  (void)ahora;
#endif
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < protocolo::CANALES; i++) pinMode(PINES[i], INPUT_PULLUP);
#if PIN_LED >= 0
  pinMode(PIN_LED, OUTPUT);
#endif

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CANAL_RADIO, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("No arrancó ESP-NOW; reiniciando");
    delay(1000);
    ESP.restart();
  }
  esp_now_register_send_cb(alEnviar);
  esp_now_register_recv_cb(alRecibir);

  arranque = esp_random();
  uint64_t ahora = ahoraMs();
  bool crudos[protocolo::CANALES];
  leerEntradas(crudos);
  luces.iniciar(crudos, ahora);
  registrarEvento(ahora);  // cómo estaba la torreta al encender el nodo

  Serial.printf("Nodo %s listo en el canal %d; buscando receptor\n", WiFi.macAddress().c_str(), CANAL_RADIO);
}

void loop() {
  uint64_t ahora = ahoraMs();
  bool crudos[protocolo::CANALES];
  leerEntradas(crudos);
  uint64_t t;
  if (luces.actualizar(crudos, ahora, t)) registrarEvento(t);
  atenderAnuncio();
  atenderEnvios(ahora);
  actualizarLed(ahora);
  delay(5);
}

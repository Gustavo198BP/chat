// Receptor: va conectado por USB a la PC del servidor. Recibe por ESP-NOW los
// mensajes de los nodos y los pasa a la PC, una línea JSON por mensaje.
// También anuncia su dirección cada 2 s para que los nodos lo encuentren solos.
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "compat.h"
#include "protocolo.h"

#define CANAL_RADIO 1  // debe ser el mismo que en nodo/config.h
#define FARO_MS 2000   // cada cuánto anuncia su dirección a los nodos

struct Recibido {
  uint8_t mac[6];
  int rssi;
  protocolo::Mensaje m;
};

static const uint8_t DIFUSION[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static QueueHandle_t recibidos;
static uint32_t ultimoFaro, ultimoAviso;

static void alRecibir(InfoRecepcion info, const uint8_t *datos, int largo) {
  Recibido r;
  if (largo != (int)sizeof r.m) return;
  memcpy(&r.m, datos, sizeof r.m);
  if (r.m.magico != protocolo::MAGICO || r.m.version != protocolo::VERSION) return;
  memcpy(r.mac, macOrigen(info), 6);
  r.rssi = rssiDe(info);
  xQueueSend(recibidos, &r, 0);
}

static void imprimir(const Recibido &r) {
  const protocolo::Mensaje &m = r.m;
  const char *tipo = m.tipo == protocolo::EVENTO ? "evento" : m.tipo == protocolo::LATIDO ? "latido" : NULL;
  if (!tipo) return;
  Serial.printf(
      "{\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\",\"rssi\":%d,\"tipo\":\"%s\",\"arranque\":%lu,"
      "\"seq\":%lu,\"edad_ms\":%lu,\"canales\":[%u,%u,%u,%u],\"activo_s\":%lu,\"perdidos\":%u,"
      "\"en_cola\":%u}\n",
      r.mac[0], r.mac[1], r.mac[2], r.mac[3], r.mac[4], r.mac[5], r.rssi, tipo,
      (unsigned long)m.arranque, (unsigned long)m.seq, (unsigned long)m.edad_ms, (unsigned)m.canales[0],
      (unsigned)m.canales[1], (unsigned)m.canales[2], (unsigned)m.canales[3], (unsigned long)m.activo_s,
      (unsigned)m.perdidos, (unsigned)m.en_cola);
}

static void anunciar() {
  protocolo::Faro faro;
  faro.magico = protocolo::MAGICO;
  faro.version = protocolo::VERSION;
  faro.tipo = protocolo::FARO;
  esp_now_send(DIFUSION, (const uint8_t *)&faro, sizeof faro);
}

static void avisarPC() {
  Serial.printf("{\"tipo\":\"receptor\",\"mac\":\"%s\",\"canal\":%d}\n", WiFi.macAddress().c_str(),
                CANAL_RADIO);
}

void setup() {
  Serial.begin(115200);
  recibidos = xQueueCreate(32, sizeof(Recibido));

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CANAL_RADIO, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("No arrancó ESP-NOW; reiniciando");
    delay(1000);
    ESP.restart();
  }
  esp_now_register_recv_cb(alRecibir);

  esp_now_peer_info_t par = {};
  memcpy(par.peer_addr, DIFUSION, 6);
  par.channel = 0;
  par.ifidx = WIFI_IF_STA;
  par.encrypt = false;
  esp_now_add_peer(&par);

  avisarPC();
}

void loop() {
  Recibido r;
  while (xQueueReceive(recibidos, &r, pdMS_TO_TICKS(10)) == pdTRUE) imprimir(r);

  uint32_t ahora = millis();
  if (ahora - ultimoFaro >= FARO_MS) {
    ultimoFaro = ahora;
    anunciar();
  }
  if (ahora - ultimoAviso >= 60000) {
    ultimoAviso = ahora;
    avisarPC();
  }
}

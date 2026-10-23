// Las firmas de los callbacks de ESP-NOW cambiaron entre versiones del core
// de ESP32 para Arduino (2.x, 3.0–3.2 y 3.3+). Aquí se toma el tipo del primer
// parámetro directo de esp_now.h para que compile con cualquiera.
// Este archivo debe ser idéntico en nodo/ y en receptor/.
#pragma once

#include <esp_idf_version.h>
#include <esp_now.h>

template <typename F>
struct PrimerParametro;
template <typename A, typename B>
struct PrimerParametro<void (*)(A, B)> {
  typedef A Tipo;
};
template <typename A, typename B, typename C>
struct PrimerParametro<void (*)(A, B, C)> {
  typedef A Tipo;
};

typedef PrimerParametro<esp_now_send_cb_t>::Tipo InfoEnvio;
typedef PrimerParametro<esp_now_recv_cb_t>::Tipo InfoRecepcion;

inline const uint8_t *macOrigen(const uint8_t *mac) { return mac; }
inline int rssiDe(const uint8_t *) { return 0; }
#if ESP_IDF_VERSION_MAJOR >= 5
inline const uint8_t *macOrigen(const esp_now_recv_info_t *info) { return info->src_addr; }
inline int rssiDe(const esp_now_recv_info_t *info) { return info->rx_ctrl ? info->rx_ctrl->rssi : 0; }
#endif

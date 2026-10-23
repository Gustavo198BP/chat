// Pruebas de torreta.h en la PC:
//   g++ -std=c++11 -Wall -Wextra -o prueba prueba_torreta.cpp && ./prueba
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <vector>

#include "../nodo/torreta.h"

using namespace torreta;

static const Ajustes AJUSTES = {30, 1300, 1500, 400};
static const uint64_t PASO = 5;  // el nodo lee las entradas cada ~5 ms

static int fallas = 0;
#define REVISAR(cond)                                              \
  do {                                                             \
    if (!(cond)) {                                                 \
      std::printf("  FALLA %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      fallas++;                                                    \
    }                                                              \
  } while (0)

struct Cambio {
  uint64_t visto, desde;
  Clase clase;
};

// Alimenta un foco con la señal cruda f(t) de 0 a fin y anota cada cambio confirmado.
static std::vector<Cambio> correr(std::function<bool(uint64_t)> f, uint64_t fin) {
  Foco foco;
  foco.iniciar(f(0), 0);
  std::vector<Cambio> cambios;
  for (uint64_t t = PASO; t <= fin; t += PASO) {
    if (foco.actualizar(f(t), t, AJUSTES)) cambios.push_back({t, foco.desde(), foco.clase()});
  }
  return cambios;
}

static bool cerca(uint64_t a, uint64_t b, uint64_t tolerancia = 2 * PASO) {
  return (a > b ? a - b : b - a) <= tolerancia;
}

static bool parpadea(uint64_t t, uint64_t inicio, uint64_t fin, uint64_t medio_periodo) {
  if (t < inicio || t >= fin) return false;
  return ((t - inicio) / medio_periodo) % 2 == 0;
}

static void encendido_fijo() {
  std::puts("encendido fijo");
  auto c = correr([](uint64_t t) { return t >= 1000; }, 5000);
  REVISAR(c.size() == 1);
  if (c.size() != 1) return;
  REVISAR(c[0].clase == ENCENDIDO);
  REVISAR(cerca(c[0].desde, 1000));  // fechado cuando prendió, no cuando se confirmó
  REVISAR(c[0].visto >= 1000 + AJUSTES.confirmar_ms);
}

static void apagado_fijo() {
  std::puts("apagado fijo");
  auto c = correr([](uint64_t t) { return t < 3000; }, 8000);
  REVISAR(c.size() == 1);
  if (c.size() != 1) return;
  REVISAR(c[0].clase == APAGADO);
  REVISAR(cerca(c[0].desde, 3000));
}

static void pulso_corto_se_ignora() {
  std::puts("pulso corto se ignora");
  auto c = correr([](uint64_t t) { return t >= 1000 && t < 1300; }, 6000);
  REVISAR(c.empty());
}

static void ruido_se_ignora() {
  std::puts("ruido se ignora");
  // pulsos de 10 ms, más cortos que el rebote
  auto c = correr([](uint64_t t) { return t >= 1000 && t < 1400 && (t / 10) % 3 == 0; }, 6000);
  REVISAR(c.empty());
}

static void rafaga_no_es_parpadeo() {
  std::puts("tres flancos seguidos y luego fijo no es parpadeo");
  // prende en 1000, se cae de 1300 a 1600 y queda prendido
  auto c = correr([](uint64_t t) { return t >= 1000 && (t < 1300 || t >= 1600); }, 8000);
  REVISAR(c.size() == 1);
  if (c.empty()) return;
  REVISAR(c[0].clase == ENCENDIDO);
  REVISAR(cerca(c[0].desde, 1000));
}

static void parpadeo_desde_apagado() {
  std::puts("parpadeo desde apagado");
  auto c = correr([](uint64_t t) { return parpadea(t, 2000, 20000, 500); }, 20000);
  REVISAR(c.size() == 1);
  if (c.empty()) return;
  REVISAR(c[0].clase == PARPADEO);
  REVISAR(cerca(c[0].desde, 2000));
}

static void parpadeo_desde_encendido() {
  std::puts("parpadeo desde encendido (sin pasar por apagado)");
  // encendido hasta 3000, luego apaga 400 / prende 400 ...
  auto f = [](uint64_t t) { return !parpadea(t, 3000, 20000, 400); };
  auto c = correr(f, 20000);
  REVISAR(c.size() == 1);
  if (c.empty()) return;
  REVISAR(c[0].clase == PARPADEO);
  REVISAR(cerca(c[0].desde, 3000));
}

static void parpadeo_lento() {
  std::puts("parpadeo lento (1 s prendido / 1 s apagado)");
  auto c = correr([](uint64_t t) { return parpadea(t, 1000, 30000, 1000); }, 30000);
  REVISAR(c.size() == 1);
  if (c.empty()) return;
  REVISAR(c[0].clase == PARPADEO);
}

static void fin_de_parpadeo() {
  std::puts("deja de parpadear y queda apagado");
  // prende en 2000, 3000, ... 9000; el último flanco es el que apaga en 9500
  auto c = correr([](uint64_t t) { return parpadea(t, 2000, 10000, 500); }, 20000);
  REVISAR(c.size() == 2);
  if (c.size() != 2) return;
  REVISAR(c[1].clase == APAGADO);
  REVISAR(cerca(c[1].desde, 9500));
}

static void fin_de_parpadeo_encendido() {
  std::puts("deja de parpadear y queda encendido");
  // apaga en 9500 y vuelve a prender en 10000, ya fijo
  auto c = correr([](uint64_t t) { return parpadea(t, 2000, 10000, 500) || t >= 10000; }, 20000);
  REVISAR(c.size() == 2);
  if (c.size() != 2) return;
  REVISAR(c[1].clase == ENCENDIDO);
  REVISAR(cerca(c[1].desde, 10000));
}

static void torreta_agrupa() {
  std::puts("torreta: verde se apaga y rojo se prende casi juntos = un evento");
  Torreta<3> torre(AJUSTES);
  bool crudos[3] = {true, false, false};
  torre.iniciar(crudos, 0);
  int eventos = 0;
  uint64_t tEvento = 0;
  for (uint64_t t = PASO; t <= 10000; t += PASO) {
    crudos[0] = t < 4000;   // verde
    crudos[2] = t >= 4080;  // rojo, 80 ms después
    uint64_t te;
    if (torre.actualizar(crudos, t, te)) {
      eventos++;
      tEvento = te;
    }
  }
  REVISAR(eventos == 1);
  REVISAR(cerca(tEvento, 4000));
  REVISAR(torre.clase(0) == APAGADO);
  REVISAR(torre.clase(1) == APAGADO);
  REVISAR(torre.clase(2) == ENCENDIDO);
}

static void torreta_separa() {
  std::puts("torreta: cambios separados por varios segundos = dos eventos");
  Torreta<3> torre(AJUSTES);
  bool crudos[3] = {true, false, false};
  torre.iniciar(crudos, 0);
  std::vector<uint64_t> eventos;
  for (uint64_t t = PASO; t <= 15000; t += PASO) {
    crudos[0] = t < 4000;
    crudos[1] = t >= 8000;
    uint64_t te;
    if (torre.actualizar(crudos, t, te)) eventos.push_back(te);
  }
  REVISAR(eventos.size() == 2);
  if (eventos.size() != 2) return;
  REVISAR(cerca(eventos[0], 4000));
  REVISAR(cerca(eventos[1], 8000));
}

static void cola_circular() {
  std::puts("cola circular");
  Cola<int, 3> cola;
  REVISAR(cola.vacia());
  REVISAR(cola.meter(1));
  REVISAR(cola.meter(2));
  REVISAR(cola.meter(3));
  REVISAR(!cola.meter(4));  // llena: descarta el 1
  REVISAR(cola.tamano() == 3);
  REVISAR(cola.frente() == 2);
  cola.sacar();
  REVISAR(cola.frente() == 3);
  cola.sacar();
  cola.sacar();
  REVISAR(cola.vacia());
  cola.sacar();  // sacar de una cola vacía no hace nada
  REVISAR(cola.vacia());
}

int main() {
  encendido_fijo();
  apagado_fijo();
  pulso_corto_se_ignora();
  ruido_se_ignora();
  rafaga_no_es_parpadeo();
  parpadeo_desde_apagado();
  parpadeo_desde_encendido();
  parpadeo_lento();
  fin_de_parpadeo();
  fin_de_parpadeo_encendido();
  torreta_agrupa();
  torreta_separa();
  cola_circular();
  if (fallas) {
    std::printf("%d revisiones fallaron\n", fallas);
    return EXIT_FAILURE;
  }
  std::puts("todo bien");
  return EXIT_SUCCESS;
}

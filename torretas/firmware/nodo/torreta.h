// Lógica para leer la torreta: decide si cada foco está apagado, encendido o
// parpadeando y fecha cada cambio en el momento real en que empezó.
//
// No depende de Arduino para poder probarla en la PC (ver firmware/pruebas).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace torreta {

enum Clase : uint8_t { APAGADO = 0, ENCENDIDO = 1, PARPADEO = 2 };

struct Ajustes {
  uint32_t rebote_ms;     // la entrada debe quedarse estable este tiempo para aceptarla
  uint32_t max_fase_ms;   // lo más que dura prendido o apagado un foco que parpadea
  uint32_t confirmar_ms;  // un cambio debe sostenerse este tiempo para reportarlo;
                          // debe ser mayor que max_fase_ms
  uint32_t agrupar_ms;    // cambios de varios focos dentro de este tiempo van en un solo evento
};

// Un foco de la torreta.
class Foco {
 public:
  void iniciar(bool crudo, uint64_t ahora) {
    crudo_ = nivel_ = crudo;
    tCrudo_ = ahora;
    nFlancos_ = 0;
    confirmada_ = crudo ? ENCENDIDO : APAGADO;
    desde_ = ahora;
    hayCandidata_ = false;
  }

  // Devuelve true si el estado confirmado del foco cambió con esta lectura.
  bool actualizar(bool crudo, uint64_t ahora, const Ajustes &a) {
    if (crudo != crudo_) {
      crudo_ = crudo;
      tCrudo_ = ahora;
    }
    if (crudo_ != nivel_ && ahora - tCrudo_ >= a.rebote_ms) {
      nivel_ = crudo_;
      registrarFlanco(tCrudo_);
    }

    uint64_t inicio;
    Clase c = instantanea(ahora, a, inicio);
    if (c == confirmada_) {
      hayCandidata_ = false;
      return false;
    }
    if (!hayCandidata_) {
      hayCandidata_ = true;
      candidata_ = c;
      inicioCandidata_ = inicio;
      vistaDesde_ = ahora;
    } else if (c != candidata_) {
      // p. ej. encendido -> apagado -> parpadeo: el cambio empezó en el primer flanco
      candidata_ = c;
      if (inicio < inicioCandidata_) inicioCandidata_ = inicio;
      vistaDesde_ = ahora;
    }
    if (ahora - vistaDesde_ < a.confirmar_ms) return false;

    confirmada_ = candidata_;
    desde_ = inicioCandidata_;
    hayCandidata_ = false;
    return true;
  }

  Clase clase() const { return confirmada_; }
  uint64_t desde() const { return desde_; }

 private:
  void registrarFlanco(uint64_t t) {
    flancos_[2] = flancos_[1];
    flancos_[1] = flancos_[0];
    flancos_[0] = t;
    if (nFlancos_ < 3) nFlancos_++;
  }

  // Lo que se ve en este instante, y desde cuándo. Parpadea si los últimos
  // tres flancos llegaron seguidos y el último fue hace poco.
  Clase instantanea(uint64_t ahora, const Ajustes &a, uint64_t &inicio) const {
    if (nFlancos_ == 3 && ahora - flancos_[0] <= a.max_fase_ms &&
        flancos_[0] - flancos_[1] <= a.max_fase_ms && flancos_[1] - flancos_[2] <= a.max_fase_ms) {
      inicio = flancos_[2];
      return PARPADEO;
    }
    inicio = nFlancos_ ? flancos_[0] : desde_;
    return nivel_ ? ENCENDIDO : APAGADO;
  }

  bool crudo_ = false, nivel_ = false;
  uint64_t tCrudo_ = 0;
  uint64_t flancos_[3] = {0, 0, 0};  // [0] es el más reciente
  uint8_t nFlancos_ = 0;
  Clase confirmada_ = APAGADO;
  uint64_t desde_ = 0;
  bool hayCandidata_ = false;
  Clase candidata_ = APAGADO;
  uint64_t inicioCandidata_ = 0, vistaDesde_ = 0;
};

// Todos los focos de una torreta. Junta en un solo evento los cambios que
// pasan casi al mismo tiempo (p. ej. se apaga el verde y se prende el rojo).
template <size_t N>
class Torreta {
 public:
  explicit Torreta(const Ajustes &a) : a_(a) {}

  void iniciar(const bool crudos[N], uint64_t ahora) {
    for (size_t i = 0; i < N; i++) focos_[i].iniciar(crudos[i], ahora);
    pendiente_ = false;
  }

  // Devuelve true cuando hay un evento listo; t es cuándo empezó el cambio.
  bool actualizar(const bool crudos[N], uint64_t ahora, uint64_t &t) {
    for (size_t i = 0; i < N; i++) {
      if (!focos_[i].actualizar(crudos[i], ahora, a_)) continue;
      uint64_t d = focos_[i].desde();
      if (!pendiente_) {
        pendiente_ = true;
        primerCambio_ = ahora;
        tEvento_ = d;
      } else if (d < tEvento_) {
        tEvento_ = d;
      }
    }
    if (!pendiente_ || ahora - primerCambio_ < a_.agrupar_ms) return false;
    pendiente_ = false;
    t = tEvento_;
    return true;
  }

  Clase clase(size_t i) const { return focos_[i].clase(); }

 private:
  Ajustes a_;
  Foco focos_[N];
  bool pendiente_ = false;
  uint64_t primerCambio_ = 0, tEvento_ = 0;
};

// Cola circular de tamaño fijo. Si se llena, descarta lo más viejo.
template <typename T, size_t N>
class Cola {
 public:
  bool vacia() const { return n_ == 0; }
  size_t tamano() const { return n_; }
  const T &frente() const { return datos_[inicio_]; }

  void sacar() {
    if (!n_) return;
    inicio_ = (inicio_ + 1) % N;
    n_--;
  }

  // Devuelve false si tuvo que descartar el elemento más viejo.
  bool meter(const T &x) {
    bool cupo = n_ < N;
    if (!cupo) sacar();
    datos_[(inicio_ + n_) % N] = x;
    n_++;
    return cupo;
  }

 private:
  T datos_[N];
  size_t inicio_ = 0, n_ = 0;
};

}  // namespace torreta

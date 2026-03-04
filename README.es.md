# MARLOW

**Autonomous Windows Desktop Agent**

*IA que trabaja a tu lado, no en tu lugar*

<p align="center">
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/v/marlow-mcp?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/pyversions/marlow-mcp" alt="Python versions"></a>
  <a href="https://github.com/jarb02/marlow/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jarb02/marlow" alt="License"></a>
  <a href="https://github.com/jarb02/marlow"><img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Platform"></a>
</p>

> :book: [English version](README.md)

Un agente autonomo que ve, entiende y opera cualquier aplicacion de Windows. Construido sobre el Model Context Protocol (MCP), Marlow convierte objetivos en lenguaje natural en acciones reales de escritorio — sin robar tu mouse ni tu teclado.

> **Proyecto en desarrollo activo.** Marlow es ambicioso y esta lejos de ser perfecto. Algunas cosas funcionan increiblemente bien, otras todavia estan encontrando su camino. Si te gusta la idea de un agente de escritorio que aprende de sus errores y planifica como un personaje de videojuegos, estas en el lugar correcto.

---

## Que puede hacer hoy

- **96 herramientas MCP** para controlar cualquier app de Windows
- **Planificacion autonoma en 3 niveles** — regex templates, GOAP con A* search, LLM planner
- **Metodos silenciosos** — UIA patterns, CDP para Electron, COM invisible. No roba el foco
- **Escucha y habla** — VAD neuronal (Silero), TTS con voz mexicana (Jorge), ASR con Whisper + GPU auto-detect
- **Aprende de cada accion** — EMA per app, tiempos adaptativos, reliability tracking, error journal
- **Sistema de interrupciones P0-P4** inspirado en juegos de estrategia en tiempo real
- **EventBus** con 17 eventos tipados y circuit breakers
- **Sensor Fusion** — detecciones de UIA, OCR, CDP y vision unificadas con IoU merge
- **Seguridad desde el commit #1** — kill switch, apps bloqueadas, defensa contra inyeccion de prompts, revision dual de planes
- **Se conecta a Claude Desktop, VS Code, o cualquier cliente MCP**
- **Cero telemetria** — todo se queda en tu maquina

---

## Como funciona

Marlow tiene 4 capas, de lo concreto a lo abstracto:

**1. Herramientas MCP (96 tools)** — Acciones atomicas: click, type, screenshot, OCR, CDP, audio, COM, watchers, schedulers. Cada una devuelve exito o error, nunca crashes.

**2. Escalamiento inteligente** — Cascada de lo barato a lo caro: UIA tree (0 tokens, ~10ms) → OCR (0 tokens, ~200ms) → Screenshot + LLM (~1,500 tokens, ultimo recurso). Si lo barato funciona, lo caro nunca se ejecuta.

**3. Kernel autonomo** — Maquina de estados jerarquica (HSM) de 8 estados, GoalEngine de 13 estados con replan automatico, GOAP planner con A* search, PlanValidator con 3 capas de validacion. El kernel decide que hacer, cuando pausar, y cuando replantear.

**4. Aprendizaje** — ReliabilityTracker (EMA per app), AdaptiveWaits (tiempos que se ajustan solos), Blackboard (estado compartido entre componentes), DesktopWeather (tendencias del desktop en 4 climas), PlanGranularity (apps confiables ejecutan rapido, apps fragiles verifican mas).

---

## Patrones de IA de videojuegos

Marlow usa patrones probados en Game AI. No es marketing — estas tecnicas llevan 20 anos funcionando en juegos AAA:

| Patron | Origen | En Marlow |
|--------|--------|-----------|
| **Utility AI** | F.E.A.R. (2005) | `PreActionScorer` — evalua cada accion con pesos: reliability, urgencia, relevancia, costo |
| **GOAP** | F.E.A.R. (2005) | `GOAPPlanner` — 16 acciones con precondiciones/efectos, busqueda A* para el plan mas barato |
| **AI Director** | Left 4 Dead (2008) | `AdaptiveWaits` + `DesktopWeather` — ajusta dificultad y velocidad segun el estado del desktop |
| **Blackboard** | Halo (2001) | `Blackboard` — almacen centralizado donde todos los componentes comparten estado |
| **Priority Interrupts** | RTS games | `InterruptManager` — 5 niveles de prioridad con suspend/resume tipo LIFO |

---

## Instalacion

```bash
pip install marlow-mcp
```

Agrega a la config de tu cliente MCP:

```json
{
  "mcpServers": {
    "marlow": {
      "command": "marlow"
    }
  }
}
```

Reinicia tu cliente MCP. Listo.

> **Control por voz:** Presiona `Ctrl+Shift+M` para hablar con Marlow. Pregunta *"Que puedes hacer?"* para empezar.

> **Nota:** La libreria `keyboard` requiere **privilegios de administrador** en Windows para hotkeys globales (kill switch `Ctrl+Shift+Escape`, voice hotkey `Ctrl+Shift+M`).

### Extras opcionales

```bash
# OCR con Tesseract (requiere instalar el binario por separado)
pip install marlow-mcp[ocr]
```

Audio, voz y TTS ya vienen incluidos en la instalacion principal. `torch` es opcional (~2GB) — si no esta instalado, el VAD usa deteccion por RMS como fallback.

---

## Seguridad

> Construida desde el primer commit. No son 34 parches despues.

| Capa | Que hace |
|------|----------|
| **Kill Switch** | `Ctrl+Shift+Escape` detiene TODO al instante |
| **Confirmacion** | Cada accion requiere aprobacion (default para usuarios nuevos) |
| **Apps bloqueadas** | Bancos, gestores de contraseñas, authenticators — nunca se acceden |
| **Comandos bloqueados** | `format`, `del /f`, `rm -rf`, `shutdown`, `reg delete` — siempre bloqueados |
| **Sanitizacion** | Tarjetas de credito, SSN, passwords — redactados antes de ir al LLM |
| **Anti prompt-injection** | 21 patrones detectados, spotlighting, neutralizacion automatica |
| **Revision dual de planes** | Planes peligrosos son bloqueados antes de ejecutar |
| **Cero telemetria** | Tus datos nunca salen de tu maquina. **Nunca.** |
| **Logs encriptados** | Audit trail completo con AES-256 |
| **Focus Guard** | Nunca roba tu ventana activa — save/restore en cada tool call |

---

## Estado del proyecto

| Metrica | Valor |
|---------|-------|
| Herramientas MCP | 96 |
| Tests pasando | 878 |
| Fases completadas | 8 de 8 (Master Plan v7) |
| Python | 3.10+ (desarrollo en 3.14) |
| MCP SDK | v1.26.0 |
| Plataforma | Windows 11, dual monitor |

### Fases completadas

| Fase | Nombre |
|------|--------|
| 1 | Percepcion: WindowTracker, DialogType, AppAwareness |
| 2 | Audio: Silero VAD, Piper TTS, GPU detect, voz Jorge |
| 3 | Game AI-A: PreActionScorer, InterruptManager, AdaptiveWaits |
| 4 | EventBus: 17 eventos tipados, pub/sub, circuit breakers |
| 5 | Planning: GOAP A*, DesktopWeather, 3-tier planning |
| 6 | Seguridad: injection defense, dual safety review |
| 7 | Aprendizaje: Blackboard, adaptive plan granularity |
| 8 | AI Vision: Sensor Fusion, Vision Pipeline |

---

## Lo que viene

- **Shadow Mode** — Virtual Desktops invisibles + SendMessage + PrintWindow. El agente trabaja en un escritorio que no ves
- **Training distribuido** — 3 nodos (workstation + laptop + futuro cloud) sincronizando conocimiento
- **Calibracion con datos reales** — Ajustar todos los thresholds con datos de uso real, no intuicion
- **Aceleracion GPU** — Whisper en GPU, VLM local para vision, Moonshine para streaming ASR
- **Publicacion PyPI v0.20.0** — Primera version publica del agente completo

---

## Desarrollo

```bash
git clone https://github.com/jarb02/marlow.git
cd marlow
pip install -e ".[ocr]"
python -m pytest tests/ -v
```

---

## Contribuir

Marlow es un proyecto personal que se convirtio en algo mas grande de lo esperado. Si te interesa:

- **Probar y reportar bugs** — [Abre un issue](https://github.com/jarb02/marlow/issues). Issues en español son bienvenidos
- **Compartir ideas** — Si tienes una idea para un patron de Game AI, un caso de uso, o una mejora, me encantaria escucharla
- **Usar Marlow** — La mejor forma de contribuir es usarlo y contarme que funciono y que no

---

## Licencia

MIT — Free and open source. See [LICENSE](LICENSE).

---

## Security Policy

Found a vulnerability? Please report it responsibly. See [SECURITY.md](.github/SECURITY.md).

---

<p align="center">
  <em>Marlow — Your friendly ghost in the machine</em>
</p>

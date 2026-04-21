Actúa como un **arquitecto de software senior**, **ingeniero full stack**, **ingeniero de IA aplicada**, **diseñador de sistemas autónomos con control** y **arquitecto de plataformas de trading research y execution**. Quiero que me ayudes a diseñar e implementar una **app de trading research + execution** basada en el patrón **“agente único + herramientas”**, con **memoria persistente**, **journal** y un **flujo PDCA orientado a eventos**.

## Objetivo general

Quiero construir una plataforma impulsada por IA para **acciones del mercado norteamericano**, centrada en oportunidades de inversión con horizonte temporal de **días, semanas y largo plazo**. No me interesa el scalping.

La plataforma debe permitir que una **IA central única** supervise el flujo completo de extremo a extremo:

* formular hipótesis
* gestionar estrategias
* configurar y lanzar screeners
* crear watchlists por hipótesis o setup
* analizar candidatos
* decidir entradas y salidas
* abrir y seguir posiciones
* registrar decisiones y razonamientos
* revisar resultados
* proponer mejoras
* aplicar mejora continua siguiendo **PDCA**

No quiero una arquitectura multiagente como solución principal. Quiero una arquitectura de **una sola IA central con visión global**, apoyada por **herramientas o módulos especializados**.

La IA central debe tener acceso a todo el contexto del flujo y poder razonar sobre el sistema completo.

---

## Aclaración fundamental sobre PDCA

Quiero que interpretes **PDCA como una filosofía de trabajo y mejora continua orientada a eventos**, no como un proceso batch diario que se ejecuta una vez al día.

En esta app, el PDCA debe estar ligado al **ciclo de vida de hipótesis, estrategias, setups, operaciones, resultados y mejoras**.

Ejemplo conceptual:

* En **PLAN**, la IA formula una hipótesis, por ejemplo:
  “En acciones USA de calidad y tendencia alcista, un pullback ordenado dentro de una estructura alcista puede ofrecer una entrada con buena esperanza matemática si se confirma con señales de fortaleza y contexto favorable”.

* En **DO**, la IA operacionaliza esa hipótesis:

  * selecciona o define la estrategia
  * configura el screener
  * escanea el mercado
  * detecta candidatos
  * los analiza
  * abre posiciones si procede
  * registra el razonamiento
  * hace seguimiento durante horas, días o semanas

* En **CHECK**, la IA revisa el resultado observado cuando se cierra una posición o cuando existe muestra suficiente:

  * compara resultados con la hipótesis inicial
  * revisa expectativas frente a realidad
  * extrae conclusiones

* En **ACT**, la IA propone mejoras:

  * screeners
  * filtros
  * señales
  * pesos
  * reglas de entrada/salida
  * criterios de análisis
  * nuevas versiones
  * registro de mejoras en memoria y journal
  * validación antes de pasar a live

Por tanto, el sistema debe ser **event-driven**. El PDCA se activa por eventos relevantes, no por una secuencia diaria rígida.

Eventos importantes pueden ser, por ejemplo:

* creación o revisión de una hipótesis
* activación o modificación de una estrategia
* nueva versión de un screener
* ejecución de un scan
* incorporación de un ticker a una watchlist
* apertura de posición
* actualización relevante del contexto
* modificación de stop o plan
* cierre de posición
* revisión de resultados
* propuesta de mejora
* promoción o retirada de una versión

---

## Aclaración conceptual importante

Quiero que el sistema distinga claramente entre:

* **hipótesis**
* **estrategias**
* **setups**
* **señales técnicas**
* **reglas de ejecución**

No quiero que señales técnicas aisladas como **golden cross** o **death cross** se modelen como estrategias de inversión completas.

Estas señales deben tratarse como:

* filtros de contexto
* confirmaciones técnicas
* triggers de timing
* condiciones auxiliares de validación

dentro de estrategias más amplias como:

* breakout long
* pullback long
* breakdown short
* squeeze breakout
* mean reversion
* trend following
* momentum de medio/largo plazo
* estrategias de posición de largo plazo

Por ejemplo:

* un **golden cross** puede actuar como confirmación de sesgo alcista o trigger de entrada dentro de una estrategia de posición o de continuación de tendencia;
* un **death cross** puede actuar como señal de deterioro estructural o filtro bajista dentro de una estrategia short o de salida.

El sistema debe evitar confundir una **señal técnica aislada** con una **estrategia operativa completa**.

---

## Jerarquía conceptual que quiero en la app

Quiero que el diseño de la app refleje explícitamente esta jerarquía:

### 1. Hipótesis

Idea o proposición que se quiere poner a prueba.

Ejemplo:

* “En acciones USA con tendencia primaria alcista, los pullbacks ordenados tienden a reanudar la subida cuando el contexto general acompaña”.

### 2. Estrategia

Marco operativo general.

Ejemplo:

* pullback long en tendencia
* breakout long
* strategy de posición de largo plazo
* breakdown short

### 3. Setup

Patrón concreto buscado dentro de la estrategia.

Ejemplo:

* retroceso hacia SMA20 o SMA50 dentro de una tendencia alcista
* ruptura de máximo de 20 sesiones tras consolidación
* compresión de volatilidad previa a expansión

### 4. Señales técnicas

Filtros, confirmaciones o triggers que ayudan a validar o temporizar el setup.

Ejemplos:

* golden cross
* death cross
* precio > SMA200
* RSI entre 45 y 60
* volumen relativo alto
* expansión de volumen
* anchura de Bandas de Bollinger baja
* ADX > 20
* ruptura de máximo reciente
* vela de giro

### 5. Reglas de ejecución

Cómo se entra, se sigue y se sale de la operación.

Ejemplos:

* trigger de entrada
* ubicación del stop
* take profit
* trailing stop
* tamaño de posición
* criterios de cierre total o parcial
* invalidación de la tesis

Quiero que esta jerarquía aparezca claramente en el diseño funcional, en el modelo de datos y en la interfaz.

---

## Principios de diseño

Diseña la solución siguiendo estos principios:

### 1. Agente único con visión global

La IA central debe conocer:

* la hipótesis
* la estrategia
* el setup
* el screener usado
* el análisis técnico
* el análisis visual
* el razonamiento de entrada
* la evolución de la operación
* el cierre
* la revisión posterior
* las mejoras propuestas

No quiero varios agentes independientes que fragmenten el contexto.

### 2. Herramientas especializadas

La IA central debe apoyarse en herramientas o módulos con responsabilidades claras, por ejemplo:

* hypothesis manager
* strategy registry
* setup registry
* signal registry
* skill registry
* skill router
* screener builder
* screener runner
* market data / OHLCV analyzer
* chart renderer
* visual chart analysis
* execution manager
* portfolio tracker
* journal writer
* memory manager
* review / improvement manager

### 2.b Skills procedurales separadas de las herramientas

Quiero que el sistema distinga explícitamente entre:

* **tool**: función o integración ejecutable
* **skill**: procedimiento reutilizable que enseña **cuándo** y **cómo**
  usar tools o criterios del sistema
* **playbook / estrategia**: marco operativo amplio
* **regla aprendida**: ajuste validado que modifica el comportamiento futuro

Una skill no es una integración técnica ni una estrategia completa.
Es una pieza operativa intermedia, por ejemplo:

* `analizar_ticker_post_noticia`
* `evaluar_breakout_diario`
* `detectar_condiciones_risk_off`
* `hacer_post_mortem_de_trade`
* `clasificar_error_operativo`
* `proponer_mejora_pdca`

Cada skill debe declarar al menos:

* nombre / código
* objetivo
* descripción corta
* cuándo usarla
* cuándo no usarla
* inputs requeridos
* salida esperada
* herramientas o contextos que puede necesitar
* incompatibilidades o dependencias, si aplica

No quiero meter siempre todas las skills completas en el contexto del modelo.
Prefiero:

* un **catálogo compacto** de skills
* selección contextual de candidatas
* carga **on-demand** de la skill elegida

La activación de skills debe combinar:

* router determinista por estado/contexto
* selección o priorización del LLM sobre un conjunto pequeño de candidatas

No quiero que el LLM elija entre todo el universo de skills sin filtro previo.

### 3. Trazabilidad total

Cada decisión debe quedar registrada y ser reconstruible.

Debe poder reconstruirse la secuencia completa:

* hipótesis formulada
* estrategia seleccionada
* setup elegido
* señales activas
* versión usada
* configuración del screener
* motivo de selección del ticker
* análisis cuantitativo
* análisis visual
* decisión
* ejecución
* seguimiento
* salida
* revisión posterior
* mejora propuesta

### 4. Mejora continua controlada

La IA puede proponer mejoras, pero no aplicarlas sin control.

Debe existir:

* versionado
* baseline
* comparación
* validación
* paper testing
* promoción controlada a live

### 5. Memoria persistente útil

La IA debe recordar:

* hipótesis activas
* estrategias activas
* setups definidos
* señales relevantes utilizadas
* configuraciones vigentes
* watchlists por hipótesis y setup
* posiciones abiertas
* decisiones recientes
* observaciones relevantes de revisiones previas
* lecciones aprendidas
* mejoras propuestas y su estado
* skills activas, skills en borrador y skills retiradas

### 6. Enfoque realista

Quiero una primera versión funcional y ampliable.
Prioriza:

* claridad
* mantenibilidad
* trazabilidad
* observabilidad
* control
* mejora incremental

sobre sofisticación innecesaria.

---

## Qué quiero que construyas conmigo

Quiero que me ayudes a diseñar y después implementar una app con estos componentes.

---

## Arquitectura de aprendizaje procedimental

Quiero que el diseño refleje explícitamente estas cuatro capas:

### 1. Herramientas

Capacidades ejecutables del sistema:

* market data
* news
* calendar
* charts
* execution
* broker / paper trading
* reportes

### 2. Memoria

Persistencia de experiencia y contexto:

* trades
* journal
* revisiones
* errores repetidos
* contexto de mercado
* reglas propuestas
* lecciones validadas o descartadas

### 3. Skills

Procedimientos reutilizables que ayudan al agente a operar con consistencia.

Las skills deben servir para:

* análisis
* diagnóstico
* revisión
* mejora

No quiero que la app “aprenda” solo por acumular texto en memoria.
Quiero que aprenda cuando convierte experiencia pasada en **cambios
reutilizables de comportamiento**.

### 4. Política de mejora

Debe existir un flujo explícito para decidir cuándo una observación pasa a ser:

* nota
* lección
* hipótesis
* regla temporal
* skill consolidada

Ese flujo debe exigir:

* muestra mínima
* evidencia trazable
* validación suficiente
* promoción controlada

No quiero que una observación aislada se convierta automáticamente en una skill
activa ni en una regla de ejecución live.

### Regla fundamental de aprendizaje

El bot no debe considerarse “más listo” por tener más tools.

Debe considerarse que aprende cuando:

* detecta un patrón repetido
* formula una hipótesis
* la valida con datos o revisión suficiente
* y modifica el procedimiento futuro de forma controlada

Por tanto, el sistema debe registrar no solo:

* qué tool usó
* qué decisión tomó

sino también:

* qué skill aplicó
* por qué se activó
* si fue útil
* si faltó otra skill complementaria
* si la experiencia justifica promover una mejora reutilizable

---

## Arquitectura de research y backtesting

Quiero que el bot disponga de una capacidad explícita de `research/backtesting`,
pero no como un añadido aislado ni como una excusa para sobreoptimizar.

El objetivo no es que el bot "haga más cosas", sino que pueda recorrer este
flujo de forma controlada:

* formular una hipótesis
* traducirla a una especificación de backtest
* ejecutar validación histórica reproducible
* separar `in-sample`, `out-of-sample` y, cuando sea razonable, `walk-forward`
* registrar métricas, decisiones y supuestos
* decidir si la hipótesis merece:

  * descarte
  * research adicional
  * paper trading
  * promoción a regla o skill candidata

### Principio clave

No quiero atar el bot al backtester propietario de un broker ni a una librería
externa concreta como dependencia estructural del core.

Prefiero:

* una interfaz propia y pequeña de `backtest engine`
* desplegar el motor de backtesting en un **servicio externo**
* mantener este repo como cliente/orquestador
* posibilidad de añadir adaptadores externos más adelante

### Herramientas externas evaluadas

Herramientas gratuitas o abiertas que sí merece la pena considerar como
referencia o posible integración futura:

* `Backtesting.py`
* `Backtrader`
* `LEAN`
* `vectorbt`

Pero para el core de esta app, la decisión inicial es:

* **no acoplar la v1 a una de esas librerías dentro del runtime principal**
* construir un motor nativo y acotado para validación diaria reproducible
* alojarlo preferiblemente en un **repositorio/servicio separado**
* dejar una interfaz para conectar motores externos después

Razones:

* el stack actual del bot es principalmente `1D + OHLCV + contexto`
* ya existe una base de `trade replay` y validación de candidatos
* varias librerías externas añaden peso, complejidad o restricciones de
  licencia que no quiero meter prematuramente en el core
* separar el servicio reduce el crecimiento del monolito principal
* una API dedicada permite reutilizar el backtesting desde otras apps
* quiero que el comportamiento del bot siga siendo trazable y consistente con
  sus propias reglas, no con una caja negra externa

### Motor elegido para la v1

La v1 debe introducir un motor propio, por ejemplo:

* `native_daily_ohlcv_replay`

Este motor debe:

* operar sobre `OHLCV` diario
* reutilizar, en lo posible, las mismas reglas y contextos que ya usa el bot
  para decidir en runtime
* soportar costes y fricción básicos:

  * comisión
  * slippage
  * tamaño de posición
  * invalidación por stop
  * take profit / trailing si aplica

No quiero que la v1 intente resolver todavía:

* order book
* microestructura
* intradía fino
* simulación exacta de ejecución real
* optimización masiva de parámetros
* portfolio construction compleja

### Frontera de despliegue preferida

El motor propio v1 no tiene por qué vivir dentro de este repositorio.

La frontera preferida es:

* este repo = `trading brain / orchestrator / journal / memory / policy`
* servicio externo = `historical research / backtests / experiment runs`

Eso implica:

* otro repositorio dedicado
* despliegue separado, por ejemplo en un VPS
* consumo vía API versionada
* persistencia local mínima en este bot:

  * `backtest_spec_ref`
  * `backtest_run_id`
  * `status`
  * métricas resumidas
  * enlaces a artefactos

Quiero que el bot siga pudiendo trabajar aunque el servicio de backtesting no
esté disponible. Debe tratarse como una capacidad opcional y desacoplada,
similar a otros providers externos.

### Candidato externo prioritario

Si en una fase posterior se decide integrar un motor externo más serio, el
primer candidato a evaluar como sidecar o adaptador debería ser `LEAN`, no por
ser el más simple sino por ser el más limpio para una integración seria y
broker-independiente.

No obstante, eso debe llegar después del motor nativo v1.

### Contrato mínimo de backtesting

Quiero una abstracción pequeña y pragmática, algo del estilo:

* `BacktestSpec`
* `BacktestRun`
* `BacktestTrade`
* `BacktestMetricSnapshot`
* `BacktestEngine`

Donde:

* `BacktestSpec` describe la hipótesis, setup o skill que se quiere validar
* `BacktestEngine` ejecuta la simulación sobre datos históricos
* `BacktestRun` guarda configuración, particiones temporales, supuestos y
  resultados
* `BacktestTrade` guarda la secuencia simulada de entradas y salidas
* `BacktestMetricSnapshot` resume métricas comparables y trazables

Además, el servicio externo debe exponer como mínimo:

* `GET /health`
* `GET /capabilities`
* `POST /api/v1/backtests`
* `GET /api/v1/backtests/{run_id}`
* `GET /api/v1/backtests/{run_id}/trades`
* `GET /api/v1/backtests/{run_id}/equity`
* `GET /api/v1/backtests/{run_id}/metrics`
* `POST /api/v1/backtests/{run_id}/cancel`

Y la especificación debe ser declarativa, no código arbitrario ejecutado
remotamente.

### Integración con el flujo actual

El módulo de backtesting debe encajar con piezas ya existentes:

* `hypotheses`
* `strategies`
* `setups`
* `signal_definitions`
* `skill_candidates`
* `candidate_validation_snapshots`
* `research_tasks`
* `journal`
* `memory`

El flujo deseado es:

1. una hipótesis, lesson o skill candidata solicita validación
2. se genera un `BacktestSpec`
3. el motor ejecuta un `BacktestRun`
4. el resultado queda persistido y enlazado a la entidad origen
5. el sistema decide si:

   * rechaza
   * pide más research
   * abre paper validation
   * promueve a revisión candidata

### MVP concreto que quiero primero

La primera versión debe ser estrecha y útil:

* universo: acciones USA ya soportadas por el bot
* timeframe: `1D`
* datos: `OHLCV` e indicadores/contextos ya presentes en el sistema
* sesgo inicial: sobre todo `long`
* foco: validar setups y skills ya existentes antes de abrir la puerta a ideas
  más complejas

Capacidades mínimas del MVP:

* ejecutar backtests reproducibles desde una especificación persistente
* soportar `in-sample` y `out-of-sample`
* calcular métricas básicas:

  * trade count
  * win rate
  * expectancy
  * profit factor
  * max drawdown
  * return
  * exposure

* guardar trades simulados y equity curve
* enlazar resultados con `research_task`, `hypothesis`, `strategy_version` o
  `skill_candidate`
* reutilizar estos resultados dentro de `CHECK` y `ACT`

La preferencia de implementación para este MVP es:

* motor nativo en servicio externo
* este bot como cliente
* sin UI compleja al principio
* sin optimizador masivo ni notebooks embebidos en este repo

### Reglas de seguridad metodológica

No quiero que un backtest aislado cambie el comportamiento live del bot.

Por tanto:

* una sola corrida no basta para promover cambios
* debe existir separación entre muestra de diseño y muestra de validación
* cuando sea posible, prefiero `walk-forward` a una única ventana estática
* toda promoción debe pasar además por `paper` o por otra forma de validación
  complementaria

### Cómo debe aprender el bot usando backtesting

La secuencia correcta debe ser:

* observación
* hipótesis
* backtest
* validación fuera de muestra
* paper/replay
* promoción controlada

No quiero un sistema que:

* busque parámetros hasta encontrar uno que "funciona"
* active cambios automáticamente por una mejora aparente
* confunda ajuste histórico con edge real

El backtesting debe servir para aprender mejor, no para generar overfitting.

---

## 1. Catálogo de hipótesis, estrategias, setups y señales

La app debe poder manejar varias familias de estrategias para acciones USA, por ejemplo:

* breakout long
* pullback long
* breakdown short
* squeeze breakout
* mean reversion
* trend following
* momentum de medio/largo plazo
* estrategias de posición de largo plazo

Cada **hipótesis** debe tener:

* id
* nombre
* descripción
* proposición a validar
* mercado
* horizonte temporal
* sesgo long/short
* criterios de éxito esperados
* estado
* versión

Cada **estrategia** debe tener:

* id
* nombre
* descripción
* hipótesis asociada
* mercado
* horizonte temporal
* sesgo
* reglas generales
* parámetros
* versión
* estado: drafted / active / paper / live / paused / retired

Cada **setup** debe tener:

* id
* nombre
* estrategia asociada
* descripción del patrón buscado
* contexto ideal
* condiciones necesarias
* versión

Cada **señal técnica** debe tener:

* id
* nombre
* tipo: filtro / confirmación / trigger
* definición
* parámetros
* condiciones de activación
* uso previsto dentro de setups y estrategias

Ejemplos de señales:

* golden cross
* death cross
* cruce de medias
* precio > SMA200
* Relative Volume > 1.5
* RSI entre rangos
* Bandas de Bollinger
* ADX
* ATR
* ruptura de máximo 20/50 sesiones
* rebote en media
* expansión de volumen

---

## 2. Screeners configurables

La IA debe poder:

* definir screeners en lenguaje estructurado
* guardar filtros
* versionarlos
* ejecutarlos
* asociarlos a hipótesis, estrategias o setups concretos

Ejemplos de filtros:

* precio > SMA50
* precio > SMA200
* SMA20 > SMA50
* Relative Volume > 1.5
* RSI entre 55 y 70
* cerca de máximos 20/50 sesiones
* Bandas de Bollinger
* ADX
* ATR
* performance 1W / 1M
* liquidez mínima
* capitalización
* sector
* volatilidad
* spread aceptable

Importante:
los screeners no deben limitarse a señales aisladas. Deben expresar una lógica coherente con la estrategia y el setup.

---

## 3. Watchlists por hipótesis o setup

La IA debe poder crear y mantener listas de seguimiento como:

* breakout_long_candidates
* pullback_long_candidates
* breakdown_short_candidates
* long_term_momentum_watchlist
* setup_watchlist_pullback_trend
* setup_watchlist_squeeze_breakout

Cada ticker en una watchlist debe tener:

* ticker
* hipótesis asociada
* estrategia asociada
* setup asociado
* señales observadas
* score
* fecha de incorporación
* motivo
* métricas clave
* estado: active / watching / entered / discarded / closed

---

## 4. Análisis híbrido de candidatos

Quiero combinar dos tipos de análisis.

### A. Análisis cuantitativo

Sobre OHLCV e indicadores:

* tendencia
* momentum
* volumen relativo
* cercanía a máximos/mínimos
* ATR
* ADX
* Bollinger
* Donchian
* riesgo/beneficio
* niveles de entrada, stop y take profit
* contexto del mercado general
* contexto sectorial
* estructura de medias

### B. Análisis visual

Quiero que la app pueda generar capturas estandarizadas de gráficos y permitir que una IA visual las analice para valorar:

* claridad estructural
* calidad del setup
* consolidación
* soporte o resistencia visible
* si parece breakout, pullback, squeeze, continuación, deterioro, etc.

La IA central debe fusionar ambos análisis.

Importante:
la visión artificial debe ser una **capa auxiliar**, no el único criterio.

---

## 5. Gestión de posiciones

La app debe poder:

* registrar entradas
* almacenar stop loss y take profit
* monitorizar posiciones abiertas
* registrar modificaciones del plan
* decidir salidas
* registrar cierres parciales o totales
* asociar cada operación a:

  * hipótesis
  * estrategia
  * setup
  * señales activas
  * versión

También debe registrar:

* motivo de entrada
* motivo de salida
* cambios durante la operación
* actualización de tesis si el contexto cambia

---

## 6. Journal y memoria de decisiones

La app debe incluir un journal rico, no solo logs técnicos.

Debe registrar:

* fecha y hora
* evento
* hipótesis
* estrategia
* setup
* señales relevantes
* ticker
* contexto del mercado
* datos clave observados
* razonamiento de la IA
* decisión tomada
* expectativas
* resultado posterior
* reflexión posterior
* lecciones aprendidas
* skill usada o skills activadas
* si la skill fue suficiente, inadecuada o incompleta

Quiero que el journal sirva para:

* auditoría
* aprendizaje
* memoria de trabajo del agente
* mejora continua
* revisión histórica de decisiones

La memoria del agente debe poder recuperar:

* decisiones anteriores
* hipótesis relacionadas
* estrategias activas o pasadas
* setups frecuentes
* conclusiones previas
* mejoras propuestas y aprobadas
* skills validadas
* skills candidatas o retiradas

---

## 7. PDCA orientado a eventos

Quiero que el sistema implemente explícitamente un flujo PDCA basado en eventos.

### PLAN

Se activa cuando:

* nace una nueva hipótesis
* se revisa una hipótesis
* se diseña o revisa una estrategia
* se define o ajusta un setup
* se crean o modifican señales y filtros
* se redefine un screener
* se decide activar una nueva versión

En esta fase la IA debe poder:

* formular la hipótesis
* definir criterios de éxito
* seleccionar mercado y universo
* seleccionar timeframe
* elegir estrategia
* definir setup
* seleccionar señales relevantes
* definir screener
* definir reglas de entrada
* definir reglas de salida
* definir reglas de seguimiento
* registrar expectativas iniciales

### DO

Se activa cuando la hipótesis pasa a operación.

En esta fase la IA debe poder:

* ejecutar screeners
* detectar tickers candidatos
* analizarlos
* incorporarlos a watchlists
* decidir si hay operación o no
* abrir posiciones
* registrar el razonamiento seguido
* hacer seguimiento durante horas, días o semanas
* actualizar contexto y tesis operativa dejando trazabilidad

### CHECK

Se activa cuando:

* se cierra una posición
* se alcanza una muestra mínima de operaciones
* se acumula evidencia suficiente para revisar la hipótesis o la estrategia

En esta fase la IA debe poder:

* analizar resultados
* comparar con expectativas
* revisar el comportamiento observado
* evaluar si la hipótesis conserva validez
* evaluar si la skill o procedimiento usado fue adecuado
* registrar conclusiones en journal y memoria

### ACT

Se activa cuando existe una propuesta razonable de mejora.

En esta fase la IA debe poder:

* proponer mejoras en screeners
* proponer mejoras en filtros, señales, pesos y criterios
* ajustar reglas de entrada/salida o seguimiento
* crear nuevas versiones
* promover una observación a hipótesis o regla temporal
* proponer creación o actualización de una skill
* registrar mejoras en memoria y journal
* dejar la mejora pendiente de validación o paper testing antes de pasar a live

---

## Requisitos técnicos

Ayúdame a proponer una arquitectura moderna, clara y realista.

### Backend

Sugiere stack y justifica.
Puedes proponer, por ejemplo:

* Python con FastAPI
* sistema event-driven
* scheduler
* workers
* cola de tareas
* motor de reglas
* integración con APIs de market data y broker

### Frontend

Quiero una interfaz moderna y clara.
Puedes proponer, por ejemplo:

* React + TypeScript
* Vite
* Tailwind
* paneles para:

  * hipótesis
  * estrategias
  * setups
  * señales
  * screeners
  * watchlists
  * posiciones
  * journal
  * revisiones PDCA
  * mejoras propuestas

### Base de datos

Propón una base de datos y esquema inicial para persistir:

* hipótesis
* estrategias
* setups
* señales
* screeners
* watchlists
* análisis
* posiciones
* journal
* revisiones PDCA
* propuestas de mejora
* skills
* versiones de skills
* evaluaciones de uso de skills
* configuraciones
* memoria persistente del agente

### Integraciones

Quiero contemplar:

* market data provider
* screener source o screener propio
* broker API
* generación de gráficos/capturas
* modelo LLM
* análisis visual

---

## Qué espero de tu respuesta

No me des una respuesta superficial. Quiero profundidad, estructura y pragmatismo.

Quiero que me entregues, en este orden:

### Fase 1: visión del sistema

* resumen ejecutivo de la arquitectura propuesta
* explicación del patrón “agente único + herramientas”
* explicación de por qué el PDCA debe modelarse por eventos y no como batch diario
* explicación de la jerarquía hipótesis → estrategia → setup → señales → ejecución
* diagrama conceptual del sistema

### Fase 2: diseño funcional

* módulos funcionales
* responsabilidades de cada módulo
* flujo de datos entre módulos
* catálogo de eventos del sistema
* cómo se implementa el PDCA orientado a eventos
* cómo funciona el router de skills
* cómo funciona la memoria del agente
* cómo funciona el journal

### Fase 3: modelo de datos

* entidades principales
* relaciones
* campos importantes
* propuesta de tablas o colecciones
* versionado de hipótesis, estrategias, setups, señales, screeners y skills

### Fase 4: arquitectura técnica

* stack recomendado
* backend
* frontend
* event bus o mecanismo equivalente
* scheduler / workers
* servicios internos
* persistencia
* observabilidad y logs

### Fase 5: MVP realista

Quiero un MVP reducido pero útil, por ejemplo:

* 2 o 3 estrategias iniciales
* 1 flujo PDCA orientado a eventos
* 1 journal funcional
* 1 memoria útil
* 3 o 4 skills iniciales útiles
* 1 integración inicial de market data
* 1 sistema de paper trading antes de live trading

### Fase 6: roadmap

* versión 1
* versión 2
* versión 3
* mejoras futuras

#### Roadmap específico del loop de aprendizaje

Además del roadmap general del producto, quiero un roadmap explícito para la
evolución del loop de aprendizaje inspirado en OpenClaw/Hermes pero adaptado a
trading con control de riesgo fuerte.

El principio rector es este:

* no copiar frameworks enteros
* sí copiar lo que mejora memoria procedimental, disciplina operativa y
  aprendizaje reutilizable
* nunca permitir auto-modificación rápida de reglas live sin validación

Estado actual esperado del proyecto:

* `tools` separadas de `skills`
* memoria persistente
* journal y reviews
* skills catalogadas y validadas
* claims/evidence persistidos
* skills y claims cargados on-demand en runtime

Lo que debe venir después, en este orden:

##### Prioridad 1: workflows explícitos de aprendizaje

Convertir revisiones y mantenimiento del conocimiento en workflows de primer
nivel, no solo en efectos secundarios del loop principal.

Workflows iniciales deseados:

* `premarket_review`
* `postmarket_review`
* `weekly_skill_audit`
* `stale_claim_review`
* `regime_shift_review`

Cada workflow debe tener:

* trigger claro
* inputs requeridos
* skills aplicables
* outputs persistidos
* criterios de cierre

##### Prioridad 2: detección de skill gaps

El sistema debe registrar no solo qué skill se usó, sino también cuándo:

* faltó una skill
* una skill fue insuficiente
* faltó una skill complementaria
* el operador corrigió una decisión del bot

Eso debe poder generar:

* `gap_note`
* `research_task`
* `claim`
* `skill_candidate`

##### Prioridad 3: puente explícito `claim -> skill_candidate`

No quiero que claims y skills vivan como capas paralelas sin conexión fuerte.

Debe existir un camino formal:

* claim repetido o validado
* evidencia suficiente
* propuesta procedimental concreta
* `skill_candidate`
* validación replay/paper
* `validated_skill_revision`

##### Prioridad 4: observabilidad del presupuesto cognitivo

El runtime debe dejar claro cuánto contexto consume cada capa:

* doctrine fija
* market state
* runtime skills
* durable claims
* journal reciente
* failure patterns

Esto debe servir para:

* no saturar el prompt
* entender qué contexto cambió la decisión
* reducir coste cognitivo sin perder edge

##### Prioridad 5: skills más portables y versionables

Más adelante, las skills deben poder vivir también como artefactos más
portables y revisables, por ejemplo:

* `SKILL.md`
* YAML estructurado
* export/import desde la base de datos

No es prioritario para la primera fase, pero sí importante para:

* diff
* review
* test
* portabilidad

##### Prioridad 6: destilación y compacción de memoria

La memoria no debe crecer solo acumulando objetos.

El sistema debe poder:

* fusionar claims duplicados
* degradar claims viejos
* retirar conocimiento superado
* resumir periodos largos
* distinguir entre conocimiento actual y conocimiento histórico

#### Entregables esperados por slice

Cada nueva slice del loop de aprendizaje debe dejar:

* modelo o contrato claro
* persistencia
* trazabilidad en journal/memory/context
* API o surface operativa
* tests dirigidos
* actualización de `IMPLEMENTATION_JOURNAL.md`

### Fase 7: implementación

Después del diseño, ayúdame a empezar a construirlo.
Por favor:

* propón estructura de carpetas
* sugiere nombres de módulos y archivos
* define endpoints o servicios clave
* define el esquema inicial de base de datos
* empieza por el backend
* luego el frontend

---

## Estilo de trabajo que quiero de ti

* No me abrumes con teoría vacía.
* Sé concreto, técnico y pragmático.
* Justifica las decisiones importantes.
* Cuando haya varias opciones, compáralas y recomienda una.
* Piensa en escalabilidad sin perder realismo.
* Prioriza una arquitectura que pueda empezar a funcionar pronto.
* Si propones una arquitectura compleja, primero dame una versión simple y luego la ampliación.
* No cambies el concepto de “agente único + herramientas”.
* No propongas una arquitectura multiagente como solución principal.
* No incluyas taxonomía de errores.
* Mantén siempre el foco en acciones del mercado norteamericano y en horizontes temporales de días, semanas o largo plazo.

---

## Contexto adicional importante

Mi visión es que la IA central no sea solo un analista, sino un **operador-investigador** que:

* piensa en términos de hipótesis
* selecciona estrategias
* identifica setups
* usa señales técnicas como confirmaciones o triggers dentro de una estrategia
* crea watchlists
* opera con método
* registra lo que hace
* revisa sus resultados
* mejora el sistema continuamente

Quiero que la aplicación tenga mentalidad de:

* laboratorio de estrategias
* gestor disciplinado de decisiones
* memoria de aprendizaje
* operación continua basada en PDCA orientado a eventos

Empieza por la **Fase 1: visión del sistema** y sigue el orden indicado. No saltes directamente al código sin dejar clara antes la arquitectura.

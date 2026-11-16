
Desarrollar una app que analice el tenis a partir de video y ofrezca correcciones técnicas personalizadas es un proyecto ambicioso, pero viable. La clave está en descomponerlo en dos grandes capas: la **técnica** (cómo "ve" y "entiende" el movimiento) y la **de conocimiento** (cómo interpreta esos datos en errores y correcciones útiles). A continuación, te detallo qué necesitas y las alternativas para cada capa.

### 🧠 Qué necesitas: los componentes del sistema

Independientemente del camino que elijas, tu app requerirá estos módulos funcionando en cadena:

1.  **Captura y preprocesamiento de video**: La app debe recibir el video (subida o en tiempo real) y prepararlo. Idealmente, guiará al usuario para grabar desde un ángulo que permita un análisis claro (perfil o frontal), ya que la calidad del video condiciona todo lo demás.
2.  **Detección de pose (Pose Estimation)**: Es el núcleo técnico. El sistema debe identificar las articulaciones clave del jugador (hombros, codos, muñecas, caderas, rodillas) en cada fotograma para reconstruir su movimiento.
3.  **Clasificación del golpe y segmentación**: El sistema debe reconocer automáticamente si el video corresponde a un forehand, revés, saque o volea, y aislar los fotogramas que componen el golpe (desde la preparación hasta el follow-through).
4.  **Motor de análisis biomecánico**: A partir de los puntos articulares, se calculan métricas objetivas: ángulos de las articulaciones en momentos clave (impacto, máxima rotación), velocidad de la cabeza de la raqueta, sincronización de la rotación de caderas y hombros, posición de los pies, etc.
5.  **Base de conocimiento de errores y correcciones**: Necesitas un "manual" digitalizado que relacione esas métricas con errores técnicos concretos y sus correcciones. Por ejemplo: *"Si en el impacto el ángulo del codo es menor a X y la rotación de cadera es baja, entonces el error es 'golpeo con el brazo' y la corrección es 'iniciar el movimiento desde las caderas'"*. Este manual debe ser validado por entrenadores.
6.  **Lógica de priorización (de simple a complejo)**: Este es un punto diferencial. No basta con listar errores; hay que ordenarlos de forma útil. Una lógica efectiva se basa en el **impacto en el rendimiento** y la **facilidad de corrección**:
    *   **Errores fundamentales (simples)**: Suelen ser posturales o de preparación (ej. no girar los hombros, empuñadura incorrecta). Corregirlos suele desbloquear mejoras inmediatas.
    *   **Errores de sincronización (complejos)**: Afectan a la cadena cinética (ej. la cadera no lidera el movimiento). Son más difíciles de corregir porque requieren reeducar el patrón motor.
    *   El sistema debe priorizar los primeros, ya que su corrección facilita la resolución de los segundos.
7.  **Interfaz de usuario (UX) y generación de feedback**: El usuario debe recibir una explicación clara, idealmente con superposición visual sobre el video (esqueleto, ángulos) y un plan de drills o ejercicios correctivos.

### 🛠️ Alternativas: los caminos para construirlo

Puedes combinar estos enfoques según tus recursos, plazos y nivel de control deseado.

#### **Alternativa A: Construir sobre una API especializada (Camino rápido y con menor riesgo técnico)**

Consiste en delegar todo el análisis de video a un servicio externo y enfocarte en la experiencia de usuario y la capa de conocimiento.

*   **Cómo funciona**: Envías el video a una API (como **UNFORCE** o **SportAI**) y recibes de vuelta un JSON con los datos del análisis (tipo de golpe, métricas de la pose, calidad del golpe, etc.).
*   **Ventajas**: Pones la app en el mercado en semanas o pocos meses, sin necesidad de un equipo de visión por computadora. Te beneficias de motores ya entrenados con miles de golpes.
*   **Inconvenientes**: Dependes de un tercero (costes recurrentes, posibles cambios de precio o política). Tienes menos control sobre el modelo y la personalización profunda.
*   **Proveedores destacados**:
    *   **UNFORCE**: Ofrece una API REST y MCP con pago por minuto (aprox. $0.80/min). Su pipeline incluye detección de cancha, seguimiento de pelota, pose (MediaPipe) y clasificación de golpes. Es la opción más directa para un backend de análisis.
    *   **SportAI**: Plataforma B2B con enfoque en análisis técnico y táctico, utilizada por clubes y redes de canchas. Ofrece integración vía API/SaaS, aunque su modelo está más orientado a instalaciones deportivas.

#### **Alternativa B: Desarrollar un pipeline propio con librerías de código abierto (Camino de mayor control y diferenciación)**

Construyes tu propio motor de análisis usando herramientas de visión por computadora de código abierto. Es más laborioso, pero te da propiedad total sobre la tecnología y los datos.

*   **Cómo funciona**: Implementas tu propio pipeline: detección de pose con **MediaPipe Pose** o **MoveNet**, clasificación de golpes con modelos como **YOLO** o transformers, y un motor de reglas propio para el feedback.
*   **Ventajas**: Control total sobre el modelo, los datos y la lógica de negocio. Puedes crear una ventaja competitiva única. No pagas por minuto de análisis.
*   **Inconvenientes**: Requiere un perfil técnico especializado (Ingeniero/a de ML/CV). El desarrollo y la validación llevan mucho más tiempo. Necesitarás un **dataset etiquetado** de golpes de tenis para entrenar o validar tus modelos; existen algunos de investigación, pero tendrás que trabajar en su anotación.
*   **Componentes técnicos clave**:
    *   **Pose**: **MediaPipe Pose** (33 puntos clave, coordenadas 3D) es el estándar de facto por su equilibrio entre precisión y velocidad.
    *   **Clasificación de golpes**: Modelos como **YOLO-Pose** o arquitecturas basadas en transformers han demostrado alta precisión para clasificar forehand, revés y saque.
    *   **Lógica de feedback**: Puedes empezar con un **sistema de reglas** (si ángulo X < Y, entonces error Z) o, para más adelante, entrenar un modelo que aprenda a identificar errores directamente de las secuencias de pose, como se explora en la investigación con Graph Convolutional Networks (GCN).

#### **Alternativa C: Híbrido (Equilibrio entre velocidad y control)**

Usas una API para la parte más compleja (detección de pose y segmentación del golpe) y construyes tu propio motor de reglas y base de conocimiento encima.

*   **Cómo funciona**: La API te devuelve los puntos de la pose por fotograma. Tú te encargas de interpretar esos datos con tu propio conocimiento de tenis para generar los errores y correcciones priorizadas.
*   **Ventajas**: Reduces el tiempo de desarrollo técnico, pero mantienes el control sobre el núcleo de valor de tu producto: la lógica de coaching y la experiencia de usuario.

### 💎 Consideraciones clave y próximos pasos

*   **El verdadero foso de tu producto no es la IA, sino el conocimiento de tenis**. Cualquiera puede usar MediaPipe; lo que marcará la diferencia es la calidad de las correcciones y la lógica de priorización. **Asóciate con entrenadores de tenis desde el día uno** para construir y validar la base de conocimiento de errores y drills.
*   **Empieza acotado**. En lugar de intentar cubrir "cualquier golpe" desde el inicio, lanza una primera versión centrada **solo en el forehand**. Domina ese caso de uso, valida que las correcciones son útiles para los usuarios y luego expande a revés, saque, etc.
*   **La calidad del video de entrada es crítica**. Tu app debe guiar activamente al usuario para grabar desde un ángulo y distancia adecuados. Un video mal encuadrado arruinará cualquier análisis por bueno que sea el modelo.

En resumen, si tu prioridad es **validar el producto rápidamente**, la **Alternativa A** (API de UNFORCE) es el camino más directo. Si tu visión es **construir una tecnología central propia y diferenciada a largo plazo**, la **Alternativa B** o **C** te darán ese control, a cambio de un mayor esfuerzo inicial en ingeniería y datos.

***
Perfecto. Con el conocimiento de tenis y biomecánica ya cubierto por tu equipo, el reto se concentra enteramente en la ingeniería. La Alternativa B implica construir un pipeline de visión por computadora reproducible y modular. La buena noticia es que la investigación reciente ha validado componentes clave de este camino, por lo que no partes de cero.

A continuación, te presento el desglose técnico paso a paso, basado en los enfoques que están demostrando funcionar en la literatura y proyectos de código abierto más recientes.

### 🗺️ Arquitectura Técnica del Pipeline (Visión General)

El flujo de datos de tu aplicación debería ser secuencial y modular, permitiendo que tu equipo de biomecánica pueda intervenir en los puntos de decisión.

1.  **Entrada de Video** → 2. **Detección de Pose (Pose Estimation)** → 3. **Segmentación y Clasificación del Golpe** → 4. **Extracción de Métricas Biomecánicas** → 5. **Motor de Reglas y Feedback**.

Cada eslabón de esta cadena tiene alternativas de código abierto viables.

### 🦴 Módulo 1: Detección de Pose (El Núcleo)

Este es el componente que "ve" al jugador. La elección aquí condiciona todo el análisis posterior.

*   **La opción recomendada: MediaPipe Pose (BlazePose).** Es el estándar de facto para análisis deportivo de código abierto por una razón clave: extrae **33 puntos clave** e incluye **coordenadas métricas 3D del mundo (`world coordinates`)**.
    *   **Por qué es crucial para tenis:** Un estudio de 2026 demostró que usar las coordenadas 3D del mundo (en metros) es vital para la generalización entre jugadores. Al cambiar a coordenadas 2D de la imagen, la precisión en la clasificación de golpes entre jugadores se desplomó del 83% al 47%. Para tu equipo de biomecánica, tener ángulos y rotaciones en un espacio 3D real (aunque sea aproximado) es infinitamente más útil que coordenadas de píxeles.
    *   **Contrapunto (MoveNet):** MoveNet de Google es muy rápido, pero solo detecta **17 puntos clave**, lo cual es insuficiente para analizar con precisión las rotaciones complejas de hombro y cadera en un golpe de tenis.

*   **Cómo empezar:** Implementa `MediaPipe Pose Landmarker` para procesar el video fotograma a fotograma y obtener la secuencia de poses (33 puntos por fotograma).

### 🎯 Módulo 2: Segmentación y Clasificación del Golpe

Tu app no puede analizar un video completo; necesita aislar el golpe. Aquí tienes dos enfoques complementarios.

*   **Enfoque A (Basado en reglas de movimiento - Más simple):** Para empezar, puedes usar una heurística de bajo coste computacional. Un *paper* de 2026 propone detectar el golpe calculando una **puntuación de velocidad articular ponderada** (dando más peso a la muñeca, luego codo y hombro). Los picos de esta puntuación delimitan la ventana temporal del golpe. Es un excelente *baseline* para empezar a extraer datos.

*   **Enfoque B (Basado en Deep Learning - Más robusto):**
    *   **YOLO-Pose para clasificación:** El framework **HL-TAR-YOLO** (basado en YOLO11-Pose) está diseñado específicamente para el reconocimiento de golpes de tenis en tiempo real. Reporta métricas excelentes: **95.2% de precisión y 93.9% de recall** con un modelo muy ligero (2.6M de parámetros), ideal para despliegue en servidores o incluso dispositivos con recursos limitados.
    *   **Transformers para secuencias de pose:** El proyecto **TennisTransformerGPU** trata cada golpe como una secuencia de 30 fotogramas con 39 características (derivadas de los 33 puntos de la pose). Usa un transformer compacto para clasificar el tipo de golpe con una precisión del **83.7%**, con la ventaja de que el código es completamente reproducible en hardware gratuito (GPU T4 de Kaggle).

*   **Segmentación Temporal de Acciones (TAL):** Si quieres que la app encuentre automáticamente los golpes en un video más largo (por ejemplo, un entrenamiento completo), necesitarás un módulo de TAL. Investigaciones recientes, como **TAL4Tennis**, exploran el uso de *State Space Models* para identificar con precisión los límites temporales de los golpes en videos de tenis.

### 📊 Módulo 3: Extracción de Métricas Biomecánicas (El Puente a tu Equipo)

Con la pose y los límites del golpe definidos, tu equipo de biomecánica puede definir qué métricas calcular. El sistema técnico debe encargarse de extraerlas. Los estudios te dan una hoja de ruta de las variables más relevantes:

*   **Ángulos Articulares Clave:** Ángulo del codo y la muñeca en el momento del impacto, ángulo de la raqueta (inferido de la mano).
*   **Rotaciones de la Cadena Cinética:** El "timing" y la magnitud de la rotación de **cadera, tronco y hombros** son factores que distinguen a los jugadores de élite. Un sistema técnico puede calcular estos ángulos a partir de los puntos 3D de MediaPipe.
*   **Métricas de Tiempo y Velocidad:** Velocidad de la cabeza de la muñeca (proxy de la raqueta), duración de las fases del golpe (preparación, aceleración, impacto).
*   **Posición y Desplazamiento:** Posición de los pies en el momento del impacto, tipo de stance (abierto/cerrado).

El *paper* sobre análisis de saque con MediaPipe y reglas basadas en umbrales es un excelente ejemplo de cómo transformar estas métricas en fases del movimiento identificables automáticamente.

### 🧠 Módulo 4: Motor de Errores y Correcciones (Tu Valor Diferencial)

Aquí es donde tu equipo de biomecánica toma el control total. Con las métricas extraídas, se construye un **sistema experto basado en reglas** (rule-based expert system). El *paper* sobre el saque valida exactamente este enfoque: utiliza reglas basadas en umbrales para generar feedback correctivo automático.

*   **Lógica de implementación:** El motor recibe un JSON con las métricas del golpe (ej. `{"angulo_codo_impacto": 155, "rotacion_cadera_max": 12, ...}`). Tu equipo define reglas como: *"SI `angulo_codo_impacto` > 160° Y `rotacion_cadera_max` < 15°, ENTONCES error = 'Golpeo con el brazo, sin transferencia de cadera'. Corrección: 'Inicia el movimiento desde las caderas...'"*.
*   **Priorización:** La lógica de "de simple a complejo" se implementa asignando un peso a cada regla. Los errores posturales básicos (ej. empuñadura) tienen prioridad sobre los errores de sincronización fina. Esto es 100% configurable por tus expertos, sin necesidad de tocar código de visión.

### 🗃️ Módulo 0 (El punto de partida): Datos para Entrenar y Validar

Para entrenar los modelos de clasificación (Módulo 2) y validar todo el sistema, necesitas datos.

*   **Dataset THETIS:** Es un punto de partida excelente y gratuito. Contiene **8.374 secuencias de video** de 55 sujetos (31 principiantes, 24 expertos) realizando **12 acciones de tenis** diferentes (incluyendo forehand plano, abierto, cortado, saque, etc.). Aunque fue capturado con Kinect, los videos RGB son utilizables.
*   **Datasets de investigación recientes:** El *paper* de HL-TAR-YOLO menciona **TAR-Det**, un dataset anotado con poses para acciones de tenis. El *paper* de TennisTransformerGPU usó 1.281 golpes etiquetados de 7 profesionales y 1 amateur, y su código es reproducible.

### 💎 Resumen y Próximos Pasos Concretos

La ruta técnica para la Alternativa B está bien definida por la investigación reciente.

1.  **Prueba de Concepto (PoC) Rápida (1-2 semanas):** Implementa un script en Python que use **MediaPipe Pose** para procesar un video de un forehand. Extrae los 33 puntos y visualízalos superpuestos. Esto valida que la captura de pose funciona en tus condiciones.
2.  **Módulo de Segmentación Básico:** Añade la lógica de **puntuación de velocidad articular** para aislar automáticamente la ventana del golpe en el video. Empieza a extraer las primeras métricas simples (ángulo del codo en el pico de velocidad de la muñeca).
3.  **Integración con Biomecánica:** Siéntate con tu equipo. Muéstrales los datos extraídos. Definid juntos las **primeras 5-10 reglas** de error/corrección para el forehand. Implementa el motor de reglas.
4.  **Escalado:** Una vez validado el flujo con el forehand, expande a otros golpes. Para la clasificación automática robusta, evalúa entrenar un modelo **YOLO11-Pose** (como HL-TAR-YOLO) o un **Transformer de secuencias de pose** usando el dataset THETIS como base.

El camino es técnicamente desafiante pero muy estructurado. La combinación de **MediaPipe Pose (para 3D), YOLO/Transformers (para clasificación) y un motor de reglas experto (para el feedback)** es la arquitectura que la vanguardia de la investigación está validando.

***
Entendido. El objetivo es construir la **infraestructura técnica funcional de extremo a extremo**: un pipeline que reciba un video y devuelva un conjunto de métricas estructuradas, listo para que, en una fase posterior, tu equipo de biomecánica conecte sus reglas de negocio. Todo lo que sigue es código y arquitectura de sistemas.

La hoja de ruta se basa en los componentes que la investigación de 2026 está validando como más robustos y reproducibles.

### 🗺️ La Arquitectura Objetivo (El "Esqueleto" Funcional)

El sistema será una cadena de procesamiento modular. Cada etapa es una función pura (recibe datos, devuelve datos, sin efectos secundarios), lo que facilita el desarrollo, las pruebas y la sustitución de componentes.

**`Video Subido` → `Backend (FastAPI)` → `Módulo de Pose (MediaPipe)` → `Segmentación (Velocidad Articular)` → `Clasificación (Transformer/YOLO)` → `Extracción de Métricas` → `JSON de Salida`**

Tu entregable final será un endpoint de API que, al recibir un video, devuelva un JSON con las métricas clave del golpe detectado.

---

### 📋 Paso a Paso para la Infraestructura (2026)

#### Paso 1: Configuración del Entorno y Backend Mínimo

El objetivo es tener un servidor que reciba videos y gestione el procesamiento en segundo plano para no bloquear al cliente.

*   **Stack:** Python 3.11+, **FastAPI** para la API y **Uvicorn** como servidor.
*   **Gestión de Tareas:** Usa `BackgroundTasks` de FastAPI. El endpoint `POST /upload` guardará el video, encolará la tarea de análisis y devolverá un `video_id` inmediatamente. Un endpoint `GET /results/{video_id}` permitirá consultar el estado y, finalmente, obtener el JSON de resultados.
*   **Almacenamiento:** Para el MVP, usa el sistema de archivos local (una carpeta `uploads/` y `results/`). Esto es suficiente para validar la infraestructura.

#### Paso 2: El Núcleo de Visión — Detección de Pose con MediaPipe

Esta es la base de todo. Usarás `MediaPipe Pose Landmarker` para extraer 33 puntos clave del cuerpo en cada fotograma.

*   **Por qué MediaPipe:** Es el estándar de facto en investigación aplicada a tenis por su balance precisión/velocidad y porque corre eficientemente en CPU.
*   **El Detalle Crítico (Coordenadas 3D):** No uses las coordenadas 2D de la imagen. Debes usar las **`metric world coordinates`** (coordenadas 3D en metros) que también proporciona MediaPipe. Un estudio de 2026 demostró que usar coordenadas 2D hace que la precisión del modelo se desplome al evaluar a un jugador nuevo (del 83% al 47%), porque no generaliza entre personas con proporciones corporales distintas. Las coordenadas 3D son indispensables para un análisis biomecánico útil.
*   **Implementación:** Crea un módulo `pose_detector.py`. Su salida debe ser una secuencia de fotogramas, donde cada fotograma contiene un array de 33 puntos con sus coordenadas (x, y, z) en metros y un `visibility_score`. Filtra los puntos con un `visibility_score` bajo (ej. < 0.5) para evitar datos ruidosos.

#### Paso 3: Aislar el Golpe — Segmentación Temporal Automática

No analizarás el video completo. Necesitas encontrar los fotogramas que componen el golpe de forma automática.

*   **El Algoritmo (Validado):** Implementa la **puntuación de velocidad articular ponderada** propuesta en la investigación de 2026. La fórmula es: `s(t) = 0.5 * v_muñeca + 0.3 * m_codo + 0.2 * m_hombro`. Los picos de esta puntuación (usando `scipy.signal.find_peaks`) delimitan la ventana temporal del golpe.
*   **Entregable:** Una función `segment_stroke(pose_sequence)` que devuelva los índices de inicio y fin de la secuencia de fotogramas que corresponde al golpe.

#### Paso 4: Clasificación del Tipo de Golpe

Con la secuencia del golpe aislada, clasifícala (forehand, revés, saque, etc.).

*   **Opción A (Rápida y de Alta Precisión): YOLO11-Pose.** El framework **HL-TAR-YOLO** está diseñado exactamente para esto. Logra un **95.2% de precisión** y **93.9% de recall** para el reconocimiento de golpes de tenis, con un modelo muy ligero (2.6M de parámetros) que puede correr a más de 120 FPS en GPU, ideal para un backend.
*   **Opción B (Más Flexible y Reproducible): Transformer de Secuencias de Pose.** El proyecto **TennisTransformerGPU** trata cada golpe como una secuencia de 30 fotogramas con 39 características por fotograma (derivadas de los 33 puntos). Usa un transformer compacto de ~564k parámetros. Su precisión es del **83.7%**, y lo más importante: **el código y el enfoque son completamente reproducibles en hardware gratuito (GPU T4 de Kaggle)**. Esta opción te da control total sobre el modelo y los datos de entrenamiento.

**Recomendación:** Empieza con la **Opción A** para tener un clasificador funcional rápidamente. Si necesitas personalización profunda, migra a la **Opción B**.

#### Paso 5: Extracción de Métricas Biomecánicas (El "Contrato" de Salida)

Este es el paso donde conviertes la secuencia de pose en el JSON de métricas que tu equipo de biomecánica consumirá. Debes definir un **contrato de datos claro**.

*   **Qué calcular:** El sistema debe extraer métricas objetivas en momentos clave del golpe (ej. en el pico de velocidad de la muñeca, que aproxima el impacto):
    *   **Ángulos articulares:** Codo (hombro-codo-muñeca), rodilla, cadera.
    *   **Rotaciones de la cadena cinética:** Diferencia de ángulo entre hombros y caderas (separación X-factor).
    *   **Velocidades:** Velocidad lineal de la muñeca, velocidad angular de la cadera.
    *   **Tiempos:** Duración de las fases (preparación, aceleración, impacto).

*   **El JSON de Salida (Ejemplo):**
    ```json
    {
      "stroke_type": "forehand",
      "metrics": {
        "elbow_angle_at_impact_deg": 162.5,
        "hip_shoulder_separation_deg": 14.2,
        "wrist_speed_peak_kmh": 78.3,
        "phase_durations_ms": {"prep": 450, "accel": 220, "follow": 300}
      }
    }
    ```
    Este JSON es tu producto. El equipo de biomecánica escribirá reglas sobre estos campos.

#### Paso 6: Estructura del Motor de Reglas (Listo para la Fase 2)

Para que la infraestructura esté "funcional", debes incluir un motor de reglas básico que demuestre el flujo de extremo a extremo. No necesitas las reglas finales, sino la **estructura** para que tu equipo las inserte.

*   **Diseño:** Un archivo `rules_engine.py` con una función `apply_rules(metrics: dict) -> list`. Inicialmente, puede contener una regla de ejemplo (placeholder):
    ```python
    def apply_rules(metrics):
        feedback = []
        # Regla de ejemplo (placeholder para el equipo de biomecánica)
        if metrics["elbow_angle_at_impact_deg"] > 160:
            feedback.append({
                "error_id": "TECH_001",
                "message": "Brazo excesivamente extendido en el impacto.",
                "priority": 1
            })
        return feedback
    ```
*   **Valor:** Esto cierra el pipeline. Tu API devolverá un JSON con las métricas **y** una lista de errores (aunque sea de ejemplo). El equipo de biomecánica solo tendrá que reemplazar la lógica dentro de `apply_rules` sin tocar la visión por computadora.

### 💎 Entregable Concreto y Criterio de Éxito

Al final de esta hoja de ruta, tendrás un repositorio con:

1.  Una API FastAPI funcional (`POST /upload`, `GET /results/{id}`).
2.  Un pipeline en Python que procesa el video en segundo plano.
3.  Un JSON de salida que contiene el `stroke_type` y un diccionario de `metrics` biomecánicas extraídas con coordenadas 3D.
4.  Un `rules_engine.py` con la estructura para que tu equipo inyecte el conocimiento de tenis.

El **criterio de éxito** es simple: subes un video de tu forehand a la API y, en unos segundos, recibes un JSON con métricas como el ángulo del codo en el impacto y la velocidad de la muñeca, calculadas sobre coordenadas 3D reales. Con esta base sólida, la integración del conocimiento de tu equipo será un proceso directo y desacoplado del complejo trabajo de visión por computadora.


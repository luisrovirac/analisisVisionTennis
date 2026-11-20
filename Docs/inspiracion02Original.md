
### Primer grupo

*   **https://github.com/johndp/tennis-transformer** - Es un pipeline multi-tarea para analizar la biomecánica de golpes de tenis a partir de video RGB. Utiliza MediaPipe Pose (33 puntos) y un modelo Transformer para clasificar el tipo de golpe, predecir su dirección y evaluar la calidad de la postura.
    *   **Idea para tu sistema**: Su enfoque de usar un Transformer para modelar secuencias de pose es exactamente lo que necesitas para tu clasificador `pose_seq`. Podrías usar su arquitectura como referencia para entrenar tu propio modelo con el dataset THETIS y superar el macro-F1 de 0.759 actual.

*   **https://github.com/MindDock/sport-vision** - Una plataforma de análisis para deportes de raqueta que realiza un seguimiento de pose en tiempo real con MediaPipe y un análisis biomecánico que incluye ángulos articulares, velocidad de muñeca, inclinación del cuerpo y simetría.
    *   **Idea para tu sistema**: Te da una lista concreta de métricas biomecánicas que puedes empezar a calcular en tu worker (velocidad de muñeca, inclinación del cuerpo, simetría) para enriquecer el reporte, más allá de los ángulos articulares.

*   **https://github.com/jasnwag/tennis_serve_dataset** - Código y dataset de un pipeline de estimación de pose 3D a gran escala (RTMDet + RTMPose + MotionBERT) para saques profesionales.
    *   **Idea para tu sistema**: Útil si necesitas una arquitectura de referencia más avanzada para el análisis de saques y estimación de pose en 3D.

*   **https://github.com/dollar-zhu/ProjectDjoker** - Un pipeline reproducible para analizar los saques de Djokovic. Incluye adaptador de salida de MediaPipe y un contrato de datos (DMS v0.1) para estandarizar el JSON de métricas. Extrae 8 trayectorias de ángulos articulares con medias, desviaciones y bandas percentiles.
    *   **Idea para tu sistema**: Es una referencia de oro para tu Portal de Expertos. Define cómo estructurar los datos de un golpe de élite (medias, desviaciones, bandas de percentiles) para que sirvan como "perfil de referencia" contra el cual comparar a los usuarios. Además, su contrato DMS es un ejemplo de buenas prácticas para la interoperabilidad de datos.

*   **https://github.com/devanshkhandekar/Tennis-Swing-Tracker-Using-Pose-Estimation** - Una herramienta que analiza la pose y la trayectoria del swing para comparar a jugadores profesionales con amateurs.
    *   **Idea para tu sistema**: Aporta la idea de comparación contra un referente. Podrías implementar una funcionalidad que compare la pose del usuario con la de un "jugador modelo" para dar un score de similitud, lo que es muy potente para el feedback.

*   **https://github.com/rubenG1009/tennis-analyzer** - Un analizador de técnica de golpes por video que extrae métricas biomecánicas y genera feedback con un LLM. Su README destaca un aspecto crucial: en su prueba, la muñeca derecha solo fue fiable en el 35.8% de los frames, por lo que su sistema prefiere decir "no hay suficiente información" antes que inventar una recomendación.
    *   **Idea para tu sistema**: Esta es una lección de diseño fundamental. Tu motor de reglas ya tiene un campo `confidence`. Este proyecto valida la importancia de filtrar métricas con baja confianza para no generar diagnósticos erróneos, algo que deberías replicar en tu lógica de negocio.

*   **https://github.com/build-small-hackathon/StrokeSense** - Un pipeline de dos etapas que primero detecta golpes con MoveNet y luego utiliza un modelo de lenguaje visual (VLM) local (MiniCPM-V) para puntuar la mecánica del golpe en cuatro dimensiones (preparación, punto de contacto, swing, equilibrio) y generar recomendaciones.
    *   **Idea para tu sistema**: Este es un enfoque híbrido muy potente. No dependes de un LLM en la nube (es local) y las recomendaciones se basan en la imagen del video, no solo en métricas numéricas. Podrías integrar una capa similar para enriquecer tu narrativa, complementando tu motor de reglas YAML.

*   **https://github.com/nexusfb/TennisVision** - Utiliza OpenPose para la estimación de pose, SVM para el reconocimiento de acciones (forehand, backhand, saque) y Dynamic Time Warping (DTW) para comparar los movimientos del jugador con los de un profesional y dar una puntuación.
    *   **Idea para tu sistema**: La técnica de DTW es ideal para tu Fase 3 (Multi-golpe). Te permite comparar la secuencia temporal de un golpe de un usuario con la de un golpe de referencia, independientemente de la velocidad de ejecución. Es una métrica de similitud muy robusta.

*   **https://github.com/DayDreamingSheep/tennis-team** - Son "skills" para agentes de IA (como Claude) que actúan como un equipo de coaching. El primero te permite hablar con un "Entrenador Principal" respaldado por especialistas.
    *   **Idea para tu sistema**: Es un modelo mental excelente para tu Portal de Expertos. Podrías estructurar tu base de conocimiento no como reglas aisladas, sino como un diálogo entre diferentes "roles" expertos (biomecánico, entrenador de saque, preparador físico), cada uno aportando su perspectiva al diagnóstico.

*   **https://github.com/zakezhang/tennis-video-review-public** - Analiza un video, detecta golpes, y genera un plan de práctica concreto para la próxima sesión.
    *   **Idea para tu sistema**: Es perfecto como inspiración para tu Portal de Expertos. Muestra cómo estructurar un flujo de trabajo donde el experto (o un agente que lo asiste) no solo da un diagnóstico, sino que prescribe un plan de acción personalizado con ejercicios (drills). Podrías emular su estructura de "plan de entrenamiento" en tu base de datos de `drills`.

*   **https://github.com/henryphamduc/TennisKnowledgeBase** - Una base de conocimiento bilingüe (inglés/vietnamita) con más de 200 artículos que cubren biomecánica aplicada, mecánica de golpes, neuro-atletismo y táctica.
    *   **Idea para tu sistema**: Es una mina de oro para tus expertos. Podrían usar esta base de conocimiento como punto de partida para definir los umbrales de sus reglas YAML y para redactar las explicaciones y correcciones que los usuarios verán en el reporte.

*   **https://github.com/LZYAndy/TennisExpert** - Se enfoca en el razonamiento a nivel de rally, con comprensión táctica y análisis de video de nivel experto.
    *   **Idea para tu sistema**: Aunque tu enfoque actual es el análisis de un solo golpe, este proyecto te da una visión de hacia dónde puede evolucionar tu producto (Fase 4). Podría servir para inspirar una futura funcionalidad de análisis táctico basado en secuencias de golpes.

---

### Segundo grupo

*   **https://github.com/Malk97sc/Tennis_Pose_Estimation_Analysis** - Un sistema que utiliza YOLO11 y PyTorch para la detección de jugadores, la pelota y los puntos clave de la cancha, y luego aplica YOLO11-pose para estimar la pose del jugador. Incluye post-procesamiento para limpiar los keypoints ruidosos y generar un video con la superposición del esqueleto.
    *   **Idea para tu sistema**: Su enfoque en la detección de la cancha y la selección del jugador relevante es muy útil para filtrar videos de partidos reales. Además, su "Future Work" menciona explícitamente la detección de movimientos de riesgo para la prevención de lesiones, lo que podría ser una línea de análisis para tu Portal de Expertos.

*   **https://github.com/alshami52/Pose2Trajectory** - Utiliza una arquitectura Transformer (encoder-decoder) para predecir la trayectoria futura de un jugador de tenis a partir de la secuencia de sus joints corporales y la posición de la pelota.
    *   **Idea para tu sistema**: Aunque está orientado a la trayectoria, su uso de Transformers sobre secuencias de pose valida tu enfoque para el clasificador `pose_seq`. Podrías usar una arquitectura similar para predecir la fase del golpe (preparación, impacto, follow-through) basándote en la evolución de la pose.

*   **https://github.com/HarshTomar1234/Tennis-Vision** - Una pipeline de análisis para video de broadcast de un solo ángulo. Utiliza TrackNet para la detección de la pelota, YOLOv8x + ByteTrack para el seguimiento de jugadores y realiza una reconstrucción 3D de la trayectoria de la pelota. Su característica más destacada es su "puerta de evidencia": si no puede medir algo con confianza (ej. la pelota no es visible), rechaza dar una respuesta en lugar de inventar un dato plausible.
    *   **Idea para tu sistema**: Esta filosofía es oro puro para tu objetivo de "confiabilidad y certeza". Deberías implementar una lógica similar en tu worker: si la confianza de una métrica (ej. `elbow_angle_at_impact`) es baja, el sistema debe abstenerse de generar un diagnóstico sobre ella, tal como ya sugiere tu campo `confidence`.

*   **https://github.com/andrenatal/AI_Tennis_Coach** - Un proyecto de Stanford (CS230) que utiliza MoveNet para la extracción de pose y redes LSTM (una para clasificación y un autoencoder para similitud) para clasificar golpes y comparar al jugador con profesionales de referencia.
    *   **Idea para tu sistema**: Su enfoque de similitud con jugadores de referencia es una excelente manera de enriquecer el feedback. En lugar de solo decir "tu codo está a 150°", podrías decir "tu codo está a 150°, mientras que en un forehand de referencia similar el ángulo es de 160°". Esto es muy potente para el Portal de Expertos.

*   **https://github.com/jiaawe/tennis-prediction** - Un sistema que analiza partidos de dobles para predecir múltiples aspectos: tipo de golpe (saque, resto, volea), dirección, lado del jugador (forehand/backhand), formación y resultado del punto.
    *   **Idea para tu sistema**: Es un ejemplo de análisis a nivel de rally. Aunque tu enfoque actual es un solo golpe, este repo te da ideas para la Fase 3 (expansión), mostrando cómo predecir la intención táctica detrás de un golpe, no solo la mecánica.

*   **https://github.com/puccj/padel_shot_recognition** - Un proyecto que utiliza Movenet para extraer la pose y luego redes Dense NN y RNN para reconocer golpes (saque, derecha, revés) en secuencias de 30 frames (~1 segundo).
    *   **Idea para tu sistema**: Su enfoque de clasificar secuencias de 30 frames como un "golpe" es directamente aplicable a tu segmentador. Podrías usar esta idea para definir la ventana temporal de análisis de forma más robusta que un simple pico de velocidad.

*   **https://github.com/vxia777/tennis_serve_recognition** - Código del paper "Tennis Serve Classifier" de Stanford. Utiliza InceptionV3 para extraer features de los frames y una LSTM many-to-many para clasificar el tipo de saque (flat, kick, slice) utilizando el dataset THETIS.
    *   **Idea para tu sistema**: Es una referencia directa y probada para la clasificación de saques. Si decides expandir tu clasificador `pose_seq` para incluir los tipos de saque, este repo te proporciona una arquitectura y un baseline de rendimiento con el mismo dataset que usas.

*   **https://github.com/dukesky/rate-my-tennis-skill** - Una "skill" para agentes de IA que, al subir un video, genera un informe de coaching ilustrado con puntuaciones, frames anotados (en rojo lo actual, en verde el objetivo), correcciones animadas y seguimiento de progreso.
    *   **Idea para tu sistema**: Es una inspiración perfecta para el Portal de Expertos y la UX del reporte. Muestra cómo presentar la información de manera visualmente atractiva y fácil de entender: no solo texto, sino comparaciones visuales (actual vs. objetivo) que son mucho más efectivas para el aprendizaje motor.

*   **https://github.com/Bot-Derpy/racquet-sports-analyzer** - Una pipeline completa estilo SwingVision que soporta tenis, bádminton, pickleball y pádel. Detecta rallies, sigue jugadores, clasifica 12 tipos de golpes (con VideoMAE) y genera perfiles de rendimiento del jugador, incluyendo fortalezas y debilidades.
    *   **Idea para tu sistema**: Su estructura de salida (perfil de jugador con fortalezas/debilidades) es exactamente el tipo de diagnóstico de nivel entrenador que buscas. Te puede servir como plantilla para estructurar tu objeto `diagnosis` en el reporte.

*   **https://github.com/Federer123/TennisServeDetection-ML** - Código y modelos de un paper de IEEE (2024) que se enfoca específicamente en el análisis de la posición de la raqueta durante el saque, utilizando segmentación de color y un modelo de detección de keypoints para la raqueta.
    *   **Idea para tu sistema**: Es un recurso muy específico para añadir la raqueta como objeto de análisis (Fase 5 de tu plan). Te da una metodología concreta para aislar la raqueta y extraer sus puntos clave, lo que desbloquearía métricas como el "racket lag" o el plano de swing.
    
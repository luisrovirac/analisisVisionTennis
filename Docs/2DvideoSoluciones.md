
Primero una aclaración: el sistema no trabaja en 2D puro. MediaPipe devuelve dos cosas:

  - image: coordenadas 2D en la imagen. Son fiables en el plano de la cámara.
  - world: un 3D estimado a partir de una sola cámara. Casi todas las métricas usan estas.

  El punto débil está en la profundidad, es decir, la distancia a la cámara: el modelo la adivina. Por eso las métricas que
  dependen de ella, como el giro de hombros, la separación cadera-hombros o el punto de impacto, son las menos fiables. Las que
  se ven en el plano de la cámara, como el ángulo de rodilla y codo en vista lateral o las alturas, son bastante más fiables.

  Lo que debe hacerse (sin esto, los umbrales no sirven)

  1. Fijar un protocolo de grabación. Es lo que más mejora la precisión y no cuesta nada:
     - Móvil en trípode y fijo, nunca en la mano.
     - Vista lateral a la altura de la cadera, a unos 4–6 m.
     - Cuerpo entero en el encuadre todo el tiempo.
     - 60 fps como mínimo, y mejor 120 o 240 (cámara lenta). A 30 fps el impacto cae entre dos fotogramas y la muñeca sale
       borrosa.
     - Buena luz, ropa que contraste con el fondo y un solo jugador en la imagen.

     La app debería explicarlo antes de grabar y comprobarlo después: fps, cuerpo visible y vista declarada. Parte de esto ya
     existe en los avisos de calidad.
  2. Calibrar con datos propios, no con la literatura. Para cada golpe y cada vista, graba unos 30 golpes buenos y 30 malos, y
     que un entrenador los etiquete. Con eso:
     - El umbral se coloca donde mejor separa los dos grupos.
     - Se comprueba si cada métrica es fiable: graba al mismo jugador haciendo 10 golpes iguales. Si la métrica varía más entre
       esos golpes que entre un golpe bueno y uno malo, no sirve y hay que quitarla o rediseñarla.
  3. Tener en cuenta la vista de la cámara. El trabajo ya guarda camera_view (lateral, frontal o trasera), pero las reglas no
     la usan. Cada vista mide bien cosas distintas:
     - Lateral: punto de impacto, flexión de rodillas, codo y altura de la terminación.
     - Frontal o trasera: anchura de la base, inclinación de hombros en el saque y cabeza.

     Las reglas deberían declarar en qué vistas son válidas. En las demás, la métrica se marca como «no medible desde esta
     vista» en lugar de dar un diagnóstico falso.

  Lo que puede hacerse (mejoras, de más barata a más cara)

  ┌───────────────────────────────────────────────────────────┬───────────────────────────────────────┬────────────────────┐
  │                          Mejora                           │             Qué resuelve              │       Coste        │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Suavizado temporal (filtro One-Euro o Savitzky-Golay)     │ Reduce el temblor de los puntos       │ Bajo               │
  │                                                           │ fotograma a fotograma                 │                    │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Detectar el impacto por el sonido (el pico de audio al    │ Da el instante exacto del impacto;    │                    │
  │ golpear)                                                  │ ahora se estima por la velocidad de   │ Bajo               │
  │                                                           │ la muñeca                             │                    │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Modelo de pose «heavy» de MediaPipe                       │ Algo más de precisión                 │ Bajo               │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Mejor modelo 2D (RTMPose o ViTPose) más un modelo que     │ 3D mucho más estable que el de        │                    │
  │ reconstruye el 3D usando varios fotogramas (MotionBERT o  │ MediaPipe, que analiza cada fotograma │ Medio              │
  │ VideoPose3D)                                              │  por separado                         │                    │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Detectar raqueta y bola con YOLO                          │ Contacto real entre raqueta y bola,   │ Medio-alto         │
  │                                                           │ ángulo de la raqueta y efecto         │                    │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Dos móviles sincronizados (triangulación)                 │ 3D real                               │ Alto, y molesto    │
  │                                                           │                                       │ para el usuario    │
  ├───────────────────────────────────────────────────────────┼───────────────────────────────────────┼────────────────────┤
  │ Modelo entrenado con clips etiquetados por expertos       │ Detecta errores que las reglas no     │ Alto: necesita     │
  │                                                           │ capturan                              │ cientos de clips   │
  └───────────────────────────────────────────────────────────┴───────────────────────────────────────┴────────────────────┘

  La solución que propongo, por fases

  Fase 1, ahora y casi sin coste:
  - Protocolo de grabación con comprobaciones automáticas.
  - Reglas que tienen en cuenta la vista de la cámara.
  - Suavizado temporal.
  - Detección del impacto por el sonido.
  - Un conjunto de referencia etiquetado por un entrenador, con el que calibrar los umbrales en el portal y descartar las
    métricas poco fiables.

  Esto convierte los umbrales provisionales en umbrales medidos.

  Fase 2: cambiar el modelo de pose por RTMPose más MotionBERT y compararlo con MediaPipe sobre el mismo conjunto de
  referencia. Solo se adopta si mejora la repetibilidad de las métricas. La arquitectura ya lo permite, porque el estimador de
  pose es intercambiable.

  Fase 3: detección de raqueta y bola y, cuando haya suficientes clips etiquetados, un modelo entrenado que complemente las
  reglas. Las reglas se quedan para explicar el «por qué» al jugador.

  El principio que lo guía todo: ninguna métrica entra en una regla validada sin haber demostrado que es repetible y que
  coincide con el criterio de un entrenador. El portal ya tiene el flujo para eso; lo que falta son los datos de referencia.

Las fases 1 y 2 están hechas y probadas, y el worker de Docker ya corre con la fase 1 activa. No hace falta tocar tu .env: los valores por defecto ya activan la fase 1, y la fase 2 queda como opción. 

  ┌──────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────┐
  │        Mejora        │                                            Qué hace                                             │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Impacto por el       │ Busca el chasquido de la bola cerca del pico de velocidad de la muñeca y solo lo acepta si      │
  │ sonido               │ sobresale 10 dB del ruido. En tu video corrigió el impacto 3 fotogramas (de 44 a 41), con +39   │
  │                      │ dB                                                                                              │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Prueba de            │ Recalcula cada métrica moviendo el impacto dentro de su margen de error: ±1 fotograma con       │
  │ sensibilidad         │ audio, ±0,05 s sin él. Si el valor salta, la métrica pierde confianza y deja de disparar reglas │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Suavizado            │ Filtro Savitzky-Golay de 0,1 s, con un mínimo de 5 fotogramas                                   │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Reglas según la      │ Las reglas pueden indicar en qué vistas son fiables. Ya están marcadas en las semillas, en la   │
  │ vista de cámara      │ API (Go) y en el editor del portal. El reporte avisa de qué vista grabar para completar el      │
  │                      │ análisis                                                                                        │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Avisos de grabación  │ Menos de 50 fps, jugador pequeño en la imagen, cabeza o pies fuera del encuadre. Además, la     │
  │                      │ guía de grabación de la web está actualizada                                                    │
  └──────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────┘

  Fase 1: que un mal video no produzca un diagnóstico falso

  Efecto en tu video (30 fps): 4 métricas pasaron a «no fiables» porque dependen demasiado del instante exacto del impacto:
  giro de hombros, anchura de la base, antelación de la preparación y empuje de piernas. Ya no generan conclusiones dudosas, y
  el reporte pide grabar a 60 fps. Es el comportamiento que buscábamos.

  Un problema que apareció al probar: la primera versión medía el margen en fotogramas, y con ±3 fotogramas a 30 fps casi todo
  salía inestable. Lo cambié a tiempo real. El suavizado también fallaba a 30 fps (la ventana de 3 fotogramas no suavizaba
  nada); ahora usa un mínimo de 5.

  Fase 2: RTMPose + MotionBERT como estimador alternativo

  - RTMPose (2D): detector YOLOX + RTMPose-m con inferencia ONNX propia. Los modelos oficiales se descargan la primera vez y se
    verifican por SHA-256.
  - MotionBERT (3D a partir de la secuencia completa): el comando python -m tennis_ml motionbert --publish exporta los pesos
    oficiales y verifica que ONNX da lo mismo que PyTorch (diferencia de 1,1e-05). Ya está publicado en MinIO como
    mb-lite-h36m-global-5cd873a3.
  - Funciona dentro del contenedor del worker: detección del 100 % y 11,5 s para tu video.
  - Comparador: scripts/compare_pose.py pasa el mismo video por todo el pipeline con los dos estimadores.

  Resultado con tu único video: empate. Quedan estables 9 de 13 métricas con MediaPipe y 8 de 13 con RTMPose + MotionBERT, cada
  uno mejor en métricas distintas, y el segundo es unas 3 veces más lento. Discrepan bastante en algunos valores (rodilla:
  107° frente a 75°). MediaPipe sigue por defecto. Para decidir hace falta el conjunto de referencia: 20–30 videos por golpe a
  60 fps, con la valoración de un entrenador.

  Verificación

  - Worker: ruff y mypy sin errores, 232 tests en verde.
  - API (Go): todos los tests en verde.
  - Web: sintaxis de JavaScript correcta.
  - Docker: análisis de punta a punta correcto en el contenedor.
  - No compilado: la app Android; el selector de golpes que cambié en la tarea anterior sigue pendiente de compilar.
  - Ojo: la API y el worker se deben desplegar juntos por el nuevo campo views. Lo dejé anotado en Docs/CONTRATOS.md.

  Queda pendiente el punto de calibración de la evaluación anterior: umbrales por percentiles de un perfil de referencia, y el
  perfil del saque con el dataset de jasnwag. Todo está documentado en Docs/BASE_CONOCIMIENTO.md (secciones «Cómo se protege la
  medición» y «Comparar estimadores de pose»).


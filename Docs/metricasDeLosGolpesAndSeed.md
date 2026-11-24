
La falta de reglas no viene de que falte información de tenis. Viene de lo que el sistema sabe medir.

  Por qué solo hay 15 reglas

  Cada regla es una condición sobre una métrica, por ejemplo knee_angle_min > 160. Revisé el código:

  - Forehand: el worker solo calcula 7 métricas, en worker/src/tennis_worker/pipeline/biomechanics/registry.py. Las 15 reglas
    que tienes (8 errores y 7 puntos fuertes) ya combinan casi todo lo que esas 7 permiten detectar.
  - Revés, saque y volea: no tienen ninguna métrica. El registro solo define "forehand", así que aunque escribas reglas para
    esos golpes, el worker no puede evaluarlas. Además las rechaza al validar, porque citan métricas que no existen.

  Por eso, ampliar la base de conocimiento son dos trabajos distintos:

  ┌───────────────────────────────────┬───────────────────────┬────────────────────────────────────────┐
  │                Qué                │         Quién         │                 Dónde                  │
  ├───────────────────────────────────┼───────────────────────┼────────────────────────────────────────┤
  │ Medir cosas nuevas (métricas)     │ Programación          │ metrics.py + registry.py del worker    │
  ├───────────────────────────────────┼───────────────────────┼────────────────────────────────────────┤
  │ Escribir reglas con esas métricas │ Entrenadores/expertos │ Portal profesional (o el YAML semilla) │
  └───────────────────────────────────┴───────────────────────┴────────────────────────────────────────┘

  Plan concreto

  1. Forehand: añadir métricas, y con ellas reglas nuevas. Ejemplos que se pueden sacar del esqueleto 2D:
  - Anchura de la base: distancia entre tobillos respecto a la anchura de hombros.
  - Transferencia de peso: cuánto avanza la cadera hacia la bola.
  - Estabilidad de la cabeza durante el impacto.
  - Trayectoria de abajo hacia arriba: altura de la muñeca antes del impacto respecto al impacto.
  - Momento del giro de hombros: si empieza temprano o tarde respecto al impacto.
  - Uso del brazo no dominante en la preparación.

  Cada métrica nueva permite 2 o 3 reglas: un error, un punto fuerte y combinaciones con las demás.

  2. Golpes nuevos. Para cada uno hace falta definir sus métricas, crear worker/knowledge_base/<golpe>.yaml como semilla y que
  el clasificador reconozca el golpe.
  - Saque: flexión de rodillas en la posición de trofeo, ángulo del codo en el trofeo, altura del impacto, inclinación de la
    línea de hombros, brazo del lanzamiento extendido.
  - Revés: igual que el forehand pero del lado contrario. Además hay que distinguir el revés a una mano del de dos.
  - Volea: preparación corta, impacto adelantado, rodillas flexionadas, poco recorrido final.

  Dónde conseguir la información técnica

  - ITF Coaching & Sport Science Review: revista gratuita en línea de la Federación Internacional de Tenis, con artículos de
    técnica y biomecánica por golpe.
  - Libros: Biomechanics of Advanced Tennis (Elliott, Reid y Crespo, editado por la ITF) y Biomechanical Principles of Tennis
    Technique (Duane Knudson). Son la base clásica de la cadena cinética, las fases de cada golpe y los errores típicos.
  - Saque: el «modelo de 8 fases» de Kovacs y Ellenbecker (revista Sports Health, 2011) sirve casi directamente como lista de
    reglas.
  - Artículos científicos: busca en Google Scholar o PubMed términos como tennis forehand kinematics, tennis serve biomechanics
    o two-handed backhand kinematics. Los artículos sirven para el campo references de cada regla.
  - Materiales de federaciones: USTA (Estados Unidos), Tennis Australia y la RFET (España) publican guías de técnica y de
    detección de errores para entrenadores.
  - Entrenadores reales: es para lo que está el portal. Un entrenador propone las reglas y un validador las aprueba.

  Una advertencia importante sobre los umbrales: los números de los artículos se miden casi siempre con captura de movimiento
  3D en laboratorio. Tu sistema estima la postura en 2D a partir de un video, así que esos valores no se pueden copiar tal
  cual. La literatura sirve para decidir qué medir y en qué dirección está el error. El valor exacto se calibra con tus propios
  videos:
  1. Graba unos 20–30 golpes buenos (de un entrenador o un jugador avanzado) y otros tantos de principiantes.
  2. Mira qué valores salen en cada grupo y pon el umbral entre ambos.
  3. El portal tiene una vista previa que muestra en cuántos análisis reales se dispararía una regla, y sirve para afinar.

  Por dónde empezaría yo

  1. Añadir 4 o 5 métricas nuevas al forehand y sus reglas. Es lo que más rápido mejora el resultado, porque el pipeline del
     forehand ya funciona.
  2. Después el saque: es el golpe más estudiado y el que tiene fases más claras.
  3. Luego el revés y la volea.

    Qué hice

  1. Métricas nuevas en el worker (worker/src/tennis_worker/pipeline/biomechanics/metrics.py, 13 en total):
  - Forehand y revés: anchura de la base, cabeza quieta en el impacto, swing de abajo arriba, empuje de piernas y antelación de
    la preparación.
  - Solo forehand: uso del brazo libre.
  - Solo revés: separación de las muñecas en el impacto. Sirve para distinguir el revés a una mano del de dos, y así hay reglas
    solo para el de una mano, como «codo flexionado», que es riesgo de codo de tenista.
  - Saque: altura del impacto, codo en la posición de trofeo, altura del brazo de lanzamiento e inclinación de hombros. La
    posición de trofeo se detecta como el punto más alto del brazo de lanzamiento.
  - Volea: longitud del armado y longitud de la terminación.

  2. Reglas semilla. Amplié forehand.yaml y creé backhand.yaml, serve.yaml y volley.yaml. Cada regla trae explicación,
  corrección, ejercicios, puntos fuertes, relaciones de causa raíz y referencias bibliográficas.

  3. Actualización automática de la semilla. Antes, el YAML solo se cargaba la primera vez, así que las reglas nuevas del
  forehand nunca habrían llegado a la base de datos. Ahora, si subes la version del YAML y nadie ha tocado ese golpe desde el
  portal, el worker publica la versión nueva al arrancar. En cuanto un experto propone o publica algo para un golpe, el YAML ya
  no pisa su trabajo, y si alguien reactiva una versión anterior, se respeta.

  4. Interfaz.
  - Web: revés, saque y volea ya se pueden elegir; antes estaban como «próximamente».
  - App Android: añadí los mismos golpes al selector.
  - Pruebas: scripts/e2e.py acepta ahora --stroke.

  Comprobaciones

  - Worker: ruff y mypy sin errores, y 175 tests en verde con 89 % de cobertura. Hay tests nuevos para cada métrica y para la
    lógica de actualización de la semilla.
  - API (Go): todos los tests en verde con el catálogo de métricas regenerado.
  - Docker: reconstruí la API y el worker, y la base de datos tiene activas las 4 versiones. Reiniciar el worker no vuelve a
    sembrar.
  - Análisis reales con samples/forehand.mp4: con forehand, revés, saque y volea, los cuatro terminan bien y con reportes
    válidos. Solo tengo un video de forehand, así que en los otros golpes los resultados no significan nada; solo demuestran
    que el sistema funciona.
  - App Android: no la compilé. Antes de pasar la apk, ejecuta el paso 5 de Docs/PasosLuegoDeUpdate.md.

  Lo que debes saber

  - Los umbrales son provisionales. Los estimé a partir de la literatura, pero están pensados para captura 3D y no para pose 2D
    desde video. Con un solo video no se pueden calibrar. Por ejemplo, en tu video de forehand la métrica de cabeza quieta dio
    0,254, justo en el límite de 0,25, y saltó el error. Calibrarlos es justo el trabajo de un experto desde el portal, con
    20–30 videos buenos y malos de cada golpe. El método está explicado en Docs/BASE_CONOCIMIENTO.md.
  - Corregí la métrica de la cabeza. La primera versión la medía respecto a la cadera, y el giro del tronco hacía que saltara
    en cualquier forehand normal. Ahora se mide en la imagen y en largos de torso. Esto supone que la cámara está fija y es
    algo menos precisa en videos apaisados.
  - La transferencia de peso no se puede medir, porque las coordenadas de MediaPipe están centradas en la cadera. En su lugar
    se usa el empuje de piernas.
  - Para probar cada golpe de verdad hacen falta videos de ese golpe, por ejemplo samples/serve.mp4, que se prueban con
    scripts/e2e.py samples/serve.mp4 --stroke serve.
    

Con las fases 0–4 ya construidas, lo que queda no es añadir más arquitectura, sino **cerrar los cuatro frentes que separan tu repositorio de un sistema validado y operativo**. El orden importa: cada paso desbloquea el siguiente.

### 🧹 Paso 0: Sanear el entorno de desarrollo (prerrequisito)

Los dos problemas que detectaste no son del proyecto, pero bloquean la ejecución local y el CI si no se corrigen.

- **Visual C++ Redistributable 14.36:** `torch`, `onnx` y `onnxruntime` recientes no cargan en Windows con esa versión. Instala el redistributable actual. Mientras tanto, todo el ML va en Docker, lo cual es correcto, pero el CI en Linux no debería tener este problema.
- **PATH con comilla suelta (`D:\Program Files\src\flutter\bin"`):** Rompe los tests de Gradle. Elimínala del PATH del sistema, no solo del comando puntual. Sin esto, el job de móvil en CI puede fallar de forma intermitente.

**Criterio de salida:** `pytest` y `gradle test` corren en local sin parches manuales en el comando.

---

### 🔀 Paso 1: Primer commit y CI en verde

Tienes cuatro jobs definidos (API, worker, ML, móvil) pero sin remoto ni ejecuciones reales. Este paso valida que la infraestructura que construiste es reproducible fuera de tu máquina.

- **Haz el primer commit** con todo el estado actual (fases 0–4). Incluye `.gitattributes` y los contratos JSON. No commitees datos de THETIS ni artefactos de MLflow; esos van en DVC o en el registro de modelos.
- **Crea el remoto** y observa la primera ejecución del CI. Es probable que aparezcan fallos de dependencias o de versiones de runtime; resuélvelos ahora, antes de introducir el modelo real, para aislar la causa.
- **Para el job de iOS**, GitHub Actions necesita un runner `macos-latest` porque el xcframework solo se enlaza en macOS. Los runners macOS cuestan aproximadamente 10 veces más por minuto que los de Ubuntu, pero son inevitables para compilar iOS. El código compartido ya declara los targets; lo que falta es la app y el login, que requieren Mac o un runner macOS en CI.

**Criterio de salida:** Los cuatro jobs pasan en verde en el remoto. El de iOS puede limitarse a compilar el módulo `shared` en macOS si la app aún no está lista.

---

### 🎾 Paso 2: THETIS, el dataset real (desbloquea todo lo demás)

Sin un modelo real, el worker sigue dependiendo de que el usuario declare el golpe. THETIS es el punto de partida porque es el dataset público con el que se han reportado resultados comparables en la literatura reciente.

**Qué descargar y cómo organizarlo:**

THETIS contiene **1,980 clips RGB** (`.avi`) distribuidos uniformemente en **12 clases** de golpes (forehand flat, open-stance, slice, volley; revés a una y dos manos, slice, volley; tres tipos de saque y smash). Los clips duran entre 2 y 5 segundos y fueron grabados en interior con punto de vista fijo. La estructura de directorios del repositorio ya está organizada por tipo de video y por acción; para el clasificador necesitas el directorio `Video_RGB`, con subdirectorios por clase y archivos con el formato `{actor}_{action}_{sequence}.avi`.

Coloca los clips en `ml/data/raw/thetis/Video_RGB/`, respetando la estructura de subdirectorios por clase.

**Sobre los splits:** La partición estándar para THETIS RGB es **70:20:10**, con **1,386 clips para entrenamiento, 396 para validación y 198 para test**. Si tu pipeline de `ml/` ya espera una estructura concreta, adáptala a esta proporción.

**Ejecuta `dvc repro export_pose_seq`** una vez colocado el dataset. Esto reutiliza el código de pose y de características del worker, así que las secuencias de pose que genere el pipeline de ML serán **exactamente las mismas** que las que el worker calcula en producción. Esa es la garantía de que entrenamiento y servicio no divergen.

**Criterio de salida:** `dvc repro` completa y el hash del dataset en `dvc.lock` coincide con el contenido real de `ml/data/raw/thetis`.

---

### 🧠 Paso 3: Entrenar y validar el clasificador (el objetivo es macro-F1 ≥ 0.90)

Con las secuencias de pose exportadas, ejecuta el pipeline de entrenamiento. Tu configuración ya permite elegir y combinar clasificadores mediante `STROKE_CLASSIFIERS` y `STROKE_COMBINER` (`first`, `cascade`, `vote`, `verify`).

**Qué esperar y cómo interpretarlo:**

El macro-F1 de **0.90 en THETIS es alcanzable, pero no trivial**. Los benchmarks recientes dan contexto: un sistema de diagnóstico de errores en seis clases reporta un macro-F1 de **0.913**; un modelo de reconocimiento de fases alcanza **94.8%** de macro-F1 en su tarea, y ese mismo sistema mantiene **92.4%** de macro-F1 incluso con la cámara inclinada 30°. Modelos más antiguos sobre THETIS, como Inception + LSTM, se quedaban en **84.10% de accuracy** con validación LOSO (leave-one-subject-out). La diferencia entre 0.84 y 0.90 suele estar en la arquitectura y en cómo se maneja la variabilidad entre sujetos, no en el volumen de datos.

**Estrategia de entrenamiento:**

1.  **Empieza con el clasificador de secuencia de pose** (el que usa las secuencias exportadas). Es el que más se beneficia de la reutilización de código entre worker y ML.
2.  **Si el macro-F1 se queda corto, prueba el combinador `vote` o `verify`** con el clasificador YOLO. La configuración ya lo permite sin cambiar código. El YOLO puede aportar información visual que la secuencia de pose pierde (por ejemplo, el contexto de la raqueta).
3.  **Monitoriza el macro-F1, no solo el accuracy.** THETIS está balanceado (165 clips por clase), así que el accuracy y el macro-F1 deberían ser cercanos, pero el macro-F1 penaliza que una clase concreta (por ejemplo, el saque kick) se confunda sistemáticamente con otra.
4.  **Si necesitas mejorar, prioriza la aumentación temporal y la normalización de esqueleto** antes que añadir más capas al modelo. La variabilidad entre los 55 sujetos de THETIS es el principal factor de error.

**Criterio de salida:** El split de test (198 clips) reporta un macro-F1 ≥ 0.90. Si no lo alcanzas, documenta la brecha y la estrategia de mejora antes de publicar el modelo.

---

### 🚀 Paso 4: Publicar el modelo y activar la clasificación automática

Con el modelo validado, regístralo en MLflow y asígnale un **alias** (por ejemplo, `champion`). Los alias de MLflow son referencias mutables a versiones concretas: puedes apuntar `champion` a la versión validada y, cuando mejores el modelo, reasignar el alias sin cambiar el código del worker.

**Configura el worker para descargar el modelo por alias** (`models:/<nombre_del_modelo>@champion`) al arrancar. Así, cuando publiques una nueva versión y muevas el alias, el worker la adoptará en el siguiente reinicio sin necesidad de reconstruir la imagen.

**Prueba de punta a punta:** Sube un video real de un forehand **sin declarar `stroke_type`**. El worker debe clasificarlo automáticamente, segmentar el golpe, calcular las métricas y devolver el reporte. Si el clasificador no supera el umbral de confianza que definas, el análisis debe fallar con `UNSUPPORTED_STROKE` y un mensaje accionable, tal como está previsto en los contratos.

**Criterio de salida:** Un video sin `stroke_type` produce un reporte `completed` con el golpe correctamente clasificado y las métricas calculadas.

---

### 📱 Paso 5: Probar la app Android en emulador o dispositivo

La app compila, pero no se ha ejecutado contra el backend. El flujo de red ya está en el módulo `shared`; lo que falta es verificar que la subida directa al almacenamiento y el *polling* funcionan en un entorno real.

**Cómo conectarla al backend local:**

Usa `adb reverse` para redirigir un puerto del dispositivo al puerto del host donde corre la API. El comando es:

```bash
adb reverse tcp:8080 tcp:8080
```

Esto hace que `localhost:8080` en el dispositivo apunte a `localhost:8080` en tu máquina. Ajusta el puerto si tu API corre en otro. Evita la IP especial `10.0.2.2` del emulador para depuración de WebView; `adb reverse` es más explícito y funciona igual en dispositivo físico.

**Flujo a validar:** Login por navegador (PKCE) → creación del análisis → subida del video en *streaming* directo al almacenamiento → *polling* de `GET /v1/analyses/{id}` hasta `completed` → visualización del reporte.

**Criterio de salida:** El flujo completo funciona en el emulador con `adb reverse` activo.

---

### 🍎 Paso 6: Cerrar iOS (el único frente que requiere Mac)

El código compartido ya declara los targets de iOS. Lo que falta es la capa de UI en SwiftUI y el login con `ASWebAuthenticationSession`, que solo se pueden compilar en macOS.

**Opciones, en orden de pragmatismo:**

1.  **Runner macOS en GitHub Actions:** Puedes configurar un job que compile el xcframework y, si añades la app, la construya. GitHub ofrece runners macOS; el coste es mayor que Linux, pero elimina la dependencia de un Mac físico para el CI.
2.  **Mac local o en la nube:** Si tienes acceso a un Mac, incluso temporalmente, puedes generar el xcframework y probar la app en el simulador. Para “culminar” el proyecto no necesitas publicar en la App Store; basta con que el CI compile el módulo compartido para iOS y que el código de la app esté estructurado.
3.  **Compilación sin Mac:** Es posible generar el XCFramework usando GitHub Actions con un runner macOS, sin poseer un Mac. Esto te permite al menos verificar que el módulo compartido compila para iOS.

**Criterio de salida:** El job de iOS en CI compila el módulo `shared` y, si la app está implementada, la construye en un runner macOS. La app puede quedar sin probar en dispositivo físico, pero el código y el CI están cerrados.

---

### 🗺️ Mapa de culminación

```
[0] Sanear entorno (VC++ / PATH)
        │
        ▼
[1] Primer commit + CI verde (4 jobs)
        │
        ▼
[2] THETIS RGB → dvc repro export_pose_seq
        │
        ▼
[3] Entrenar → macro-F1 ≥ 0.90 en test (198 clips)
        │
        ▼
[4] MLflow alias "champion" → worker descarga modelo
        │
        ├──► [5] Android: adb reverse → flujo completo en emulador
        │
        └──► [6] iOS: CI en macOS compila shared (app si hay Mac)
```

Cada paso tiene un criterio de salida verificable. Cuando el Paso 3 cierre con macro-F1 ≥ 0.90, tendrás un **clasificador validado contra un benchmark público**. Cuando el Paso 4 cierre, el sistema **detectará el golpe solo**, sin intervención del usuario. Cuando el Paso 5 cierre, tendrás **evidencia de que el flujo móvil funciona de punta a punta**. El Paso 6 es el único que depende de hardware externo, y puedes cerrarlo a nivel de CI aunque no pruebes en dispositivo.
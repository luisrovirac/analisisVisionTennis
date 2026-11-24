# Qué hace `analisisVisionTennisActivar.bat`

Analisis del script de arranque (estado a 2026-11-20) y de lo que falta para considerar
**activados todos los elementos** del proyecto.

Archivos analizados:

- `C:\Users\User\analisisVisionTennisActivar.bat`
- `C:\Users\User\analisisVisionTennisModelo.ps1` (auxiliar que llama el `.bat` en el paso 8)

> Resumen: es un **arranque idempotente del stack en Docker** (backend + web embebida + BD + cola +
> storage + IdP + telemetría + MLflow), con un paso opcional y protegido para publicar/activar el
> clasificador de golpes. **No ejecuta tests, no compila la app Android y no corre la prueba de
> punta a punta.**

---

## 1. Paso a paso del `.bat`

| Paso | Qué hace | Detalle importante |
| --- | --- | --- |
| Pre-arranque (líneas 10-17) | Comprueba que existe `C:\Users\User\Projects\SaxNew\analisisVisionTennis\deploy\docker-compose.yml` y entra en esa carpeta | El `.bat` vive fuera del repo, por eso usa ruta absoluta |
| **[1/9] Docker** | `where docker`, `docker info`; si el motor no responde, lanza `Docker Desktop.exe` y espera hasta 3 min (36 × 5 s). Luego exige `docker compose version` | Si falla, termina con `pause`. La espera es más larga que los típicos 15 s, lo cual es acertado |
| **[2/9] `.env`** | Si no existe, lo copia de `.env.example` | ⚠️ Si **ya existe**, no lo compara ni añade variables nuevas. Solo avisa de las contraseñas |
| **[3/9] Compose** | Verifica `deploy\docker-compose.yml` | Redundante con la comprobación inicial, pero inofensivo |
| **[4/9] Stack** | `docker compose -f deploy\docker-compose.yml up -d --build` | Levanta `postgres`, `redis`, `minio`, `minio-init`, `keycloak`, `otel-lgtm`, `api`, `worker`. La API aplica migraciones; el worker siembra reglas y descarga el modelo. Repetirlo es seguro |
| **[5/9] MLflow** | `docker compose -f deploy\docker-compose.yml --profile ml up -d --build mlflow` | Solo el servidor (puerto 5001). Si falla, avisa pero **no detiene** el script |
| **[6/9] Estado** | `docker compose -f deploy\docker-compose.yml --profile ml ps` | Muestra contenedores; el servicio `ml` no aparece corriendo porque es de uso puntual (`run --rm`) |
| **[7/9] Salud** | Hasta 60 intentos × 2 s (2 min) consultando `/readyz` con PowerShell | Si no responde, solo avisa. ⚠️ `/readyz` cubre Postgres/Redis/storage, **no** comprueba login OIDC ni que el worker procese trabajos |
| **[8/9] Modelo** | Busca `C:\Users\User\analisisVisionTennisModelo.ps1` junto al `.bat` y lo llama en modo `check`. Si el archivo falta, avisa y salta | Detallado en la sección 2 |
| **[9/9] Salida** | Imprime URLs, usuarios de prueba y comandos útiles; abre `http://localhost:8080` en el navegador y hace `pause` | ⚠️ Abre el navegador **incluso si el paso 7 avisó** de que la API no está lista |

Al terminar deja levantados los servicios por defecto del compose y MLflow. No detiene nada al
salir: para bajar el stack, `docker compose -f deploy/docker-compose.yml --profile ml down`.

---

## 2. El paso 8 en detalle (`analisisVisionTennisModelo.ps1`)

### Modo `check`

Lee `ml/export/pose_seq/meta.json` y `ml/metrics/pose_seq.json`:

1. Si no existe `meta.json`, imprime «No hay modelo exportado» y sale con código **3** (el `.bat` omite el paso).
2. Si `POSE_SEQ_MODEL_VERSION` coincide con la versión del export **y** `STROKE_CLASSIFIERS` contiene
   `pose_seq`, sale con código **2 = «ya está activo»**. El `.bat` no pregunta nada y sigue.
3. En caso contrario evalúa las 6 compuertas de `Docs/pasosSiguientesEnDev.md` (§3.3-3.4):

   | Compuerta | Umbral |
   | --- | --- |
   | `macro-F1` en test | ≥ 0.90 |
   | F1 de la clase `other` | ≥ 0.75 |
   | Confusión crítica `other → serve` (smash → saque) | ≤ 1 |
   | `threshold_reached_target` | `true` |
   | `accuracy_at_threshold` | ≥ 0.95 |
   | `coverage_at_threshold` | ≥ 0.50 |

   Sale **0** si pasan todas, **1** si falla alguna.

### Confirmación en el `.bat`

| Código del check | Comportamiento |
| --- | --- |
| 0 | «El modelo pasa las compuertas» → pregunta `S/N`, 20 s de timeout, **por defecto N** → si `S`, llama a `-Mode deploy` |
| 1 | Aviso de que no pasa las compuertas y de que activarlo es solo para pruebas → misma pregunta, **por defecto N** |
| 2 | Ya activo → no pregunta, sigue |
| 3 | Sin modelo o error → no pregunta, sigue |

Gracias al timeout con defecto `N`, una ejecución desatendida **no publica nada**.

### Modo `deploy`

Si se responde `S`:

1. Publica con `docker compose -f $compose --profile ml run --rm ml python -m tennis_ml publish export/pose_seq`.
   Si falla, no toca `.env`.
2. Copia `.env` a `.env.bak-<yyyyMMdd-HHmmss>`.
3. Añade `pose_seq` a `STROKE_CLASSIFIERS` (conservando los clasificadores que ya hubiera, o `user`
   si no había ninguno).
4. Fija `POSE_SEQ_MODEL_VERSION` y `STROKE_MIN_CONFIDENCE` (este último desde
   `recommended_min_confidence` del `meta.json`).
5. Recrea solo el worker con `docker compose -f $compose up -d worker`, espera 10 s e imprime
   `ps` y las últimas 15 líneas de logs.
6. Indica cómo deshacerlo: copiar el `.env.bak-*` sobre `.env` y ejecutar
   `docker compose -f deploy\docker-compose.yml up -d worker`.

---

## 3. Estado real de esta máquina (al analizar el script)

- `.env` ya contiene `POSE_SEQ_MODEL_VERSION=20261116-7cce35ad`,
  `STROKE_CLASSIFIERS=user,pose_seq`, `STROKE_MIN_CONFIDENCE=0.99` y un email de admin configurado.
- `ml/export/pose_seq/meta.json` declara exactamente esa versión
  (`20261116-7cce35ad`, `recommended_min_confidence: 0.99`).

**Consecuencia:** hoy el paso 8 imprimirá «El modelo pose_seq 20261116-7cce35ad ya está publicado
y activo en el worker» y no hará nada.

⚠️ **Contradicción documental:** `Docs/pasosSiguientesEnDev.md` afirma que no se publicó ningún
modelo y que el worker sigue en `STROKE_CLASSIFIERS=user`, pero el entorno local sí lo tiene activo.
Además, `ml/metrics/pose_seq.json` corresponde a la variante **baseline** (macro-F1 0.7594, F1 de
`other` 0.6087, precisión@umbral 0.8492): no pasaría las compuertas. Con `STROKE_COMBINER=first`,
si el usuario declara el golpe se respeta su declaración; si no lo declara, clasifica el modelo.
Conviene decidir si ese estado es intencionado antes de dar el stack por bueno.

---

## 4. Qué activa y qué le falta para activarlo todo

| Elemento | ¿Lo activa el `.bat`? | Qué falta |
| --- | --- | --- |
| Postgres + migraciones | ✅ | Verificar `schema_migrations` y logs de la API (el script solo mira `/readyz`) |
| Redis + cola | ✅ | — |
| MinIO + buckets | ✅ (`minio-init`) | — |
| Keycloak / login | ✅ si el contenedor es nuevo | ⚠️ Si cambió `realm-tennis.json` y el contenedor ya existía, no se reimporta: hace falta `up -d --force-recreate keycloak` |
| Grafana / telemetría | ✅ | Revisar errores y DLQ en `http://localhost:3001` |
| API + web embebida | ✅ (`--build` reinyecta el frontend) | Validación visual del portal y de los roles |
| Worker + reglas + modelo | ✅ | — |
| MLflow | ✅ | — |
| Pipeline de entrenamiento ML | ❌ | `dvc repro export_pose_seq` + `dvc push` + `pytest -q` del perfil `ml` |
| Publicación del modelo | ⚠️ Condicionada | En este entorno ya está activo; el script lo detecta y no repite |
| App Android / APK | ❌ | `gradlew :androidApp:assembleDebug`, `adb reverse` (8080/8180/9000) e `installDebug` |
| Prueba de punta a punta | ❌ | `worker/.venv/Scripts/python.exe scripts/e2e.py samples/forehand.mp4 --api http://localhost:8080` |
| Tests/lint de Go, Python, JS y Kotlin | ❌ | `go test`, `ruff/mypy/pytest`, `node --check`, `gradlew test` |
| CI de GitHub | ❌ | Requiere `git push` y revisar la pestaña Actions |
| Documentación | ❌ | Sin cambios automáticos |

---

## 5. Detalles a vigiar al ejecutarlo

1. **No hace `git pull`**: arranca el código que haya en disco, no el último de `main`.
2. **`.env` desactualizado**: si alguien añadió variables a `.env.example`, el `.env` existente no las
   recibe. Conviene comparar ambos archivos antes (aquí el admin ya está configurado, pero el script
   no lo sabe).
3. **Keycloak**: para un cambio de realm, `up -d` normal no basta; hace falta `--force-recreate`,
   que a su vez borra usuarios creados a mano (alice y bob se recrean desde el JSON).
4. **Puerto 8080**: si otro stack local lo ocupa, el paso 7 avisará y el navegador se abrirá igual.
5. **Idempotencia**: repetir el `.bat` no duplica nada. La única acción no idempotente (publicar el
   modelo) está detrás del `check` y de una confirmación `S/N`. Sí se crea un `.env.bak-*` nuevo
   cada vez que se ejecuta `deploy`.

---

## 6. Comandos para cerrar las gaps

Todo desde la raíz del repo, salvo el APK y el e2e:

```bash
# Comparar .env con .env.example y añadir lo que falte a mano (el .env nunca se commitea).

# Si cambió el realm:
docker compose -f deploy/docker-compose.yml up -d --force-recreate keycloak

# Comprobar migraciones y salud real:
docker compose -f deploy/docker-compose.yml logs --tail=50 api worker
curl -s http://localhost:8080/readyz
docker exec tennis-vision-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version, dirty FROM schema_migrations ORDER BY version"'

# Tests:
cd api && gofmt -l . && go vet ./... && go test ./... -cover -count=1 && cd ..
cd worker && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m mypy src && .venv/Scripts/python.exe -m pytest --cov --cov-fail-under=80 -q && cd ..
for f in app.js auth.js portal.js; do node --check "api/web/static/$f" || exit 1; done

# Pipeline de ML (solo si cambió ml/):
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml sh -c "cd ml && ruff check src tests && pytest -q"

# Punta a punta:
worker/.venv/Scripts/python.exe scripts/e2e.py samples/forehand.mp4 --api http://localhost:8080

# Android:
cd mobile && ./gradlew :shared:testDebugUnitTest :androidApp:assembleDebug --console=plain
adb reverse tcp:8080 tcp:8080 && adb reverse tcp:8180 tcp:8180 && adb reverse tcp:9000 tcp:9000
./gradlew :androidApp:installDebug
```

La guía completa y ordenada de «después de actualizar» está en el fichero que acompaña a este
análisis fuera del repo: `C:\Users\User\Documents\000DeSax\DeanalisisVisionTennis\PasosDespuesDeActualizar.md`.

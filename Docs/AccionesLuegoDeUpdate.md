# Acciones luego de un update

Checklist paso a paso para después de **cualquier cambio** en el proyecto. Recorre las secciones en
orden: cada una dice **cuándo aplica** y **qué hacer**. Si una sección no aplica a tu cambio, sáltala.

Al final hay un apartado específico para el **upgrade del 19-11-2026** (roles, portal de expertos,
contrato 1.1).

> Todos los comandos se ejecutan desde la raíz del repositorio salvo que se indique otra carpeta.
> En Windows usa Git Bash. Para conectar desde el host a Postgres usa `127.0.0.1` y no `localhost`:
> `localhost` puede resolver a IPv6 y la conexión se queda colgada.

---

## 0. Antes de empezar: identificar qué tocaste

| Si cambiaste… | Secciones obligatorias |
| --- | --- |
| `contracts/*.schema.json` o `contracts/fixtures/` | 1, 2, 3, 4, 6, 7, 9 |
| `api/migrations/` | 2, 5, 8, 9 |
| `api/` (Go) | 2, 8, 9 |
| `worker/` (Python) | 3, 8, 9 |
| `worker/knowledge_base/*.yaml` | 3, 10 |
| `api/web/static/` (frontend web) | 6, 8 |
| `mobile/` | 7 |
| `deploy/keycloak/realm-tennis.json` | 11, 8 |
| `.env.example` (variables nuevas) | 12, 8 |
| `ml/` | 13 |
| Cualquier cosa visible para el usuario o para otro equipo | 14 |

---

## 1. Contratos (`contracts/`)

`contracts/` es la frontera entre la API, el worker y los clientes. Un error aquí rompe a todos a la vez.

1. Decide si el cambio es **compatible** o **incompatible**:
   - **Compatible:** un campo nuevo y opcional. **No** subas `schema_version`; los clientes deben ignorar
     los campos que no conocen.
   - **Incompatible:** un campo renombrado, eliminado o que pasa a ser obligatorio. Sube `schema_version`
     y haz que los consumidores acepten las dos versiones durante la migración.
2. Actualiza los **tres espejos** del contrato:
   - Go: `api/internal/domain/` y `api/internal/httpapi/dto.go`.
   - Python: `worker/src/tennis_worker/contracts.py` (y `pipeline/knowledge/models.py` para la base de
     conocimiento).
   - Kotlin: `mobile/shared/src/commonMain/kotlin/com/tennisvision/shared/model/Models.kt`. Todo campo
     nuevo debe tener un valor por defecto.
3. Actualiza o crea los **fixtures** en `contracts/fixtures/`. Mantén siempre uno de la versión anterior
   (p. ej. `report.v1_0.example.json`) para comprobar que lo antiguo sigue funcionando.
4. Si cambió el esquema de la base de conocimiento, regenera
   `contracts/fixtures/knowledge_base.example.json` y `metric_catalog.example.json` a partir del YAML y
   del registro de métricas del worker. Los validan a la vez los tests de Go y los de Python.
5. Documenta el cambio en `Docs/CONTRATOS.md`, en la tabla **Versionado**.
6. **Orden de despliegue** cuando el cambio es incompatible: primero el **consumidor** (quien lee),
   después el **productor** (quien escribe). Ejemplo: worker antes que API para `job_message`; clientes
   antes que worker para `report`.

---

## 2. Backend API (Go, `api/`)

```bash
cd api
gofmt -l .            # no debe listar nada; si lista archivos: gofmt -w .
go vet ./...
go test ./... -cover
```

**Tests de integración** (Postgres real). Usa una base de datos **temporal**, nunca la de desarrollo:

```bash
docker exec tennis-vision-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE tennis_it"'
set -a && . ../.env && set +a
TEST_DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/tennis_it?sslmode=disable" \
  go test -tags integration -count=1 ./...
docker exec tennis-vision-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS tennis_it WITH (FORCE)"'
```

Comprueba además:

- [ ] **Ruta nueva:** está en `internal/httpapi/router.go`, dentro del grupo con el `requireRole`
      correcto (`/v1/kb`, `/v1/expert`, `/v1/validator`, `/v1/admin`), y tiene su test en la matriz
      de roles (`internal/httpapi/roles_test.go`).
- [ ] **Error de dominio nuevo:** está mapeado a HTTP en `internal/httpapi/errors.go`, con un mensaje
      que se pueda mostrar al usuario.
- [ ] **Variable de configuración nueva:** se lee y se valida en `internal/config/config.go` y está en
      `.env.example` (sección 12).
- [ ] **Acción sensible** (roles, validaciones, publicaciones): queda registrada en `audit_log`.
- [ ] **Cuerpos JSON del portal:** se leen con `decodeStrict`, que rechaza campos desconocidos.

---

## 3. Worker (Python, `worker/`)

```bash
cd worker
.venv/Scripts/python.exe -m ruff check src tests     # Linux/macOS: .venv/bin/python
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe -m pytest --cov --cov-fail-under=80
```

Con un video real en `samples/` y el modelo de MediaPipe descargado:

```bash
.venv/Scripts/python.exe -m pytest -m integration -s   # imprime el reporte; útil para calibrar umbrales
```

Comprueba además:

- [ ] **Métrica nueva:** está registrada en `pipeline/biomechanics/registry.py` y tiene un test con pose
      sintética (`tests/synthetic.py`). Al arrancar, el worker la publica sola en `metric_catalog`; a
      partir de ese momento los expertos pueden usarla en el portal.
- [ ] **Campo nuevo del reporte:** el test `test_report_validates_against_json_schema` sigue pasando.
- [ ] **Variable de configuración nueva:** está en `config.py`, validada al arrancar, y en
      `.env.example`.
- [ ] **SQL nuevo** (`infrastructure/`): lo probaste contra una base de datos temporal (ver sección 2).

---

## 4. Contrato desde cada consumidor

Después de tocar `contracts/`, valida los fixtures desde los tres lados:

```bash
cd api    && go test ./internal/domain/ ./internal/service/
cd worker && .venv/Scripts/python.exe -m pytest tests/test_contracts.py tests/test_knowledge.py tests/test_published_knowledge.py
cd mobile && ./gradlew :shared:testDebugUnitTest
```

---

## 5. Migraciones de base de datos (`api/migrations/`)

1. Crea el par `NNNN_descripcion.up.sql` / `NNNN_descripcion.down.sql` con el número siguiente.
   **Nunca** edites una migración que ya se aplicó en algún entorno.
2. Usa `IF NOT EXISTS` / `IF EXISTS` y valores por defecto, para que funcione sobre una base con datos.
3. Pruébala en la base temporal (sección 2): los tests de integración aplican `migrations.Up` al empezar.
4. Prueba también el `down` en la base temporal.
5. La API aplica las migraciones al arrancar.
   > ⚠ Una API **antigua** no arranca contra una base migrada a una versión que no conoce. Despliega
   > siempre la API nueva, y no hagas rollback de la imagen de la API sin revertir antes la migración.
6. El worker escribe en tablas cuyo esquema es de la API (`analyses`, `kb_releases`, `metric_catalog`).
   Si cambias esas tablas, revisa también el SQL de `worker/src/tennis_worker/infrastructure/`.

---

## 6. Frontend web (`api/web/static/`)

Los archivos estáticos van **embebidos en el binario de la API**: un cambio en el frontend obliga a
reconstruir la imagen de la API (sección 8).

```bash
node --check api/web/static/app.js
node --check api/web/static/auth.js
node --check api/web/static/portal.js
```

Comprueba a mano en el navegador (`http://localhost:8080`):

- [ ] **Página del usuario:** login, subir un video, ver el reporte (puntos fuertes, errores, plan,
      fiabilidad) y el checkbox «Permitir que expertos vean este video».
- [ ] **Reporte antiguo:** un análisis viejo del historial (sin campos 1.1) se sigue viendo bien.
- [ ] **Portal (`/portal.html`):** entra con un usuario de cada rol (usuario, experto, validador, admin).
      Cada uno ve solo sus pestañas.
- [ ] **XSS:** se sigue construyendo el DOM con `el()` / `textContent`; nunca con `innerHTML` y datos
      del servidor.
- [ ] **Modo oscuro y móvil:** se ven bien (estilos en `styles.css`).
- [ ] **Página nueva:** su ruta queda cubierta por los `redirectUris` del cliente `tennis-web` en
      Keycloak (`http://localhost:8080/*` ya cubre cualquier página).

---

## 7. App móvil (`mobile/`, Android + KMP)

```bash
cd mobile
./gradlew :shared:testDebugUnitTest :androidApp:assembleDebug
```

- [ ] **Campo nuevo del contrato:** está en `Models.kt` con valor por defecto, y hay un test en
      `SharedTest.kt` (`ReportContractTest`) que decodifica la versión nueva y la antigua.
- [ ] **Prueba manual:** la app contra el backend local (ver `mobile/README.md`).
- [ ] **Portales:** son solo web; la app móvil cubre el rol de usuario.

---

## 8. Reconstruir y levantar el entorno local

```bash
# Reconstruye solo lo que cambió (api incluye el frontend web embebido)
docker compose -f deploy/docker-compose.yml up -d --build api worker

# Comprueba que arrancaron bien
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=50 api worker
curl -s http://localhost:8080/readyz
```

- [ ] **API:** el log dice «API escuchando», sin errores de migración.
- [ ] **Worker:** el log muestra la base de conocimiento cargada o sembrada. Si el worker arranca
      antes de que la API termine de migrar, falla y se reinicia solo (`restart: unless-stopped`).
- [ ] **Grafana** (`http://localhost:3001`): no hay errores nuevos en los logs ni trabajos en la DLQ.

---

## 9. Prueba de punta a punta

```bash
python scripts/e2e.py samples/forehand.mp4 --api http://localhost:8080
```

Recorre: login de alice y bob, subida, análisis completo y aislamiento entre usuarios.

Si el cambio toca roles o el portal, verifica además a mano el flujo completo:
admin invita → experto propone → validador aprueba → un análisis nuevo muestra la nueva
`knowledge_base_version`.

---

## 10. Base de conocimiento (reglas del análisis)

- **Cambios de reglas o umbrales en producción:** se hacen desde el **portal**, no editando el YAML.
  El experto propone, el validador aprueba y la versión nueva entra sola en el worker.
- **`worker/knowledge_base/*.yaml`:** es solo la **semilla**. El worker lo importa únicamente si ese
  golpe todavía no tiene ninguna versión en Postgres. Editar el YAML **no cambia** una base que ya está
  en la base de datos.
- **Golpe nuevo** (p. ej. revés):
  1. Registra sus métricas en el worker.
  2. Crea `backhand.yaml`.
  3. Reinicia el worker: siembra la versión inicial.
  4. A partir de ahí, se gestiona desde el portal.
- **Cambio del esquema de reglas:** actualiza a la vez `contracts/knowledge_base.schema.json`,
  `worker/.../knowledge/models.py` y `api/internal/domain/knowledge.go`. Regenera después los fixtures
  (sección 1.4).
- **Rollback de reglas:** desde el portal, en «Historial de versiones» → «Reactivar».
- **Desarrollo sin portal:** `KNOWLEDGE_SOURCE=yaml` hace que el worker lea directamente los archivos.

---

## 11. Keycloak (`deploy/keycloak/realm-tennis.json`)

Keycloak importa el realm **solo si no existe**. Para aplicar cambios del JSON en local hay que recrear
el contenedor. En `start-dev` no hay volumen persistente, así que recrearlo reimporta el realm:

```bash
docker compose -f deploy/docker-compose.yml up -d --force-recreate keycloak
```

- [ ] **Recrear tiene coste:** se pierden los usuarios creados a mano en Keycloak (alice y bob se
      recrean desde el JSON).
- [ ] **Producción:** aplica los cambios por la consola de administración o con la Admin API; no
      reimportes el realm.
- [ ] **Invitados:** necesitan el email **verificado** para recibir roles. En producción activa
      «Verify email» con un servidor SMTP configurado.

---

## 12. Variables de entorno

1. Añade la variable a `.env.example`, con un comentario y un valor seguro por defecto.
2. Añádela a tu `.env` local (el `.env` **nunca** se commitea; tampoco los `.env.bak-*`).
3. Recrea los servicios que la leen. `docker compose up -d` sin `--build` basta si solo cambió el `.env`.
4. En producción, cárgala en el gestor de secretos o de configuración del entorno.

---

## 13. MLOps (`ml/`)

```bash
cd ml && ruff check src tests && pytest -q
```

Un modelo nuevo solo se publica si pasa las compuertas de `Docs/pasosSiguientesEnDev.md`. Cambiar de
clasificador es un cambio de configuración (`STROKE_CLASSIFIERS`, `STROKE_COMBINER`, versiones de los
modelos), no de código.

---

## 14. Documentación y cierre

- [ ] **`Docs/CONTRATOS.md`:** rutas nuevas, códigos de error, campos del reporte, tabla de versionado.
- [ ] **`Docs/BASE_CONOCIMIENTO.md`:** cambios en reglas, métricas o en el flujo de validación.
- [ ] **`Docs/ARQUITECTURA.md`:** componentes o decisiones nuevas.
- [ ] **`README.md`:** si cambian los comandos para levantar o probar el proyecto.
- [ ] **CI** (`.github/workflows/ci.yml`): los jobs `api`, `worker`, `ml` y `mobile` en verde. El
      worker exige ≥ 80 % de cobertura.
- [ ] **Commits** en bloques lógicos (contrato, backend, worker, frontend, móvil, docs), sin secretos.

---

## Apartado específico: upgrade del 19-11-2026 (roles + portal de expertos + contrato 1.1)

Pasos **una sola vez** para poner en marcha este upgrade en un entorno que ya existía:

1. **Primer admin:** añade a tu `.env` el email con el que entrarás, por ejemplo
   `BOOTSTRAP_ADMIN_EMAILS=alice@example.test`. Con `AUTH_DISABLED=true`, usa `dev@localhost`.
2. **Variables opcionales:** si quieres cambiar los valores por defecto, añade
   `KNOWLEDGE_SOURCE=postgres` y `KNOWLEDGE_REFRESH_SECONDS=15`.
3. **Reconstruir la API y el worker juntos:**
   `docker compose -f deploy/docker-compose.yml up -d --build api worker`.
   - La API aplica las migraciones `0005_roles` y `0006_knowledge`. Las imágenes antiguas ya no
     arrancan contra esa base de datos.
   - El worker publica el catálogo de métricas y siembra `forehand@0.2.0` (con los 7 puntos fuertes y
     las relaciones de causa raíz).
4. **Comprobar la siembra:** el log del worker debe mostrar «base de conocimiento sembrada en Postgres».
5. **Entrar como admin:** abre `http://localhost:8080`, inicia sesión con el email del paso 1 y entra en
   «Portal profesional» → «Administración».
6. **Invitar al equipo:** invita a los expertos y validadores (rol `expert` y/o `validator`). Cada uno
   completa su perfil en «Mi perfil».
7. **Prueba del flujo completo:**
   1. Un experto propone cambiar un umbral.
   2. Otro validador lo aprueba.
   3. Sube un video nuevo: el reporte muestra la nueva `knowledge_base_version` y «Regla validada» en
      ese hallazgo.
   4. Haz rollback desde el «Historial de versiones» y comprueba que el siguiente análisis usa la
      versión anterior.
8. **App móvil:** reconstruye la app Android para mostrar puntos fuertes, plan y el checkbox. Las
   versiones antiguas siguen funcionando: ignoran los campos nuevos.
9. **Pendientes conocidos:**
   - Activar la verificación de email en Keycloak con SMTP antes de producción.
   - Ampliar `scripts/e2e.py` con el flujo del portal.
   - Construir la biblioteca de ejercicios como entidades propias (fase 4 del plan,
     `Docs/planIII-upgrade-expertos.md`).

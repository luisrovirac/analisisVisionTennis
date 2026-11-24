analisisVisionTennis
Pasos luego de un update (después de editar código)

Cómo usar esta guía:
- Abre **Git Bash** (no PowerShell ni CMD). Todos los comandos son para Git Bash.
- Sigue los pasos en orden. Cada paso dice **cuándo hacerlo**. Si no aplica a lo que cambiaste, sáltalo.
- Si un comando da error, para y arréglalo antes de seguir.
- Los pasos marcados con **(siempre)** se hacen después de cualquier cambio.

Usuarios de prueba (para entrar en la web):
- alice / alice-dev-only
- bob / bob-dev-only

---

* 1. Ver qué cambiaste (siempre)
cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
git status

Mira qué carpetas aparecen y usa esta tabla para saber qué pasos te tocan:

| Si aparece…                          | Haz los pasos      |
| ------------------------------------ | ------------------ |
| api/ (archivos .go)                  | 2, 8, 9, 13        |
| api/migrations/                      | 2, 7, 8, 9, 13     |
| api/web/static/ (la web)             | 4, 8, 9, 12        |
| worker/                              | 3, 8, 9, 13        |
| worker/knowledge_base/*.yaml         | 3, 8 y lee el 14   |
| contracts/                           | 2, 3, 5, 6, 8, 13  |
| mobile/                              | 5                  |
| deploy/keycloak/realm-tennis.json    | 10, 9              |
| .env.example                         | 11, 8              |
| ml/                                  | 6b                 |

Al final, siempre el paso 15 (git).

---

* 2. Revisar y probar la API (Go)
Cuándo: cambiaste algo en api/ o en contracts/.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis/api
gofmt -l .
go vet ./...
go test ./... -cover

- `gofmt -l .` no debe mostrar nada. Si muestra archivos, ejecuta `gofmt -w .` y vuelve a empezar.
- `go test` debe terminar con `ok` en todas las líneas (ninguna con `FAIL`).

---

* 3. Revisar y probar el worker (Python)
Cuándo: cambiaste algo en worker/ o en contracts/.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis/worker
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe -m pytest --cov --cov-fail-under=80

- ruff debe decir `All checks passed!`.
- mypy debe decir `Success: no issues found`.
- pytest debe terminar en verde y con cobertura de 80 % o más (si baja de 80 %, falla).

---

* 4. Revisar la web
Cuándo: cambiaste algo en api/web/static/.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
node --check api/web/static/app.js
node --check api/web/static/auth.js
node --check api/web/static/portal.js
node --check api/web/static/portal-validation.js
node --check api/web/static/skeleton-viewer.js

Si no sale nada, está bien. Si sale un error, hay un fallo de sintaxis en ese archivo.

Ojo: la web va metida dentro de la API, así que después hay que reconstruir la API (paso 8).

---

* 5. Probar y compilar la app Android
Cuándo: cambiaste algo en mobile/ o en contracts/.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis/mobile
./gradlew :shared:testDebugUnitTest :androidApp:assembleDebug

Debe terminar con `BUILD SUCCESSFUL`.
La apk queda en:
C:\Users\User\Projects\SaxNew\analisisVisionTennis\mobile\androidApp\build\outputs\apk\debug
Para probarla contra el backend local, sigue mobile/README.md.

---

* 6. Si cambiaste contracts/ (el formato de los datos)
contracts/ define cómo se pasan los datos entre la API, el worker y la app. Si lo cambias, hay que probar los tres. Basta con haber hecho los pasos 2, 3 y 5 sin errores.

Regla sencilla para no romper nada:
- Si **añadiste** un campo nuevo y opcional: no pasa nada, puedes actualizar en cualquier orden.
- Si **borraste o renombraste** un campo: actualiza primero el programa que lee ese dato y después el
  que lo escribe. En la práctica, en local: reconstruye API y worker juntos (paso 8) y luego compila
  la app (paso 5). Apúntalo en Docs/CONTRATOS.md, tabla «Versionado».

* 6b. Si cambiaste ml/ (entrenamiento de modelos)
cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml sh -c "cd ml && ruff check src tests && pytest -q"

---

* 7. Probar las migraciones en una base de datos temporal
Cuándo: añadiste un archivo en api/migrations/ o cambiaste SQL en worker/src/tennis_worker/infrastructure/.
Usa una base de datos de usar y tirar; tu base de datos de desarrollo no se toca.
Requiere que Postgres esté levantado (si no: `docker compose -f deploy/docker-compose.yml up -d postgres`).

Crear la base temporal:
cd /c/Users/User/Projects/SaxNew/analisisVisionTennis/api
docker exec tennis-vision-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE tennis_it"'

Ejecutar los tests de integración contra ella (copia las tres líneas juntas):
set -a && . ../.env && set +a
TEST_DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/tennis_it?sslmode=disable" \
  go test -tags integration -count=1 ./...

Borrar la base temporal (hazlo aunque los tests fallen):
docker exec tennis-vision-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS tennis_it WITH (FORCE)"'

Reglas de las migraciones:
- Nunca edites una migración que ya se ejecutó. Crea una nueva con el número siguiente
  (la última ahora es 0007, la siguiente sería 0008_algo.up.sql y 0008_algo.down.sql).
- Una vez migrada la base, una API antigua ya no arranca contra ella. No vuelvas a una versión
  vieja de la API sin deshacer antes la migración.

---

* 8. Reconstruir y levantar el sistema en Docker
Cuándo: cambiaste api/, worker/, la web, contracts/ o el .env.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
docker compose -f deploy/docker-compose.yml up -d --build api worker

Tarda unos minutos la primera vez. La API ejecuta sola las migraciones al arrancar.

---

* 9. Comprobar que todo arrancó bien (siempre que hiciste el paso 8 o el 10)
cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=50 api worker
curl -s http://localhost:8080/readyz

Qué tienes que ver:
- En `ps`: api y worker con estado `Up` (no `Restarting` ni `Exited`).
- En los logs de la API: «API escuchando» y ningún error de migración.
- En los logs del worker: que cargó o sembró la base de conocimiento.
  Si el worker falla una vez al principio es normal: arrancó antes que la API y se reinicia solo.
  Espera 30 segundos y vuelve a mirar los logs.
- `curl` responde sin error.
- Opcional: en Grafana http://localhost:3001 no hay errores nuevos.

---

* 10. Aplicar cambios de Keycloak (login / usuarios / roles)
Cuándo: cambiaste deploy/keycloak/realm-tennis.json.

Aviso: esto borra los usuarios que creaste a mano en Keycloak. alice y bob se vuelven a crear solos.

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
docker compose -f deploy/docker-compose.yml up -d --force-recreate keycloak

Después haz el paso 9.
(En producción no se hace así: allí los cambios se hacen a mano en la consola de Keycloak.)

---

* 11. Variables de entorno nuevas
Cuándo: cambiaste .env.example (alguien añadió una variable nueva).

1. Abre los dos archivos y compara:
   C:\Users\User\Projects\SaxNew\analisisVisionTennis\.env.example
   C:\Users\User\Projects\SaxNew\analisisVisionTennis\.env
2. Copia a tu .env las variables que falten, con su valor.
3. Haz el paso 8.

El .env nunca se sube a git (tampoco los .env.bak-*).

---

* 12. Probar a mano en el navegador
Cuándo: cambiaste la web, la API o algo que ve el usuario.

Abre http://localhost:8080 y comprueba:
- Entras con alice / alice-dev-only.
- Subes un video y ves el reporte (puntos fuertes, errores, plan de mejora, fiabilidad).
- Aparece el checkbox «Permitir que expertos vean este video».
- Un análisis viejo del historial se sigue viendo bien.
- Portal: http://localhost:8080/portal.html — cada rol (usuario, experto, validador, admin) ve
  solo sus pestañas.
- Se ve bien en modo oscuro y con la ventana estrecha (como en el móvil).

---

* 13. Prueba completa automática (de punta a punta)
Cuándo: cambiaste la API, el worker o contracts/.

Necesitas un video de una derecha en la carpeta samples (créala si no existe):
C:\Users\User\Projects\SaxNew\analisisVisionTennis\samples\forehand.mp4

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
worker/.venv/Scripts/python.exe scripts/e2e.py samples/forehand.mp4 --api http://localhost:8080

Para otro golpe añade `--stroke backhand`, `--stroke serve` o `--stroke volley` (con un video de ese golpe).

Entra como alice y bob, sube el video, espera el análisis y comprueba que uno no ve los datos del otro.
Debe terminar sin errores.

---

* 14. Si cambiaste las reglas del análisis (worker/knowledge_base/*.yaml)
Importante: editar el YAML normalmente **no cambia** las reglas que ya están en uso. El YAML se usa:
- la primera vez, cuando ese golpe todavía no existe en la base de datos;
- o si subes su `version` y ese golpe **nunca** se ha tocado desde el portal (ni propuestas ni
  versiones publicadas por expertos). En ese caso el worker publica la versión nueva al arrancar.
En cuanto un experto trabaja un golpe desde el portal, el YAML ya no lo pisa.

- Para cambiar una regla o un umbral: hazlo desde el portal (http://localhost:8080/portal.html).
  Un experto lo propone, un validador lo aprueba y el worker lo usa solo.
- Para volver a una versión anterior de las reglas: portal → «Historial de versiones» → «Reactivar».
- Para añadir un golpe nuevo (p. ej. revés): crea worker/knowledge_base/backhand.yaml, haz los pasos
  3 y 8, y desde ahí se gestiona en el portal.
- Para probar reglas sin portal: pon `KNOWLEDGE_SOURCE=yaml` en el .env y haz el paso 8.

---

* 15. Guardar en git (siempre)
cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
git status
git add .
git commit -m "describe aquí el cambio, p. ej. portal de expertos 19-11-2026"
git push

Antes del `git add .`, revisa en `git status` que no aparezca el .env ni ningún .env.bak-*.
Después, en GitHub (pestaña Actions) comprueba que los jobs api, worker, ml y mobile salen en verde.

Si cambiaste rutas, errores, campos del reporte o reglas, actualiza también la documentación:
Docs/CONTRATOS.md, Docs/BASE_CONOCIMIENTO.md, Docs/ARQUITECTURA.md o README.md.

---
---

Solo una vez: upgrade del 19-11-2026 (roles + portal de expertos)

Haz esto una única vez para poner en marcha este upgrade sobre tu instalación actual.

* A. Darte permisos de administrador
Tu .env todavía no tiene esta variable. Añádela con el email con el que vas a entrar:

cd /c/Users/User/Projects/SaxNew/analisisVisionTennis
echo "BOOTSTRAP_ADMIN_EMAILS=alice@example.test" >> .env

(Si usas AUTH_DISABLED=true, el email es dev@localhost.)

* B. Reconstruir API y worker juntos
docker compose -f deploy/docker-compose.yml up -d --build api worker

Esto migra la base de datos (0005_roles y 0006_knowledge) y carga las reglas iniciales
(forehand@0.2.0). Después de esto, las versiones viejas de la API ya no arrancan.

* C. Comprobar
docker compose -f deploy/docker-compose.yml logs --tail=50 worker

Debe aparecer: «base de conocimiento sembrada en Postgres».

* D. Entrar como admin e invitar al equipo
1. Abre http://localhost:8080 y entra con alice / alice-dev-only.
2. Ve a «Portal profesional» → «Administración».
3. Invita a los expertos y validadores (rol expert y/o validator).
   Cada uno completa su perfil en «Mi perfil».

* E. Probar el flujo completo
1. Un experto propone cambiar un umbral.
2. Un validador (otra persona) lo aprueba.
3. Sube un video nuevo: el reporte debe mostrar la nueva versión de reglas y «Regla validada».
4. En «Historial de versiones» reactiva la versión anterior y comprueba que el siguiente análisis
   usa esa versión.

* G. Validación formal (revisiones, campañas Delphi, calibración)
Requiere al menos 3 cuentas con rol de experto o validador y análisis con permiso de revisión.
1. En «Casos», abre un análisis, califica algunas reglas (1-7 + veredicto) y pulsa «Guardar mi revisión».
   Como validador, pulsa «Agregar al corpus» en varios casos.
2. En «Campañas y corpus», crea una campaña con esos casos y 3 evaluadores. Entra con cada evaluador:
   la vista del caso es ciega (sin hallazgos ni métricas). Califica, abre la ronda 2 (verás la
   distribución anónima) y cierra la campaña: el α y el ground truth aparecen en el detalle.
3. En el editor de una regla, pulsa «Calibrar»: curva precisión/recall contra el ground truth.
4. «Calidad de la base» muestra los KPIs y las reglas a auditar. Proponer una regla con
   «Pedir validada con datos» sin evidencia suficiente debe fallar al aprobar (422).

* F. App Android
Haz el paso 5 y pasa la apk nueva. Las apks viejas siguen funcionando, solo que no muestran lo nuevo.

* Pendiente (no se hace ahora)
- Activar la verificación de email en Keycloak con un servidor de correo antes de producción.
- Completar el flujo del portal en scripts/e2e.py: ya cubre la revisión libre y los KPIs de calidad; faltan propuestas, campañas Delphi y calibración.
- Biblioteca de ejercicios (fase 4 de Docs/planIII-upgrade-expertos.md).

# App móvil (Kotlin Multiplatform)

| Módulo | Contenido |
| --- | --- |
| `shared/` | Red (Ktor), autenticación (`AuthRepository`, refresco OIDC), modelos y el flujo de análisis. `commonMain` es el mismo código para Android e iOS; solo el motor HTTP cambia (OkHttp / Darwin). |
| `androidApp/` | UI en Compose, login con AppAuth (PKCE) y almacén de tokens en el Android Keystore. |

El flujo es el mismo que el del cliente web: `POST /v1/analyses` → `PUT` directo a la URL prefirmada
(el video nunca pasa por la API) → `POST /submit` → *polling* con backoff de `GET /v1/analyses/{id}`.

## Probar contra el stack local

```bash
docker compose -f deploy/docker-compose.yml up -d        # desde la raíz del repo
# El emulador o el teléfono (USB) ven los puertos del PC como localhost. Así coinciden el `iss` de
# los tokens (http://localhost:8180/...) y el host de las URLs prefirmadas (localhost:9000).
adb reverse tcp:8080 tcp:8080 && adb reverse tcp:8180 tcp:8180 && adb reverse tcp:9000 tcp:9000
./gradlew :androidApp:installDebug
```

Usuarios de prueba del realm de desarrollo: `alice` / `alice-dev-only` y `bob` / `bob-dev-only`.
Las URLs se configuran en `gradle.properties` (`tennis.apiBaseUrl`, `tennis.oidcIssuer`, `tennis.oidcClientId`).

## Tests

```bash
./gradlew :shared:testDebugUnitTest      # flujo completo, refresco proactivo y 401 con MockEngine
./gradlew :androidApp:assembleDebug
```

**Windows:** si los tests fallan con `no se ha encontrado o cargado la clase principal ...`, el `PATH`
tiene una entrada con comillas (`"`) que rompe la línea de comandos del JVM de tests. Quítala del
`PATH` del sistema (o ejecuta Gradle con `PATH` saneado).

## iOS

`shared` ya declara los targets `iosX64`, `iosArm64` e `iosSimulatorArm64` y el motor Darwin. Falta:
compilar el framework en macOS (`./gradlew :shared:linkDebugFrameworkIosSimulatorArm64`), una app
SwiftUI que lo consuma, un `TokenStore` sobre el Keychain y el login con AppAuth-iOS (o
`ASWebAuthenticationSession`). La lógica de red, tokens y polling no hay que reescribirla.

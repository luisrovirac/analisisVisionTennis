@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM ==========================================================
REM  Tennis Vision - Arranque completo desde cero (Windows)
REM  Levanta backend + frontend web embebido via Docker Compose
REM ==========================================================

REM Ir a la raíz del proyecto (este .bat está en Docs\)
cd /d "%~dp0\.."

echo.
echo [1/7] Verificando Docker...
where docker >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker no está instalado o no está en el PATH.
  echo Instala Docker Desktop y vuelve a intentar.
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker no parece estar en ejecución.
  echo Abre Docker Desktop, espera a que inicie y vuelve a ejecutar este script.
  pause
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo ERROR: "docker compose" no está disponible.
  echo Actualiza Docker Desktop a una versión que incluya Docker Compose v2.
  pause
  exit /b 1
)

echo.
echo [2/7] Verificando archivo .env...
if not exist ".env" (
  if exist ".env.example" (
    echo No existe .env. Copiando desde .env.example...
    copy /Y ".env.example" ".env" >nul
    echo IMPORTANTE: revisa y ajusta contraseñas/valores en .env antes de usar en un entorno compartido.
  ) else (
    echo ERROR: No se encontró .env ni .env.example en la raíz del proyecto.
    pause
    exit /b 1
  )
) else (
  echo .env encontrado.
)

echo.
echo [3/7] Comprobando archivo de compose...
if not exist "deploy\docker-compose.yml" (
  echo ERROR: No se encontró deploy\docker-compose.yml.
  pause
  exit /b 1
)

echo.
echo [4/7] Construyendo y levantando servicios (primera vez puede tardar varios minutos)...
echo       Servicios: postgres, redis, minio, keycloak, otel-lgtm, api, worker
docker compose -f deploy\docker-compose.yml up -d --build
if errorlevel 1 (
  echo.
  echo ERROR: Falló "docker compose up". Revisa los mensajes anteriores.
  echo Para ver logs: docker compose -f deploy\docker-compose.yml logs --tail=200
  pause
  exit /b 1
)

echo.
echo [5/7] Estado de contenedores:
docker compose -f deploy\docker-compose.yml ps

echo.
echo [6/7] Esperando a que la API responda (healthcheck /readyz)...
set "READY="
for /l %%i in (1,1,60) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8080/readyz' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 (
    set "READY=1"
    goto :ready_done
  )
  timeout /t 2 /nobreak >nul
)
:ready_done

if defined READY (
  echo API lista: http://localhost:8080/readyz
) else (
  echo AVISO: La API no respondió lista aún. Puede seguir iniciando (Keycloak/DB pueden tardar).
  echo Revisa: docker compose -f deploy\docker-compose.yml logs -f api keycloak postgres
)

echo.
echo [7/7] Accesos útiles:
echo   - Web + API:            http://localhost:8080
echo   - Keycloak (dev):       http://localhost:8180
echo   - Grafana:              http://localhost:3001
echo   - Consola MinIO:        http://localhost:9001
echo.
echo Usuarios de desarrollo (Keycloak, realm importado): alice/alice-dev-only, bob/bob-dev-only
echo.
echo Comandos útiles:
echo   Ver logs:     docker compose -f deploy\docker-compose.yml logs -f
echo   Detener:      docker compose -f deploy\docker-compose.yml down
echo   Reiniciar:    docker compose -f deploy\docker-compose.yml up -d
echo.

REM Opcional: abrir la app web en el navegador
start "" "http://localhost:8080"

echo Listo.
pause
endlocal

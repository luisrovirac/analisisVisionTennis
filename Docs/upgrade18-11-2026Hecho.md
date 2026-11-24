
  1. Contrato del reporte (1.1)
  - Qué recibe el usuario: el reporte ahora trae, además de los errores, lo que haces bien (hasta 5 puntos fuertes), un resumen con la causa raíz, un plan de ejercicios de 4 semanas y cuánto confiar en el informe.
  - Puntos fuertes: añadí 7 en forehand.yaml. Un punto fuerte nunca se muestra si se midió con poca confianza o si choca con un error de salud.
  - Causa raíz: enlacé errores entre sí; por ejemplo, la preparación sin giro causa el impacto atrasado, y este la terminación corta.
  - Web y Android: muestran las secciones nuevas y el checkbox «Permitir que expertos vean este video», marcado por defecto.
  - Compatibilidad: los reportes antiguos se siguen viendo igual que antes.

  2. Roles
  - Los 4 roles: admin, usuario, experto y validador, guardados en Postgres.
  - Invitaciones: el admin invita por email y la persona recibe el rol al entrar con ese email verificado.
  - Experto y validador a la vez: se puede, pero la API y la propia base de datos le impiden aprobar lo suyo.
  - Protecciones del admin: no puede quitarse su propio rol ni suspender su cuenta.
  - Auditoría: cada cambio de rol, invitación y decisión queda registrado.

  3. Base de conocimiento en la base de datos
  - Arranque: la primera vez, el worker importa el YAML como versión inicial.
  - Propuesta del experto: antes de enviarla puede ver en cuántos análisis reales se activaría su regla, antes y después del cambio.
  - Decisión del validador: aprueba, pide cambios o rechaza. Al aprobar se publica una versión nueva y los análisis la usan en unos 15 segundos, sin reiniciar nada.
  - Si la base cambió entretanto: la aprobación se bloquea y se pide al experto que reenvíe la propuesta.
  - Rollback: un validador o el admin puede volver a cualquier versión anterior.
  - Dónde está: todo vive en un portal nuevo, /portal.html, con pestañas según el rol: Base de conocimiento, Mis propuestas, Validación, Solicitudes de métricas, Administración y Mi perfil.

  Para las pruebas de integración creé una base de datos temporal (tennis_it) en tu Postgres, separada de tus datos, y la borré al terminar.

  Para verlo funcionando
  1. Añade a tu .env la línea BOOTSTRAP_ADMIN_EMAILS=tu-email (en desarrollo, alice@example.test).
  2. Reconstruye con docker compose -f deploy/docker-compose.yml up -d --build api worker.
  3. Entra en la web: verás el enlace «Portal profesional».

  Ojo: los contenedores que tienes corriendo son la versión anterior. En cuanto la API nueva aplique las migraciones, esa versión anterior ya no podrá arrancar contra esa base de datos, así que hay que reconstruir los dos.

  Diferencias con el plan y pendientes
  - Reglas sin tabla propia: cada versión publicada guarda todas sus reglas juntas, en lugar de tener una tabla separada de reglas. Es más simple y las versiones quedan inmutables.
  - Ejercicios: siguen siendo texto dentro de cada regla; la biblioteca de ejercicios queda para la fase 4.
  - Verificación de email en Keycloak: no la activé, porque en desarrollo no hay servidor de correo y bloquearía los registros nuevos. En producción hace falta.
  - Prueba de punta a punta: no amplié scripts/e2e.py con el flujo del portal.
  - Diff del contrato: contracts/report.schema.json quedó reformateado, así que su diff se ve más grande de lo que es el cambio real.

***
He creado Docs/PasosLuegoDeUpdate.md con el mismo estilo que tu guía de LearnAllChess: cada paso tiene un título, el cd a la carpeta y los comandos para copiar y pegar. También dice qué tienes que ver para saber que todo salió bien (por ejemplo «BUILD SUCCESSFUL», «estado Up» o ««API escuchando» en el log»).

  Cómo está organizado:
  - Paso 1: git status y una tabla que indica qué pasos te tocan según la carpeta que cambiaste.
  - Pasos 2 a 14: probar la API, el worker, la web, la app Android y las migraciones; reconstruir en Docker; comprobar los logs; Keycloak; variables del .env; prueba en el navegador; prueba completa automática; y cómo cambiar las reglas del análisis.
  - Paso 15: git add, commit y push, y comprobar en GitHub Actions que todo sale en verde.
  - Al final: los pasos que se hacen una sola vez para el upgrade del 19-11-2026.

  El «Orden de despliegue» que pusiste de ejemplo ahora dice: si solo añadiste un campo, da igual el orden; si borraste o renombraste uno, en local reconstruye la API y el worker juntos (paso 8) y luego compila la app (paso 5).

  Lo que encontré al revisar el proyecto:
  - Te falta BOOTSTRAP_ADMIN_EMAILS en tu .env. Sin esa variable nadie tiene permisos de administrador en el portal. En la sección del upgrade está el comando para añadirla.
  - No existe la carpeta samples/, así que la prueba completa automática (paso 13) no funcionará
    hasta que pongas ahí un video forehand.mp4.
  - ml/ no tiene entorno de Python propio, así que en el paso 6b sus tests se ejecutan dentro de Docker. Ese comando lo armé leyendo docker-compose.yml y no lo he ejecutado
    
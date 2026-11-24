
Revisé el código y la base de datos. Lo más importante: la app no envía ningún correo. Ahora mismo nadie tiene el rol de
  admin, y por eso no ves el área de expertos.

  Por qué no ves el área de expertos

  - El enlace «Portal profesional» solo aparece si tu cuenta tiene un rol: admin, experto o validador. Ninguna cuenta lo tiene
    ahora.
  - En tu .env está BOOTSTRAP_ADMIN_EMAILS=aitestsax@gmail.com. Nadie ha entrado todavía con ese email.
  - El rol admin solo se da si el email está verificado en Keycloak. Las cuentas que registras desde la web (por ejemplo
    luisrovirac@gmail.com) quedan sin verificar, porque Keycloak no tiene servidor de correo configurado. alice y bob sí están
    verificados.

  Cómo entrar como admin (elige una opción)

  Opción A, la más rápida: usar alice como admin
  1. En C:\Users\User\Projects\SaxNew\analisisVisionTennis\.env cambia la línea a:
     BOOTSTRAP_ADMIN_EMAILS=aitestsax@gmail.com,alice@example.test
  2. En Git Bash, en la carpeta del proyecto:
     docker compose -f deploy/docker-compose.yml up -d api
  3. Entra en http://localhost:8080 con alice / alice-dev-only. Si ya tenías la sesión abierta, recarga la página. Aparecerá
     «Portal profesional» → «Administración».

  Opción B: usar tu propia cuenta
  1. Abre la consola de Keycloak en http://localhost:8180 y entra con admin / change_me_dev_only.
  2. Arriba a la izquierda cambia al realm tennis → Users → busca tu usuario → activa Email verified → Save.
  3. Pon ese email en BOOTSTRAP_ADMIN_EMAILS (o regístrate con aitestsax@gmail.com), ejecuta el comando del paso 2 de la opción
     A y vuelve a entrar en la web.

  Cómo probar la invitación a un experto (sin correo)

  Una invitación solo reserva roles para un email. Los roles se aplican cuando esa persona inicia sesión con ese email
  verificado. Para probarlo:

  1. Como admin: «Portal profesional» → «Administración» → invita a experto@example.test con el rol expert.
  2. En la consola de Keycloak (realm tennis) → Users → Add user:
     - Pon username experto, email experto@example.test y activa Email verified.
     - En la pestaña Credentials, ponle una contraseña con Temporary = Off.
  3. Cierra sesión en la web y entra como experto. Verás «Portal profesional», y en «Mi perfil» completa sus datos.

  Atajo: si invitas a bob@example.test, que ya está verificado, el rol se le aplica al momento. Así puedes probar el flujo
  completo: un experto propone un cambio y otro usuario como validador lo aprueba. Por ejemplo, bob como experto y alice con
  los roles admin y validador.

  Para que lleguen correos de verdad haría falta configurar un servidor de correo (SMTP) en Keycloak, para la verificación de
  email, y añadir el envío de invitaciones en la API. Las dos cosas están en «Pendiente» en Docs/PasosLuegoDeUpdate.md.
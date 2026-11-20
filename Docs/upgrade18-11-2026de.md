
Tú has sido muy claro en tu petición actual: **quieres que el análisis sea confiable y certero, y quieres crear un área para que los expertos (tenis/biomecánica) enriquezcan el sistema, con 4 roles definidos.** 

---

# Plan de Upgrade: Confiabilidad del Análisis + Portal de Expertos

## 1. El diagnóstico: ¿Por qué el análisis actual no es "confiable y certero"?

Ahora mismo, tu sistema extrae 7 métricas con MediaPipe y las pasa por un YAML con umbrales provisionales. Tienes dos problemas de fiabilidad:
1. **Falta de validación experta:** Los umbrales (`validated_by: null`) son conjeturas. Un ángulo de codo de 150° puede ser un error para un principiante, pero ser correcto para un avanzado.
2. **Visión incompleta:** Solo analizas errores (lo negativo). Un análisis confiable debe decir qué está bien (puntos fuertes), por qué falla (causa raíz) y qué hacer para mejorar (plan de drills).

**El objetivo es pasar de un "detector de errores" a un "diagnóstico de nivel entrenador".**

---

## 2. Sistema de Roles (Los 4 Usuarios)

Para que los expertos puedan enriquecer el sistema, necesitas una capa de permisos sólida basada en OIDC (Keycloak ya lo soporta).

| Rol | Permisos y Funciones |
| :--- | :--- |
| **Admin** | Dios del sistema. Crea y gestiona a los expertos. Puede ver todo, modificar la base de datos, gestionar la infraestructura y aprobar cambios críticos. |
| **Experto (Tenis/Biomecánica)** | Accede a un **Portal de Expertos**. Puede revisar videos, validar reglas (YAML), ajustar umbrales, crear puntos fuertes y añadir drills. Su trabajo se refleja en los análisis de los usuarios. |
| **Usuario** | Sube videos, solicita análisis, ve sus resultados y su progreso. (El usuario actual). |
| **Usuario Futuro** | Existe en la base de datos y puede autenticarse, pero su UI está bloqueada o muestra un mensaje de "Próximamente". No tiene acceso a análisis. |

---

## 3. El Portal de Expertos (El nuevo entorno a crear)

Este es el corazón de tu petición. Los profesionales necesitan una interfaz donde su conocimiento se convierta en código (o configuración) sin romper el sistema.

### ¿Qué puede hacer un Experto en el Portal?
1. **Editor de Reglas (YAML):** Una interfaz donde el experto ve la regla (ej. "Golpeo con el brazo") y puede ajustar los umbrales viendo videos de ejemplo de la vida real.
2. **Validación:** Cuando un experto aprueba una regla, ésta se firma (`validated_by: "Nombre del Experto"`) y deja de ser "provisional".
3. **Añadir Puntos Fuertes:** Crear reglas positivas (ej. "Excelente rotación de hombros").
4. **Catálogo de Drills:** Crear y editar ejercicios correctivos. Asignarles videos demostrativos.
5. **Revisión de Casos:** Ver los análisis reales de los usuarios (anonimizados o con permiso) y marcar si el sistema acertó o falló, para calibrar el modelo.

### Arquitectura del Portal
- **Frontend:** Una nueva sección en tu web actual (protegida por rol). No necesitas un framework nuevo, con JS modular es suficiente.
- **Backend:** La API en Go recibe peticiones del experto y actualiza los archivos YAML en el worker (o en una base de datos de conocimiento).
- **Validación:** El sistema no se actualiza "en caliente" de forma peligrosa. El experto guarda un borrador y, al publicarlo, el worker recarga la nueva base de conocimiento.

---

## 4. Plan de Ejecución (Fases de Ingeniería)

### Fase 1: El Core Técnico (Confiabilidad del Análisis)
*Objetivo: Que las métricas y la visión sean robustas antes de que los expertos las manipulen.*

1. **Normalización de Video y Pose:** Antes de calcular métricas, el worker debe normalizar el esqueleto (centrar en la cadera, escalar por tamaño corporal). Esto evita que el modelo se confunda por la distancia de la cámara.
2. **Métricas de Puntos Fuertes:** Implementa en `biomechanics/metrics.py` las métricas necesarias para detectar los puntos fuertes actuales (ya tienes las 7, solo hay que añadir la lógica de "si es mayor que X, es bueno").
3. **Contrato v1.1 (Aditivo):** Actualiza `report.schema.json` para incluir:
   - `strengths[]` (puntos fuertes).
   - `diagnosis` (causa raíz).
   - `drill_plan` (plan de ejercicios).
   *(Asegúrate de que los clientes actuales ignoran estos campos si no los entienden).*

### Fase 2: El Portal de Expertos y Roles
*Objetivo: Construir el entorno para los profesionales.*

1. **Base de Datos de Roles:** Implementa la tabla de roles en Postgres y configura Keycloak para inyectar el rol (`admin`, `expert`, `user`, `future`) en el token JWT.
2. **API para Expertos:** Nuevos endpoints en Go:
   - `GET /v1/expert/rules` (Listar reglas).
   - `PUT /v1/expert/rules/{id}` (Actualizar umbrales).
   - `POST /v1/expert/validate` (Firmar regla).
   - `GET /v1/expert/feedback` (Ver análisis reales para calibrar).
3. **Frontend del Portal:** Una vista en la web (o subdominio) donde el experto ve el YAML de forma amigable, lo edita y lo publica.
4. **El "Usuario Futuro":** Implementa la lógica en el frontend y backend: si el rol es `future`, la API devuelve un `403 Forbidden` con un mensaje amigable ("Tu cuenta está en lista de espera").

### Fase 3: La Capa de Conocimiento (Enriquecimiento)
*Objetivo: Poblar el sistema con la experticia.*

1. **Migración de YAML a Base de Datos:** Para que el portal sea rápido y seguro, mueve la base de conocimiento (`forehand.yaml`) de archivos estáticos a tablas de Postgres (`knowledge_rules`, `drills`, `rule_evidence`).
2. **Causalidad (Root Cause):** Añade a las reglas el campo `caused_by` y `prerequisites`. Esto permite que el sistema diga: *"Tu impacto es tardío (error A) porque tu preparación es lenta (causa raíz B)"*.
3. **Prescripción de Drills:** Crea la biblioteca de ejercicios y enlázala a las reglas. El reporte final incluirá un plan de 4 semanas.

### Fase 4: Expansión (Opcional pero recomendado)
1. **Multi-golpe:** Hasta ahora solo analizas forehand. Con los expertos validando, expande las métricas y reglas a revés, saque y volea.
2. **Multi-repetición:** Que un solo video pueda contener 5 golpes y el sistema analice la consistencia.

---

## 5. Cambios Clave respecto a tus documentos anteriores

- **Prioridad al Portal de Expertos:** En tu plan anterior, el portal era la Fase B6 (muy al final). Aquí lo subimos a la Fase 2, porque es el motor que permitirá que el resto del sistema sea confiable.
- **Roles Claros:** Tu documento mencionaba `coach`, `admin`, `player`. Yo lo he adaptado a tus 4 roles exactos (`admin`, `experto`, `usuario`, `futuro`).
- **Sin dependencia de MLflow para el worker:** Mantienes la decisión de que el worker cargue modelos fijos desde S3 por determinismo. El portal de expertos solo modifica las reglas, no los modelos de visión.
- **Enfoque en la confiabilidad:** Antes de añadir métricas nuevas (como raqueta/pelota), primero validamos las 7 actuales con los expertos.

## 6. Próximos pasos inmediatos (Lo que yo haría mañana)

1. **Definir el contrato v1.1:** Añadir `strengths` y `diagnosis` al JSON schema.
2. **Configurar Keycloak:** Añadir los 4 roles y proteger las rutas de la API en Go.
3. **Crear la tabla de reglas en Postgres:** Migrar `forehand.yaml` a una tabla para que el experto pueda editarla sin tocar archivos.
4. **Prototipo del Portal:** Una página web donde el experto vea las reglas y un botón de "Validar".


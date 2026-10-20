# Seguridad: modelo de amenazas

Este documento dice qué ataques considera el diseño, cómo los mitiga y qué queda fuera. Léelo antes de instalar el revisor en un repositorio público o con datos sensibles.

## El principio: Regla de dos

La Regla de dos de Meta para agentes dice que, dentro de una misma sesión, un agente no debe reunir más de dos de estas tres propiedades:

- **A.** Procesa contenido no confiable (el PR).
- **B.** Tiene acceso a secretos o datos sensibles (la llave de la IA).
- **C.** Puede cambiar estado o comunicarse hacia afuera (comentar, etiquetar, hacer push, llamar a internet).

Los incidentes reales de 2025 y 2026 con agentes en CI (Clinejection, PromptPwnd, CamoLeak, el RCE de CodeRabbit) tienen en común que reunían las tres. Aquí cada etapa tiene como máximo dos:

| Etapa | A | B | C |
| --- | --- | --- | --- |
| Preparar | ✔ | ✘ | ✘ |
| Revisar (IA) | ✔ | ✔ | ✘ (sin red salvo la API, sin escritura, token de GitHub de solo lectura y fuera de su entorno) |
| Decidir | ✘ (solo un JSON validado) | ✘ | ✔ |

## Amenazas y mitigaciones

| # | Amenaza | Mitigación |
| --- | --- | --- |
| 1 | **Prompt injection en el PR** (descripción, comentarios en el código, nombres de archivo) para que la IA apruebe | El contenido del PR va en bloques marcados como no confiables con un identificador aleatorio que el autor no conoce. La IA tiene instrucciones de reportar estos intentos. Los controles deterministas buscan frases dirigidas al revisor y llevan el PR a una persona. Y lo principal: aunque la IA apruebe, ella nunca aprueba el PR, y los tests desactivados, los secretos y las rutas de riesgo se deciden sin IA |
| 2 | **Comentarios HTML ocultos** en la descripción (la técnica de CamoLeak) | Se retiran antes de enviar la descripción a la IA: ve lo mismo que una persona. Si contienen texto dirigido al revisor, el PR va a REVISIÓN HUMANA |
| 3 | **Caracteres invisibles o bidireccionales** (Trojan Source, ASCII smuggling con etiquetas Unicode) | Se detectan, se muestran como marcas visibles (`⟦U+202E⟧`) en lo que ve la IA, y los bidireccionales llevan el PR a una persona |
| 4 | **Robo de la llave de la IA** por un agente engañado | El proceso de Claude corre con `--restricted`: sin Bash, sin herramientas de código, sin WebFetch y con la lectura de archivos limitada al repositorio y al contexto. Solo recibe una lista cerrada de variables de entorno. La respuesta se revisa contra patrones de secretos antes de publicarla |
| 5 | **Robo del token de GitHub** | El checkout usa `persist-credentials: false`, así el token no queda en `.git/config`. El proceso de la IA no recibe `GITHUB_TOKEN` ni `ACTIONS_RUNTIME_TOKEN`. La etapa que lee el PR con IA tiene solo `contents: read` |
| 6 | **Exfiltración por el comentario** (imágenes o enlaces con datos en la URL) | Todo texto de la IA se limpia antes de publicarse: se quitan imágenes, enlaces, HTML y menciones; las URLs quedan como texto no clicable. Los campos tienen longitud máxima |
| 7 | **Un PR que debilita su propia revisión** (cambia la rúbrica, los patrones o los scripts) | Scripts, prompts, esquema y configuración se leen de la rama base, no del PR. Cambiar `.revisor/`, `.github/` o la configuración de agentes es siempre de alto riesgo y requiere aprobación de CODEOWNERS |
| 8 | **Configuración del repositorio que ejecuta código** (hooks de `.claude/settings.json`, `.mcp.json`, `CLAUDE.md`, plugins) | `--bare` y `--restricted` hacen que Claude Code no lea nada de eso del checkout del PR |
| 9 | **Git ejecutando código del PR** (drivers de diff, hooks, fsmonitor) | `git diff` corre con `--no-ext-diff --no-textconv`, `core.hooksPath=/dev/null`, `core.fsmonitor=false` y sin la configuración del sistema |
| 10 | **Inyección en el workflow** (`${{ github.event.pull_request.title }}` dentro de `run:`) | El workflow no interpola texto del PR en comandos. Los scripts leen el evento desde el archivo JSON de GitHub. Los SHA se validan con expresión regular |
| 11 | **Cadena de suministro** (una acción o el CLI comprometidos) | Acciones fijadas por SHA de commit. Claude Code se instala con `npm ci` y un lockfile con hashes de integridad. Dependabot propone actualizaciones con 7 días de espera. Los scripts del revisor solo usan la biblioteca estándar de Python |
| 12 | **Envenenamiento de caché** (la técnica de Clinejection) | El workflow no usa cachés: `setup-node` con `package-manager-cache: false` y sin `actions/cache` |
| 13 | **Veredicto manipulado entre etapas** | La etapa de decisión vuelve a validar el JSON contra el esquema. Uno inválido se trata como falla de la IA |
| 14 | **Que una falla del revisor deje pasar un PR** | La etapa de decisión corre siempre. Si la preparación falla, el check falla. Si la IA falla, la decisión por defecto es REVISIÓN HUMANA. Un check omitido cuenta como aprobado en GitHub, por eso nunca se omite cuando hay algo que decidir |
| 15 | **Gasto descontrolado** | Tope por revisión (`presupuesto_usd`), límite de turnos y de tiempo, sin revisión de borradores, cancelación de ejecuciones anteriores del mismo PR. Usa una llave dedicada con límite de gasto en la Claude Console |
| 16 | **Secretos del repositorio enviados al proveedor** | El diff se envía con los secretos detectados ya redactados. La IA tiene prohibido leer `.env`, llaves, `.npmrc`, `.netrc`, `secrets/` y `.git/` |
| 17 | **Esconder un archivo del análisis** con un nombre que git pone entre comillas (`"`, `\`, tabulador), con espacios, o con separadores Unicode (U+2028) que fingen una línea o una cabecera `diff --git` | El lector del diff decodifica las rutas entre comillas, corrige la ruta con la línea `+++`, solo corta líneas en `\n` y distingue cabecera de contenido. Una cabecera que no se puede leer no se pega al archivo anterior: va a la IA, cuenta como riesgo ALTO y lleva el PR a una persona |
| 18 | **Enlaces simbólicos** que apuntan fuera del repositorio (p. ej. a `/proc/self/environ`, donde está la llave de la IA) | Si el PR agrega o cambia un enlace simbólico, la IA no se ejecuta y el PR va a REVISIÓN HUMANA. Además, antes de ejecutar la IA se retira cualquier enlace del checkout que apunte fuera del repositorio (en local, la IA no se ejecuta) |
| 19 | **El autor anula su propio bloqueo** con la etiqueta de anulación | Se consulta en la API quién puso la etiqueta. Solo cuenta si no es el autor del PR y tiene un rol de `roles_que_anulan` (`admin` o `maintain` por defecto) |
| 20 | **Exfiltración con enlaces sin esquema** (`www.sitio/?d=...`, que GitHub convierte en enlace) | La limpieza también neutraliza `www.` |
| 21 | **Revisión local de una rama no confiable** | El modo local usa `--safe-mode` y `--restricted`: no carga `CLAUDE.md`, hooks, skills, plugins, MCP ni ajustes de la rama revisada |

## Lo que este diseño NO cubre

Ser explícito con esto es parte de la seguridad:

- **Una persona con permiso de escritura que quiere atacar.** En el evento `pull_request`, el workflow que corre es el del PR. Alguien con permisos de escritura puede modificarlo en su rama y ejecutar lo que quiera con la llave de la IA. Mitígalo con CODEOWNERS y aprobación obligatoria para `.github/`, una llave dedicada con límite de gasto y, si lo necesitas, un *environment* con revisores obligatorios (abajo).
- **Que la IA se equivoque.** La IA puede aprobar algo malo o bloquear algo bueno. Por eso nunca aprueba, por eso hay controles sin IA y por eso las rutas de riesgo siempre van a una persona. La etiqueta de anulación existe para los falsos positivos.
- **Ataques que los patrones no conocen.** Los controles deterministas detectan formas conocidas de desactivar tests o esconder texto. Un atacante creativo puede encontrar otras. Son una capa, no la defensa completa.
- **PRs desde forks.** GitHub no entrega secretos a ejecuciones de `pull_request` desde forks, así que la IA no corre en ellos (los controles sin IA sí). No incluimos una variante con `pull_request_target` porque ejecuta con los secretos del repositorio base sobre código de terceros; `actions/checkout` v7 se niega a hacerlo por defecto por esa razón, y es la puerta que se usó en varios incidentes. Si tu repositorio es público, revisa los PRs de forks con el comando local o pide a quien contribuye que lo corra.
- **Runners propios (self-hosted).** No los uses con este workflow en repositorios públicos: un runner persistente puede quedar contaminado entre ejecuciones.
- **Lo que haga el proveedor con los datos.** El diff y los archivos que la IA lea se envían a la API de Anthropic. Revisa las políticas de retención de datos de tu cuenta y las reglas de tu organización antes de usarlo en código confidencial.

## Endurecimiento opcional

1. **Environment con aprobación.** Crea un environment `revision-ia` en **Settings → Environments**, guarda ahí `ANTHROPIC_API_KEY` (en vez de en los secretos del repositorio) y agrega `environment: revision-ia` al trabajo `revisar`. Si le pones revisores obligatorios, cada revisión con IA espera aprobación: más control, menos automatización.
2. **Llave dedicada.** Un workspace de la Claude Console solo para el revisor, con límite de gasto mensual, y rotación periódica de la llave.
3. **Federación de identidad.** Si usas Bedrock, Google Cloud o Foundry, autentica por OIDC y no guardes llaves fijas.
4. **Auditoría.** El workflow `pruebas-revisor.yml` corre zizmor en línea en cada cambio de `.github/workflows/`, así también verifica que cada SHA fijado pertenezca a su acción. Mantenlo.
5. **Origen del check.** En el ruleset, fija el check requerido **Decisión** a la app *GitHub Actions* como origen, para que otra integración no pueda publicar un check con el mismo nombre.

## Reportar una vulnerabilidad

Ver [SECURITY.md](../SECURITY.md).

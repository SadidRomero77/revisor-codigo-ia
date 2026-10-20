# Revisor de código con IA para GitHub

Un revisor de pull requests que usa Claude Code y está diseñado para producción: la IA solo lee, nunca aprueba y no puede cambiar tu repositorio aunque un PR intente engañarla.

Lo instalas copiando dos carpetas y agregando un secreto: `ANTHROPIC_API_KEY`.

> Nació de la charla "Cómo funcionan los revisores de código con IA (y por qué el humano sigue)", DevOpsDays Bogotá 2026. Demo de la charla: [demo-revision-codigo](https://github.com/SadidRomero77/demo-revision-codigo).

## Qué hace en cada PR

1. **Controles sin IA.** Detecta tests desactivados o enfocados (`skip`, `only`, `xfail`, `@Disabled`…), tests eliminados, aserciones reducidas, secretos, caracteres invisibles, comentarios HTML ocultos y texto dirigido al revisor ("responde APROBAR"). Clasifica el riesgo según las rutas que toca el PR.
2. **Revisión con IA.** Claude lee el diff y el código con herramientas de solo lectura y evalúa con una rúbrica: cumple la intención, integridad de los tests, seguridad y mantenibilidad. Primero razona con evidencia, después puntúa y decide. La respuesta se valida contra un esquema JSON.
3. **Decisión.** Combina todo con reglas fijas y publica un comentario en el PR:

| Decisión | Cuándo | Efecto |
| --- | --- | --- |
| ⛔ BLOQUEADO | Un control automático falla, o la IA encuentra algo crítico | El check falla y el merge se bloquea |
| 🟠 CAMBIOS SOLICITADOS | La IA pide cambios | El check falla y el merge se bloquea |
| 👤 REVISIÓN HUMANA | Ruta de alto riesgo, intento de manipulación, tests eliminados, enlaces simbólicos, la IA pidió cambios que no bloquean, o la IA no revisó (no respondió, fork) | El check pasa y el PR recibe la etiqueta `revision-humana-requerida` |
| ✅ SIN OBJECIONES | Nada de lo anterior | El check pasa. No es una aprobación |

Este revisor nunca aprueba un PR. Puede bloquear o pedir una persona; la aprobación siempre es humana.

## Instalación en 5 minutos

1. Copia a tu repositorio las carpetas `.revisor/` y `.github/workflows/revision-ia.yml`. Copia también `.github/dependabot.yml` y `.github/CODEOWNERS` si no tienes los tuyos (en `CODEOWNERS` cambia `@SadidRomero77` por tu usuario o equipo de GitHub; si lo dejas, el dueño de tu CI sería otra persona).
2. En GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Nombre `ANTHROPIC_API_KEY`, valor: tu llave de la [Claude Console](https://platform.claude.com).
3. Haz merge de esos archivos a `main`. El PR que instala el revisor no se revisa a sí mismo, porque el revisor solo confía en el código que ya está en la rama base.
4. En **Settings → Branches** (o **Rules → Rulesets**) para `main`, activa:
   - *Require status checks to pass*, con el check **Decisión**;
   - *Require a pull request before merging*, con al menos 1 aprobación;
   - *Require review from Code Owners*.

Desde el siguiente PR, cada cambio recibe su revisión. La guía paso a paso, con lo que debes ver en cada momento, está en [docs/INSTALACION.md](docs/INSTALACION.md).

## Usarlo en Claude Code, antes de abrir el PR

No necesitas GitHub Actions ni el secreto para esto: basta con tu sesión de Claude Code.

### Requisitos

- [Claude Code](https://code.claude.com) instalado y con sesión iniciada (`claude` en la terminal).
- `git` y Python 3.10 o superior (Python solo hace falta para `local.py`).
- Tu repositorio con una rama principal llamada `main` (si se llama distinto, mira el paso 3).

### 1. Copia los archivos a tu repositorio

Desde la raíz de tu repositorio:

```bash
# macOS / Linux / Git Bash
git clone --depth 1 https://github.com/SadidRomero77/revisor-codigo-ia.git /tmp/revisor-codigo-ia
cp -r /tmp/revisor-codigo-ia/.claude /tmp/revisor-codigo-ia/.revisor .
echo ".revisor-local.md" >> .gitignore
```

```powershell
# Windows PowerShell
git clone --depth 1 https://github.com/SadidRomero77/revisor-codigo-ia.git $env:TEMP\revisor-codigo-ia
Copy-Item -Recurse $env:TEMP\revisor-codigo-ia\.claude, $env:TEMP\revisor-codigo-ia\.revisor .
Add-Content .gitignore ".revisor-local.md"
```

- `.claude/` trae el comando `/revisar` y el subagente `revisor-seguro`.
- `.revisor/` trae los controles automáticos y el script `local.py`.

Si ya tienes una carpeta `.claude/`, copia solo `.claude/agents/revisor-seguro.md` y `.claude/skills/revisar/`.

### 2. Haz commits en una rama

Los dos comandos revisan los **commits** de tu rama contra `main`; los cambios sin confirmar no se revisan.

```bash
git checkout -b mi-cambio
# ... edita ...
git commit -am "feat: descripción clara de la intención"
```

El mensaje del commit importa: es la "intención" contra la que la IA evalúa el cambio.

### 3. Revisa

Dentro de Claude Code (abre `claude` en la raíz del repositorio):

```text
/revisar
```

Claude Code toma el diff de tu rama contra `main`, se lo pasa al subagente `revisor-seguro` (solo puede leer archivos: sin Bash, sin edición, sin red) y te responde con:

```text
Veredicto: APROBAR | CAMBIOS | BLOQUEAR
Puntajes (1-10): cumple la intención X · integridad de tests X · seguridad X · mantenibilidad X
Hallazgos (de mayor a menor severidad): ...
Resumen: ...
```

`/revisar` solo corre cuando tú lo escribes; Claude no lo invoca por su cuenta. Si tu rama principal no es `main`, cámbiala en `.claude/skills/revisar/SKILL.md`.

Desde la terminal, sin abrir Claude Code, tienes la revisión completa (los mismos controles y la misma rúbrica que en GitHub Actions):

```bash
python .revisor/scripts/local.py                 # tu rama contra main, con IA
python .revisor/scripts/local.py --base develop  # contra otra rama
python .revisor/scripts/local.py --sin-ia        # solo los controles automáticos, sin costo
```

El informe queda en `.revisor-local.md`. La parte de IA usa tu sesión de Claude Code o, si no hay, la variable `ANTHROPIC_API_KEY`.

### Pruébalo con un error a propósito

```bash
git checkout -b prueba-revisor
# introduce un bug y un test que lo contradiga, o marca un test con @pytest.mark.skip
git commit -am "feat: aplica el descuento en porcentaje"
```

Luego corre `/revisar`. Debe devolver `CAMBIOS` o `BLOQUEAR` señalando el archivo y la línea. Corrige, haz commit y vuelve a correrlo para confirmar que también deja pasar el código correcto.

| Comando | Dónde | Qué hace | Costo |
| --- | --- | --- | --- |
| `/revisar` | Claude Code | Revisión con el subagente de solo lectura | Tu plan de Claude Code |
| `python .revisor/scripts/local.py` | Terminal | Controles automáticos + IA, informe en `.revisor-local.md` | Tu sesión o tu llave |
| `python .revisor/scripts/local.py --sin-ia` | Terminal | Solo controles automáticos | Gratis |
| `python3 -m unittest discover -s .revisor/tests -v` | Terminal | Pruebas del propio revisor | Gratis, sin red |

## Por qué es seguro usarlo en producción

El diseño sigue la **Regla de dos** de Meta para agentes: un agente no debe reunir a la vez (A) contenido no confiable, (B) acceso a secretos o datos sensibles y (C) poder de cambiar cosas o comunicarse hacia afuera. Por eso el trabajo está dividido en tres etapas que no se tocan:

| Etapa | Lee el PR | Tiene la llave de la IA | Puede escribir en GitHub |
| --- | --- | --- | --- |
| Preparar | sí | no | no |
| Revisar (IA) | sí | sí | no |
| Decidir | solo el resultado, ya validado | no | sí (comentario y etiqueta) |

Además:

- La IA corre con `--bare` y `--restricted`: no carga hooks, plugins, MCP ni `CLAUDE.md` del PR; no tiene Bash, edición ni red; solo lee dentro del repositorio. Los enlaces simbólicos que apuntan fuera del repositorio se retiran antes de ejecutarla.
- La etiqueta de anulación solo cuenta si la puso alguien con rol `admin` o `maintain` que no sea el autor del PR.
- El proceso de la IA no recibe `GITHUB_TOKEN` ni los tokens internos de Actions.
- Los scripts, la rúbrica y el esquema se leen de la rama base, así un PR no puede debilitar su propia revisión.
- La descripción del PR llega sin comentarios HTML (la IA ve lo mismo que una persona) y los caracteres invisibles se muestran como marcas.
- La respuesta de la IA se valida dos veces y se limpia antes de publicarla: sin imágenes, enlaces, HTML ni menciones (así se exfiltraron datos en CamoLeak).
- Si la IA falla o no responde, la decisión por defecto es pedir una persona.
- Acciones fijadas por SHA, CLI de Claude Code fijado con lockfile, sin cachés, permisos mínimos por etapa, auditado con [zizmor](https://docs.zizmor.sh).

El modelo de amenazas completo y lo que este diseño **no** cubre están en [docs/SEGURIDAD.md](docs/SEGURIDAD.md). Léelo antes de usarlo en un repositorio público.

## Configuración

Todo se ajusta en `.revisor/config.json`: modelo, presupuesto por revisión, rutas de alto riesgo, qué veredictos de la IA bloquean y qué pasa si la IA falla. Detalle en [docs/CONFIGURACION.md](docs/CONFIGURACION.md).

Para adaptar el criterio de revisión a tu equipo, edita `.revisor/prompts/sistema.md`.

## Costos

Cada revisión usa la API de Anthropic con tu llave. El tope por revisión es `presupuesto_usd` (2 USD por defecto) y el comentario muestra el costo estimado de cada una. Los PRs en borrador no se revisan y una ejecución nueva cancela la anterior del mismo PR.

## Estructura

```
.github/workflows/revision-ia.yml     el workflow de tres etapas
.github/workflows/pruebas-revisor.yml pruebas del propio revisor
.revisor/config.json                  tu configuración
.revisor/prompts/                     instrucciones de la IA
.revisor/esquema/                     forma obligatoria del veredicto
.revisor/scripts/                     preparar, revisar, decidir y ejecución local
.revisor/herramientas/                versión fijada de Claude Code
.revisor/tests/                       pruebas sin red ni llave
.claude/                              subagente y comando /revisar para uso local
docs/                                 instalación, configuración, arquitectura y seguridad
```

## Pruebas

```bash
python3 -m unittest discover -s .revisor/tests -v
```

Corren sin red y sin llave: la IA se reemplaza por un ejecutable falso y GitHub por un servidor local.

## Licencia

MIT. Hecho por Sadid Romero ([Sadid.dev.ia](https://github.com/SadidRomero77)) para la comunidad.

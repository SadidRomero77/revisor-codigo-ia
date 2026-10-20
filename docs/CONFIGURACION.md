# Configuración

Todo está en `.revisor/config.json`. Los cambios aplican desde que llegan a la rama base: un PR que cambia la configuración se revisa con la configuración anterior.

| Opción | Por defecto | Qué hace |
| --- | --- | --- |
| `modelo` | `claude-sonnet-5-5` | Modelo de la revisión. Acepta un ID completo o un alias (`sonnet`, `opus`) |
| `max_turnos` | `25` | Máximo de pasos de la IA (leer archivos, buscar). Más turnos, más contexto y más costo |
| `presupuesto_usd` | `2.0` | Tope de gasto estimado por revisión. Al llegar, la revisión se detiene y aplica `si_falla_la_ia` |
| `timeout_segundos` | `600` | Tiempo máximo de la revisión |
| `max_diff_kb` | `300` | Tamaño máximo del diff que ve la IA. Si se supera, se recorta y el PR pasa a REVISIÓN HUMANA |
| `rutas_alto_riesgo` | auth, pagos, seguridad, migraciones, infra, Dockerfile, manifiestos de dependencias | Expresiones regulares sobre la ruta del archivo. Un PR que toque alguna va a REVISIÓN HUMANA aunque todo esté bien |
| `rutas_excluidas_del_diff` | lockfiles, minificados, `dist/`, `vendor/`, binarios | Archivos que no se envían a la IA (sí cuentan para los controles y el riesgo) |
| `ia_bloquea_en` | `["BLOQUEAR", "CAMBIOS"]` | Qué veredictos de la IA bloquean el merge. Usa `["BLOQUEAR"]` si al inicio prefieres que la IA solo bloquee lo grave |
| `si_falla_la_ia` | `revision_humana` | Qué hacer si la IA no responde: `revision_humana` (no bloquea, pide una persona) o `bloquear` |
| `omitir_ia_si_ya_esta_bloqueado` | `true` | Si un control automático ya bloquea, no se llama a la IA y no se gasta |
| `etiqueta_revision_humana` | `revision-humana-requerida` | Etiqueta que se pone cuando el PR necesita a una persona |
| `etiqueta_anulacion` | `revision-ia-anulada` | Etiqueta con la que una persona anula un bloqueo (falso positivo) |
| `roles_que_anulan` | `["admin", "maintain"]` | Roles que pueden anular con la etiqueta. El autor del PR nunca puede, aunque tenga uno de estos roles |

La configuración se valida al cargarla: un valor fuera de rango o desconocido (por ejemplo `"bloquear"` en minúscula en `ia_bloquea_en`) detiene la ejecución en vez de desactivar un control en silencio.

Siempre son de alto riesgo, sin importar la configuración: `.github/`, `.revisor/`, `.claude/`, `CODEOWNERS`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.gitattributes` y `.gitmodules`.

## Ajustar el criterio de revisión

- `.revisor/prompts/sistema.md`: el rol, el método, la escala de puntajes y cuándo bloquear. Agrega aquí las reglas de tu equipo (por ejemplo, "toda consulta SQL usa parámetros", "los endpoints nuevos requieren autorización explícita").
- `.revisor/prompts/tarea.md`: cómo se presenta cada PR a la IA. Cámbialo con cuidado: los marcadores `{{...}}` y los bloques `datos_no_confiables` son parte de la defensa contra prompt injection.
- `.revisor/esquema/veredicto.schema.json`: la forma obligatoria de la respuesta. Si lo cambias, ajusta `decidir.py`.

## Actualizar Claude Code

La versión del CLI está fijada en `.revisor/herramientas/package-lock.json`. Dependabot abre un PR semanal cuando hay una versión nueva, con 7 días de espera para no tomar versiones recién publicadas. Para actualizar a mano:

```bash
cd .revisor/herramientas
npm install @anthropic-ai/claude-code@VERSION --package-lock-only
```

## Ajustar los controles automáticos

Los patrones de tests desactivados, secretos, caracteres invisibles y frases dirigidas al revisor están en `.revisor/scripts/comun.py`. Si agregas un patrón, agrega su prueba en `.revisor/tests/test_revisor.py` y corre:

```bash
python3 -m unittest discover -s .revisor/tests -v
```

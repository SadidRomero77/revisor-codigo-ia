"""Etapa 2 · Revisar con IA (con la llave de la IA, sin permisos de escritura en GitHub).

Arma el prompt, ejecuta Claude Code en modo no interactivo con herramientas de solo lectura y
valida la respuesta contra el esquema. Siempre escribe veredicto.json, aunque la IA falle,
para que la etapa de decisión aplique la política de fallo configurada.

Uso:
  python3 -I revisar.py --revisor base/.revisor --repo pr --contexto contexto --salida veredicto \
                        [--claude ruta/al/binario]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C  # noqa: E402

# Variables de entorno que el proceso de Claude puede ver. Todo lo demás (GITHUB_TOKEN,
# ACTIONS_RUNTIME_TOKEN, ACTIONS_ID_TOKEN_REQUEST_TOKEN, etc.) se queda fuera.
ENTORNO_PERMITIDO = {
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ",
    "SYSTEMROOT", "SYSTEMDRIVE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "TEMP", "TMP",  # Windows
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_API_KEY",
}
ENTORNO_FIJO = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
}

# Archivos del repositorio que el modelo no debe leer aunque estén en el checkout.
LECTURAS_PROHIBIDAS = [
    "Read(**/.env)", "Read(**/.env.*)", "Read(**/*.pem)", "Read(**/*.key)", "Read(**/*.p12)",
    "Read(**/*.pfx)", "Read(**/id_rsa*)", "Read(**/id_ed25519*)", "Read(**/.npmrc)", "Read(**/.pypirc)",
    "Read(**/.netrc)", "Read(**/secrets/**)", "Read(**/.git/**)",
    "Grep(**/.env)", "Grep(**/.env.*)", "Grep(**/.git/**)",
]


def armar_prompt(plantilla: str, ctx: dict, diff: str) -> str:
    """Rellena la plantilla. Lo que viene del PR va dentro de un bloque con un identificador
    aleatorio, para que el autor del PR no pueda "cerrar" el bloque desde su texto."""
    marca = secrets.token_hex(8)
    pr = ctx["pr"]
    resumen_controles = json.dumps(
        {
            "riesgo": ctx["riesgo"],
            "controles": ctx["controles"],
            "comentarios_html_retirados_de_la_descripcion": pr["comentarios_html_retirados"],
            "rutas_excluidas_del_diff": ctx["rutas_excluidas_del_diff"],
            "diff_recortado": ctx["diff_recortado"],
        },
        ensure_ascii=False,
        indent=1,
    )
    archivos = "\n".join(f"- {f['estado']} {C.hacer_visibles(f['ruta'])} (+{f['agregadas']}/-{f['eliminadas']})" for f in ctx["archivos"][:200])
    valores = {
        "MARCA": marca,
        "NUMERO": str(pr["numero"]),
        "TITULO": pr["titulo"],
        "DESCRIPCION": pr["descripcion_visible"] or "(sin descripción)",
        "ARCHIVOS": archivos,
        "CONTROLES": resumen_controles,
        "DIFF": diff,
    }
    # Reemplazo en una sola pasada: el texto del PR nunca se vuelve a interpretar como plantilla.
    salida, i = [], 0
    while i < len(plantilla):
        if plantilla.startswith("{{", i):
            fin = plantilla.find("}}", i)
            clave = plantilla[i + 2:fin].strip() if fin != -1 else ""
            if clave in valores:
                salida.append(valores[clave])
                i = fin + 2
                continue
        salida.append(plantilla[i])
        i += 1
    return "".join(salida)


def enlaces_externos(repo: Path) -> list[Path]:
    """Enlaces simbólicos del checkout cuyo destino queda fuera del repositorio."""
    raiz = repo.resolve()
    hallados = []
    for actual, dirs, archivos in os.walk(raiz):
        dirs[:] = [d for d in dirs if d != ".git" or Path(actual) != raiz]
        for nombre in [*dirs, *archivos]:
            p = Path(actual, nombre)
            if p.is_symlink():
                try:
                    destino = p.resolve()
                except (OSError, RuntimeError):  # bucle de enlaces o destino inaccesible
                    hallados.append(p)
                    continue
                if destino != raiz and raiz not in destino.parents:
                    hallados.append(p)
    return hallados


def comando(claude: str, cfg: dict, esquema: str, sistema: Path, contexto_dir: Path, local: bool = False) -> list[str]:
    ajustes = {"permissions": {"deny": LECTURAS_PROHIBIDAS, "defaultMode": "dontAsk"}}
    # En CI: --bare no carga hooks, plugins, MCP, CLAUDE.md ni ajustes del repo del PR, y exige ANTHROPIC_API_KEY.
    # En local, --bare no permite usar la sesión de tu suscripción; --safe-mode desactiva igual CLAUDE.md,
    # hooks, skills, plugins y MCP de la rama revisada, y --restricted ignora sus archivos de ajustes.
    modo = ["--safe-mode"] if local else ["--bare"]
    return [
        claude,
        *modo,
        "--restricted",                 # sin herramientas que ejecuten código; archivos solo en los directorios de trabajo
        "-p",
        "--output-format", "json",
        "--json-schema", esquema,
        "--model", str(cfg["modelo"]),
        "--max-turns", str(int(cfg["max_turnos"])),
        "--max-budget-usd", str(float(cfg["presupuesto_usd"])),
        "--tools", "Read,Grep,Glob",    # solo lectura
        "--disallowedTools", "mcp__*", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "NotebookEdit",
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--no-session-persistence",
        "--settings", json.dumps(ajustes),
        "--add-dir", str(contexto_dir),
        "--append-system-prompt-file", str(sistema),
    ]


def ejecutar(cmd: list[str], prompt: str, cwd: Path, timeout: int, local: bool = False) -> tuple[dict | None, str]:
    entorno = {k: v for k, v in os.environ.items() if k.upper() in ENTORNO_PERMITIDO and v}
    entorno.update(ENTORNO_FIJO)
    if not local and not any(entorno.get(k) for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
        return None, "no hay credencial: falta el secreto ANTHROPIC_API_KEY"
    try:
        r = subprocess.run(cmd, input=prompt.encode("utf-8"), cwd=cwd, env=entorno, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"la revisión superó {timeout} s"
    except FileNotFoundError:
        return None, "no se encontró el ejecutable de Claude Code"
    texto = r.stdout.decode("utf-8", "replace").strip()
    for candidato in (texto, texto.splitlines()[-1] if texto else ""):
        try:
            dato = json.loads(candidato)
            if isinstance(dato, dict):
                return dato, ""
        except ValueError:
            pass
    else:
        error = C.redactar_secretos(r.stderr.decode("utf-8", "replace"))[-600:]
        return None, f"salida no es JSON (código {r.returncode}): {error or texto[:300]}"


def interpretar(resultado: dict | None, error: str, esquema: dict) -> dict:
    if resultado is None:
        return {"estado": "error", "motivo": error}
    base = {
        "costo_usd": resultado.get("total_cost_usd"),
        "turnos": resultado.get("num_turns"),
        "duracion_ms": resultado.get("duration_ms"),
    }
    if resultado.get("is_error") or resultado.get("subtype") != "success":
        if resultado.get("is_error") and resultado.get("result"):
            motivo = C.redactar_secretos(str(resultado["result"]))[:300]
        else:
            motivo = str(resultado.get("subtype") or "error")
        return {**base, "estado": "error", "motivo": motivo}
    veredicto = resultado.get("structured_output")
    if not veredicto:
        return {**base, "estado": "error", "motivo": "la IA terminó sin entregar el veredicto estructurado"}
    errores = C.validar(veredicto, esquema)
    if errores:
        return {**base, "estado": "error", "motivo": "el veredicto no cumple el esquema: " + "; ".join(errores[:5])}
    return {**base, "estado": "ok", "veredicto": veredicto}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revisor", required=True, help="carpeta .revisor de la rama base (confiable)")
    ap.add_argument("--repo", required=True, help="checkout del PR (solo se lee)")
    ap.add_argument("--contexto", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--claude", default=shutil.which("claude") or "claude")
    ap.add_argument("--local", action="store_true", help="ejecución en tu máquina (usa tu sesión de Claude Code)")
    a = ap.parse_args()

    revisor = Path(a.revisor).resolve()
    contexto_dir = Path(a.contexto).resolve()
    cfg = C.cargar_config(revisor / "config.json")
    ctx = C.leer_json(contexto_dir / "contexto.json")
    salida = Path(a.salida) / "veredicto.json"
    if not ctx:
        C.escribir_json(salida, {"estado": "error", "motivo": "no se encontró contexto.json"})
        return 0

    esquema_txt = (revisor / "esquema" / "veredicto.schema.json").read_text(encoding="utf-8")
    esquema = json.loads(esquema_txt)
    plantilla = (revisor / "prompts" / "tarea.md").read_text(encoding="utf-8")
    diff = (contexto_dir / "diff.patch").read_text(encoding="utf-8")
    prompt = armar_prompt(plantilla, ctx, diff)
    cmd = comando(a.claude, cfg, json.dumps(esquema), revisor / "prompts" / "sistema.md", contexto_dir, a.local)

    # Defensa en profundidad: aunque preparar ya omite la IA si el PR agrega enlaces simbólicos,
    # aquí no se deja ninguno que apunte fuera del repositorio.
    repo = Path(a.repo).resolve()
    externos = enlaces_externos(repo)
    if externos and a.local:
        C.escribir_json(salida, {"estado": "error", "motivo": f"hay {len(externos)} enlaces simbólicos que apuntan fuera del repositorio; la IA no se ejecuta"})
        print(f"::warning::Enlaces simbólicos fuera del repositorio: {', '.join(str(p.relative_to(repo)) for p in externos[:5])}")
        return 0
    for p in externos:  # en CI el checkout es desechable
        p.unlink()
        print(f"::warning::Se retiró un enlace simbólico que apuntaba fuera del repositorio: {p.relative_to(repo)}")

    inicio = time.time()
    resultado, error = ejecutar(cmd, prompt, repo, int(cfg["timeout_segundos"]), a.local)
    veredicto = interpretar(resultado, error, esquema)
    veredicto["modelo"] = cfg["modelo"]
    veredicto["segundos"] = round(time.time() - inicio, 1)
    C.escribir_json(salida, veredicto)

    if veredicto["estado"] == "ok":
        v = veredicto["veredicto"]
        print(f"Veredicto de la IA: {v['veredicto']} · {len(v['hallazgos'])} hallazgos · costo estimado US${veredicto.get('costo_usd') or 0:.2f}")
    else:
        print(f"::warning::La IA no entregó veredicto: {veredicto['motivo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

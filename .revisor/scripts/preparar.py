"""Etapa 1 · Preparar (sin IA, sin secretos).

Lee el PR como dato, calcula el diff con git y corre los controles deterministas:
tests desactivados o eliminados, contenido oculto, frases dirigidas al revisor, secretos y ruta de riesgo.
Deja todo en una carpeta de contexto que las otras dos etapas leen.

Uso:
  python3 -I preparar.py --repo pr --base-sha SHA --head-sha SHA --evento evento.json \
                         --config config.json --salida contexto
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C  # noqa: E402

SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def git(repo: str, *args: str) -> str:
    """Corre git sin hooks, sin drivers externos y sin pedir credenciales."""
    cmd = [
        "git", "-C", repo,
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.quotePath=false",
        "-c", "diff.external=",
        "-c", "core.fsmonitor=false",
        *args,
    ]
    entorno = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "HOME", "SYSTEMROOT", "SYSTEMDRIVE", "USERPROFILE", "TEMP", "TMP")}
    entorno.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1"})
    r = subprocess.run(cmd, capture_output=True, env=entorno, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} falló: {r.stderr.decode('utf-8', 'replace')[:500]}")
    return r.stdout.decode("utf-8", "replace")


def datos_del_evento(evento: dict) -> dict:
    pr = evento.get("pull_request") or {}
    cabeza = (pr.get("head") or {}).get("repo") or {}
    base = (pr.get("base") or {}).get("repo") or {}
    return {
        "numero": int(pr.get("number") or 0),
        "titulo": pr.get("title") or "",
        "cuerpo": pr.get("body") or "",
        "autor": (pr.get("user") or {}).get("login", ""),
        "asociacion_autor": pr.get("author_association", ""),
        "es_fork": bool(cabeza.get("full_name")) and cabeza.get("full_name") != base.get("full_name"),
        "borrador": bool(pr.get("draft")),
        "etiquetas": [e.get("name", "") for e in pr.get("labels") or []],
    }


def controles(archivos: list[dict], textos_pr: dict) -> dict:
    tests_desactivados, tests_eliminados, aserciones = [], [], []
    invisibles, manipulacion, secretos = [], [], []
    enlaces, ilegibles = [], []
    eliminados_por_nombre: dict[str, str] = {}
    nombres_agregados: set[str] = set()

    for a in archivos:
        ruta = a["ruta"]
        if a.get("ilegible"):
            ilegibles.append(C.recortar(C.hacer_visibles(ruta), 200))
        if a.get("enlace") and a["estado"] != "D":
            enlaces.append(ruta)
        for inv in C.buscar_invisibles(ruta):
            invisibles.append({"donde": "nombre de archivo", "linea": 0, "codigo": inv["codigo"], "tipo": inv["tipo"]})
        propio = ruta.startswith(".revisor/")  # los patrones y fixtures del revisor no son tests del proyecto
        es_test = C.es_archivo_de_test(ruta)
        if not propio:
            for n, linea in a["agregadas"]:
                if (lenguaje := C.desactiva_test(linea, es_test)):
                    tests_desactivados.append({"archivo": ruta, "linea": n, "lenguaje": lenguaje, "codigo": C.recortar(linea.strip(), 160)})
        if es_test:
            for l in a["eliminadas"]:
                if (nombre := C.nombre_de_test(l)):
                    eliminados_por_nombre.setdefault(nombre, ruta)
            nombres_agregados.update(n for n in (C.nombre_de_test(l) for _, l in a["agregadas"]) if n)
            if a["estado"] == "D" and not any(C.nombre_de_test(l) for l in a["eliminadas"]):
                tests_eliminados.append({"archivo": ruta, "test": "(archivo de tests eliminado)"})
            quitadas = sum(C.es_asercion(l) for l in a["eliminadas"])
            puestas = sum(C.es_asercion(l) for _, l in a["agregadas"])
            if quitadas > puestas:
                aserciones.append({"archivo": ruta, "eliminadas": quitadas, "agregadas": puestas})
        texto_agregado = "\n".join(l for _, l in a["agregadas"])
        for inv in C.buscar_invisibles(texto_agregado):
            n = a["agregadas"][inv["linea"] - 1][0] if inv["linea"] - 1 < len(a["agregadas"]) else 0
            invisibles.append({"donde": ruta, "linea": n, "codigo": inv["codigo"], "tipo": inv["tipo"]})
        if not propio and not es_test:
            for frase in C.buscar_manipulacion(texto_agregado, en_codigo=True):
                manipulacion.append({"donde": ruta, "texto": frase})
        for nombre in C.buscar_secretos(texto_agregado):
            secretos.append({"archivo": ruta, "tipo": nombre})

    # Un test que se mueve de archivo o se reescribe con el mismo nombre no cuenta como eliminado.
    for nombre, ruta in sorted(eliminados_por_nombre.items()):
        if nombre not in nombres_agregados:
            tests_eliminados.append({"archivo": ruta, "test": nombre})

    for donde, texto in textos_pr.items():
        for inv in C.buscar_invisibles(texto):
            invisibles.append({"donde": donde, "linea": inv["linea"], "codigo": inv["codigo"], "tipo": inv["tipo"]})
        for frase in C.buscar_manipulacion(texto):
            manipulacion.append({"donde": donde, "texto": frase})
        for nombre in C.buscar_secretos(texto):
            secretos.append({"archivo": donde, "tipo": nombre})

    return {
        "tests_desactivados": tests_desactivados,
        "tests_eliminados": tests_eliminados,
        "aserciones_reducidas": aserciones,
        "caracteres_invisibles": _resumir_invisibles(invisibles),
        "intentos_de_manipulacion": manipulacion,
        "secretos": secretos,
        # Un enlace simbólico puede apuntar fuera del repositorio (p. ej. /proc/self/environ) y
        # hacer que la herramienta Read de la IA lea secretos del runner.
        "enlaces_simbolicos": enlaces,
        # Cabeceras de diff que no se pudieron leer: no se sabe con certeza qué archivo cambió.
        "rutas_ilegibles": ilegibles,
    }


def _resumir_invisibles(lista: list[dict]) -> list[dict]:
    """Agrupa por lugar y tipo para no producir cientos de entradas."""
    grupos: dict[tuple, dict] = {}
    for i in lista:
        clave = (i["donde"], i["tipo"])
        g = grupos.setdefault(clave, {"donde": i["donde"], "tipo": i["tipo"], "cantidad": 0, "primera_linea": i["linea"], "codigos": []})
        g["cantidad"] += 1
        if i["codigo"] not in g["codigos"] and len(g["codigos"]) < 5:
            g["codigos"].append(i["codigo"])
    return list(grupos.values())


def riesgo(rutas: list[str], cfg: dict, ilegibles: list[str] | None = None) -> dict:
    motivos = []
    if ilegibles:
        motivos.append(f"el diff tiene rutas que no se pudieron leer: {ilegibles[0]}")
    for patron in C.RUTAS_SIEMPRE_ALTO_RIESGO:
        hit = next((r for r in rutas if re.search(patron, r)), None)
        if hit:
            motivos.append(f"toca configuración de CI, del revisor o de agentes: {hit}")
    for patron in cfg["rutas_alto_riesgo"]:
        hit = next((r for r in rutas if re.search(patron, r, re.I)), None)
        if hit:
            motivos.append(f"ruta de alto riesgo ({patron}): {hit}")
    return {"nivel": "ALTO" if motivos else "BAJO", "motivos": motivos[:10]}


def diff_para_el_modelo(diff: str, cfg: dict) -> tuple[str, list[str], bool]:
    """Excluye rutas configuradas, hace visibles los invisibles, redacta secretos y recorta por tamaño."""
    excluidas, partes = [], []
    for ruta, bloque in C.separar_diff_por_archivo(diff):
        if any(re.search(p, ruta) for p in cfg["rutas_excluidas_del_diff"]):
            excluidas.append(ruta)
            continue
        partes.append(bloque)
    texto = C.redactar_secretos(C.hacer_visibles("".join(partes)))
    limite = int(cfg["max_diff_kb"]) * 1024
    recortado = len(texto.encode("utf-8")) > limite
    if recortado:
        texto = texto.encode("utf-8")[:limite].decode("utf-8", "ignore") + "\n\n[DIFF RECORTADO: supera el límite configurado]\n"
    return texto, excluidas, recortado


def bloquea_sin_ia(ctrl: dict) -> list[str]:
    motivos = []
    if ctrl["tests_desactivados"]:
        motivos.append("se desactivaron o enfocaron tests")
    if ctrl["secretos"]:
        motivos.append("hay algo con forma de secreto en el cambio")
    return motivos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="checkout del PR (solo se lee)")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", default="HEAD", help="por defecto, el commit del checkout del PR")
    ap.add_argument("--evento", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--salida", required=True)
    a = ap.parse_args()

    if a.head_sha == "HEAD" and Path(a.repo, ".git").exists():
        a.head_sha = git(a.repo, "rev-parse", "HEAD").strip()
    if not (SHA.match(a.base_sha) and SHA.match(a.head_sha)):
        print("::error::base-sha o head-sha no tienen formato de SHA")
        return 2
    cfg = C.cargar_config(a.config)
    pr = datos_del_evento(C.leer_json(a.evento, {}))

    base_comun = git(a.repo, "merge-base", a.base_sha, a.head_sha).strip()
    diff = git(a.repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-renames", "-U3", f"{base_comun}..{a.head_sha}")
    archivos = C.leer_diff(diff)
    rutas = [f["ruta"] for f in archivos]

    cuerpo_visible, comentarios = C.separar_comentarios_html(pr["cuerpo"])
    textos_pr = {"título del PR": pr["titulo"], "descripción del PR": pr["cuerpo"]}
    ctrl = controles(archivos, textos_pr)
    rsg = riesgo(rutas, cfg, ctrl["rutas_ilegibles"])
    diff_modelo, excluidas, recortado = diff_para_el_modelo(diff, cfg)

    motivos_bloqueo = bloquea_sin_ia(ctrl)
    omitir, motivo_omitir = False, ""
    if pr["es_fork"]:
        omitir, motivo_omitir = True, "PR desde un fork: GitHub no entrega secretos a estas ejecuciones"
    elif not archivos:
        omitir, motivo_omitir = True, "el PR no tiene cambios"
    elif ctrl["enlaces_simbolicos"]:
        omitir, motivo_omitir = True, "el PR agrega o cambia enlaces simbólicos; la IA no lee un checkout que puede apuntar fuera del repositorio"
    elif motivos_bloqueo and cfg["omitir_ia_si_ya_esta_bloqueado"]:
        omitir, motivo_omitir = True, "los controles automáticos ya bloquean el PR; no se gasta en la IA"

    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "diff.patch").write_text(diff_modelo, encoding="utf-8")
    contexto = {
        "version": 1,
        "pr": {
            "numero": pr["numero"],
            "autor": pr["autor"],
            "asociacion_autor": pr["asociacion_autor"],
            "es_fork": pr["es_fork"],
            "titulo": C.redactar_secretos(C.hacer_visibles(pr["titulo"]))[:300],
            "descripcion_visible": C.redactar_secretos(C.hacer_visibles(cuerpo_visible))[:20000],
            "comentarios_html_retirados": len(comentarios),
            "base_sha": a.base_sha,
            "head_sha": a.head_sha,
        },
        "archivos": [{"ruta": C.hacer_visibles(f["ruta"]), "estado": f["estado"], "agregadas": len(f["agregadas"]), "eliminadas": len(f["eliminadas"])} for f in archivos][:500],
        "total_archivos": len(archivos),
        "rutas_excluidas_del_diff": excluidas[:100],
        "diff_recortado": recortado,
        "controles": ctrl,
        "bloqueo_sin_ia": motivos_bloqueo,
        "riesgo": rsg,
        "omitir_ia": omitir,
        "motivo_omitir_ia": motivo_omitir,
    }
    C.escribir_json(salida / "contexto.json", contexto)
    C.salida_github("omitir_ia", "true" if omitir else "false")

    print(f"Archivos: {len(archivos)} · riesgo {rsg['nivel']} · bloqueo sin IA: {', '.join(motivos_bloqueo) or 'no'}")
    if omitir:
        print(f"La IA no se ejecutará: {motivo_omitir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

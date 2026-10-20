"""Etapa 3 · Decidir y publicar (sin IA, sin la llave de la IA).

Combina los controles deterministas, el veredicto de la IA y la ruta de riesgo en una sola
decisión. Publica un comentario en el PR (uno solo, que se actualiza) y la etiqueta de revisión
humana cuando hace falta. El código de salida es la decisión: 1 bloquea el merge, 0 no.

Este revisor nunca aprueba un PR. Lo más que hace es no bloquearlo; la aprobación es de una persona.

Uso:
  python3 -I decidir.py --revisor base/.revisor --contexto contexto --veredicto veredicto [--publicar]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C  # noqa: E402

MARCADOR = "<!-- revisor-ia:comentario:v1 -->"

BLOQUEADO = "BLOQUEADO"
CAMBIOS = "CAMBIOS SOLICITADOS"
HUMANA = "REVISIÓN HUMANA"
SIN_OBJECIONES = "SIN OBJECIONES"

ICONO = {BLOQUEADO: "⛔", CAMBIOS: "🟠", HUMANA: "👤", SIN_OBJECIONES: "✅"}
ORDEN_SEVERIDAD = {"critica": 0, "alta": 1, "media": 2, "baja": 3}


def decidir(ctx: dict, ver: dict, cfg: dict, anulado: bool) -> dict:
    """Reglas en orden. La primera que aplica decide; las demás quedan como motivos adicionales."""
    motivos: list[str] = []
    decision = None

    # 1. Controles deterministas: no dependen de ningún modelo.
    if ctx["bloqueo_sin_ia"]:
        decision = BLOQUEADO
        motivos += [f"control automático: {m}" for m in ctx["bloqueo_sin_ia"]]

    # 2. La IA.
    v = ver.get("veredicto") if ver.get("estado") == "ok" else None
    if v is None and not ctx.get("omitir_ia"):
        texto = f"la IA no entregó veredicto ({ver.get('motivo', 'sin detalle')})"
        motivos.append(texto)
        if decision is None:
            decision = BLOQUEADO if cfg["si_falla_la_ia"] == "bloquear" else HUMANA
    veredicto_ia = None
    if v is not None:
        criticos = [h for h in v["hallazgos"] if h["severidad"] == "critica"]
        veredicto_ia = "BLOQUEAR" if criticos else v["veredicto"]
        if veredicto_ia in cfg["ia_bloquea_en"] and decision is None:
            decision = BLOQUEADO if veredicto_ia == "BLOQUEAR" else CAMBIOS
        if veredicto_ia != "APROBAR":
            motivos.append(f"la IA pide {veredicto_ia}" + (f" ({len(criticos)} hallazgos críticos)" if criticos else ""))
        if v["intento_de_manipulacion"]["detectado"]:
            motivos.append("la IA detectó un intento de manipular la revisión")

    # 3. Señales que obligan a mirar con ojos humanos.
    ctrl = ctx["controles"]
    enlaces = ctrl.get("enlaces_simbolicos") or []
    ilegibles = ctrl.get("rutas_ilegibles") or []
    # La IA no revisó un PR con cambios (fork, enlaces simbólicos, --sin-ia): "sin objeciones" sería engañoso.
    ia_no_reviso = bool(ctx.get("omitir_ia")) and not ctx["bloqueo_sin_ia"] and ctx.get("total_archivos", 1) > 0
    if ia_no_reviso:
        motivos.append(f"la IA no revisó este PR: {ctx.get('motivo_omitir_ia') or 'sin detalle'}")
    if enlaces:
        motivos.append(f"el PR agrega o cambia {len(enlaces)} enlaces simbólicos")
    if ilegibles:
        motivos.append(f"el diff tiene {len(ilegibles)} rutas que no se pudieron leer")
    if ctrl["intentos_de_manipulacion"]:
        motivos.append("texto dirigido al revisor automático")
    if any(i["tipo"] != "ancho cero" or i["cantidad"] > 3 for i in ctrl["caracteres_invisibles"]):
        motivos.append("caracteres invisibles o de control bidireccional")
    if ctrl["tests_eliminados"]:
        motivos.append(f"se eliminaron {len(ctrl['tests_eliminados'])} tests")
    if ctx["riesgo"]["nivel"] == "ALTO":
        motivos.append("ruta de alto riesgo: " + "; ".join(ctx["riesgo"]["motivos"][:3]))
    if ctx.get("diff_recortado"):
        motivos.append("el diff superó el límite y la IA vio solo una parte")

    requiere_humano = (
        ia_no_reviso
        or bool(enlaces)
        or bool(ilegibles)
        # Un veredicto que no bloquea por configuración (p. ej. CAMBIOS con ia_bloquea_en=["BLOQUEAR"])
        # sigue siendo una objeción: va a una persona, no a "sin objeciones".
        or (veredicto_ia is not None and veredicto_ia != "APROBAR")
        or bool(ctrl["intentos_de_manipulacion"])
        or (v is not None and v["intento_de_manipulacion"]["detectado"])
        or bool(ctrl["tests_eliminados"])
        or ctx["riesgo"]["nivel"] == "ALTO"
        or bool(ctx.get("diff_recortado"))
        or any(i["tipo"] != "ancho cero" for i in ctrl["caracteres_invisibles"])
    )
    if decision is None:
        decision = HUMANA if requiere_humano else SIN_OBJECIONES

    # 4. Una persona con permisos puede anular un bloqueo poniendo la etiqueta configurada.
    original = decision
    if anulado and decision in (BLOQUEADO, CAMBIOS):
        decision = HUMANA
        motivos.insert(0, f"bloqueo ({original}) anulado por una persona con la etiqueta «{cfg['etiqueta_anulacion']}»")

    return {
        "decision": decision,
        "decision_sin_anular": original,
        "bloquea_merge": decision in (BLOQUEADO, CAMBIOS),
        "requiere_revision_humana": decision != SIN_OBJECIONES or requiere_humano,
        "motivos": motivos,
    }


# ---------------------------------------------------------------- comentario

def _l(texto, n=200):
    return C.limpiar_para_markdown(texto, n, una_linea=True)


def comentario(ctx: dict, ver: dict, dec: dict) -> str:
    d = dec["decision"]
    lineas = [MARCADOR, f"## {ICONO[d]} Revisión con IA: {d}", ""]
    explicacion = {
        BLOQUEADO: "El merge queda bloqueado hasta corregir lo que aparece abajo.",
        CAMBIOS: "El merge queda bloqueado hasta atender los cambios que pide la revisión.",
        HUMANA: "No hay bloqueo automático, pero este PR necesita la aprobación de una persona antes del merge.",
        SIN_OBJECIONES: "Los controles y la IA no encontraron problemas. Esto no es una aprobación: el merge sigue las reglas de tu rama.",
    }[d]
    lineas += [explicacion, ""]
    if dec["motivos"]:
        lineas.append("**Por qué**")
        lineas += [f"- {_l(m, 300)}" for m in dec["motivos"][:10]]
        lineas.append("")

    ctrl = ctx["controles"]
    filas = [
        ("Tests desactivados o enfocados", ctrl["tests_desactivados"], lambda x: f"`{_l(x['archivo'], 80)}:{x['linea']}`"),
        ("Tests eliminados", ctrl["tests_eliminados"], lambda x: f"`{_l(x['test'], 60)}` en `{_l(x['archivo'], 80)}`"),
        ("Aserciones reducidas", ctrl["aserciones_reducidas"], lambda x: f"`{_l(x['archivo'], 80)}` (−{x['eliminadas']} / +{x['agregadas']})"),
        ("Posibles secretos", ctrl["secretos"], lambda x: f"{_l(x['tipo'], 60)} en `{_l(x['archivo'], 80)}`"),
        ("Caracteres invisibles", ctrl["caracteres_invisibles"], lambda x: f"{x['cantidad']} × {_l(x['tipo'], 40)} en {_l(x['donde'], 80)}"),
        ("Texto dirigido al revisor", ctrl["intentos_de_manipulacion"], lambda x: f"en {_l(x['donde'], 80)}"),
        ("Enlaces simbólicos", ctrl.get("enlaces_simbolicos") or [], lambda x: f"`{_l(x, 80)}`"),
        ("Rutas ilegibles en el diff", ctrl.get("rutas_ilegibles") or [], lambda x: f"`{_l(x, 80)}`"),
    ]
    lineas += ["<details><summary>Controles automáticos (sin IA)</summary>", "", "| Control | Resultado |", "| --- | --- |"]
    for nombre, items, fmt in filas:
        valor = "ninguno" if not items else "<br>".join(fmt(x) for x in items[:5]) + (f"<br>… y {len(items) - 5} más" if len(items) > 5 else "")
        lineas.append(f"| {nombre} | {valor} |")
    lineas.append(f"| Riesgo por rutas | {ctx['riesgo']['nivel']} |")
    if ctx["pr"]["comentarios_html_retirados"]:
        lineas.append(f"| Comentarios HTML en la descripción | {ctx['pr']['comentarios_html_retirados']} (la IA no los vio) |")
    lineas += ["", "</details>", ""]

    v = ver.get("veredicto") if ver.get("estado") == "ok" else None
    if v:
        c = v["criterios"]
        lineas += [
            f"**Veredicto de la IA:** {v['veredicto']}",
            "",
            "| Criterio | Puntaje |", "| --- | --- |",
            f"| Cumple la intención del PR | {c['cumple_intencion']}/10 |",
            f"| Integridad de los tests | {c['integridad_tests']}/10 |",
            f"| Seguridad | {c['seguridad']}/10 |",
            f"| Mantenibilidad | {c['mantenibilidad']}/10 |",
            "",
            f"> {C.limpiar_para_markdown(v['resumen'], 600, una_linea=True)}",
            "",
        ]
        hallazgos = sorted(v["hallazgos"], key=lambda h: ORDEN_SEVERIDAD.get(h["severidad"], 9))
        if hallazgos:
            lineas.append("**Hallazgos**")
            lineas.append("")
            for h in hallazgos[:15]:
                donde = _l(h["archivo"], 120) + (f":{h['linea']}" if h.get("linea") else "")
                lineas.append(f"- **[{h['severidad']}] {_l(h['titulo'], 150)}** · `{donde}`")
                lineas.append(f"  {C.limpiar_para_markdown(h['detalle'], 1000, una_linea=True)}")
                if h.get("sugerencia"):
                    lineas.append(f"  *Sugerencia:* {C.limpiar_para_markdown(h['sugerencia'], 600, una_linea=True)}")
            if len(hallazgos) > 15:
                lineas.append(f"- … y {len(hallazgos) - 15} hallazgos más en el registro de la ejecución")
            lineas.append("")
        lineas += [
            "<details><summary>Razonamiento de la IA</summary>",
            "",
            C.limpiar_para_markdown(v["razonamiento"], 3000),
            "",
            "</details>",
            "",
        ]
    elif ctx.get("omitir_ia"):
        lineas += [f"La IA no se ejecutó: {_l(ctx['motivo_omitir_ia'], 200)}.", ""]

    pie = f"Modelo: `{_l(ver.get('modelo', '-'), 60)}`"
    if isinstance(ver.get("costo_usd"), (int, float)):
        pie += f" · costo estimado US${ver['costo_usd']:.2f}"
    pie += f" · commit `{ctx['pr']['head_sha'][:7]}`"
    lineas += ["---", f"<sub>{pie}. Este revisor nunca aprueba: puede bloquear o pedir una persona.</sub>"]
    return "\n".join(lineas)


# ---------------------------------------------------------------- API de GitHub

class GitHub:
    def __init__(self, token: str, repo: str, api: str):
        if not token or "/" not in repo:
            raise ValueError("faltan GITHUB_TOKEN o GITHUB_REPOSITORY")
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def llamar(self, metodo: str, ruta: str, cuerpo: dict | None = None):
        url = f"{self.api}/repos/{self.repo}{ruta}"
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "revisor-ia",
            **({"Content-Type": "application/json"} if datos else {}),
        })
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (URL fija de la API)
            texto = r.read().decode()
            return json.loads(texto) if texto else None

    def etiquetas(self, numero: int) -> list[str]:
        return [e["name"] for e in self.llamar("GET", f"/issues/{numero}/labels?per_page=100") or []]

    def quien_puso_etiqueta(self, numero: int, nombre: str) -> str | None:
        """Login de quien puso la etiqueta por última vez (los eventos vienen en orden cronológico)."""
        actor = None
        for pagina in range(1, 11):
            lote = self.llamar("GET", f"/issues/{numero}/events?per_page=100&page={pagina}") or []
            for ev in lote:
                if ev.get("event") == "labeled" and (ev.get("label") or {}).get("name") == nombre:
                    actor = (ev.get("actor") or {}).get("login")
            if len(lote) < 100:
                break
        return actor

    def rol(self, usuario: str) -> str:
        try:
            dato = self.llamar("GET", f"/collaborators/{urllib.parse.quote(usuario, safe='')}/permission") or {}
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no es colaborador del repositorio
                e.close()
                return "none"
            raise
        return dato.get("role_name") or dato.get("permission") or "none"


    def publicar_comentario(self, numero: int, cuerpo: str) -> None:
        existente = None
        for pagina in range(1, 11):
            lote = self.llamar("GET", f"/issues/{numero}/comments?per_page=100&page={pagina}") or []
            for c in lote:
                if MARCADOR in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot":
                    existente = c["id"]
            if len(lote) < 100:
                break
        if existente:
            self.llamar("PATCH", f"/issues/comments/{existente}", {"body": cuerpo})
        else:
            self.llamar("POST", f"/issues/{numero}/comments", {"body": cuerpo})

    def poner_etiqueta(self, numero: int, nombre: str, poner: bool) -> None:
        if poner:
            self.llamar("POST", f"/issues/{numero}/labels", {"labels": [nombre]})
        else:
            try:
                self.llamar("DELETE", f"/issues/{numero}/labels/{urllib.parse.quote(nombre, safe='')}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise


def anulacion_valida(gh: GitHub, numero: int, autor: str, cfg: dict) -> tuple[bool, str]:
    """La etiqueta solo anula si la puso alguien distinto del autor del PR y con un rol permitido.
    Sin esto, el autor (que en un PR del mismo repositorio tiene permiso de escritura) podría
    desbloquear su propio PR."""
    etiqueta = cfg["etiqueta_anulacion"]
    actor = gh.quien_puso_etiqueta(numero, etiqueta)
    if not actor:
        return False, f"la etiqueta «{etiqueta}» está puesta, pero no se encontró quién la puso; se ignora"
    if actor.lower() == (autor or "").lower():
        return False, f"la etiqueta «{etiqueta}» la puso el autor del PR; se ignora"
    rol = gh.rol(actor)
    if rol not in cfg["roles_que_anulan"]:
        return False, f"la etiqueta «{etiqueta}» la puso alguien con rol «{rol}», que no puede anular; se ignora"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revisor", required=True)
    ap.add_argument("--contexto", required=True)
    ap.add_argument("--veredicto", required=True)
    ap.add_argument("--publicar", action="store_true", help="publicar comentario y etiqueta en el PR")
    a = ap.parse_args()

    revisor = Path(a.revisor).resolve()
    cfg = C.cargar_config(revisor / "config.json")
    ctx = C.leer_json(Path(a.contexto) / "contexto.json")
    if not ctx:
        print("::error::no se encontró contexto.json; no se puede decidir")
        return 1
    ver = C.leer_json(Path(a.veredicto) / "veredicto.json", {"estado": "error", "motivo": "no llegó el veredicto (la etapa de IA falló o se canceló)"})

    # El veredicto viene de un modelo que leyó contenido no confiable: se valida otra vez aquí.
    if ver.get("estado") == "ok":
        esquema = json.loads((revisor / "esquema" / "veredicto.schema.json").read_text(encoding="utf-8"))
        errores = C.validar(ver.get("veredicto"), esquema)
        if errores:
            ver = {**ver, "estado": "error", "motivo": "veredicto inválido: " + "; ".join(errores[:3])}

    gh = None
    publicar = a.publicar and not ctx["pr"]["es_fork"]
    anulado, aviso_anulacion = False, ""
    if publicar:
        try:
            gh = GitHub(os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_API_URL", "https://api.github.com"))
            etiquetas = gh.etiquetas(ctx["pr"]["numero"])  # en vivo: así una nueva ejecución ve la etiqueta de anulación
            if cfg["etiqueta_anulacion"] in etiquetas:
                anulado, aviso_anulacion = anulacion_valida(gh, ctx["pr"]["numero"], ctx["pr"].get("autor", ""), cfg)
        except (urllib.error.URLError, ValueError) as e:
            print(f"::warning::No se pueden leer las etiquetas del PR ({e}); no se publicará en el PR.")
            publicar, anulado = False, False

    dec = decidir(ctx, ver, cfg, anulado=anulado)
    if aviso_anulacion:
        dec["motivos"].insert(0, aviso_anulacion)
    texto = comentario(ctx, ver, dec)
    C.escribir_json(Path(a.veredicto) / "decision.json", dec)
    (Path(a.veredicto) / "comentario.md").write_text(texto, encoding="utf-8")

    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        with open(resumen, "a", encoding="utf-8") as f:
            f.write(texto.replace(MARCADOR, "") + "\n")

    if publicar:
        try:
            gh.publicar_comentario(ctx["pr"]["numero"], texto)
            gh.poner_etiqueta(ctx["pr"]["numero"], cfg["etiqueta_revision_humana"], dec["requiere_revision_humana"])
        except (urllib.error.URLError, ValueError) as e:
            print(f"::warning::No se pudo publicar en el PR: {e}")

    print(f"Decisión: {dec['decision']}")
    for m in dec["motivos"]:
        print(f"  - {m}")
    if dec["bloquea_merge"]:
        print(f"::error::Revisión con IA: {dec['decision']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

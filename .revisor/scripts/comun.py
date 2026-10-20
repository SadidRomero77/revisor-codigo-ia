"""Piezas compartidas del revisor: configuración, patrones, limpieza de texto y validación.

Solo usa la biblioteca estándar de Python (3.10+). No instala nada.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------- configuración

CONFIG_POR_DEFECTO = {
    "modelo": "claude-sonnet-5-5",
    "max_turnos": 25,
    "presupuesto_usd": 2.0,
    "timeout_segundos": 600,
    "max_diff_kb": 300,
    "rutas_alto_riesgo": [],
    "rutas_excluidas_del_diff": [],
    "ia_bloquea_en": ["BLOQUEAR", "CAMBIOS"],
    "si_falla_la_ia": "revision_humana",
    "omitir_ia_si_ya_esta_bloqueado": True,
    "etiqueta_revision_humana": "revision-humana-requerida",
    "etiqueta_anulacion": "revision-ia-anulada",
    "roles_que_anulan": ["admin", "maintain"],
}

VALORES_FALLO = {"revision_humana", "bloquear"}
VEREDICTOS_QUE_BLOQUEAN = {"BLOQUEAR", "CAMBIOS"}
ROLES_GITHUB = {"admin", "maintain", "write", "triage"}


def cargar_config(ruta: str | Path) -> dict:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    cfg = {**CONFIG_POR_DEFECTO, **{k: v for k, v in datos.items() if not k.startswith("_")}}
    if cfg["si_falla_la_ia"] not in VALORES_FALLO:
        raise ValueError(f"si_falla_la_ia debe ser uno de {sorted(VALORES_FALLO)}")
    for clave in ("rutas_alto_riesgo", "rutas_excluidas_del_diff"):
        for patron in cfg[clave]:
            re.compile(patron)  # falla temprano si un patrón está mal escrito
    if not (1 <= int(cfg["max_turnos"]) <= 100):
        raise ValueError("max_turnos debe estar entre 1 y 100")
    if not (0 < float(cfg["presupuesto_usd"]) <= 50):
        raise ValueError("presupuesto_usd debe estar entre 0 y 50")
    if not (30 <= int(cfg["timeout_segundos"]) <= 3600):
        raise ValueError("timeout_segundos debe estar entre 30 y 3600")
    if not (1 <= int(cfg["max_diff_kb"]) <= 5000):
        raise ValueError("max_diff_kb debe estar entre 1 y 5000")
    # Un error de escritura aquí (p. ej. "bloquear" en minúscula) haría que la IA nunca bloquee sin avisar.
    if not isinstance(cfg["ia_bloquea_en"], list) or not set(cfg["ia_bloquea_en"]) <= VEREDICTOS_QUE_BLOQUEAN:
        raise ValueError(f"ia_bloquea_en solo acepta valores de {sorted(VEREDICTOS_QUE_BLOQUEAN)}")
    if not isinstance(cfg["roles_que_anulan"], list) or not set(cfg["roles_que_anulan"]) <= ROLES_GITHUB:
        raise ValueError(f"roles_que_anulan solo acepta valores de {sorted(ROLES_GITHUB)}")
    return cfg


# Rutas que siempre son de alto riesgo: controlan el CI, el propio revisor o la configuración de agentes.
RUTAS_SIEMPRE_ALTO_RIESGO = [
    r"^\.github/",
    r"^\.revisor/",
    r"(^|/)CODEOWNERS$",
    r"^\.claude/",
    r"(^|/)CLAUDE(\.local)?\.md$",
    r"(^|/)AGENTS\.md$",
    r"^\.mcp\.json$",
    r"^\.gitattributes$",
    r"^\.gitmodules$",
]

# ---------------------------------------------------------------- caracteres invisibles

# Caracteres que no se ven en pantalla pero el modelo sí lee (o que reordenan el texto, como en Trojan Source).
_INVISIBLES = {
    **{cp: "ancho cero" for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x180E, 0x00AD, 0x034F)},
    **{cp: "control bidireccional" for cp in (0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A))},
    # Rellenos que se ven como espacio vacío y sirven para identificadores invisibles.
    **{cp: "relleno invisible" for cp in (0x115F, 0x1160, 0x3164, 0xFFA0, 0x17B4, 0x17B5)},
    # Separadores de línea que algunos editores muestran como salto y otros no.
    **{cp: "separador de línea Unicode" for cp in (0x2028, 0x2029, 0x0085)},
}


def _tipo_invisible(cp: int) -> str | None:
    if cp in _INVISIBLES:
        return _INVISIBLES[cp]
    if 0xE0000 <= cp <= 0xE007F:
        return "etiqueta Unicode (ASCII smuggling)"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return None  # selectores de variación: comunes en emojis, no los reportamos
    return None


def buscar_invisibles(texto: str) -> list[dict]:
    """Devuelve cada carácter invisible con su posición (línea empieza en 1)."""
    hallados = []
    for n, linea in enumerate(lineas(texto), start=1):
        for ch in linea:
            tipo = _tipo_invisible(ord(ch))
            if tipo:
                hallados.append({"linea": n, "codigo": f"U+{ord(ch):04X}", "tipo": tipo})
    return hallados


def hacer_visibles(texto: str) -> str:
    """Reemplaza cada carácter invisible por una marca legible, p. ej. ⟦U+200B⟧."""
    return "".join(f"⟦U+{ord(c):04X}⟧" if _tipo_invisible(ord(c)) else c for c in texto)


def quitar_invisibles(texto: str) -> str:
    return "".join(c for c in texto if not _tipo_invisible(ord(c)))


# ---------------------------------------------------------------- comentarios HTML y manipulación

_COMENTARIO_HTML = re.compile(r"<!--(.*?)(-->|$)", re.S)


def separar_comentarios_html(texto: str) -> tuple[str, list[str]]:
    """Separa lo que una persona ve en GitHub de los comentarios HTML que no se muestran."""
    comentarios = [m.group(1).strip() for m in _COMENTARIO_HTML.finditer(texto)]
    visible = _COMENTARIO_HTML.sub("", texto)
    return visible, comentarios


# Frases dirigidas al revisor automático. No buscan ser exhaustivas: son una señal más, no la defensa.
_MANIPULACION = [
    r"\b(ignor[ae]|olvid[ae]|disregard|forget)\b.{0,40}\b(instrucci|instruction|previous|anterior|above|reglas|rules)",
    r"\b(revisor|reviewer|juez|judge|evaluador|llm|ia|ai|assistant|asistente|claude|copilot|gpt|codex)\b.{0,60}\b(aprueba|aprobar|approve|lgtm|10\s*/\s*10|puntaje|score)",
    r"\b(responde|responda|respond|reply|answer|output)\b.{0,30}\b(aprobar|approve|approved|10\s*/\s*10|lgtm)\b",
    r"\b(system\s*prompt|prompt\s*del\s*sistema|developer\s*message)\b",
    r"\b(ya\s+fue\s+(revisado|aprobado)|already\s+(reviewed|approved))\b.{0,40}\b(seguridad|security|equipo|team)",
    r"</?\s*(system|instrucciones|instructions|datos_no_confiables)\b",
]
_MANIPULACION_RE = [re.compile(p, re.I | re.S) for p in _MANIPULACION]


# En código fuente se omiten los patrones que chocan con proyectos que trabajan con LLM
# (es normal hablar de "system prompt" o de "AI score" en un repo de IA).
_SOLO_EN_TEXTO_DEL_PR = {1, 3}


def buscar_manipulacion(texto: str, en_codigo: bool = False) -> list[str]:
    plano = unicodedata.normalize("NFKC", quitar_invisibles(texto))
    encontrados = []
    for i, patron in enumerate(_MANIPULACION_RE):
        if en_codigo and i in _SOLO_EN_TEXTO_DEL_PR:
            continue
        m = patron.search(plano)
        if m:
            encontrados.append(recortar(m.group(0), 120))
    return encontrados


# ---------------------------------------------------------------- secretos

_SECRETOS = [
    ("clave de Anthropic", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("clave de OpenAI", r"sk-(proj-)?[A-Za-z0-9_\-]{32,}"),
    ("token de GitHub", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    ("clave de acceso de AWS", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("clave de Google", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("token de Slack", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    ("clave privada", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("cadena de conexión con contraseña", r"\b[a-z][a-z0-9+.\-]{1,20}://[^\s:/@]{1,64}:[^\s@/]{6,}@[^\s/]+"),
]
_SECRETOS_RE = [(nombre, re.compile(p)) for nombre, p in _SECRETOS]


def buscar_secretos(texto: str) -> list[str]:
    return [nombre for nombre, patron in _SECRETOS_RE if patron.search(texto)]


def redactar_secretos(texto: str) -> str:
    for _, patron in _SECRETOS_RE:
        texto = patron.sub("[SECRETO-REDACTADO]", texto)
    return texto


# ---------------------------------------------------------------- tests desactivados o eliminados

# Marcas que desactivan un test o enfocan uno solo (y así se dejan de correr los demás).
# Las marcadas con True solo se buscan en archivos de test, porque fuera de ellos chocan con
# código normal (por ejemplo, `model.fit(` o una variable `skip`).
_DESACTIVA_TEST = [
    ("Python", False, r"@(unittest\.)?skip(If|Unless)?\b|@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.skip\(|\braise\s+(unittest\.)?SkipTest\b|\bself\.skipTest\("),
    ("Java/Kotlin", False, r"@(Disabled|Ignore)\b"),
    ("C#", False, r"\[\s*(Ignore|Explicit)\b|\[\s*(Fact|Theory)\s*\(\s*Skip\s*="),
    ("Rust", False, r"#\[\s*ignore\s*\]"),
    ("PHP", False, r"\$this->markTestSkipped\("),
    ("JavaScript/TypeScript", True, r"(?<![\w.$])(it|test|describe|context|suite)\.(skip|only|todo)\s*\(|(?<![\w.$])(xit|xtest|xdescribe|fit|fdescribe)\s*\("),
    ("Go", True, r"\bt\.Skip(f|Now)?\("),
    ("Ruby", True, r"^\s*(skip|pending)(\s*\(|\s+['\"]|\s*$)|^\s*(xit|xdescribe)\b"),
]
_DESACTIVA_TEST_RE = [(leng, solo_tests, re.compile(p)) for leng, solo_tests, p in _DESACTIVA_TEST]

_DEFINE_TEST = re.compile(
    r"^\s*(async\s+)?def\s+(test\w*)\s*\("            # Python
    r"|^\s*(it|test)\s*\(\s*['\"`](.{1,120}?)['\"`]"  # JS/TS
    r"|^\s*func\s+(Test\w+)\s*\("                       # Go
    r"|^\s*fn\s+(test_\w+)\s*\("                        # Rust
)
_ASERCION = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\bshould\b|\b(Assert|Assertions)\.\w+\(|\brequire\.\w+\(")

_RUTA_TEST = re.compile(r"(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|Tests?\.(java|kt|cs)$", re.I)


def es_archivo_de_test(ruta: str) -> bool:
    return bool(_RUTA_TEST.search(ruta))


def desactiva_test(linea: str, en_archivo_de_test: bool = True) -> str | None:
    for lenguaje, solo_tests, patron in _DESACTIVA_TEST_RE:
        if solo_tests and not en_archivo_de_test:
            continue
        if patron.search(linea):
            return lenguaje
    return None


def nombre_de_test(linea: str) -> str | None:
    m = _DEFINE_TEST.search(linea)
    if not m:
        return None
    grupos = m.groups()
    return next(grupos[i] for i in (1, 3, 4, 5) if grupos[i])


def es_asercion(linea: str) -> bool:
    return bool(_ASERCION.search(linea))


# ---------------------------------------------------------------- diff unificado

def lineas(texto: str, keepends: bool = False) -> list[str]:
    """Divide solo en \\n. str.splitlines() también corta en U+2028, U+0085 y otros: con eso un PR
    podría esconder una línea agregada o fingir una cabecera `diff --git` dentro del contenido."""
    partes = texto.split("\n")
    if keepends:
        partes = [p + "\n" for p in partes[:-1]] + ([partes[-1]] if partes[-1] else [])
    elif partes and partes[-1] == "":
        partes.pop()
    return partes


# Git pone una ruta entre comillas (estilo C) si tiene comillas, barra invertida, tabulador o
# caracteres de control, aunque se use core.quotePath=false.
_RUTA = r'(?:"((?:[^"\\]|\\.)*)"|(\S.*?))'
_CABECERA = re.compile(rf"^diff --git {_RUTA} {_RUTA}$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
MODO_ENLACE = "120000"


def desentrecomillar(texto: str) -> str:
    """Decodifica una ruta con comillas de git: \\t, \\", \\\\ y octales (bytes UTF-8)."""
    salida, i = bytearray(), 0
    while i < len(texto):
        c = texto[i]
        if c == "\\" and i + 1 < len(texto):
            sig = texto[i + 1]
            octal = texto[i + 1:i + 4]
            if len(octal) == 3 and all(d in "01234567" for d in octal):
                salida.append(int(octal, 8) & 0xFF)
                i += 4
                continue
            if sig in _ESCAPES:
                salida.append(_ESCAPES[sig])
                i += 2
                continue
        salida += c.encode("utf-8")
        i += 1
    return salida.decode("utf-8", "replace")


def _lado(entrecomillada: str | None, simple: str | None, prefijo: str) -> str | None:
    ruta = desentrecomillar(entrecomillada) if entrecomillada is not None else simple
    if ruta is None or not ruta.startswith(prefijo):
        return None
    return ruta[len(prefijo):]


def ruta_de_cabecera(linea: str) -> tuple[str, str] | None:
    """(ruta_anterior, ruta) de una línea `diff --git`, o None si no se puede leer con certeza."""
    m = _CABECERA.match(linea)
    if not m:
        return None
    resto = linea[len("diff --git "):]
    n = (len(resto) - 5) // 2
    if m.group(1) is None and m.group(3) is None and resto[:2] == "a/" and resto[2 + n:5 + n] == " b/" and resto[2:2 + n] == resto[5 + n:]:
        return resto[2:2 + n], resto[2:2 + n]  # sin renombre: "a/P b/P" no es ambiguo aunque P tenga espacios
    anterior, nueva = _lado(m.group(1), m.group(2), "a/"), _lado(m.group(3), m.group(4), "b/")
    if anterior is None or nueva is None:
        return None
    return anterior, nueva


def _ruta_de_marcador(resto: str, prefijo: str) -> str | None:
    """Ruta de una línea `+++ b/...` o `rename to ...` (puede venir entre comillas)."""
    resto = resto.rstrip("\t")
    if resto.startswith('"') and resto.endswith('"') and len(resto) >= 2:
        resto = desentrecomillar(resto[1:-1])
    if resto == "/dev/null":
        return None
    return resto[len(prefijo):] if resto.startswith(prefijo) else None


def leer_diff(texto: str) -> list[dict]:
    """Convierte un diff unificado en una lista de archivos con sus líneas agregadas y eliminadas.

    Falla de forma segura: una cabecera `diff --git` que no se puede leer genera una entrada con
    `ilegible: True` en vez de pegar su contenido al archivo anterior.
    """
    archivos: list[dict] = []
    actual = None
    linea_nueva = 0
    en_cabecera = False
    for linea in lineas(texto):
        linea = linea[:-1] if linea.endswith("\r") else linea
        if linea.startswith("diff --git "):
            rutas = ruta_de_cabecera(linea)
            actual = {"ruta": rutas[1] if rutas else linea[len("diff --git "):],
                      "ruta_anterior": rutas[0] if rutas else "", "estado": "M", "agregadas": [], "eliminadas": []}
            if rutas is None:
                actual["ilegible"] = True
            archivos.append(actual)
            en_cabecera = True
            continue
        if actual is None:
            continue
        if en_cabecera:
            if linea.startswith("new file mode"):
                actual["estado"] = "A"
                actual["enlace"] = linea.endswith(MODO_ENLACE) or actual.get("enlace", False)
            elif linea.startswith("deleted file mode"):
                actual["estado"] = "D"
            elif linea.startswith("new mode"):
                actual["enlace"] = linea.endswith(MODO_ENLACE)
            elif linea.startswith("index ") and linea.endswith(" " + MODO_ENLACE):
                actual["enlace"] = True  # enlace simbólico existente al que le cambia el destino
            elif linea.startswith("rename to "):
                actual["estado"] = "R"
                if not actual.get("ilegible") and (r := _ruta_de_marcador(linea[len("rename to "):], "")):
                    actual["ruta"] = r
            elif linea.startswith("Binary files"):
                actual["binario"] = True
            elif linea.startswith("+++ "):
                # La línea +++ no es ambigua con espacios en el nombre: corrige la ruta de la cabecera.
                if not actual.get("ilegible") and (r := _ruta_de_marcador(linea[4:], "b/")):
                    actual["ruta"] = r
            elif (h := _HUNK.match(linea)):
                linea_nueva = int(h.group(1))
                en_cabecera = False
            continue
        # Dentro de un hunk todo es contenido: una línea agregada que empieza con "++" o una
        # eliminada que empieza con "--" no se confunde con las marcas +++/---.
        if (h := _HUNK.match(linea)):
            linea_nueva = int(h.group(1))
        elif linea.startswith("+"):
            actual["agregadas"].append((linea_nueva, linea[1:]))
            linea_nueva += 1
        elif linea.startswith("-"):
            actual["eliminadas"].append(linea[1:])
        elif linea.startswith(" "):
            linea_nueva += 1
    return archivos


def separar_diff_por_archivo(texto: str) -> list[tuple[str, str]]:
    """Divide el diff en bloques (ruta, texto del bloque) para poder excluir archivos."""
    bloques: list[list[str]] = []
    for linea in lineas(texto, keepends=True):
        if linea.startswith("diff --git "):
            bloques.append([linea])
        elif bloques:
            bloques[-1].append(linea)
    resultado = []
    for ls in bloques:
        bloque = "".join(ls)
        archivo = leer_diff(bloque)[0]  # la misma ruta que ven los controles
        # Una cabecera ilegible lleva una ruta que ningún patrón de exclusión reconoce: su contenido
        # llega a la IA en vez de esconderse.
        resultado.append(("\0ilegible" if archivo.get("ilegible") else archivo["ruta"], bloque))
    return resultado


# ---------------------------------------------------------------- limpieza de texto para publicar

_IMAGEN_MD = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_ENLACE_MD = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Incluye "www.": GitHub lo convierte en enlace clicable aunque no tenga esquema.
_URL = re.compile(r"\b(?:(?:https?|ftp|file|data|javascript):|www\.)[^\s`]*", re.I)


def _neutralizar_url(url: str) -> str:
    roto = url.replace(":", "[:]", 1) if ":" in url.split(".", 1)[0] else url.replace(".", "[.]", 1)
    return "`" + roto[:120] + "`"
_MENCION = re.compile(r"(?<![\w`])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9_.-]+)?)")


def limpiar_para_markdown(texto, maximo: int = 1000, una_linea: bool = False) -> str:
    """Prepara texto que escribió el modelo (no confiable) antes de publicarlo en GitHub.

    Quita imágenes y enlaces (por ahí se exfiltraron datos en CamoLeak), neutraliza HTML,
    menciones y URLs, y redacta cualquier cosa con forma de secreto.
    """
    if not isinstance(texto, str):
        texto = str(texto)
    texto = quitar_invisibles(unicodedata.normalize("NFKC", texto))
    texto = "".join(c for c in texto if c in "\n\t" or unicodedata.category(c)[0] != "C")
    texto = redactar_secretos(texto)
    if una_linea:
        texto = texto.replace("`", "'")  # que nadie cierre un bloque de código que abrimos nosotros
    texto = _IMAGEN_MD.sub(lambda m: f"[imagen retirada: {m.group(1)[:40]}]", texto)
    texto = _ENLACE_MD.sub(r"\1", texto)
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    texto = _URL.sub(lambda m: _neutralizar_url(m.group(0)), texto)
    texto = _MENCION.sub(lambda m: f"`@{m.group(1)}`", texto)
    if una_linea:
        texto = " ".join(texto.split())
        texto = texto.replace("|", "\\|")
    return recortar(texto.strip(), maximo)


def recortar(texto: str, maximo: int) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


# ---------------------------------------------------------------- validación de JSON Schema (subconjunto draft-07)

def validar(dato, esquema: dict, ruta: str = "$") -> list[str]:
    """Valida el subconjunto de JSON Schema que usa veredicto.schema.json. Devuelve la lista de errores."""
    errores: list[str] = []
    tipo = esquema.get("type")
    tipos = {
        "object": dict, "array": list, "string": str, "boolean": bool,
        "integer": int, "number": (int, float), "null": type(None),
    }
    if tipo:
        esperado = tipos[tipo]
        if not isinstance(dato, esperado) or (tipo in ("integer", "number") and isinstance(dato, bool)):
            return [f"{ruta}: se esperaba {tipo}"]
    if "enum" in esquema and dato not in esquema["enum"]:
        errores.append(f"{ruta}: valor fuera de {esquema['enum']}")
    if isinstance(dato, str):
        if "maxLength" in esquema and len(dato) > esquema["maxLength"]:
            errores.append(f"{ruta}: más de {esquema['maxLength']} caracteres")
        if "minLength" in esquema and len(dato) < esquema["minLength"]:
            errores.append(f"{ruta}: menos de {esquema['minLength']} caracteres")
    if isinstance(dato, (int, float)) and not isinstance(dato, bool):
        if "minimum" in esquema and dato < esquema["minimum"]:
            errores.append(f"{ruta}: menor que {esquema['minimum']}")
        if "maximum" in esquema and dato > esquema["maximum"]:
            errores.append(f"{ruta}: mayor que {esquema['maximum']}")
    if isinstance(dato, dict):
        props = esquema.get("properties", {})
        for clave in esquema.get("required", []):
            if clave not in dato:
                errores.append(f"{ruta}: falta '{clave}'")
        if esquema.get("additionalProperties") is False:
            for clave in dato:
                if clave not in props:
                    errores.append(f"{ruta}: propiedad no permitida '{clave}'")
        for clave, sub in props.items():
            if clave in dato:
                errores += validar(dato[clave], sub, f"{ruta}.{clave}")
    if isinstance(dato, list):
        if "maxItems" in esquema and len(dato) > esquema["maxItems"]:
            errores.append(f"{ruta}: más de {esquema['maxItems']} elementos")
        if "items" in esquema:
            for i, elem in enumerate(dato):
                errores += validar(elem, esquema["items"], f"{ruta}[{i}]")
    return errores


def leer_json(ruta: str | Path, defecto=None):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def escribir_json(ruta: str | Path, dato) -> None:
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    Path(ruta).write_text(json.dumps(dato, ensure_ascii=False, indent=2), encoding="utf-8")


def salida_github(nombre: str, valor: str) -> None:
    """Escribe una salida del paso en $GITHUB_OUTPUT (si existe)."""
    import os
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino and re.fullmatch(r"[A-Za-z0-9_]+", nombre) and "\n" not in valor:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"{nombre}={valor}\n")

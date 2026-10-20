"""Corre la misma revisión en tu máquina, antes de abrir el PR. No publica nada.

Uso (desde la raíz de tu repositorio, en Windows, macOS o Linux):
  python .revisor/scripts/local.py                 # compara tu rama contra main
  python .revisor/scripts/local.py --base develop  # contra otra rama
  python .revisor/scripts/local.py --sin-ia        # solo los controles deterministas

Necesita git, Python 3.10+ y, para la parte de IA, Claude Code instalado y con sesión iniciada
(o la variable ANTHROPIC_API_KEY).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="main", help="rama contra la que se compara (por defecto: main)")
    ap.add_argument("--sin-ia", action="store_true", help="no llamar a la IA")
    a = ap.parse_args()

    raiz = Path(git("rev-parse", "--show-toplevel"))
    revisor = AQUI.parent
    base_sha = git("rev-parse", a.base)
    head_sha = git("rev-parse", "HEAD")
    if base_sha == head_sha:
        print(f"Tu rama no tiene commits nuevos respecto a {a.base}.")
        return 0
    asunto = git("log", "-1", "--format=%s")
    cuerpo = git("log", f"{a.base}..HEAD", "--format=- %s%n%b")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        evento = {"pull_request": {"number": 0, "title": asunto, "body": cuerpo, "user": {"login": "local"},
                                   "author_association": "OWNER", "head": {"repo": {"full_name": "local"}},
                                   "base": {"repo": {"full_name": "local"}}}}
        (tmp / "evento.json").write_text(json.dumps(evento), encoding="utf-8")
        py = [sys.executable, "-I"]
        subprocess.run([*py, str(AQUI / "preparar.py"), "--repo", str(raiz), "--base-sha", base_sha, "--head-sha", head_sha,
                        "--evento", str(tmp / "evento.json"), "--config", str(revisor / "config.json"),
                        "--salida", str(tmp / "contexto")], check=True)
        (tmp / "veredicto").mkdir()
        if a.sin_ia:
            ctx = json.loads((tmp / "contexto" / "contexto.json").read_text(encoding="utf-8"))
            ctx["omitir_ia"], ctx["motivo_omitir_ia"] = True, "ejecución local con --sin-ia"
            (tmp / "contexto" / "contexto.json").write_text(json.dumps(ctx), encoding="utf-8")
        else:
            subprocess.run([*py, str(AQUI / "revisar.py"), "--revisor", str(revisor), "--repo", str(raiz),
                            "--contexto", str(tmp / "contexto"), "--salida", str(tmp / "veredicto"), "--local"], check=True)
        r = subprocess.run([*py, str(AQUI / "decidir.py"), "--revisor", str(revisor), "--contexto", str(tmp / "contexto"),
                            "--veredicto", str(tmp / "veredicto")])
        destino = raiz / ".revisor-local.md"
        destino.write_text((tmp / "veredicto" / "comentario.md").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nInforme completo en {destino} (agrégalo a tu .gitignore).")
        return r.returncode


if __name__ == "__main__":
    sys.exit(main())

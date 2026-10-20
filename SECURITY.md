# Política de seguridad

Si encuentras una forma de que el revisor apruebe algo que no debería, filtre un secreto o ejecute código del PR, repórtala en privado: usa **Security → Report a vulnerability** en este repositorio (GitHub Private Vulnerability Reporting). No abras un issue público.

Incluye:

- qué hiciste (un PR de ejemplo, el texto o el archivo que usaste);
- qué esperabas que pasara y qué pasó;
- la versión del revisor (commit) y del CLI de Claude Code (`.revisor/herramientas/package-lock.json`).

Respondo en un plazo de 7 días. Los hallazgos confirmados se corrigen, se publican en un aviso de seguridad y se agradecen a quien los reportó, si así lo quiere.

El modelo de amenazas y lo que el diseño no cubre están en [docs/SEGURIDAD.md](docs/SEGURIDAD.md).

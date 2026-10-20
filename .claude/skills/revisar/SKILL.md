---
name: revisar
description: Revisa los cambios de tu rama actual contra main con el subagente revisor-seguro, antes de abrir el PR.
disable-model-invocation: true
context: fork
agent: revisor-seguro
allowed-tools: Bash(git diff *) Bash(git log *)
---

Revisa los cambios confirmados (commits) de la rama actual respecto a `main`. Si tu rama principal tiene otro nombre, cámbialo en este archivo.

Los comandos no usan `$(...)` ni comillas: Claude Code no puede revisar una sustitución de comandos antes de ejecutarla y la bloquea. `main...HEAD` (tres puntos) compara contra el ancestro común, igual que `git merge-base`.

## Intención del cambio (mensajes de commit)

!`git log --format=-%x20%s%n%b main..HEAD`

## Archivos cambiados

!`git diff --stat main...HEAD`

## Diff

!`git diff --no-ext-diff --no-textconv --no-color main...HEAD`

Revisa con el método y el formato de tus instrucciones. Si el diff está vacío, dilo y termina.

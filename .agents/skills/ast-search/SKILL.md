---
name: ast-search
description: >-
  Enforces token-efficient code navigation using Tree-sitter AST (grep-ast) and surgical line-range
  reading. Use when exploring, locating, reviewing, or debugging code across the repository to minimize
  token consumption and avoid dumping large files into context.
---

# AST Search & Token Optimization

This skill enforces strict token-efficient code navigation using Tree-sitter AST parsing (`grep-ast`) and surgical file inspection.

---

## 1. Core Principles

1. **Never read entire files blindly**:
   - Avoid calling `view_file` on complete files (hundreds of lines) just to find where a function, class, or route is defined.
   - Every unnecessary line read burns context tokens and degrades model reasoning speed.

2. **AST Hierarchy First**:
   - Leverage `grep-ast` to extract structural context: it shows the matching line inside its enclosing class, function, or decorator hierarchy while collapsing irrelevant implementation details with `...⋮...`.

3. **Surgical Line-Range Inspection**:
   - Once the line numbers are identified via `grep-ast`, read **only** the target scope using `view_file` with explicit `StartLine` and `EndLine`.

---

## 2. CLI Execution via `uv`

All commands run through `uv` in the local virtual environment:

### Locating Classes and Functions
```bash
# Search for class definitions across the codebase
uv run grep-ast -n "class " $(find fastapi_plantilla -name "*.py")

# Search for function/method definitions in a specific module
uv run grep-ast -n "def " $(find fastapi_plantilla/modules/users -name "*.py")

# Locate a specific symbol or function name
uv run grep-ast -n "get_current_user" $(find fastapi_plantilla -name "*.py")
```

### Locating FastAPI Routes & Endpoints
```bash
# Locate HTTP route definitions
uv run grep-ast -n "@router." $(find fastapi_plantilla/modules -name "routes.py")

# Search for specific HTTP methods (e.g., POST endpoints)
uv run grep-ast -n "@router.post" $(find fastapi_plantilla -name "*.py")
```

### Inspecting a Single File Structurally
```bash
# Inspect the outline of an entire file without reading raw bodies
uv run grep-ast -n "def " fastapi_plantilla/router.py
```

---

## 3. Standard Navigation Workflow

Follow this sequence for any code exploration task:

```
[1. Locate Pattern]
       │
       ▼  `uv run grep-ast -n "<pattern>" <files...>`
          (Returns exact line numbers + enclosing class/method structure)
       │
[2. Target Read]
       │
       ▼  `view_file(AbsolutePath=..., StartLine=..., EndLine=...)`
          (Loads only the 15-40 lines needed)
       │
[3. Surgical Edit]
       │
       ▼  `replace_file_content` (Edits target lines directly)
```

---

## 4. Rules for the AI Agent

- **DO NOT** read more than 100 lines at once unless explicitly requested by the user or doing a complete file rewrite.
- **DO NOT** dump entire directories or multi-hundred-line files into the conversation context.
- **PREFER** `uv run grep-ast -n "<query>" ...` over full-file inspection whenever searching for where something is declared, imported, or called.
- When delegating large exploratory tasks, use the `research` subagent to prevent bloating the main conversation transcript.

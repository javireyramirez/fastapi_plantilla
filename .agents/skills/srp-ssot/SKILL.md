---
name: srp-ssot
description: >-
  Enforces Single Responsibility Principle (SRP) and Single Source of Truth (SSOT)
  in software design and Python development. Use when designing, writing,
  reviewing, or refactoring code across modules, classes, and functions.
---

# Single Responsibility Principle (SRP) & Single Source of Truth (SSOT)

This guide outlines core guidelines and Python-specific best practices for maintaining Single Responsibility Principle (SRP) and Single Source of Truth (SSOT) in modern software architectures.

---

## 1. Single Responsibility Principle (SRP)

A module, class, or function should have one, and only one, reason to change.

### Core Guidelines

- **Separation by Rate and Reason of Change**: Isolate components that change due to business requirements from components that change due to technical infrastructure or presentation formats.
- **Unidirectional Layering**: High-level policies should not depend on low-level implementation details.
  - **Transport / Presentation**: Handles protocol specifics (HTTP, CLI, events), serialization, status codes, and input extraction. Contains zero persistence queries or business rules.
  - **Domain / Application (Service)**: Orchestrates business workflows, validates business invariants, and enforces authorization. Remains agnostic of transport details (e.g., does not take HTTP request objects directly).
  - **Data Access / Persistence**: Encapsulates database, cache, or external API communication. Returns domain objects or clean data structures; does not make business decisions.
- **Functions with Single Intent**: A function should do one cohesive task. Avoid functions that simultaneously parse input, execute queries, format output, and handle unrelated notifications.

### Python Recommendations

- **Keep Domain Code Transport-Agnostic**: Services and domain functions should accept primitive types, dataclasses, or Pydantic models, never web framework request/response objects (e.g., `fastapi.Request`, `flask.request`).
- **Use Dependency Injection**: Pass database sessions or repositories into services as dependencies rather than instantiating them directly inside business functions.
- **Compose Rather than Overload**: Prefer small classes and utility functions composed together over massive "God classes" with dozens of unrelated methods.
- **Explicit Error Handling**: Let domain services raise domain exceptions (e.g., `EntityNotFoundError`, `PermissionDeniedError`) and have the transport layer translate them into protocol-specific responses (e.g., HTTP 404 or 403).

---

## 2. Single Source of Truth (SSOT)

Every piece of domain knowledge, state, configuration value, or business rule must have a single, unambiguous, authoritative representation in the system.

### Core Guidelines

- **Zero Magic Numbers and String Literals**: Any limit, timeout, status name, or default threshold must be defined once as a named constant or enum, and referenced everywhere else.
- **No Parallel State**: Derived data should be calculated on demand or maintained automatically through authoritative sources, rather than manually synchronized across multiple variables or tables.
- **Canonical Enums**: System statuses, roles, and categories must be declared as central enums. Never compare raw string literals across different files.
- **Shared Contracts**: Validation rules and schema boundaries should be declared once in a shared contract rather than re-implemented independently in routers, services, and database layers.

### Python Recommendations

- **Use `enum.StrEnum` or `enum.Enum`**: Centralize states, types, and scopes using standard Python enums (`class RecordStatus(StrEnum): ...`). This enables autocompletion, static type checking, and safe refactoring.
- **Centralize Configuration**: Keep default values, limits, and environmental parameters in a dedicated configuration or module-level constant (e.g., `DEFAULT_PAGE_SIZE = 50`).
- **Reuse Schema Validation**: Use Pydantic or `dataclasses` as the canonical source for request/response validation contracts instead of manual dictionary checking.
- **Configurable Attributes on Base Classes**: When building extensible base classes, define key field names or thresholds as class attributes (`id_field = "id"`, `max_items = 100`) so subclasses can customize behavior by overriding a single attribute.
- **DRY Single vs. Bulk Operations**: When an operation exists in both single and batch/bulk forms, extract the core validation and transformation into a shared function instead of duplicating logic inside loops.

---

## 3. Review Checklist

When writing or reviewing code, verify:

1. Does each class or function have a single clear responsibility?
2. Does the transport layer contain direct database queries or business decisions?
3. Does the domain layer know about web framework details?
4. Are limits, thresholds, and status strings defined as named constants or enums?
5. If a default value or schema is changed in one place, will dependent code adapt without manual edits in multiple files?

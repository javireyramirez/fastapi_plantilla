---
name: backend-driven
description: >-
  Enforces a strict Backend-Driven Architecture across the backend. Ensures this
  API acts as the authoritative Single Source of Truth (SSOT) that serves all
  functional modules, navigation catalogs, permissions, categories, entity
  metadata, and configuration parameters to the frontend. Forbids forcing the
  frontend to hardcode entity types, navigation, or UI metadata.
---

# Backend-Driven Architecture: Server-Side Provider Policy

This skill establishes the non-negotiable architectural principles for ensuring that **this backend serves as the authoritative Single Source of Truth (SSOT)** for all application parameters, module catalogues, navigation schemas, permissions, statuses, and entity metadata.

The frontend is built to be a **pure, dynamic projection** of backend state and catalogs. It explicitly forbids hardcoding entity types, navigation bars, module lists, categories, and permission actions. Every backend endpoint and schema must support and honor this contract.

---

## 1. Core Principles: Backend as the Single Source of Truth (SSOT)

1. **The Frontend Hardcodes Nothing**: The frontend dynamically requests catalog metadata and renders navigation, routes, icons, labels, and action buttons strictly based on backend responses.
2. **Zero Frontend Deployments for New Modules**: Adding a new functional module, entity, category, or permission in the backend must automatically make it available, properly categorized, sorted, and permission-gated in the frontend without requiring frontend code changes.
3. **Authoritative Fallback Labels**: The backend is responsible for providing sensible, human-readable default names (`name`, `category_name`) so the frontend's i18n layer (`t('key', { defaultValue: module.name })`) renders gracefully even if frontend translation files have not been updated yet.
4. **Rich Metadata over Primitive IDs**: Expose comprehensive metadata (icons, display orders, category grouping, supported actions, flags) rather than raw identifiers alone.

---

## 2. System Module Catalog (`CORE_SYSTEM_MODULES`)

All functional modules and entities exposed to users or administrators must be registered in the backend catalog (`fastapi_plantilla/modules/rbac/catalog.py`) and synchronized into the `sys_modules` table.

### Canonical Module Definition Pattern

When creating or modifying a module, define it completely in `CORE_SYSTEM_MODULES` using `SystemModuleDefinition`:

```python
{
    "code": "companies",                     # Unique machine slug / identifier
    "name": "Compañías",                     # Authoritative default display name
    "description": "Gestión de compañías / clientes",
    "category": "business",                  # Category key ('business', 'files', 'security', 'system')
    "category_name": "Negocio",              # Authoritative category display label
    "category_icon": "briefcase",            # Category Lucide icon key
    "category_order": 1,                     # Visual ordering of the category group
    "icon": "briefcase",                     # Module Lucide icon key
    "sort_order": 3,                         # Ordering of the module within its category
    "is_active": True,                       # Global availability flag
    "supported_actions": [                   # Authoritative list of RBAC actions supported by this entity
        RbacActions.CREATE,
        RbacActions.READ,
        RbacActions.UPDATE,
        RbacActions.DELETE,
        RbacActions.RESTORE,
        RbacActions.EXPORT,
    ],
    "requires_super_admin": False,           # Restricts access to SuperAdmin users
}
```

### Module Catalog Rules:
- **`code`**: Must be snake_case or kebab-free clean slug (e.g., `companies`, `audit`, `storage`, `users`). The frontend uses this to construct dynamic routes (`/${code}` or `/admin/${code}`).
- **`category`**:
  - Use `business` for core customer/user business domains (rendered in the primary app navigation).
  - Use `security`, `system`, or `files` for administrative modules (rendered under `/admin` navigation).
- **`icon` / `category_icon`**: Use standard Lucide icon identifiers in lowercase kebab-case (e.g. `shield`, `briefcase`, `hard-drive`, `file-text`, `cpu`, `users`, `settings`).
- **`supported_actions`**: Explicitly declare which actions the module actually supports (`CREATE`, `READ`, `UPDATE`, `DELETE`, `RESTORE`, `EXPORT`, `IMPORT`, `SETTINGS`). The frontend inspects this array to enable/disable UI buttons (e.g. export button, trash restore button, create button).
- **Database Synchronization**: Ensure `sync_system_modules()` runs on application startup (`lifespan`) so that updates to the catalog immediately propagate to the database.

---

## 3. Dynamic Categories (`MODULE_CATEGORIES`)

Categories define how navigation menus group modules. All categories must be centrally registered in `MODULE_CATEGORIES`:

```python
MODULE_CATEGORIES: Final[dict[str, dict[str, Any]]] = {
    "business": {
        "name": "Negocio",
        "icon": "briefcase",
        "order": 1,
    },
    "files": {
        "name": "Archivos",
        "icon": "file-text",
        "order": 2,
    },
    "security": {
        "name": "Seguridad",
        "icon": "shield",
        "order": 3,
    },
    "system": {
        "name": "Sistema",
        "icon": "cpu",
        "order": 4,
    },
}
```

Never invent ad-hoc category names inside individual models or queries without updating `MODULE_CATEGORIES`.

---

## 4. Serving Metadata to Frontend Consumers

### Primary Catalog Endpoint: `GET /api/rbac/modules`
The frontend consumes `GET /api/rbac/modules` via its `useModules()` hook to construct:
1. Dynamic sidebar navigation.
2. Route guards and permission evaluation (`can(module.code, 'READ')`).
3. Entity selectors in filters, audit logs, background jobs, and trash/recycle bins.

The backend response model (`ModuleResponse`) must always include:
- `id`: UUID
- `code`: `str`
- `name`: `str`
- `description`: `str | None`
- `category`: `str`
- `category_name`: `str | None`
- `category_icon`: `str | None`
- `category_order`: `int`
- `icon`: `str | None`
- `sort_order`: `int`
- `is_active`: `bool`
- `supported_actions`: `list[RbacActions]`
- `requires_super_admin`: `bool`

---

## 5. Selectors, Filters, and Option Endpoints

The frontend requires dynamic options for dropdowns, filters, and relationship selectors.

### Anti-Patterns to Forbid:
- **Do not expect frontend to hardcode entity choices** (e.g. status lists, roles, module filters, supported file extensions, job types).
- **Do not send untyped integers or raw magic strings** without label metadata.

### Canonical Patterns to Follow:
- **Centralized Enums**: Define statuses and scopes using `StrEnum` (e.g., `RecordStatus`, `ScopeType`, `RbacActions`).
- **Options Endpoints**: Provide dedicated catalog/options endpoints or include choice lists in responses where entities need to be selected in dropdowns (e.g. `GET /api/rbac/modules` powering `useModulesOptions()`, or module-specific filter options).
- **Trash & Soft Delete**: If an entity supports soft deletion and trash recovery, ensure `RbacActions.RESTORE` is included in `supported_actions` and register it in the trash listener system so `useEntityTrashModulesOptions()` dynamically includes it.

---

## 6. Permissions and Action Gating

1. **Action Granularity**: Always map endpoints to explicit actions in `RbacActions`:
   - `CREATE`, `READ`, `UPDATE`, `DELETE`, `RESTORE`, `EXPORT`, `IMPORT`, `SETTINGS`.
2. **No Secret Permissions**: Do not create arbitrary ad-hoc permission strings in backend code that are not registered in the system actions enum or module catalog.
3. **User Permission Matrix**: The endpoint `GET /api/rbac/users/{id}/permissions` or current user profile must return the authoritative calculated permissions matrix (module $\times$ action $\times$ scope) so the frontend can check capabilities dynamically with `can(moduleCode, action)`.

---

## 7. Step-by-Step Guide: Adding a New Backend Module

When introducing a new feature module (e.g. `invoices`, `projects`, `tickets`):

1. **Create the Module Folder**: Under `fastapi_plantilla/modules/<feature_name>/` with router, service, repository, models, and schemas.
2. **Register in `CORE_SYSTEM_MODULES`**:
   - Open `fastapi_plantilla/modules/rbac/catalog.py`.
   - Add the new module definition with code, human-readable name, category, icon, sort order, and `supported_actions`.
3. **Include Router in `api_router`**:
   - Open `fastapi_plantilla/router.py`.
   - Import and include the new router under `api_router.include_router(...)`.
4. **Register Soft-Delete / Trash (if applicable)**:
   - If the entity supports soft delete and restore, implement `SoftDeleteMixin`, add `RbacActions.RESTORE` to `supported_actions`, and register with `TrashListener`.
5. **Verify Catalog Synchronization**:
   - Ensure the catalog sync logic (`sync_system_modules`) executes on startup, synchronizing the module into the database.
   - Test that `GET /api/rbac/modules` returns the new module with all metadata.

---

## 8. Backend Developer Review Checklist

Before finishing any module, router, or entity modification, verify:

1. [ ] **Is the module registered in `CORE_SYSTEM_MODULES`?** Does it have a valid `code`, `name`, `category`, `icon`, and `sort_order`?
2. [ ] **Are `supported_actions` explicitly declared?** Did you specify only the actions this module actually supports (e.g., `READ`, `CREATE`, `UPDATE`, `DELETE`, `EXPORT`, `RESTORE`)?
3. [ ] **Are fallback display names human-friendly?** Is `name` and `category_name` clear and authoritative so the frontend renders properly even without i18n keys?
4. [ ] **Is the router registered in `fastapi_plantilla/router.py`?**
5. [ ] **Does `GET /api/rbac/modules` return the module?**
6. [ ] **Are all statuses, roles, and action scopes defined as canonical `StrEnum` types?**
7. [ ] **Does any new dropdown/filter requirement have a corresponding backend endpoint or catalog entry instead of forcing the frontend to hardcode options?**

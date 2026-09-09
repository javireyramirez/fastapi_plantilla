import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.audit_diff import (
    compute_create_diff,
    compute_update_diff,
    is_sensitive_audit_field,
    serialize_audit_val,
)
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import AuditLevel, WriteOptions
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.audit.routes import router as audit_router
from fastapi_plantilla.modules.audit.service import AuditService
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.service import RbacService
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.teams.service import TeamService
from fastapi_plantilla.modules.users.service import UserAdminService


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        is_system=user.is_system,
        is_super_admin=user.is_super_admin,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class SamplePartialAuditService(BaseAuditService[Team]):
    """Service configured with partial audit level."""

    resource_name = "Team"
    audit_level = AuditLevel.PARTIAL


class SampleNoneAuditService(BaseAuditService[Team]):
    """Service configured with no audit level."""

    resource_name = "Team"
    audit_level = AuditLevel.NONE


@pytest.fixture
async def audit_users(
    dbsession: AsyncSession,
) -> tuple[UserResponse, UserResponse]:
    """Create a superadmin and a normal user."""
    repo = BaseRepository(User, dbsession)
    admin = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Audit Admin",
            "email": f"audit_admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    user = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Normal User",
            "email": f"normal_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    await dbsession.commit()
    return _user_to_response(admin), _user_to_response(user)


@pytest.fixture
def test_app(dbsession: AsyncSession) -> FastAPI:
    """Create test FastAPI application with audit router."""
    app = FastAPI()
    app.include_router(audit_router, prefix="/api")

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield dbsession

    app.dependency_overrides[get_db_session] = override_db
    return app


@pytest.mark.anyio
async def test_audit_full_level_records_diffs_and_redaction(
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify AuditLevel.FULL logs actions with diffs and redaction."""
    admin, _ = audit_users
    repo = BaseRepository(Team, dbsession)
    service = BaseAuditService[Team](repo)
    service.resource_name = "Team"
    service.audit_level = AuditLevel.FULL

    options = WriteOptions(
        user_id=admin.id,
        ip_address="127.0.0.1",
        user_agent="Pytest-Agent",
    )

    # 1. Create
    team = await service.create(
        {
            "id": generate_uuid7(),
            "name": "Engineering",
            "slug": f"eng-{uuid.uuid4().hex[:6]}",
            "description": "Initial team",
        },
        options=options,
    )
    await dbsession.flush()

    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("team", team.id)
    assert len(logs) >= 1
    create_log = next(entry for entry in logs if entry.action == "CREATE")
    assert create_log.actor_id == admin.id
    assert create_log.ip_address == "127.0.0.1"
    assert create_log.user_agent == "Pytest-Agent"
    assert create_log.changes is not None
    assert create_log.changes["name"]["new"] == "Engineering"

    # 2. Update
    await service.update(
        team.id,
        {"name": "Core Engineering"},
        options=options,
    )
    await dbsession.flush()

    logs_after = await audit_repo.get_entity_history("team", team.id)
    update_log = next(entry for entry in logs_after if entry.action == "UPDATE")
    assert update_log.changes is not None
    assert update_log.changes["name"]["old"] == "Engineering"
    assert update_log.changes["name"]["new"] == "Core Engineering"

    # 3. Trash (Soft Delete)
    await service.trash(team.id, options=options)
    await dbsession.flush()

    logs_trash = await audit_repo.get_entity_history("team", team.id)
    trash_log = next(entry for entry in logs_trash if entry.action == "TRASH")
    assert trash_log.changes is not None
    assert trash_log.changes["status"]["new"] == "TRASHED"

    # 4. Restore
    await service.restore(team.id, options=options)
    await dbsession.flush()

    logs_restore = await audit_repo.get_entity_history("team", team.id)
    restore_log = next(entry for entry in logs_restore if entry.action == "RESTORE")
    assert restore_log.changes is not None
    assert restore_log.changes["status"]["new"] == "ACTIVE"


@pytest.mark.anyio
async def test_audit_sensitive_field_redaction(
    dbsession: AsyncSession,
) -> None:
    """Verify fields with sensitive substrings are redacted as [REDACTED]."""
    repo = BaseRepository(Team, dbsession)
    service = BaseAuditService[Team](repo)
    service.resource_name = "Team"
    service.audit_level = AuditLevel.FULL

    diff = service._compute_create_diff(  # noqa: SLF001
        {
            "name": "Secure Team",
            "password": "supersecretpassword",
            "token": "abc123xyz",
        }
    )
    assert diff["name"]["new"] == "Secure Team"
    assert diff["password"]["new"] == "[REDACTED]"
    assert diff["token"]["new"] == "[REDACTED]"


@pytest.mark.anyio
async def test_audit_partial_level_omits_diffs(
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify AuditLevel.PARTIAL logs action and metadata but changes is None."""
    admin, _ = audit_users
    repo = BaseRepository(Team, dbsession)
    service = SamplePartialAuditService(repo)

    options = WriteOptions(user_id=admin.id, ip_address="192.168.1.50")
    team = await service.create(
        {
            "id": generate_uuid7(),
            "name": "Marketing",
            "slug": f"mkt-{uuid.uuid4().hex[:6]}",
        },
        options=options,
    )
    await dbsession.flush()

    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("team", team.id)
    assert len(logs) == 1
    assert logs[0].action == "CREATE"
    assert logs[0].actor_id == admin.id
    assert logs[0].ip_address == "192.168.1.50"
    assert logs[0].changes is None


@pytest.mark.anyio
async def test_audit_none_level_emits_nothing(
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify AuditLevel.NONE emits 0 records to sys_audit_logs."""
    admin, _ = audit_users
    repo = BaseRepository(Team, dbsession)
    service = SampleNoneAuditService(repo)

    options = WriteOptions(user_id=admin.id)
    team = await service.create(
        {
            "id": generate_uuid7(),
            "name": "Ephemeral",
            "slug": f"eph-{uuid.uuid4().hex[:6]}",
        },
        options=options,
    )
    await service.update(team.id, {"name": "Ephemeral 2"}, options=options)
    await dbsession.flush()

    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("team", team.id)
    assert len(logs) == 0


@pytest.mark.anyio
async def test_system_entities_default_audit_level() -> None:
    """Verify system entities have AuditLevel.FULL by default."""
    assert TeamService.audit_level == AuditLevel.FULL
    assert RbacService.audit_level == AuditLevel.FULL
    assert UserAdminService.audit_level == AuditLevel.FULL


@pytest.mark.anyio
async def test_audit_api_endpoints(
    test_app: FastAPI,
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify GET /api/audit, GET /api/audit/{id} and RBAC access control."""
    admin, _ = audit_users

    audit_service = AuditService(AuditRepository(dbsession))
    entity_uuid = generate_uuid7()
    created_entry = await audit_service.log(
        entity_type="system_test",
        entity_id=entity_uuid,
        action="UPDATE",
        actor_id=admin.id,
        changes={"field": {"old": "A", "new": "B"}},
        details="Manual test audit log",
    )
    await dbsession.commit()

    # 1. SuperAdmin access: list
    test_app.dependency_overrides[get_current_active_superuser] = lambda: admin
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit?entity_type=system_test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] >= 1
        assert any(item["id"] == str(created_entry.id) for item in data["data"])

        # 2. SuperAdmin access: detail
        detail_resp = await client.get(f"/api/audit/{created_entry.id}")
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["id"] == str(created_entry.id)
        assert detail_data["action"] == "UPDATE"

        # 3. SuperAdmin access: entity history
        hist_resp = await client.get(f"/api/audit/entity/system_test/{entity_uuid}")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert len(hist_data) >= 1

    # 4. Non-admin access: 403 Forbidden
    async def override_forbidden() -> UserResponse:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permisos insuficientes",
        )

    test_app.dependency_overrides[get_current_active_superuser] = override_forbidden
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        forbidden_resp = await client.get("/api/audit")
        assert forbidden_resp.status_code == 403


def test_audit_diff_pure_functions() -> None:
    """Verify serialize_audit_val, is_sensitive_audit_field, and diff computations."""
    from datetime import UTC, datetime
    from enum import Enum

    class StatusEnum(Enum):
        ACTIVE = "active"

    # 1. Serialization
    now = datetime.now(UTC)
    uid = uuid.uuid4()
    assert serialize_audit_val(None) is None
    assert serialize_audit_val("test") == "test"
    assert serialize_audit_val(123) == 123
    assert serialize_audit_val(True) is True
    assert serialize_audit_val(uid) == str(uid)
    assert serialize_audit_val(now) == str(now)
    assert serialize_audit_val(StatusEnum.ACTIVE) == "active"

    # 2. Sensitive detection
    sensitive_set = frozenset({"api_key_custom"})
    assert is_sensitive_audit_field("api_key_custom", sensitive_set) is True
    assert is_sensitive_audit_field("password_hash") is True
    assert is_sensitive_audit_field("auth_token") is True
    assert is_sensitive_audit_field("client_secret") is True
    assert is_sensitive_audit_field("username") is False
    assert is_sensitive_audit_field("email") is False

    # 3. Create diff
    immutable_create = frozenset({"id", "created_at"})
    create_payload = {
        "id": uid,
        "name": "Audit Test",
        "password": "secret_password",
        "created_at": now,
    }
    create_diff = compute_create_diff(
        create_payload,
        immutable_fields=immutable_create,
        sensitive_columns=sensitive_set,
    )
    assert "id" not in create_diff
    assert "created_at" not in create_diff
    assert create_diff["name"] == {"old": None, "new": "Audit Test"}
    assert create_diff["password"] == {"old": None, "new": "[REDACTED]"}

    # 4. Update diff
    immutable_update = frozenset({"id"})
    snapshot_before = {
        "id": uid,
        "name": "Audit Test",
        "description": "Old Desc",
        "password": "old_password",
    }
    updated_payload = {
        "id": uid,
        "name": "Audit Test",  # unchanged
        "description": "New Desc",  # changed
        "password": "new_password",  # changed sensitive
    }
    update_diff = compute_update_diff(
        snapshot_before=snapshot_before,
        updated_payload=updated_payload,
        immutable_fields=immutable_update,
        sensitive_columns=sensitive_set,
    )
    assert "id" not in update_diff
    assert "name" not in update_diff
    assert update_diff["description"] == {"old": "Old Desc", "new": "New Desc"}
    assert update_diff["password"] == {"old": "[REDACTED]", "new": "[REDACTED]"}

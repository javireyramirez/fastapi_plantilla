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
from fastapi_plantilla.core.crud.schema import AuditEntry, AuditLevel, WriteOptions
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
        # 1. SuperAdmin access: list
        resp = await client.get("/api/audit?entity_type=system_test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] >= 1
        assert any(item["id"] == str(created_entry.id) for item in data["data"])
        matched_item = next(
            item for item in data["data"] if item["id"] == str(created_entry.id)
        )
        assert matched_item["module_slug"] == "system_test"
        assert matched_item["module_name"] == "System Test"

        # 2. SuperAdmin access: detail
        detail_resp = await client.get(f"/api/audit/{created_entry.id}")
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["id"] == str(created_entry.id)
        assert detail_data["action"] == "UPDATE"
        assert detail_data["module_slug"] == "system_test"
        assert detail_data["module_name"] == "System Test"

        # 3. SuperAdmin access: entity history
        hist_resp = await client.get(f"/api/audit/entity/system_test/{entity_uuid}")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert len(hist_data) >= 1
        assert hist_data[0]["module_slug"] == "system_test"

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


@pytest.mark.anyio
async def test_audit_query_singular_plural_normalization(
    test_app: FastAPI,
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify audit queries accept singular entity_type and camel aliases."""
    admin, _ = audit_users
    test_app.dependency_overrides[get_current_active_superuser] = lambda: admin

    repo = AuditRepository(dbsession)
    target_id = generate_uuid7()

    # Create log with singular entity_type="user"
    await repo.record_entry(
        AuditEntry(
            entity_type="user",
            entity_id=target_id,
            action="UPDATE",
            actor_id=admin.id,
            details="Test user update",
        )
    )
    await dbsession.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        # 1. Query with singular entity_type="user"
        res_singular = await client.get(
            f"/api/audit?page=1&limit=10&entity_type=user&entity_id={target_id}"
        )
        assert res_singular.status_code == 200
        data_singular = res_singular.json()["data"]
        assert len(data_singular) == 1
        assert data_singular[0]["entity_type"] == "user"
        assert data_singular[0]["entity_id"] == str(target_id)

        # 2. Query with camelCase aliases: entityType=user&entityId=...
        res_camel = await client.get(
            f"/api/audit?page=1&limit=10&entityType=user&entityId={target_id}"
        )
        assert res_camel.status_code == 200
        data_camel = res_camel.json()["data"]
        assert len(data_camel) == 1
        assert data_camel[0]["entity_id"] == str(target_id)

        # 3. Query entity history endpoint with "/api/audit/entity/user/..."
        res_history = await client.get(f"/api/audit/entity/user/{target_id}")
        assert res_history.status_code == 200
        data_history = res_history.json()
        assert len(data_history) == 1
        assert data_history[0]["entity_type"] == "user"


@pytest.mark.anyio
async def test_audit_actor_enrichment(
    test_app: FastAPI,
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify audit endpoints enrich actor details with user name, email, and user."""
    admin, normal_user = audit_users
    test_app.dependency_overrides[get_current_active_superuser] = lambda: admin

    repo = AuditRepository(dbsession)
    entity_id = generate_uuid7()

    # Record an entry where actor_id is normal_user.id but actor_name/email were omitted
    created_log = await repo.record_entry(
        AuditEntry(
            entity_type="storage",
            entity_id=entity_id,
            action="CREATE",
            actor_id=normal_user.id,
            details="Uploaded file",
        )
    )
    await dbsession.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        # 1. Query paginated audit endpoint
        res = await client.get("/api/audit?page=1&limit=10")
        assert res.status_code == 200
        items = res.json()["data"]
        assert len(items) >= 1
        item = next(i for i in items if i["id"] == str(created_log.id))

        # Check sorting by user_name
        sort_res = await client.get(
            "/api/audit?page=1&limit=10&sort_by=user_name&sort_order=asc"
        )
        assert sort_res.status_code == 200

        assert item["user"] is not None
        assert item["user"]["id"] == str(normal_user.id)
        assert item["user"]["name"] == normal_user.name
        assert item["user"]["email"] == normal_user.email

        # 2. Query single log by ID
        get_res = await client.get(f"/api/audit/{created_log.id}")
        assert get_res.status_code == 200
        single = get_res.json()
        assert single["user"]["name"] == normal_user.name
        assert single["user"]["email"] == normal_user.email

        # 3. Query entity history
        history_res = await client.get(f"/api/audit/entity/storage/{entity_id}")
        assert history_res.status_code == 200
        history = history_res.json()
        assert len(history) == 1
        assert history[0]["user"]["name"] == normal_user.name
        assert history[0]["user"]["email"] == normal_user.email


@pytest.mark.anyio
async def test_audit_permanent_delete_and_user_status(
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify permanent deletion, trash purge, suspend and reactivate in audit."""
    admin, normal_user = audit_users
    audit_repo = AuditRepository(dbsession)
    options = WriteOptions(user_id=admin.id, ip_address="10.0.0.1")

    # 1. Permanent delete on domain service
    team_repo = BaseRepository(Team, dbsession)
    team_service = BaseAuditService[Team](team_repo)
    team_service.resource_name = "Team"

    team = await team_service.create(
        {
            "id": generate_uuid7(),
            "name": "Perm Team",
            "slug": f"pt-{uuid.uuid4().hex[:6]}",
        },
        options=options,
    )
    await team_service.trash(team.id, options=options)
    await team_service.permanent_delete(team.id, options=options)
    await dbsession.flush()

    logs = await audit_repo.get_entity_history("team", team.id)
    perm_del_log = next((e for e in logs if e.action == "PERMANENT_DELETE"), None)
    assert perm_del_log is not None
    assert perm_del_log.actor_id == admin.id
    assert perm_del_log.changes is not None
    assert perm_del_log.changes["status"]["new"] == "PURGED"

    # 2. Suspend and reactivate on user service
    from fastapi_plantilla.modules.users.repository import UserAdminRepository

    user_repo = UserAdminRepository(dbsession)
    user_service = UserAdminService(user_repo)

    test_user_id = normal_user.id
    await user_service.suspend_user(
        test_user_id, user_id_actor=admin.id, options=options
    )
    await dbsession.flush()

    user_logs = await audit_repo.get_entity_history("user", test_user_id)
    suspend_log = next((e for e in user_logs if e.action == "SUSPEND"), None)
    assert suspend_log is not None
    assert suspend_log.actor_id == admin.id
    assert suspend_log.changes == {"is_active": {"old": True, "new": False}}

    await user_service.reactivate_user(
        test_user_id, user_id_actor=admin.id, options=options
    )
    await dbsession.flush()

    user_logs_after = await audit_repo.get_entity_history("user", test_user_id)
    reactivate_log = next(
        (e for e in user_logs_after if e.action == "REACTIVATE"), None
    )
    assert reactivate_log is not None
    assert reactivate_log.actor_id == admin.id
    assert reactivate_log.changes == {"is_active": {"old": False, "new": True}}


@pytest.mark.anyio
async def test_audit_entity_name_population_and_endpoints(
    test_app: FastAPI,
    dbsession: AsyncSession,
    audit_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify entity_name and entityName are correctly populated.

    Checks audit logs and API responses.
    """
    admin, _ = audit_users
    test_app.dependency_overrides[get_current_active_superuser] = lambda: admin

    # 1. Create team via domain service and verify entity_name is recorded
    team_repo = BaseRepository(Team, dbsession)
    team_service = BaseAuditService[Team](team_repo)
    team_service.resource_name = "Team"

    options = WriteOptions(user_id=admin.id, ip_address="192.168.1.50")
    team = await team_service.create(
        {
            "id": generate_uuid7(),
            "name": "Audit Trail Team",
            "slug": f"att-{uuid.uuid4().hex[:6]}",
        },
        options=options,
    )
    await dbsession.flush()

    # 2. Query via HTTP endpoints
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        # Check entity history endpoint
        res = await client.get(f"/api/audit/entity/team/{team.id}")
        assert res.status_code == 200
        logs = res.json()
        assert len(logs) >= 1
        create_log = next(log for log in logs if log["action"] == "CREATE")
        assert create_log["entity_name"] == "Audit Trail Team"

        # Check list endpoint with filter by entity_id
        list_res = await client.get(f"/api/audit?entity_id={team.id}")
        assert list_res.status_code == 200
        data = list_res.json()["data"]
        assert len(data) >= 1
        assert data[0]["entity_name"] == "Audit Trail Team"

        # Check list endpoint with date filter (today YYYY-MM-DD)
        from datetime import UTC, datetime

        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        date_res = await client.get(
            f"/api/audit?entity_id={team.id}&created_at_from={today_str}&created_at_to={today_str}"
        )
        assert date_res.status_code == 200
        assert len(date_res.json()["data"]) >= 1

        # Check list endpoint with past date (should return 0)
        past_res = await client.get(
            f"/api/audit?entity_id={team.id}&created_at_to=2020-01-01"
        )
        assert past_res.status_code == 200
        assert len(past_res.json()["data"]) == 0

        # Check single get endpoint
        single_res = await client.get(f"/api/audit/{create_log['id']}")
        assert single_res.status_code == 200
        single_data = single_res.json()
        assert single_data["entity_name"] == "Audit Trail Team"

    # 3. Test fallback enrichment for existing audit log with entity_name=None
    audit_repo = AuditRepository(dbsession)
    legacy_log = await audit_repo.record_entry(
        AuditEntry(
            entity_type="team",
            entity_id=team.id,
            entity_name=None,  # simulating legacy record without entity_name
            action="UPDATE",
            actor_id=admin.id,
            details="Legacy update without entity_name",
        )
    )
    await dbsession.flush()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        res = await client.get(f"/api/audit/{legacy_log.id}")
        assert res.status_code == 200
        data = res.json()
        assert data["entity_name"] == "Audit Trail Team"


@pytest.mark.anyio
async def test_audit_export(
    dbsession: AsyncSession,
) -> None:
    """Verify exporting audit logs to CSV, JSON, and with filters."""
    admin = User(
        id=generate_uuid7(),
        email="audit_exporter@example.com",
        name="Audit Exporter",
        is_active=True,
        is_super_admin=True,
    )
    dbsession.add(admin)
    await dbsession.flush()

    repo = AuditRepository(dbsession)
    log1 = await repo.record_entry(
        AuditEntry(
            entity_type="user",
            entity_id=admin.id,
            entity_name=admin.name,
            action="LOGIN",
            actor_id=admin.id,
            actor_name=admin.name,
            actor_email=admin.email,
            details="User logged in successfully",
        )
    )
    await repo.record_entry(
        AuditEntry(
            entity_type="company",
            entity_id=generate_uuid7(),
            entity_name="Export Test Corp",
            action="CREATE",
            actor_id=admin.id,
            actor_name=admin.name,
            actor_email=admin.email,
            details="Company created",
        )
    )
    await dbsession.flush()

    test_app = FastAPI()
    test_app.include_router(audit_router, prefix="/api")
    test_app.dependency_overrides[get_db_session] = lambda: dbsession
    test_app.dependency_overrides[get_current_active_superuser] = lambda: (
        _user_to_response(admin)
    )

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        # 1. Export as CSV (default)
        csv_res = await client.post(
            "/api/audit/export",
            json={"format": "csv"},
        )
        assert csv_res.status_code == 200
        assert "text/csv" in csv_res.headers["content-type"]
        assert "attachment; filename=" in csv_res.headers["content-disposition"]
        csv_text = csv_res.text
        assert "LOGIN" in csv_text
        assert "CREATE" in csv_text
        assert "Audit Exporter" in csv_text

        # 2. Export as JSON
        json_res = await client.post(
            "/api/audit/export",
            json={"format": "json"},
        )
        assert json_res.status_code == 200
        assert "application/json" in json_res.headers["content-type"]
        json_data = json_res.json()
        assert isinstance(json_data, list)
        actions = [item["action"] for item in json_data]
        assert "LOGIN" in actions
        assert "CREATE" in actions

        # 3. Export with filters (e.g. entity_type='company')
        filtered_res = await client.post(
            "/api/audit/export",
            json={"format": "json", "filters": {"entity_type": "company"}},
        )
        assert filtered_res.status_code == 200
        filtered_data = filtered_res.json()
        assert all(
            item["entity_type"] in ("company", "companies") for item in filtered_data
        )

        # 4. Export by specific ID
        id_res = await client.post(
            "/api/audit/export",
            json={"format": "json", "ids": [str(log1.id)]},
        )
        assert id_res.status_code == 200
        id_data = id_res.json()
        assert len(id_data) == 1
        assert id_data[0]["id"] == str(log1.id)

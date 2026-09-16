import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.common import (
    PrincipalEntityModule,
    enrich_principal_entities,
    register_entity_model,
    resolve_entity_model,
    resolve_module_metadata,
    resolve_principal_entity_name,
)
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.rbac.models import Role
from fastapi_plantilla.modules.teams.models import Team


def test_common_module_exports() -> None:
    """Verify common module exposes clean facade via __all__."""
    import fastapi_plantilla.modules.common as common_pkg

    expected = {
        "CODE_TO_ENTITY",
        "ENTITY_TO_CODE",
        "EntityType",
        "MODULE_NAMES",
        "PrincipalEntityModule",
        "enrich_principal_entities",
        "register_entity_model",
        "resolve_entity_model",
        "resolve_module_metadata",
        "resolve_principal_entity_name",
    }
    assert set(common_pkg.__all__) == expected
    for name in expected:
        assert hasattr(common_pkg, name)


def test_resolve_entity_model_plural_and_singular() -> None:
    """Ensure both plural codes and singular entity names resolve to models."""
    assert resolve_entity_model("users") is User
    assert resolve_entity_model("user") is User
    assert resolve_entity_model("companies") is Company
    assert resolve_entity_model("company") is Company
    assert resolve_entity_model("teams") is Team
    assert resolve_entity_model("team") is Team
    assert resolve_entity_model("roles") is Role
    assert resolve_entity_model("role") is Role
    assert resolve_entity_model("non_existent_entity_xyz") is None


def test_register_entity_model_and_resolvers() -> None:
    """Test dynamic registration of custom model and metadata resolution."""

    class DummyModel(Base):
        __abstract__ = True

    register_entity_model("dummy", DummyModel)
    assert resolve_entity_model("dummy") is DummyModel

    assert resolve_module_metadata("auth") == ("users", "Usuarios")
    assert resolve_module_metadata("user") == ("users", "Usuarios")
    assert resolve_module_metadata("users") == ("users", "Usuarios")
    assert resolve_module_metadata("companies") == ("companies", "Compañías")
    assert resolve_module_metadata(None) == ("unknown", "Desconocido")
    assert resolve_module_metadata("custom_thing") == ("custom_thing", "Custom Thing")


@pytest.mark.anyio
async def test_resolve_principal_entity_name(dbsession: AsyncSession) -> None:
    """Verify single entity name resolution for various entity types and formats."""
    user = User(
        id=generate_uuid7(),
        email=f"common_test_{uuid.uuid4().hex[:8]}@example.com",
        name="María Gómez",
    )
    company = Company(
        id=generate_uuid7(),
        name="Acme Global Inc",
        nif=f"B{uuid.uuid4().hex[:8]}",
    )
    dbsession.add_all([user, company])
    await dbsession.flush()

    # Resolution by UUID object
    user_name = await resolve_principal_entity_name(dbsession, "user", user.id)
    assert user_name == "María Gómez"

    # Resolution by plural entity_type and string UUID
    user_name_plural = await resolve_principal_entity_name(
        dbsession, "users", str(user.id)
    )
    assert user_name_plural == "María Gómez"

    # Resolution for company
    comp_name = await resolve_principal_entity_name(dbsession, "company", company.id)
    assert comp_name == "Acme Global Inc"

    # Non-existent ID returns None
    assert await resolve_principal_entity_name(dbsession, "user", uuid.uuid4()) is None

    # Malformed string UUID returns None safely without raising DataError
    assert await resolve_principal_entity_name(dbsession, "user", "not-a-uuid") is None

    # Unknown entity type returns None
    assert (
        await resolve_principal_entity_name(dbsession, "unknown_type", user.id) is None
    )


@pytest.mark.anyio
async def test_enrich_principal_entities_batch(dbsession: AsyncSession) -> None:
    """Verify batch entity enrichment across mixed entity types and structures."""
    user = User(
        id=generate_uuid7(),
        email=f"enrich_{uuid.uuid4().hex[:8]}@example.com",
        name="Enrich User",
    )
    team = Team(
        id=generate_uuid7(),
        name="Security Guild",
        slug=f"guild-{uuid.uuid4().hex[:6]}",
    )
    dbsession.add_all([user, team])
    await dbsession.flush()

    class ItemObj:
        def __init__(self, entity_type: str, entity_id: uuid.UUID | str) -> None:
            self.entity_type = entity_type
            self.entity_id = entity_id
            self.module_principal_entity = None

    item1 = ItemObj("user", user.id)
    item2 = {"entity_type": "team", "entity_id": str(team.id)}
    item3 = {"target_entity_type": "users", "target_entity_id": user.id}
    # Pre-enriched item must be preserved
    pre_enriched = PrincipalEntityModule(
        code="custom", name="Custom", entity_name="Static Name"
    )
    item4 = {
        "entity_type": "user",
        "entity_id": user.id,
        "module_principal_entity": pre_enriched,
    }
    # Invalid ID must be handled safely
    item5 = {"entity_type": "user", "entity_id": "invalid-uuid"}

    items = [item1, item2, item3, item4, item5]
    await enrich_principal_entities(dbsession, items)

    # Verify item1 (object with UUID)
    assert isinstance(item1.module_principal_entity, PrincipalEntityModule)
    assert item1.module_principal_entity.code == "users"
    assert item1.module_principal_entity.name == "Usuarios"
    assert item1.module_principal_entity.entity_name == "Enrich User"
    assert item1.module_principal_entity.entity_id == user.id

    # Verify item2 (dict with string UUID)
    p2 = item2["module_principal_entity"]
    assert isinstance(p2, PrincipalEntityModule)
    assert p2.code == "teams"
    assert p2.name == "Equipos"
    assert p2.entity_name == "Security Guild"
    assert p2.entity_id == team.id

    # Verify item3 (dict with target_entity_* and plural 'users')
    p3 = item3["module_principal_entity"]
    assert isinstance(p3, PrincipalEntityModule)
    assert p3.code == "users"
    assert p3.name == "Usuarios"
    assert p3.entity_name == "Enrich User"

    # Verify item4 (preserved pre-enriched)
    assert item4["module_principal_entity"].entity_name == "Static Name"

    # Verify empty list does not crash
    await enrich_principal_entities(dbsession, [])

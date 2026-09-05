import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.auth.models import User


async def test_base_repository_crud_flow(dbsession: AsyncSession) -> None:
    """Test full CRUD operations on BaseRepository."""
    repo = BaseRepository(User, dbsession)

    # 1. Create
    unique_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    created_user = await repo.create(
        {"name": "Alice Tester", "email": unique_email, "is_active": True}
    )
    assert created_user.id is not None
    assert created_user.email == unique_email

    # 2. Get by ID
    fetched_user = await repo.get_by_id(created_user.id)
    assert fetched_user is not None
    assert fetched_user.id == created_user.id

    # 3. Exists and Count
    exists_flag = await repo.exists(User.email == unique_email)
    assert exists_flag is True
    total_count = await repo.count(User.email == unique_email)
    assert total_count == 1

    # 4. Find first & Find many with count
    first_match = await repo.find_first(User.email == unique_email)
    assert first_match is not None
    assert first_match.name == "Alice Tester"

    items, total = await repo.find_many_with_count(
        User.email == unique_email, skip=0, limit=10
    )
    assert total == 1
    assert len(items) == 1
    assert items[0].id == created_user.id

    # 5. Update
    updated_user = await repo.update(created_user.id, {"name": "Alice Updated"})
    assert updated_user is not None
    assert updated_user.name == "Alice Updated"

    # 6. Delete
    deleted_user = await repo.delete(created_user.id)
    assert deleted_user is not None
    assert deleted_user.id == created_user.id

    # Verify deleted
    post_delete = await repo.get_by_id(created_user.id)
    assert post_delete is None


async def test_base_repository_bulk_operations(dbsession: AsyncSession) -> None:
    """Test batch create, update_many, and delete_many."""
    repo = BaseRepository(User, dbsession)

    # Bulk create
    tag = uuid.uuid4().hex[:6]
    users_data = [
        {
            "name": f"Batch User {i}",
            "email": f"batch_{tag}_{i}@example.com",
            "is_active": True,
        }
        for i in range(3)
    ]
    created_count = await repo.create_many(users_data)
    assert created_count == 3

    # Fetch created
    users = await repo.find_many(User.email.like(f"%{tag}%"))
    assert len(users) == 3
    user_ids = [u.id for u in users]

    # Update many
    updated_count = await repo.update_many(
        User.id.in_(user_ids), data={"is_active": False}
    )
    assert updated_count == 3

    # Delete many
    deleted_count = await repo.delete_many(user_ids)
    assert deleted_count == 3

    # Empty inputs edge cases
    assert await repo.create_many([]) == 0
    assert await repo.delete_many([]) == 0
    assert await repo.update_many(User.id.in_([]), data={}) == 0

import uuid

import pytest
from pydantic import ValidationError

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    ExportFormat,
    ExportRequest,
    ListQueryParams,
    PaginationMeta,
    PaginationParams,
    SortOrder,
)


def test_pagination_meta_calculations() -> None:
    """Verify PaginationMeta calculates total_pages, has_next, and has_prev properly."""
    # First page of many
    meta1 = PaginationMeta.create(page=1, limit=10, total=45)
    assert meta1.page == 1
    assert meta1.limit == 10
    assert meta1.total == 45
    assert meta1.total_pages == 5
    assert meta1.has_next is True
    assert meta1.has_prev is False

    # Middle page
    meta2 = PaginationMeta.create(page=3, limit=10, total=45)
    assert meta2.has_next is True
    assert meta2.has_prev is True

    # Last page
    meta3 = PaginationMeta.create(page=5, limit=10, total=45)
    assert meta3.has_next is False
    assert meta3.has_prev is True

    # Empty results
    meta_empty = PaginationMeta.create(page=1, limit=10, total=0)
    assert meta_empty.total == 0
    assert meta_empty.total_pages == 0
    assert meta_empty.has_next is False
    assert meta_empty.has_prev is False


def test_pagination_params_defaults_and_limits() -> None:
    """Verify PaginationParams default values and bounds validation."""
    params = PaginationParams()
    assert params.page == 1
    assert params.limit == 10
    assert params.sort_by == "created_at"
    assert params.sort_order == SortOrder.DESC
    assert params.is_trash is False

    # Invalid page (< 1)
    with pytest.raises(ValidationError):
        PaginationParams(page=0)

    # Invalid page (> 1000 Deep Pagination DoS)
    with pytest.raises(ValidationError):
        PaginationParams(page=1001)

    # Invalid limit (> 100)
    with pytest.raises(ValidationError):
        PaginationParams(limit=101)

    # Invalid search (> 100 chars Search DoS)
    with pytest.raises(ValidationError):
        PaginationParams(search="a" * 101)

    # ListQueryParams search limit (> 100 chars)
    with pytest.raises(ValidationError):
        ListQueryParams(search="a" * 101)


def test_bulk_ids_request_validation() -> None:
    """Verify BulkIdsRequest enforces non-empty and max limit array boundaries."""
    # Valid IDs
    target_id = uuid.uuid4()
    req = BulkIdsRequest(ids=[target_id])
    assert len(req.ids) == 1

    # Empty IDs should fail
    with pytest.raises(ValidationError):
        BulkIdsRequest(ids=[])

    # Over 1000 IDs should fail
    with pytest.raises(ValidationError):
        BulkIdsRequest(ids=[uuid.uuid4() for _ in range(1001)])


def test_export_request_defaults() -> None:
    """Verify ExportRequest defaults to CSV and created_at sorting."""
    req = ExportRequest()
    assert req.format == ExportFormat.CSV
    assert req.sort_by == "created_at"
    assert req.sort_order == SortOrder.DESC
    assert req.filters == {}

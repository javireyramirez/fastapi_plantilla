import mimetypes

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from fastapi_plantilla.modules.storage.dependencies import get_storage_provider
from fastapi_plantilla.modules.storage.providers import (
    LocalStorageProvider,
    StorageProvider,
)

local_router = APIRouter()


@local_router.put(
    "/local-files/{file_path:path}",
    summary="Local endpoint for uploading files with signed URL",
)
async def local_presigned_upload(
    file_path: str,
    request: Request,
    expires: int = Query(...),
    signature: str = Query(...),
    method: str = Query(default="PUT"),
    provider: StorageProvider = Depends(get_storage_provider),
) -> dict[str, str]:
    """Verify HMAC signature and write uploaded bytes to local storage."""
    if not isinstance(provider, LocalStorageProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Local presigned URLs are only supported with LocalStorageProvider.",
        )

    if not provider.verify_signature(file_path, expires, signature, method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired storage URL signature.",
        )

    body = await request.body()
    content_type = request.headers.get("content-type")
    await provider.upload(file_path, body, content_type=content_type)
    return {"status": "ok", "message": "File uploaded successfully"}


@local_router.get(
    "/local-files/{file_path:path}",
    summary="Local endpoint for downloading files with signed URL",
)
async def local_presigned_download(
    file_path: str,
    expires: int = Query(...),
    signature: str = Query(...),
    method: str = Query(default="GET"),
    provider: StorageProvider = Depends(get_storage_provider),
) -> Response:
    """Verify HMAC signature and serve file bytes from local storage."""
    if not isinstance(provider, LocalStorageProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Local presigned URLs are only supported with LocalStorageProvider.",
        )

    if not provider.verify_signature(file_path, expires, signature, method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired storage URL signature.",
        )

    data = await provider.download(file_path)
    mime, _ = mimetypes.guess_type(file_path)
    return Response(content=data, media_type=mime or "application/octet-stream")

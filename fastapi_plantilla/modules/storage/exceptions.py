from fastapi import HTTPException, status

__all__ = ["StorageBucketNotFoundError"]


class StorageBucketNotFoundError(HTTPException):
    """Raised when the storage bucket or container does not exist."""

    def __init__(self, bucket: str) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"El servicio de almacenamiento no está disponible: "
                f"el bucket o contenedor '{bucket}' no existe o no ha sido "
                "inicializado en el proveedor cloud/local."
            ),
            headers={"X-Error-Code": "STORAGE_BUCKET_NOT_FOUND"},
        )
        self.bucket = bucket

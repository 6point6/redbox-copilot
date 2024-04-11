import uuid
from uuid import UUID

from fastapi import HTTPException, FastAPI
from pydantic import AnyHttpUrl

from core_api.src.app import env, router, app
from redbox.model_db import log
from redbox.models import (
    Chunk,
    File,
    FileStatus,
)
from redbox.storage import ElasticsearchStorageHandler

s3_client = env.s3_client()

publisher = router.publisher(env.ingest_queue_name)

es = env.elasticsearch_client()

storage_handler = ElasticsearchStorageHandler(es_client=es, root_index="redbox-data")

file_app = FastAPI()


@file_app.post("/", tags=["file"])
async def create_upload_file(name: str, type: str, location: AnyHttpUrl) -> uuid.UUID:
    """Upload a file to the object store and create a record in the database

    Args:
        name (str): The file name to be recorded
        type (str): The file type to be recorded
        location (AnyHttpUrl): The presigned file resource location

    Returns:
        UUID: The file uuid from the elastic database
    """

    file = File(
        name=name,
        url=str(location),  # avoids JSON serialisation error
        content_type=type,
    )

    storage_handler.write_item(file)

    log.info(f"publishing {file.uuid}")
    await publisher.publish(file)

    return file.uuid


@file_app.get("/{file_uuid}", response_model=File, tags=["file"])
def get_file(file_uuid: UUID) -> File:
    """Get a file from the object store

    Args:
        file_uuid (str): The UUID of the file to get

    Returns:
        File: The file
    """
    return storage_handler.read_item(file_uuid, model_type="File")


@file_app.delete("/{file_uuid}", response_model=File, tags=["file"])
def delete_file(file_uuid: UUID) -> File:
    """Delete a file from the object store and the database

    Args:
        file_uuid (str): The UUID of the file to delete

    Returns:
        File: The file that was deleted
    """
    file = storage_handler.read_item(file_uuid, model_type="File")
    s3_client.delete_object(Bucket=env.bucket_name, Key=file.name)
    storage_handler.delete_item(file)

    chunks = storage_handler.get_file_chunks(file.uuid)
    storage_handler.delete_items(chunks)
    return file


@file_app.get("/{file_uuid}/chunks", tags=["file"])
def get_file_chunks(file_uuid: UUID) -> list[Chunk]:
    log.info(f"getting chunks for file {file_uuid}")
    return storage_handler.get_file_chunks(file_uuid)


@file_app.get("/{file_uuid}/status", tags=["file"])
def get_file_status(file_uuid: UUID) -> FileStatus:
    """Get the status of a file

    Args:
        file_uuid (str): The UUID of the file to get the status of

    Returns:
        File: The file with the updated status
    """
    try:
        status = storage_handler.get_file_status(file_uuid)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"File {file_uuid} not found")

    return status


app.mount("/file", file_app)


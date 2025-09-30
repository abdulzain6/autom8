import uuid
import magic
import requests
import logging
import tempfile
import shutil
import os
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Generator, Tuple
from sqlalchemy.orm import Session
from supabase import create_client, Client
from storage3.utils import StorageException
from aci.common.db.sql_models import Artifact, UserProfile
from aci.server.config import SUPABASE_URL, SUPABASE_SERVICE_KEY

logger = logging.getLogger(__name__)


class FileManager:
    """Manages file storage using Supabase Storage."""

    # Define bucket names as class attributes for consistency
    AVATAR_BUCKET = "avatars"
    ARTIFACT_BUCKET = "artifacts"

    def __init__(self, db: Session):
        self.db: Session = db
        # Initialize the Supabase client
        self.client: Client = create_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_KEY,
        )
        # requests.Session is still useful for streaming from signed URLs
        self.session = requests.Session()

    def _get_file_size(self, file_object: BinaryIO) -> int:
        """Calculates the size of a file object without consuming it."""
        file_object.seek(0, 2)  # Move to the end of the file
        size = file_object.tell()  # Get the current position (which is the size)
        file_object.seek(0)  # Move back to the start for subsequent reads
        return size

    def _upload_to_supabase(
        self,
        bucket: str,
        path_in_bucket: str,
        file_object: BinaryIO,
        allowed_mime_prefix: str | None = None,
    ) -> Tuple[str, str]:
        """Private helper to upload a file to a Supabase bucket and return its path and MIME type.
        
        Uses a temporary file to stream the upload without loading the entire content into memory.
        """
        temp_file = None
        try:
            # Create a temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            temp_path = temp_file.name

            # Stream the file_object to the temp file without loading into memory
            shutil.copyfileobj(file_object, temp_file)
            temp_file.close()  # Close the temp file to allow MIME detection and upload

            # Detect MIME type from the temp file
            detected_mime_type = magic.from_file(temp_path, mime=True)

            if allowed_mime_prefix and not detected_mime_type.startswith(
                allowed_mime_prefix
            ):
                raise ValueError(
                    f"Invalid file type. Expected '{allowed_mime_prefix}*', "
                    f"but got '{detected_mime_type}'."
                )

            # Upload from the temp file path (str is compatible with upload)
            self.client.storage.from_(bucket).upload(
                path=path_in_bucket,
                file=temp_path,
                file_options={"content-type": detected_mime_type, "upsert": "true"},
            )
        except StorageException as e:
            raise RuntimeError(f"Could not upload file to Supabase Storage: {str(e)}")
        except Exception as e:
            logger.error(f"An unexpected error occurred during Supabase upload: {e}")
            raise
        finally:
            # Clean up the temp file
            if temp_file and os.path.exists(temp_path):
                os.unlink(temp_path)

        return path_in_bucket, detected_mime_type

    def upload_avatar(self, user_id: str, file_object: BinaryIO, filename: str) -> str:
        """
        Uploads an avatar to Supabase Storage and upserts the path
        in the user's profile.

        Returns:
            The path of the uploaded avatar within the bucket.
        """
        path_in_bucket = f"{user_id}/{uuid.uuid4()}-{filename}"

        # 1. Store the file in Supabase Storage
        path_in_bucket, _ = self._upload_to_supabase(
            bucket=self.AVATAR_BUCKET,
            path_in_bucket=path_in_bucket,
            file_object=file_object,
            allowed_mime_prefix="image/",
        )

        # 2. Upsert the avatar path in the profiles table
        user_profile = (
            self.db.query(UserProfile).filter(UserProfile.id == user_id).first()
        )
        if not user_profile:
            logger.info(f"No profile found for user {user_id}. Creating a new one.")
            user_profile = UserProfile(id=user_id)
            self.db.add(user_profile)
        
        # The stored URL is now just the path within the bucket
        user_profile.avatar_url = path_in_bucket
        self.db.commit()
        logger.info(f"Updated avatar for user {user_id} to path: {path_in_bucket}")

        return path_in_bucket

    def _create_stream_generator(self, signed_url: str) -> Generator[bytes, None, None]:
        """Creates a memory-efficient generator to stream file content."""
        try:
            with self.session.get(signed_url, stream=True) as r:
                r.raise_for_status()
                yield from r.iter_content(chunk_size=65536)
        except requests.RequestException as e:
            logger.error(f"ERROR: Failed to stream from signed URL {signed_url}: {e}")
            # Optionally, re-raise or handle the error appropriately
            raise RuntimeError("Failed to stream file content.")

    def read_avatar(self, user_id: str) -> Tuple[Generator[bytes, None, None], str]:
        """
        Reads a user's avatar from storage using a temporary signed URL.

        Returns:
            A tuple containing a generator for the file's bytes and the MIME type.
        """
        user_profile = (
            self.db.query(UserProfile).filter(UserProfile.id == user_id).first()
        )
        if not user_profile or not user_profile.avatar_url:
            raise ValueError(f"Avatar not found for user with ID {user_id}.")

        path_in_bucket = user_profile.avatar_url
        
        try:
            # Create a short-lived signed URL to access the private file
            signed_response = self.client.storage.from_(self.AVATAR_BUCKET).create_signed_url(
                path=path_in_bucket, expires_in=60  # URL is valid for 60 seconds
            )
            signed_url = signed_response["signedURL"]
            
            # Perform a HEAD request to get the content type without downloading
            head_response = self.session.head(signed_url)
            head_response.raise_for_status()
            mime_type = head_response.headers.get("Content-Type", "application/octet-stream")
        except StorageException as e:
            raise ValueError(f"Could not create signed URL for avatar: {str(e)}")
        except requests.RequestException as e:
            logger.error(f"ERROR: Failed to get headers for avatar {path_in_bucket}: {e}")
            raise ValueError("Avatar file could not be accessed.")
        
        return self._create_stream_generator(signed_url), mime_type

    def upload_artifact(
        self,
        user_id: str,
        run_id: str | None,
        file_object: BinaryIO,
        filename: str,
        ttl_seconds: int,
    ) -> str:
        """Uploads a temporary artifact to Supabase and returns its Artifact ID."""
        path_in_bucket = f"{user_id}/{uuid.uuid4()}-{filename}"
        file_size = self._get_file_size(file_object)

        # Upload and get MIME type (note: _get_file_size seeks back to 0, so file_object is ready)
        path_in_bucket, detected_mime_type = self._upload_to_supabase(
            bucket=self.ARTIFACT_BUCKET,
            path_in_bucket=path_in_bucket,
            file_object=file_object,
        )
        
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        
        artifact_record = Artifact(
            file_path=path_in_bucket,  # Storing the path within the bucket
            filename=filename,
            user_id=user_id,
            mime_type=detected_mime_type,
            size_bytes=file_size,
            expires_at=expires_at,
            run_id=run_id,
        )
        self.db.add(artifact_record)
        self.db.commit()
        self.db.refresh(artifact_record)
        return str(artifact_record.id)

    def update_artifact(
        self,
        artifact_id: str,
        file_object: BinaryIO,
        user_id: str
    ) -> None:
        """
        Updates an existing artifact's content in Supabase Storage and its metadata in the database.
        This operation reuses the existing artifact ID and file path.
        """
        # 1. Fetch the existing artifact record
        artifact_record = self.db.query(Artifact).filter(Artifact.id == artifact_id, Artifact.user_id == user_id).first()
        if not artifact_record:
            raise ValueError(f"Artifact with ID {artifact_id} not found for user {user_id}.")

        # 2. Get new file size and upload the new content, overwriting the old file
        new_file_size = self._get_file_size(file_object)
        _, new_mime_type = self._upload_to_supabase(
            bucket=self.ARTIFACT_BUCKET,
            path_in_bucket=artifact_record.file_path,
            file_object=file_object,
        )

        # 3. Update the artifact record in the database
        artifact_record.size_bytes = new_file_size
        artifact_record.mime_type = new_mime_type
        self.db.commit()
        logger.info(f"Successfully updated artifact {artifact_id} in place.")


    def read_artifact(self, file_id: str) -> Tuple[Generator[bytes, None, None], str]:
        """Retrieves a temporary artifact's content via a signed URL."""
        file_record = self.db.query(Artifact).filter(Artifact.id == file_id).first()
        if not file_record:
            raise ValueError(f"Artifact with ID {file_id} not found or has expired.")

        try:
            signed_response = self.client.storage.from_(self.ARTIFACT_BUCKET).create_signed_url(
                path=file_record.file_path, expires_in=60
            )
            signed_url = signed_response["signedURL"]
        except StorageException as e:
            raise ValueError(f"Could not create signed URL for artifact: {str(e)}")

        return self._create_stream_generator(signed_url), str(file_record.mime_type)

    def cleanup_expired_artifacts(self) -> Tuple[int, int]:
        """Finds and deletes all expired artifacts from storage and the database."""
        current_time = datetime.now(timezone.utc)
        expired_records = (
            self.db.query(Artifact)
            .filter(Artifact.expires_at <= current_time)
            .all()
        )

        if not expired_records:
            return 0, 0
        
        logger.info(f"Found {len(expired_records)} expired artifacts to clean up.")
        
        paths_to_delete = [record.file_path for record in expired_records]
        
        try:
            # Batch delete files from Supabase Storage
            self.client.storage.from_(self.ARTIFACT_BUCKET).remove(paths=paths_to_delete)
            
            # Batch delete records from the database
            record_ids_to_delete = [record.id for record in expired_records]
            self.db.query(Artifact).filter(Artifact.id.in_(record_ids_to_delete)).delete(synchronize_session=False)
            self.db.commit()
            
            logger.info(f"Successfully deleted {len(paths_to_delete)} artifacts.")
            return len(paths_to_delete), 0

        except StorageException as e:
            logger.error(f"Failed to delete files from Supabase Storage: {str(e)}")
            # In case of partial failure, you might need more granular error handling
            # For simplicity, we rollback and report all as failed.
            self.db.rollback()
            return 0, len(paths_to_delete)
        except Exception as e:
            logger.error(f"An unexpected error occurred during artifact cleanup: {e}")
            self.db.rollback()
            raise

    def delete_from_storage(self, bucket: str, path_in_bucket: str):
        """
        Deletes a specific file from a given Supabase Storage bucket.
        """
        if bucket not in [self.AVATAR_BUCKET, self.ARTIFACT_BUCKET]:
            raise ValueError(f"Invalid bucket name: {bucket}")
        try:
            self.client.storage.from_(bucket).remove(paths=[path_in_bucket])
            logger.info(f"Successfully deleted '{path_in_bucket}' from bucket '{bucket}'.")
        except StorageException as e:
            logger.error(f"Failed to delete file '{path_in_bucket}' from Supabase: {str(e)}")
            raise RuntimeError(f"Could not delete file from storage: {str(e)}")

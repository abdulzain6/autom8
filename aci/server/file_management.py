import re
import uuid
import io
import magic
import requests
import logging
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Generator, Tuple
from sqlalchemy.orm import Session

# ACI framework imports
from aci.common.db.sql_models import Artifact, UserProfile # Import UserProfile
from aci.common import utils
from aci.server import config
from aci.server.config import SEAWEEDFS_URL

logger = logging.getLogger(__name__)

class FileManager:
    """Manages both temporary and permanent file storage with SeaweedFS."""
    def __init__(self, db: Session):
        self.db = db
        self.filer_url = SEAWEEDFS_URL.rstrip("/")
        self.session = requests.Session()

    def _upload_permanent_file(
        self,
        user_id: str,
        file_object: BinaryIO,
        filename: str,
        subfolder: str,
        allowed_mime_prefix: str | None = None,
    ) -> str:
        """Private helper to upload a permanent file and return its path."""
        filer_path = f"/{subfolder}/{user_id}/{uuid.uuid4()}/{filename}"
        target_url = f"{self.filer_url}{filer_path}"
        try:
            initial_bytes = file_object.read(2048)
            file_object.seek(0)
            detected_mime_type = magic.from_buffer(initial_bytes, mime=True)
            if allowed_mime_prefix and not detected_mime_type.startswith(allowed_mime_prefix):
                raise ValueError(
                    f"Invalid file type. Expected '{allowed_mime_prefix}*', "
                    f"but got '{detected_mime_type}'."
                )
            files = {"file": (filename, file_object, detected_mime_type)}
            response = self.session.post(target_url, files=files)
            response.raise_for_status()
        except requests.RequestException as e:
            error_details = e.response.text if e.response else str(e)
            raise RuntimeError(f"Could not upload file to SeaweedFS: {error_details}")
        return filer_path

    def upload_avatar(self, user_id: str, file_object: BinaryIO, filename: str) -> str:
        """
        Uploads an avatar, saves it to storage, and upserts the URL
        in the user's profile.

        Returns:
            The filer_path of the uploaded avatar.
        """
        # 1. Store the file in SeaweedFS
        avatar_filer_path = self._upload_permanent_file(
            user_id=user_id,
            file_object=file_object,
            filename=filename,
            subfolder="avatars",
            allowed_mime_prefix="image/"
        )

        # 2. Upsert the avatar URL in the profiles table
        user_profile = self.db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user_profile:
            logger.info(f"No profile found for user {user_id}. Creating a new one.")
            user_profile = UserProfile(id=user_id, name=None, avatar_url=None)
            self.db.add(user_profile)
        
        user_profile.avatar_url = avatar_filer_path
        self.db.commit()
        logger.info(f"Updated avatar for user {user_id} to path: {avatar_filer_path}")
        
        return avatar_filer_path

    def read_avatar(self, user_id: str) -> Tuple[Generator[bytes, None, None], str]:
        """
        Reads a user's avatar from storage based on their profile URL.

        Args:
            user_id: The ID of the user whose avatar is being requested.

        Returns:
            A tuple containing a generator for the file's bytes and the MIME type.
        """
        user_profile = self.db.query(UserProfile).filter(UserProfile.id == user_id).first()

        if not user_profile or not user_profile.avatar_url:
            raise ValueError(f"Avatar not found for user with ID {user_id}.")

        file_url = f"{self.filer_url}{user_profile.avatar_url}"

        try:
            # Perform a HEAD request first to get content type without downloading the body
            head_response = self.session.head(file_url)
            head_response.raise_for_status()
            mime_type = head_response.headers.get('Content-Type', 'application/octet-stream')
        except requests.RequestException as e:
            logger.error(f"ERROR: Failed to get headers for avatar {file_url}: {e}")
            raise ValueError(f"Avatar file could not be accessed at {file_url}.")

        def stream_generator():
            try:
                with self.session.get(file_url, stream=True) as r:
                    r.raise_for_status()
                    yield from r.iter_content(chunk_size=65536)
            except requests.RequestException as e:
                logger.error(f"ERROR: Failed to stream avatar from {file_url}: {e}")

        return stream_generator(), mime_type

    def upload_artifact(
        self,
        user_id: str,
        file_object: BinaryIO,
        filename: str,
        content_type: str,
        ttl_seconds: int,
    ) -> str:
        """Uploads a temporary file to a unique path and returns its Artifact ID."""
        # ... (implementation is unchanged)
        filer_path = f"/temp_files/{uuid.uuid4()}/{filename}"
        target_url = f"{self.filer_url}{filer_path}"
        try:
            initial_bytes = file_object.read(2048)
            file_object.seek(0)
            detected_mime_type = magic.from_buffer(initial_bytes, mime=True)
            final_content_type = detected_mime_type or content_type
            files = {"file": (filename, file_object, final_content_type)}
            response = self.session.post(target_url, files=files)
            response.raise_for_status()
            upload_data = response.json()
        except requests.RequestException as e:
            error_details = e.response.text if e.response else str(e)
            raise RuntimeError(f"Could not upload file to SeaweedFS Filer: {error_details}")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        temp_file = Artifact(
            filer_path=filer_path,
            filename=filename,
            user_id=user_id,
            mime_type=final_content_type,
            size_bytes=upload_data["size"],
            expires_at=expires_at,
        )
        self.db.add(temp_file)
        self.db.commit()
        self.db.refresh(temp_file)
        return str(temp_file.id)

    def read_artifact(self, file_id: str) -> Tuple[Generator[bytes, None, None], str]:
        """Retrieves a temporary file's content as a memory-efficient stream generator."""
        current_time = datetime.now(timezone.utc)
        file_record = (
            self.db.query(Artifact)
            .filter(Artifact.id == file_id)
            .filter((Artifact.expires_at == None) | (Artifact.expires_at > current_time))
            .first()
        )
        if file_record is None:
            raise ValueError(f"File with ID {file_id} not found or has expired.")
        file_url = f"{self.filer_url}{file_record.filer_path}"
        def stream_generator():
            try:
                with self.session.get(file_url, stream=True) as r:
                    r.raise_for_status()
                    yield from r.iter_content(chunk_size=65536)
            except requests.RequestException as e:
                logger.error(f"ERROR: Failed to stream file from {file_url}: {e}")
        return stream_generator(), str(file_record.mime_type)

    def cleanup_expired_artifacts(self) -> Tuple[int, int]:
        """Finds and deletes all expired files and their database records."""
        # ... (implementation is unchanged)
        deleted_count = 0
        failed_count = 0
        try:
            current_time = datetime.now(timezone.utc)
            expired_files = (
                self.db.query(Artifact).filter(Artifact.expires_at <= current_time).all()
            )
            if not expired_files:
                return 0, 0
            logger.info(f"Found {len(expired_files)} expired files to clean up.")
            for file_record in expired_files:
                try:
                    file_delete_url = f"{self.filer_url}{file_record.filer_path}"
                    response = self.session.delete(file_delete_url)
                    if response.status_code not in [202, 204, 404]:
                        response.raise_for_status()
                    self.db.delete(file_record)
                    deleted_count += 1
                except requests.RequestException as e:
                    logger.error(f"Failed to delete file from Filer: {e}")
                    failed_count += 1
                    continue
            if deleted_count > 0:
                self.db.commit()
        except Exception as e:
            logger.error(f"An unexpected error occurred during file cleanup: {e}")
            self.db.rollback()
            raise
        return deleted_count, failed_count
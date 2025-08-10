import re
import uuid
import dotenv
dotenv.load_dotenv()
import io
import requests
import logging
import requests_mock
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Generator, Tuple
from sqlalchemy.orm import Session
from aci.common.db.sql_models import TempFile
from aci.common import utils
from aci.server import config
from aci.server.config import SEAWEEDFS_URL


logger = logging.getLogger(__name__)


class FileManager:
    """Manages temporary file storage and cleanup with SeaweedFS."""
    def __init__(self, db: Session):
        self.db = db
        self.filer_url = SEAWEEDFS_URL.rstrip("/")
        self.session = requests.Session()

    def upload(
        self,
        file_object: BinaryIO,
        filename: str,
        content_type: str,
        ttl_seconds: int,
    ) -> str:
        """Uploads a file to a unique path and returns its database ID."""
        filer_path = f"/temp_files/{uuid.uuid4()}/{filename}"
        target_url = f"{self.filer_url}{filer_path}"

        try:
            response = self.session.post(
                target_url, data=file_object, headers={"Content-Type": content_type}
            )
            response.raise_for_status()
            upload_data = response.json()
        except requests.RequestException as e:
            raise RuntimeError(f"Could not upload file to SeaweedFS Filer: {e}")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        temp_file = TempFile(
            filer_path=filer_path,
            filename=filename,
            mime_type=content_type,
            size_bytes=upload_data["size"],
            expires_at=expires_at,
        )
        self.db.add(temp_file)
        self.db.commit()
        self.db.refresh(temp_file)

        return str(temp_file.id)

    def read(self, file_id: str) -> Tuple[Generator[bytes, None, None], str]:
        """Retrieves a file's content as a memory-efficient stream generator."""
        current_time = datetime.now(timezone.utc)
        file_record = (
            self.db.query(TempFile)
            .filter(TempFile.id == file_id, TempFile.expires_at > current_time)
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

    def delete_expired_files(self) -> Tuple[int, int]:
        """Finds and deletes all expired files and their database records."""
        deleted_count = 0
        failed_count = 0
        try:
            current_time = datetime.now(timezone.utc)
            expired_files = (
                self.db.query(TempFile).filter(TempFile.expires_at <= current_time).all()
            )

            if not expired_files:
                logger.info("No expired files to delete.")
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


# --- 3. Main function to test the class ---
if __name__ == "__main__":
    # Create the table in the in-memory database

    db = utils.create_db_session(config.DB_FULL_URL)
    # Use a mock adapter to simulate SeaweedFS API responses
    with requests_mock.Mocker() as m:
        # Mock data
        mock_file_content = b"This is a test file for the FileManager."
        
        # Mock endpoints using regular expressions to match unique UUID paths
        # 1. Mock the file upload endpoint
        m.post(
            re.compile(f"^{SEAWEEDFS_URL}/temp_files/"),
            json={"name": "test.txt", "size": len(mock_file_content)},
            status_code=201,
        )
        # 2. Mock the file read endpoint
        m.get(re.compile(f"^{SEAWEEDFS_URL}/temp_files/"), content=mock_file_content)
        # 3. Mock the file delete endpoint
        m.delete(re.compile(f"^{SEAWEEDFS_URL}/temp_files/"), status_code=202)

        try:
            manager = FileManager(db)
            
            # --- Test 1: Upload a file ---
            print("--- Testing File Upload ---")
            test_file_object = io.BytesIO(mock_file_content)
            file_id = manager.upload(
                file_object=test_file_object,
                filename="test.txt",
                content_type="text/plain",
                ttl_seconds=3600,
            )
            print(f"✅ File uploaded successfully. DB ID: {file_id}")

            # --- Test 2: Read the file back ---
            print("\n--- Testing File Read ---")
            generator, mime_type = manager.read(file_id)
            content = b"".join(list(generator))
            print(f"✅ File read successfully. MIME type: {mime_type}")
            assert content == mock_file_content
            print("✅ Content verification successful.")

            # --- Test 3: Cleanup expired files ---
            print("\n--- Testing File Cleanup ---")
            # Manually create an expired file record in the DB
            expired_path = f"/temp_files/{uuid.uuid4()}/expired.txt"
            expired_file = TempFile(
                filer_path=expired_path,
                filename="expired.txt",
                mime_type="text/plain",
                size_bytes=100,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.add(expired_file)
            db.commit()
            print(f"Created a fake expired file record in the database (ID: {expired_file.id}).")

            # Run the cleanup job
            deleted, failed = manager.delete_expired_files()
            print(f"✅ Cleanup job ran. Deleted: {deleted}, Failed: {failed}")
            assert deleted == 1
            assert failed == 0

            # Verify the expired file is gone from the DB
            verified_record = db.query(TempFile).filter(TempFile.id == expired_file.id).first()
            assert verified_record is None
            print("✅ Verified that the expired record was deleted from the database.")

        except Exception as e:
            logger.error(f"\n❌ An error occurred during testing: {e}", exc_info=True)
        finally:
            db.close()
            print("\n--- Test complete ---")
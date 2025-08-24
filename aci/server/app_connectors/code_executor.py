import redis
import requests
import io
from typing import List, Dict, Any
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.common.utils import create_db_session
from aci.server.file_management import FileManager

logger = get_logger(__name__)


class CodeExecutor(AppConnectorBase):
    """
    A stateful connector for executing untrusted Python code in a sandboxed
    Pyodide environment. It manages sessions per user and provides separate
    tools for file I/O and code execution.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """Initializes the connector, setting up API and Redis clients."""
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        self.user_id = linked_account.user_id
        self.base_url = config.CODE_EXECUTOR_URL.rstrip("/")
        self.redis_client = redis.Redis.from_url(config.REDIS_URL)
        self.http_session = requests.Session()
        logger.info(f"CodeExecutorConnector initialized for user {self.user_id}.")

    def _before_execute(self) -> None:
        return super()._before_execute()

    def _get_session_id(self, force_new: bool = False) -> str:
        """
        Retrieves the session ID for the user from Redis cache.
        If the session is stale or doesn't exist, it creates a new one.
        This method will raise an exception on critical failure.
        """
        redis_key = f"code_executor_session:{self.user_id}"
        session_id = None

        if not force_new:
            cached_session_id = self.redis_client.get(redis_key)
            if cached_session_id:
                session_id = cached_session_id.decode("utf-8") # type: ignore
                # Health check: Test if the session is still alive on the worker
                try:
                    test_url = f"{self.base_url}/sessions/{session_id}/execute"
                    response = self.http_session.post(
                        test_url, json={"code": "1 + 1"}, timeout=5
                    )
                    if (
                        response.status_code == 404
                        and "not found" in response.text.lower()
                    ):
                        logger.warning(
                            f"Session {session_id} for user {self.user_id} is stale. Recreating."
                        )
                        session_id = None  # Mark as stale
                    else:
                        response.raise_for_status()
                except requests.RequestException:
                    logger.warning(
                        f"Health check failed for session {session_id}. Recreating."
                    )
                    session_id = None

        if session_id is None:
            # Create a new session
            create_url = f"{self.base_url}/sessions"
            response = self.http_session.post(create_url)
            response.raise_for_status()
            session_id = response.json()["sessionId"]
            # Cache the new session ID for 1 hour
            self.redis_client.set(redis_key, session_id, ex=3600)
            logger.info(
                f"Created and cached new session {session_id} for user {self.user_id}."
            )

        return session_id

    def upload_files(self, artifact_ids: List[str]) -> Dict[str, Any]:
        """
        Uploads one or more files from Artifacts into the execution session's
        virtual filesystem.

        Args:
            artifact_ids: A list of Artifact IDs to be uploaded.

        Returns:
            A dictionary with a success message or an error.
        """
        try:
            session_id = self._get_session_id()
            db = create_db_session(config.DB_FULL_URL)
            try:
                file_manager = FileManager(db)
                uploaded_filenames = []
                for artifact_id in artifact_ids:
                    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
                    if not artifact:
                        return {"error": f"Artifact with ID {artifact_id} not found."}

                    content_generator, _ = file_manager.read_artifact(artifact_id)
                    file_buffer = io.BytesIO(b"".join(content_generator))

                    upload_url = f"{self.base_url}/sessions/{session_id}/files"
                    files = {"file": (artifact.filename, file_buffer, artifact.mime_type)}

                    logger.info(f"Uploading {artifact.filename} to session {session_id}...")
                    response = self.http_session.post(upload_url, files=files)
                    response.raise_for_status()
                    uploaded_filenames.append(artifact.filename)
            finally:
                db.close()
            
            return {"status": "success", "uploaded_files": uploaded_filenames}

        except requests.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            return {"error": f"File upload failed: {error_text}"}
        except Exception as e:
            logger.error(f"An unexpected error occurred during file upload: {e}", exc_info=True)
            return {"error": f"An unexpected internal error occurred: {str(e)}"}

    def execute_code(self, code: str) -> Dict[str, Any]:
        """
        Executes a snippet of Python code in the current session.

        Args:
            code: The Python code to execute.

        Returns:
            A dictionary containing the result or an error from the execution.
        """
        try:
            session_id = self._get_session_id()
            execute_url = f"{self.base_url}/sessions/{session_id}/execute"
            
            try:
                response = self.http_session.post(execute_url, json={"code": code}, timeout=20)
                # Handle stale session mid-flight
                if "not found" in response.text.lower():
                    logger.warning(f"Session {session_id} expired during execution. Retrying with a new session.")
                    session_id = self._get_session_id(force_new=True)
                    return {"error": "Session expired and was reset. Please reupload any files if needed and retry your code execution."}

                response.raise_for_status()
                return response.json()
            except requests.exceptions.ReadTimeout:
                logger.error("Code execution timed out.")
                return {"error": "Code execution timed out after 15 seconds."}

        except requests.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            return {"error": f"An API error occurred: {error_text}"}
        except Exception as e:
            logger.error(f"An unexpected error occurred during code execution: {e}", exc_info=True)
            return {"error": f"An unexpected internal error occurred: {str(e)}"}

    def download_files(self, filenames: List[str]) -> Dict[str, Any]:
        """
        Downloads one or more files from the execution session's virtual
        filesystem and saves them as new Artifacts.

        Args:
            filenames: A list of filenames to download from the session.

        Returns:
            A dictionary with a list of new Artifact IDs or an error.
        """
        try:
            session_id = self._get_session_id()
            db = create_db_session(config.DB_FULL_URL)
            try:
                file_manager = FileManager(db)
                new_artifact_ids = []
                for filename in filenames:
                    download_url = f"{self.base_url}/sessions/{session_id}/files"
                    params = {"path": filename}
                    response = self.http_session.get(download_url, params=params, stream=True)
                    response.raise_for_status()

                    file_buffer = io.BytesIO(response.content)
                    content_type = response.headers.get("Content-Type", "application/octet-stream")

                    new_artifact_id = file_manager.upload_artifact(
                        file_object=file_buffer,
                        filename=filename,
                        content_type=content_type,
                        ttl_seconds=24 * 3600,
                        user_id=self.user_id,
                        run_id=self.run_id
                    )
                    new_artifact_ids.append(new_artifact_id)
                return {"new_artifact_ids": new_artifact_ids}
            finally:
                db.close()
        except requests.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            return {"error": f"File download failed for '{filename}': {error_text}"}
        except Exception as e:
            logger.error(f"An unexpected error occurred during file download: {e}", exc_info=True)
            return {"error": f"An unexpected internal error occurred: {str(e)}"}

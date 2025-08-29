import requests
from typing import Optional, Dict, Any

from aci.cli import config
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.schemas.security_scheme import OAuth2Scheme, OAuth2SchemeCredentials
from aci.common.utils import create_db_session
from aci.server.app_connectors.base import AppConnectorBase
from aci.common.logging_setup import get_logger
from aci.server.file_management import FileManager

logger = get_logger(__name__)


class Wordpress(AppConnectorBase):
    """
    A connector for uploading media to a WordPress.com site.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: OAuth2Scheme,
        security_credentials: OAuth2SchemeCredentials,
        run_id: str | None = None,
    ):
        """Initializes the WordPressConnector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        db = create_db_session(config.DB_FULL_URL)
        self.db = db
        self.user_id = linked_account.user_id
        self.base_url = "https://public-api.wordpress.com/rest/v1.1"
        self.http_session = requests.Session()
        # Set the OAuth2 token for all requests made by this session
        self.http_session.headers.update({
            "Authorization": f"Bearer {security_credentials.access_token}"
        })
        self.file_manager = FileManager(db)
        logger.info(f"WordPressConnector initialized for user {self.user_id}.")

    def _before_execute(self) -> None:
        pass

    def upload_media(
        self,
        site_id: str,
        artifact_id: str,
        title: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Uploads a media file from an artifact to a WordPress site.

        Args:
            site_id: The ID or domain of the WordPress site.
            artifact_id: The ID of the artifact to upload.
            title: The title for the media file.
            description: An optional description or caption for the media.

        Returns:
            A dictionary containing the API response from WordPress on success.
        """
        self._before_execute()
        endpoint = f"{self.base_url}/sites/{site_id}/media/new"
        
        try:
            # 1. Fetch the artifact from the database
            artifact = self.db.get(Artifact, artifact_id)
            if not artifact or artifact.user_id != self.user_id:
                raise ValueError(f"Artifact with ID {artifact_id} not found or access denied.")

            # 2. Read the artifact's content
            content_generator, mime_type = self.file_manager.read_artifact(artifact_id)
            content = b"".join(content_generator)

            # 3. Prepare the multipart/form-data payload
            files_to_upload = [
                ('media[]', (artifact.filename, content, mime_type))
            ]
            
            form_data = {
                'title': title,
            }
            if description:
                form_data['description'] = description

            logger.info(f"Uploading artifact {artifact_id} ('{artifact.filename}') to WordPress site {site_id}.")
            
            # 4. Make the API request
            response = self.http_session.post(endpoint, data=form_data, files=files_to_upload, timeout=180)
            response.raise_for_status()

            logger.info(f"Successfully uploaded media to WordPress site {site_id}.")
            return response.json()

        except requests.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            logger.error(f"WordPress API error during media upload: {error_text}")
            raise Exception(f"Failed to upload media: {error_text}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred in upload_media: {e}", exc_info=True)
            raise

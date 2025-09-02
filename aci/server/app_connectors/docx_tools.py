import markdown
from typing import Optional, Dict, Any
from aci.common.utils import create_db_session
from aci.server import config
from html2docx import html2docx
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.file_management import FileManager

logger = get_logger(__name__)


class DocxTools(AppConnectorBase):
    """
    A connector for creating Microsoft Word (.docx) files directly from Markdown.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the DocxTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id
        self.db = create_db_session(config.DB_FULL_URL)
        self.file_manager = FileManager(db=self.db)

    def _before_execute(self) -> None:
        pass

    def markdown_to_docx(self, markdown_content: str, output_filename: str, title: str) -> Dict[str, Any]:
        """
        Converts a Markdown string into a complete .docx file and saves it as an artifact.

        Args:
            markdown_content: The string containing the Markdown to be converted.
            output_filename: The desired filename for the output .docx artifact.
        """
        self._before_execute()
        
        if not output_filename.lower().endswith(".docx"):
            output_filename += ".docx"

        try:
            # Convert Markdown to HTML first
            html_content = markdown.markdown(markdown_content)
            
            # Convert the HTML to DOCX and add it to our document
            file_buffer = html2docx(html_content, title)

            file_buffer.seek(0)

            # Upload the stream as an artifact
            new_artifact_id = self.file_manager.upload_artifact(
                file_object=file_buffer,
                filename=output_filename,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ttl_seconds=24 * 3600 * 7,  # 7 days
                user_id=self.user_id,
                run_id=self.run_id,
            )
            
            logger.info(f"Successfully converted Markdown to DOCX artifact {new_artifact_id}.")
            return {"new_artifact_id": new_artifact_id}
        except Exception as e:
            logger.error(f"Error converting Markdown to DOCX: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred during DOCX creation: {str(e)}"}


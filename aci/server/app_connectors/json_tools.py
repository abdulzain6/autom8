import jmespath
import json
from typing import Any, Dict
from genson import SchemaBuilder
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.common.utils import create_db_session
from aci.server.file_management import FileManager


logger = get_logger(__name__)


class JsonTools(AppConnectorBase):
    """
    A connector for JSON manipulation tasks including schema generation and querying.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """Initializes the JSONTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id
        logger.info(f"JsonTools connector initialized for user {self.user_id}.")

    def _before_execute(self) -> None:
        return super()._before_execute()

    def generate_schema(self, artifact_id: str) -> Dict[str, Any]:
        """
        Generates a JSON schema from a JSON artifact using genson.

        Args:
            artifact_id: The ID of the JSON artifact to generate schema from.

        Returns:
            A dictionary containing the generated JSON schema or an error.
        """
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            # Check if artifact exists and belongs to the user
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if not artifact:
                return {"error": f"Artifact with ID {artifact_id} not found."}
            if artifact.user_id != self.user_id:
                return {"error": f"Access denied: Artifact does not belong to the current user."}

            # Read the artifact content
            content_generator, mime_type = file_manager.read_artifact(artifact_id, user_id=self.user_id)
            content = b"".join(content_generator)

            # Parse JSON
            try:
                json_data = json.loads(content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return {"error": f"Invalid JSON in artifact: {str(e)}"}

            # Generate schema using genson
            builder = SchemaBuilder()
            builder.add_object(json_data)
            schema = builder.to_schema()

            logger.info(f"Successfully generated schema for artifact {artifact_id}.")
            return {"schema": schema}

        except Exception as e:
            logger.error(f"Error in generate_schema: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    def query_json(self, artifact_id: str, query: str) -> Dict[str, Any]:
        """
        Queries a JSON artifact using JMESPath.

        Args:
            artifact_id: The ID of the JSON artifact to query.
            query: The JMESPath query string.

        Returns:
            A dictionary containing the query result or an error.
        """
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            # Check if artifact exists and belongs to the user
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if not artifact:
                return {"error": f"Artifact with ID {artifact_id} not found."}
            if artifact.user_id != self.user_id:
                return {"error": f"Access denied: Artifact does not belong to the current user."}

            # Read the artifact content
            content_generator, mime_type = file_manager.read_artifact(artifact_id, user_id=self.user_id)
            content = b"".join(content_generator)

            # Parse JSON
            try:
                json_data = json.loads(content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return {"error": f"Invalid JSON in artifact: {str(e)}"}

            # Apply JMESPath query
            try:
                result = jmespath.search(query, json_data)
            except Exception as e:
                return {"error": f"Invalid JMESPath query: {str(e)}"}

            logger.info(
                f"Successfully queried artifact {artifact_id} with query: {query}"
            )
            return {"result": result}

        except Exception as e:
            logger.error(f"Error in query_json: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

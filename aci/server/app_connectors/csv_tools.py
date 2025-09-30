import csv
import io
from typing import Optional, Dict, Any, List
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.utils import create_db_session
from aci.server import config
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.file_management import FileManager
from sqlalchemy.orm import Session


logger = get_logger(__name__)


class CSVTools(AppConnectorBase):
    """
    A connector for creating and manipulating CSV (.csv) files.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the CSVTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id

    def _before_execute(self) -> None:
        pass

    def create_csv(self, headers: List[str], output_filename: str) -> Dict[str, Any]:
        """
        Creates a new CSV file with the given headers and saves it as an artifact.

        Args:
            headers: A list of strings to be used as the header row.
            output_filename: The desired filename for the output .csv artifact.
        """
        self._before_execute()

        if not output_filename.lower().endswith(".csv"):
            output_filename += ".csv"

        db: Optional[Session] = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)
            string_io = io.StringIO()
            writer = csv.writer(string_io)
            writer.writerow(headers)
            
            # Encode to bytes and create a buffer
            file_buffer = io.BytesIO(string_io.getvalue().encode('utf-8'))
            file_buffer.seek(0)

            new_artifact_id = file_manager.upload_artifact(
                file_object=file_buffer,
                filename=output_filename,
                ttl_seconds=24 * 3600 * 7,  # 7 days
                user_id=self.user_id,
                run_id=self.run_id,
            )
            
            logger.info(f"Successfully created CSV artifact {new_artifact_id}.")
            return {"new_artifact_id": new_artifact_id}
        except Exception as e:
            logger.error(f"Error creating CSV file: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred during CSV creation: {str(e)}"}
        finally:
            if db:
                db.close()

    def add_rows_to_csv(self, artifact_id: str, rows: List[List[str]]) -> Dict[str, Any]:
        """
        Adds new rows to an existing CSV file artifact by updating it in place.

        Args:
            artifact_id: The ID of the CSV artifact to modify.
            rows: A list of lists, where each inner list represents a row to add.
        """
        self._before_execute()
        db: Optional[Session] = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)
            
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id, Artifact.user_id == self.user_id).first()
            if not artifact:
                raise ValueError(f"Artifact with ID {artifact_id} not found for user {self.user_id}.")

            content_generator, _ = file_manager.read_artifact(artifact_id)
            content = b"".join(content_generator).decode('utf-8')
            
            string_io = io.StringIO(content)
            string_io.seek(0, io.SEEK_END)
            if content and not content.endswith('\n'):
                 string_io.write('\n')

            writer = csv.writer(string_io)
            writer.writerows(rows)
            
            file_buffer = io.BytesIO(string_io.getvalue().encode('utf-8'))
            file_buffer.seek(0)

            file_manager.update_artifact(
                artifact_id=artifact_id,
                file_object=file_buffer,
                user_id=self.user_id
            )
            
            logger.info(f"Successfully added rows to CSV artifact {artifact_id}.")
            return {"artifact_id": artifact_id}
        except Exception as e:
            logger.error(f"Error adding rows to CSV for artifact {artifact_id}: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred while adding rows: {str(e)}"}
        finally:
            if db:
                db.close()

    def read_csv(self, artifact_id: str, start_line: int = 0, end_line: Optional[int] = None) -> Dict[str, Any]:
        """
        Reads a specified range of lines from a CSV artifact, including line numbers.
        The output is a list of dictionaries, each with 'line_number' and 'row' keys.

        Args:
            artifact_id: The ID of the CSV artifact to read.
            start_line: The starting line number (0-indexed).
            end_line: The ending line number (exclusive). Reads to the end if None.
        """
        self._before_execute()
        db: Optional[Session] = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            content_generator, _ = file_manager.read_artifact(artifact_id)
            content = b"".join(content_generator).decode('utf-8')
            
            reader = csv.reader(io.StringIO(content))
            all_rows = list(reader)
            
            selected_rows_slice = all_rows[start_line:end_line]
            
            selected_rows_with_lines = [
                {"line_number": start_line + i, "row": row}
                for i, row in enumerate(selected_rows_slice)
            ]
            
            logger.info(f"Successfully read lines {start_line} to {end_line or 'end'} from artifact {artifact_id}.")
            return {"rows": selected_rows_with_lines}
        except Exception as e:
            logger.error(f"Error reading CSV artifact {artifact_id}: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred while reading the CSV: {str(e)}"}
        finally:
            if db:
                db.close()

    def delete_rows_from_csv(self, artifact_id: str, row_indices_to_delete: List[int]) -> Dict[str, Any]:
        """
        Deletes specified rows from a CSV file artifact by updating it in place.

        Args:
            artifact_id: The ID of the CSV artifact to modify.
            row_indices_to_delete: A list of 0-based row indices to delete.
        """
        self._before_execute()
        db: Optional[Session] = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            artifact = db.query(Artifact).filter(Artifact.id == artifact_id, Artifact.user_id == self.user_id).first()
            if not artifact:
                raise ValueError(f"Artifact with ID {artifact_id} not found for user {self.user_id}.")
            
            content_generator, _ = file_manager.read_artifact(artifact_id)
            content = b"".join(content_generator).decode('utf-8')

            reader = csv.reader(io.StringIO(content))
            all_rows = list(reader)
            
            indices_to_delete = set(row_indices_to_delete)
            
            kept_rows = [row for i, row in enumerate(all_rows) if i not in indices_to_delete]
            
            string_io = io.StringIO()
            writer = csv.writer(string_io)
            writer.writerows(kept_rows)
            
            file_buffer = io.BytesIO(string_io.getvalue().encode('utf-8'))
            file_buffer.seek(0)
            
            file_manager.update_artifact(
                artifact_id=artifact_id,
                file_object=file_buffer,
                user_id=self.user_id,
            )
            
            logger.info(f"Successfully deleted rows from CSV artifact {artifact_id}.")
            return {"artifact_id": artifact_id}
        except Exception as e:
            logger.error(f"Error deleting rows from CSV for artifact {artifact_id}: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred while deleting rows: {str(e)}"}
        finally:
            if db:
                db.close()


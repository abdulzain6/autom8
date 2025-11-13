import json
import requests
import io
import os
import re
import socket
from typing import List, Dict, Any
from urllib.parse import urlparse
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.common.utils import create_db_session
from aci.server.file_management import FileManager
from sqlalchemy.orm import Session

logger = get_logger(__name__)

# Known Docker Swarm services that should be explicitly BLOCKED
DISALLOWED_DOCKER_SERVICES = {
    'caddy', 'server', 'huey_worker', 'livekit', 'voice_agent',
    'gotenberg', 'code-executor', 'searxng', 'cycletls-server',
    'steel-browser-api', 'headless-browser', 'local-proxy',
    'postgres', 'redis'
}


class PdfTools(AppConnectorBase):
    """
    A connector for performing PDF manipulation tasks using a Gotenberg service.
    It handles conversions and utilities, using the Artifact system for all file I/O.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """Initializes the connector, setting up the Gotenberg API client."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id
        self.base_url = config.GOTENBERG_URL.rstrip("/")
        self.http_session = requests.Session()
        logger.info(f"PDFTools connector initialized for user {self.user_id}.")

    def _before_execute(self) -> None:
        return super()._before_execute()

    def _validate_url_security(self, url: str) -> None:
        """
        Validates URLs for security to prevent attacks on local files and internal services.

        Args:
            url (str): The URL to validate

        Raises:
            ValueError: If the URL is deemed unsafe
        """
        if not url or not isinstance(url, str):
            raise ValueError("URL must be a non-empty string")

        try:
            parsed = urlparse(url)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {str(e)}")

        # Block file:// scheme (local file access)
        if parsed.scheme.lower() == "file":
            raise ValueError("Access to local files (file://) is not allowed")

        # Block other dangerous schemes
        dangerous_schemes = {"ftp", "ftps", "data", "javascript", "vbscript", "blob"}
        if parsed.scheme.lower() in dangerous_schemes:
            raise ValueError(f"Dangerous URL scheme not allowed: {parsed.scheme}")

        # Block localhost and internal IP addresses
        if parsed.hostname:
            hostname_lower = parsed.hostname.lower()

            # Block localhost variations
            localhost_patterns = [
                "localhost",
                "127.0.0.1",
                "127.0.0.0/8",
                "::1",
                "0:0:0:0:0:0:0:1",
            ]

            for pattern in localhost_patterns:
                if hostname_lower == pattern or hostname_lower.startswith(pattern):
                    raise ValueError(
                        "Access to localhost/internal services is not allowed"
                    )

            # Block private IP ranges
            private_ip_patterns = [
                r"^10\.",  # 10.0.0.0/8
                r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",  # 172.16.0.0/12
                r"^192\.168\.",  # 192.168.0.0/16
                r"^169\.254\.",  # Link-local
                r"^fc00:",  # IPv6 private
                r"^fe80:",  # IPv6 link-local
                r"^::1$",  # IPv6 localhost
            ]

            for pattern in private_ip_patterns:
                if re.match(pattern, hostname_lower):
                    raise ValueError(
                        "Access to private/internal IP addresses is not allowed"
                    )

            # Block common internal service hostnames
            internal_hostnames = [
                "internal",
                "api.internal",
                "service.internal",
                "db",
                "database",
                "redis",
                "postgres",
                "mysql",
                "elasticsearch",
                "kibana",
                "grafana",
                "prometheus",
                "jenkins",
                "gitlab",
                "github.internal",
                "docker.internal",
                "kubernetes.internal",
            ]

            if hostname_lower in internal_hostnames:
                raise ValueError("Access to internal services is not allowed")
                
            # Block known Docker services
            if hostname_lower in DISALLOWED_DOCKER_SERVICES:
                raise ValueError(f"Access to Docker service '{hostname_lower}' is not allowed")
                
            # For all hostnames, restrict to standard HTTP/HTTPS ports only
            if parsed.port is not None:
                if parsed.scheme.lower() == "http" and parsed.port != 80:
                    raise ValueError(f"HTTP access only allowed on port 80, got port {parsed.port}")
                elif parsed.scheme.lower() == "https" and parsed.port != 443:
                    raise ValueError(f"HTTPS access only allowed on port 443, got port {parsed.port}")
            
            # Additional check: DNS resolution for suspicious hostnames
            # Be suspicious of hostnames with no dots (might be localhost-like) or very short names
            is_suspicious_hostname = (
                '.' not in hostname_lower or  # No dots at all (localhost, server, etc.)
                len(hostname_lower.split('.')[0]) <= 2  # Very short first part (db, api, etc.)
            )
            
            if is_suspicious_hostname and len(hostname_lower) > 0:
                if self._check_dns_for_internal_ip(hostname_lower):
                    raise ValueError("Access to internal services is not allowed (detected via DNS resolution)")

        # Additional security checks
        # Block URLs with suspicious patterns
        suspicious_patterns = [
            r"\.\.",  # Directory traversal
            r"%2e%2e",  # URL encoded ..
            r"%2f%2f",  # URL encoded //
            r"\\",  # Backslashes
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                raise ValueError(
                    "URL contains suspicious patterns that are not allowed"
                )

    def _check_dns_for_internal_ip(self, hostname: str) -> bool:
        """
        Performs DNS resolution to check if a hostname resolves to internal/private IP addresses.
        
        Args:
            hostname (str): The hostname to check
            
        Returns:
            bool: True if the hostname resolves to internal/private IPs, False otherwise
        """
        try:
            # Set a short timeout for DNS resolution to avoid blocking
            socket.setdefaulttimeout(2.0)
            
            # Try to resolve both IPv4 and IPv6
            try:
                ipv4_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
                ipv4_addresses = [info[4][0] for info in ipv4_info]
            except socket.gaierror:
                ipv4_addresses = []
                
            try:
                ipv6_info = socket.getaddrinfo(hostname, None, socket.AF_INET6)
                ipv6_addresses = [info[4][0] for info in ipv6_info]
            except socket.gaierror:
                ipv6_addresses = []
                
            all_addresses = ipv4_addresses + ipv6_addresses
            
            if not all_addresses:
                # If we can't resolve, assume it's safe (fail open for DNS issues)
                logger.warning(f"DNS resolution failed for {hostname}, allowing access")
                return False
                
            # Check if any resolved IP is in private ranges
            private_ip_patterns = [
                r"^10\.",  # 10.0.0.0/8
                r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",  # 172.16.0.0/12
                r"^192\.168\.",  # 192.168.0.0/16
                r"^169\.254\.",  # Link-local
                r"^127\.",  # Loopback
                r"^fc00:",  # IPv6 private
                r"^fe80:",  # IPv6 link-local
                r"^::1$",  # IPv6 localhost
            ]
            
            for ip in all_addresses:
                ip_str = str(ip).lower()
                for pattern in private_ip_patterns:
                    if re.match(pattern, ip_str):
                        logger.warning(f"Hostname {hostname} resolves to internal IP {ip}, blocking access")
                        return True
                        
            logger.info(f"Hostname {hostname} resolves to public IPs: {all_addresses[:3]}...")
            return False
            
        except Exception as e:
            # If DNS resolution fails for any reason, log and allow access
            # This prevents blocking legitimate sites due to DNS issues
            logger.warning(f"DNS check failed for {hostname}: {e}, allowing access")
            return False

    def _process_response_and_save_artifact(
        self,
        response: requests.Response,
        file_manager: FileManager,
        output_filename: str,
    ) -> Dict[str, Any]:
        """
        Processes a successful HTTP response from Gotenberg, saves the
        resulting file as an artifact, and returns its ID.
        Handles both PDF and ZIP responses.
        """
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "application/octet-stream")

        # Determine file extension from content type
        if "application/zip" in content_type:
            extension = ".zip"
        elif "application/pdf" in content_type:
            extension = ".pdf"
        else:  # Fallback for unknown types
            extension = ""

        # Ensure the output filename has the correct extension
        if extension and not output_filename.lower().endswith(extension):
            # Remove any existing extension before adding the correct one
            base_name, _ = os.path.splitext(output_filename)
            output_filename = base_name + extension

        file_buffer = io.BytesIO(response.content)
        new_artifact_id = file_manager.upload_artifact(
            file_object=file_buffer,
            filename=output_filename,
            ttl_seconds=24 * 3600 * 7,  # 7 days
            user_id=self.user_id,
            run_id=self.run_id,
        )
        logger.info(
            f"Successfully created artifact {new_artifact_id} ('{output_filename}')."
        )
        return {"new_artifact_id": new_artifact_id}

    def _fetch_artifacts_for_upload(
        self, file_manager: FileManager, db_session: Session, artifact_ids: List[str]
    ) -> List[tuple]:
        """Helper to read artifacts and prepare them for a multipart upload."""
        files_to_upload = []
        for artifact_id in artifact_ids:
            artifact = (
                db_session.query(Artifact).filter(Artifact.id == artifact_id).first()
            )
            if not artifact:
                raise ValueError(f"Artifact with ID {artifact_id} not found.")
            if artifact.user_id != self.user_id:
                raise ValueError(f"Access denied: Artifact {artifact_id} does not belong to the current user.")

            content_generator, mime_type = file_manager.read_artifact(artifact_id, user_id=self.user_id)
            content = b"".join(content_generator)
            files_to_upload.append(("files", (artifact.filename, content, mime_type)))
        return files_to_upload

    ##
    ## Chromium Conversion Routes
    ##

    def url_to_pdf(self, url: str, output_filename: str, **kwargs) -> Dict[str, Any]:
        """
        Converts a web page from a URL into a PDF.

        Args:
            url: The URL of the page to convert.
            output_filename: The desired filename for the output artifact.
            **kwargs: Optional Gotenberg parameters (e.g., `paperWidth`, `landscape`, `waitDelay='5s'`, `waitForExpression='window.ready'`).
        """
        # Security check: validate URL security
        try:
            self._validate_url_security(url)
        except ValueError as e:
            return {"error": f"URL security validation failed: {str(e)}"}
        
        endpoint = f"{self.base_url}/forms/chromium/convert/url"
        
        # Prepare the payload for multipart/form-data encoding
        form_data = {"url": (None, url)}
        for key, value in kwargs.items():
            form_data[key] = (None, str(value))

        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            logger.info(f"Requesting PDF conversion for URL: {url}")
            
            # Use the 'files' parameter to send as multipart/form-data
            response = self.http_session.post(endpoint, files=form_data, timeout=90)
            
            return self._process_response_and_save_artifact(
                response, FileManager(db), output_filename
            )
        except Exception as e:
            logger.error(f"Error in url_to_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    def html_to_pdf(
        self, html_content: str, output_filename: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Converts an HTML string into a PDF.

        Args:
            html_content: A string containing the HTML to be converted.
            output_filename: The desired filename for the output PDF artifact.
            **kwargs: Optional Gotenberg parameters (e.g., `paperWidth`, `landscape`, `waitDelay='2s'`).
        """
        endpoint = f"{self.base_url}/forms/chromium/convert/html"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            # Prepare the HTML content for upload with the required filename 'index.html'
            files = [
                ("files", ("index.html", html_content.encode("utf-8"), "text/html"))
            ]

            logger.info(f"Requesting HTML string to PDF conversion.")
            response = self.http_session.post(
                endpoint, data=kwargs, files=files, timeout=90
            )
            return self._process_response_and_save_artifact(
                response, file_manager, output_filename
            )
        except Exception as e:
            logger.error(f"Error in html_to_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    def markdown_to_pdf(
        self, markdown_content: str, output_filename: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Converts a Markdown string into a PDF using a default HTML template.

        Args:
            markdown_content: A string containing the Markdown to be converted.
            output_filename: The desired filename for the output PDF artifact.
            **kwargs: Optional Gotenberg parameters (e.g., `paperWidth`, `printBackground`).
        """
        endpoint = f"{self.base_url}/forms/chromium/convert/markdown"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            # Create a default HTML template that references a markdown file named 'content.md'
            template_html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <title>Document</title>
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown.min.css">
            </head>
            <body class="markdown-body" style="padding: 2.5em;">
                {{{{ toHTML "content.md" }}}}
            </body>
            </html>
            """.encode(
                "utf-8"
            )

            # Prepare files for upload: the template and the user's markdown content
            files_to_upload = [
                ("files", ("index.html", template_html, "text/html")),
                (
                    "files",
                    ("content.md", markdown_content.encode("utf-8"), "text/markdown"),
                ),
            ]

            logger.info(f"Requesting Markdown string to PDF conversion.")
            response = self.http_session.post(
                endpoint, data=kwargs, files=files_to_upload, timeout=90
            )
            return self._process_response_and_save_artifact(
                response, file_manager, output_filename
            )
        except Exception as e:
            logger.error(f"Error in markdown_to_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    ##
    ## LibreOffice Conversion Route
    ##

    def office_to_pdf(
        self, artifact_id: str, output_filename: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Converts a single Office document (DOCX, XLSX, PPTX, etc.) into a PDF.

        Args:
            artifact_id: The ID of the office document artifact to convert.
            output_filename: The desired filename for the output PDF artifact.
            **kwargs: Optional LibreOffice parameters (e.g., `landscape=True`, `nativePageRanges='1-2'`, `password='123'`).
        """
        endpoint = f"{self.base_url}/forms/libreoffice/convert"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)

            # Fetch the single artifact for upload
            files = self._fetch_artifacts_for_upload(file_manager, db, [artifact_id])

            logger.info(
                f"Requesting Office to PDF conversion for artifact {artifact_id}."
            )
            response = self.http_session.post(
                endpoint, data=kwargs, files=files, timeout=180
            )
            return self._process_response_and_save_artifact(
                response, file_manager, output_filename
            )
        except Exception as e:
            logger.error(f"Error in office_to_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    ##
    ## PDF Engines Routes
    ##

    def merge_pdfs(
        self, artifact_ids: List[str], output_filename: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Merges multiple PDF files from artifacts into a single PDF.

        Args:
            artifact_ids: A list of artifact IDs for the PDFs to merge (at least 2).
            output_filename: The desired filename for the output merged artifact.
            **kwargs: Optional Gotenberg parameters (e.g., `pdfa='PDF/A-2b'`).
        """
        if not artifact_ids or len(artifact_ids) < 2:
            return {"error": "At least two artifact IDs must be provided for merging."}

        endpoint = f"{self.base_url}/forms/pdfengines/merge"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            files = self._fetch_artifacts_for_upload(FileManager(db), db, artifact_ids)

            logger.info(f"Requesting to merge {len(artifact_ids)} PDF artifacts.")
            response = self.http_session.post(
                endpoint, data=kwargs, files=files, timeout=120
            )
            return self._process_response_and_save_artifact(
                response, FileManager(db), output_filename
            )
        except Exception as e:
            logger.error(f"Error in merge_pdfs: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    def split_pdf(
        self,
        artifact_id: str,
        split_mode: str,
        split_span: str,
        output_filename: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Splits a PDF from an artifact. The result may be a single PDF or a ZIP archive.

        Args:
            artifact_id: The ID of the PDF artifact to split.
            split_mode: The split mode, either 'intervals' or 'pages'.
            split_span: The intervals or page ranges to extract (e.g., '1-2' for pages, '2' for a 2-page interval).
            output_filename: The desired base filename for the output artifact.
            **kwargs: Optional Gotenberg parameters (e.g., `splitUnify=True`).
        """
        endpoint = f"{self.base_url}/forms/pdfengines/split"
        data = {"splitMode": split_mode, "splitSpan": split_span, **kwargs}
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            files = self._fetch_artifacts_for_upload(FileManager(db), db, [artifact_id])

            logger.info(
                f"Requesting to split PDF artifact {artifact_id} with mode '{split_mode}' and span '{split_span}'."
            )
            response = self.http_session.post(
                endpoint, data=data, files=files, timeout=120
            )
            return self._process_response_and_save_artifact(
                response, FileManager(db), output_filename
            )
        except Exception as e:
            logger.error(f"Error in split_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()

    def flatten_pdf(
        self, artifact_id: str, output_filename: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Flattens a PDF from an artifact to remove layers and convert annotations into static content.

        Args:
            artifact_id: The ID of the PDF artifact to flatten.
            output_filename: The desired filename for the output artifact.
            **kwargs: Future-proofing for any optional parameters.
        """
        endpoint = f"{self.base_url}/forms/pdfengines/flatten"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            files = self._fetch_artifacts_for_upload(FileManager(db), db, [artifact_id])

            logger.info(f"Requesting to flatten PDF artifact {artifact_id}.")
            response = self.http_session.post(
                endpoint, data=kwargs, files=files, timeout=120
            )
            return self._process_response_and_save_artifact(
                response, FileManager(db), output_filename
            )
        except Exception as e:
            logger.error(f"Error in flatten_pdf: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db:
                db.close()
    
    def read_pdf_metadata(self, artifact_id: str) -> Dict[str, Any]:
        """
        Reads the metadata from a single PDF artifact.

        Args:
            artifact_id: The ID of the PDF artifact to read.

        Returns:
            A dictionary containing the metadata for the file or an error.
        """
        endpoint = f"{self.base_url}/forms/pdfengines/metadata/read"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)
            files = self._fetch_artifacts_for_upload(file_manager, db, [artifact_id])

            logger.info(f"Requesting metadata read for artifact {artifact_id}.")
            response = self.http_session.post(endpoint, files=files, timeout=60)
            
            response.raise_for_status()
            
            # The API returns a dict like {"filename.pdf": {...}}, so we extract the inner metadata dict
            full_metadata = response.json()
            if full_metadata and len(full_metadata) == 1:
                return list(full_metadata.values())[0]
            
            return full_metadata # Fallback in case the format changes
            
        except requests.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            logger.error(f"Gotenberg API error for read_pdf_metadata: {error_text}")
            return {"error": f"API request failed: {error_text}"}
        except Exception as e:
            logger.error(f"Error in read_pdf_metadata: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db: db.close()

    def write_pdf_metadata(self, artifact_id: str, metadata: Dict[str, Any], output_filename: str, **kwargs) -> Dict[str, Any]:
        """
        Writes or overrides metadata for a single PDF artifact.

        Args:
            artifact_id: The ID of the PDF artifact to modify.
            metadata: A dictionary of metadata to write to the PDF.
            output_filename: The desired filename for the output artifact.
            **kwargs: Future-proofing for any optional parameters.

        Returns:
            A dictionary containing the new artifact's ID or an error.
        """
        if not metadata:
            return {"error": "A metadata dictionary must be provided."}
            
        endpoint = f"{self.base_url}/forms/pdfengines/metadata/write"
        db = None
        try:
            db = create_db_session(config.DB_FULL_URL)
            file_manager = FileManager(db)
            files = self._fetch_artifacts_for_upload(file_manager, db, [artifact_id])
            
            # The metadata dictionary must be sent as a JSON string
            data = {'metadata': json.dumps(metadata)}

            logger.info(f"Requesting metadata write for artifact {artifact_id}.")
            response = self.http_session.post(endpoint, data=data, files=files, timeout=120)
            return self._process_response_and_save_artifact(response, file_manager, output_filename)
        except Exception as e:
            logger.error(f"Error in write_pdf_metadata: {e}", exc_info=True)
            return {"error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if db: db.close()
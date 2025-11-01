import io
import os
import requests
import base64

from aci.server.config import DB_FULL_URL, TOGETHER_API_KEY, TOGETHER_BASE_URL
from typing import Optional, Dict, Any
from urllib.parse import urlparse, unquote
from aci.common.db.sql_models import LinkedAccount, Artifact
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.utils import create_db_session
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.file_management import FileManager
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from pydantic import SecretStr

logger = get_logger(__name__)

# Constants for AI-friendly image processing
MAX_IMAGE_SIZE_MB = 10.0  # Allow larger images for NASA APOD and similar content
DEFAULT_TTL_DAYS = 7

# Internal/localhost IP ranges and service names to block
INTERNAL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "fc00::/7", "fe80::/10"
}

# Common internal service names to block
INTERNAL_SERVICE_NAMES = {
    "internal_service", "api", "server", "backend", "service",
    "db", "database", "redis", "postgres", "mysql", "mongo",
    "elasticsearch", "kibana", "grafana", "prometheus",
    "caddy", "nginx", "apache", "traefik",
    "gotenberg", "searxng", "livekit", "voice_agent",
    "huey_worker", "cycletls-server", "steel-browser-api",
    "skyvern", "skyvern-ui", "code-executor"
}

class ImageTools(AppConnectorBase):
    """
    A connector for image utilities like downloading and processing images.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the ImageTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id

    def _before_execute(self) -> None:
        pass

    def _is_internal_url(self, url: str) -> bool:
        """Check if URL points to internal/localhost addresses or service names."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return True
            
            # Check for localhost variants
            if hostname.lower() in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
                return True
            
            # Check for internal service names (e.g., internal_service, api, server)
            hostname_lower = hostname.lower()
            if any(service in hostname_lower for service in INTERNAL_SERVICE_NAMES):
                return True
            
            # Check for private IP ranges (simplified)
            if (
                hostname.startswith("10.") or 
                hostname.startswith("192.168.") or 
                hostname.startswith("172.16.") or
                hostname.startswith("172.17.") or
                hostname.startswith("172.18.") or
                hostname.startswith("172.19.") or
                hostname.startswith("172.2") or
                hostname.startswith("172.30.") or
                hostname.startswith("172.31.")
            ):
                return True
                
            return False
        except Exception:
            return True  # If we can't parse, assume it's unsafe

    def _extract_filename_from_url(self, url: str) -> str:
        """Extract filename from URL or generate a default one."""
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path)
            filename = os.path.basename(path)
            
            # If no filename or extension, generate one
            if not filename or '.' not in filename:
                return "downloaded_image.jpg"
            
            return filename
        except Exception:
            return "downloaded_image.jpg"

    def download_image(
        self, 
        url: str, 
        filename: Optional[str] = None, 
    ) -> Dict[str, Any]:
        """
        Downloads an image from a URL and saves it as an artifact.
        
        Args:
            url: The URL of the image to download.
            filename: The desired filename. If not provided, extracted from URL.
            max_size_mb: Maximum file size in MB (default: 10MB for content like NASA APOD).
        """
        self._before_execute()
        
        # Security check: block internal URLs
        if self._is_internal_url(url):
            return {
                "success": False,
                "error": "Cannot download from internal/localhost URLs for security reasons."
            }
        
        # Determine filename
        if not filename:
            filename = self._extract_filename_from_url(url)
        
        max_size_bytes = int(MAX_IMAGE_SIZE_MB * 1024 * 1024)
        
        db: Optional[Session] = None
        try:
            # Download with streaming and size check
            response = requests.get(
                url, 
                stream=True, 
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Autom8/1.0)"}
            )
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return {
                    "success": False,
                    "error": f"URL does not point to an image. Content-Type: {content_type}"
                }
            
            # Check content length if available
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_size_bytes:
                return {
                    "success": False,
                    "error": f"File too large. Size: {int(content_length) / 1024 / 1024:.1f}MB, Max: {MAX_IMAGE_SIZE_MB}MB"
                }
            
            # Download content with size limit
            content = io.BytesIO()
            downloaded_size = 0
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    downloaded_size += len(chunk)
                    if downloaded_size > max_size_bytes:
                        return {
                            "success": False,
                            "error": f"File too large during download. Max: {MAX_IMAGE_SIZE_MB}MB"
                        }
                    content.write(chunk)
            
            content.seek(0)
            
            # Save as artifact
            from aci.server.config import DB_FULL_URL
            db = create_db_session(DB_FULL_URL)
            file_manager = FileManager(db)
            
            artifact_id = file_manager.upload_artifact(
                user_id=self.user_id,
                run_id=self.run_id,
                file_object=content,
                filename=filename,
                ttl_seconds=DEFAULT_TTL_DAYS * 24 * 60 * 60  # 7 days
            )
            
            logger.info(f"Downloaded image from {url} as artifact {artifact_id}")
            
            return {
                "success": True,
                "artifact_id": artifact_id,
                "filename": filename,
                "size_bytes": downloaded_size,
                "content_type": content_type,
                "message": f"Successfully downloaded image to artifact {artifact_id}"
            }
            
        except requests.RequestException as e:
            logger.error(f"Failed to download image from {url}: {e}")
            return {
                "success": False,
                "error": f"Failed to download image: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Unexpected error downloading image: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
        finally:
            if db:
                db.close()

    def analyze_image_with_llm(
        self, 
        artifact_id: str, 
        query: str
    ) -> Dict[str, Any]:
        """
        Analyzes an image artifact using a multimodal LLM.
        
        Args:
            artifact_id: The ID of the artifact containing the image to analyze.
            query: The query/prompt to send to the LLM along with the image.
            
        Returns:
            Dictionary containing the analysis result or error information.
        """
        self._before_execute()
        
        db: Optional[Session] = None
        try:            
            db = create_db_session(DB_FULL_URL)
            file_manager = FileManager(db)
            
            # Check if artifact exists and belongs to the user
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if not artifact:
                return {
                    "success": False,
                    "error": f"Artifact with ID {artifact_id} not found."
                }
            if artifact.user_id != self.user_id:
                return {
                    "success": False,
                    "error": f"Access denied: Artifact does not belong to the current user."
                }
            
            # Retrieve the artifact
            try:
                content_generator, mime_type = file_manager.read_artifact(artifact_id, user_id=self.user_id)
            except ValueError as e:
                return {
                    "success": False,
                    "error": f"Artifact not found: {str(e)}"
                }
            
            # Check if it's an image
            if not mime_type.startswith("image/"):
                return {
                    "success": False,
                    "error": f"Artifact is not an image. MIME type: {mime_type}"
                }
            
            # Convert image content to base64
            image_content = b""
            for chunk in content_generator:
                image_content += chunk
            
            # Encode to base64
            base64_image = base64.b64encode(image_content).decode('utf-8')
            
            # Initialize the multimodal LLM
            llm = ChatOpenAI(
                base_url=TOGETHER_BASE_URL,
                api_key=SecretStr(TOGETHER_API_KEY),
                model="Qwen/Qwen3-235B-A22B-fp8-tput",
                timeout=300,
                max_retries=3,
            )
            
            # Create the prompt template for multimodal analysis
            prompt = ChatPromptTemplate.from_messages([
                (
                    "system",
                    "You are a helpful AI assistant specialized in analyzing images. Provide detailed, accurate analysis based on the user's query and the image provided."
                ),
                (
                    "human",
                    [
                        {"type": "text", "text": query},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                        }
                    ]
                )
            ])
            
            # Create the chain
            chain = prompt | llm | StrOutputParser()
            
            # Generate the analysis
            analysis = chain.invoke({})
            
            logger.info(f"Successfully analyzed image artifact {artifact_id} with query: {query[:50]}...")
            
            return {
                "success": True,
                "analysis": analysis,
                "artifact_id": artifact_id,
                "mime_type": mime_type,
                "query": query,
                "message": f"Successfully analyzed image with multimodal LLM"
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze image artifact {artifact_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to analyze image: {str(e)}"
            }
        finally:
            if db:
                db.close()
import re
import time
import random
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urljoin
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import CYCLE_TLS_SERVER_URL, HTTP_PROXY
from aci.server.cycletls_client import CycleTlsServerClient
import html2text



logger = get_logger(__name__)

# Constants for LLM-friendly content processing
MAX_CONTENT_LENGTH = 50000  # 50K characters max for LLM processing
DEFAULT_TRIM_LENGTH = 10000  # Default trim length
REQUEST_TIMEOUT = 30  # 30 second timeout

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

class HttpTools(AppConnectorBase):
    """
    A connector for HTTP utilities like fetching web content with security checks.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the HTTPTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id
        
        # Set up CycleTLS client for bypassing bot detection
        if HTTP_PROXY:
            logger.info(f"HTTP Tools configured with proxy: {HTTP_PROXY}")
            self.client = CycleTlsServerClient(server_url=CYCLE_TLS_SERVER_URL, proxy=HTTP_PROXY)
        else:
            self.client = CycleTlsServerClient(server_url=CYCLE_TLS_SERVER_URL)
        
        if html2text is None:
            logger.warning("html2text not available. Install with: pip install html2text")

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

    def _extract_links(self, html_content: str, base_url: str) -> List[Dict[str, str]]:
        """Extract links from HTML content."""
        links = []
        try:
            # Simple regex to find links - could be enhanced with BeautifulSoup if needed
            link_pattern = r'<a[^>]*href=["\']([^"\'>]+)["\'][^>]*>([^<]*)</a>'
            matches = re.findall(link_pattern, html_content, re.IGNORECASE)
            
            for href, text in matches:
                # Convert relative URLs to absolute
                full_url = urljoin(base_url, href)
                if full_url.startswith(('http://', 'https://')):
                    links.append({
                        "url": full_url,
                        "text": text.strip()
                    })
        except Exception as e:
            logger.warning(f"Failed to extract links: {e}")
        
        return links[:50]  # Limit to 50 links for LLM processing

    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to clean text using html2text."""
        if html2text is None:
            # Fallback: simple HTML tag removal
            text = re.sub(r'<[^>]+>', '', html_content)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
        
        try:
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.ignore_emphasis = False
            h.body_width = 0  # Don't wrap lines
            text = h.handle(html_content)
            
            # Clean up extra whitespace
            text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)  # Remove excessive newlines
            text = re.sub(r'[ \t]+', ' ', text)  # Normalize spaces
            return text.strip()
        except Exception as e:
            logger.warning(f"html2text conversion failed: {e}")
            # Fallback to simple tag removal
            text = re.sub(r'<[^>]+>', '', html_content)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()

    def get_url(
        self,
        url: str,
        max_length: int = DEFAULT_TRIM_LENGTH,
        include_links: bool = True
    ) -> Dict[str, Any]:
        """
        Fetches content from a URL using CycleTLS and returns clean text.
        
        Args:
            url: The URL to fetch content from.
            max_length: Maximum length of returned text (default: 10000 characters).
            include_links: Whether to include extracted links (default: True).
        """
        self._before_execute()
        
        # Security check: block internal URLs
        if self._is_internal_url(url):
            return {
                "success": False,
                "error": "Cannot access internal/localhost URLs for security reasons."
            }
        
        # Ensure max_length is reasonable
        max_length = min(max_length, MAX_CONTENT_LENGTH)
        
        try:
            # Add small random delay to appear more human-like
            time.sleep(random.uniform(0.5, 2.0))
            
            # Make GET request using CycleTLS to bypass bot detection
            content = self.client.get(url)
            
            if not content:
                return {
                    "success": False,
                    "error": "Failed to fetch content from URL. The server may be blocking requests."
                }
            
            # Extract links if requested
            links = []
            if include_links:
                links = self._extract_links(content, url)
            
            # Convert HTML to text
            text_content = self._html_to_text(content)
            
            # Trim content for LLM processing
            if len(text_content) > max_length:
                text_content = text_content[:max_length] + "...\n[Content truncated for LLM processing]"
            
            logger.info(f"Successfully fetched content from {url} ({len(text_content)} characters)")
            
            result = {
                "success": True,
                "url": url,
                "content": text_content,
                "content_type": "text/html",  # CycleTLS primarily handles HTML
                "length": len(text_content),
                "truncated": len(text_content) >= max_length,
                "message": f"Successfully fetched and processed content from {url}"
            }
            
            if include_links and links:
                result["links"] = links
                result["link_count"] = len(links)
            
            return result
            
        except Exception as e:
            logger.error(f"Unexpected error fetching URL {url}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
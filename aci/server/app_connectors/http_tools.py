import re
import time
import random
import socket
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urljoin

import jsonschema
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server import config
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import CYCLE_TLS_SERVER_URL, HTTP_PROXY
from aci.server.cycletls_client import CycleTlsServerClient
from crawl4ai import LLMConfig, LLMExtractionStrategy
import html2text



logger = get_logger(__name__)

# Constants for LLM-friendly content processing
MAX_CONTENT_LENGTH = 50000  # 50K characters max for LLM processing
DEFAULT_TRIM_LENGTH = 10000  # Default trim length
REQUEST_TIMEOUT = 30  # 30 second timeout

# Known Docker Swarm services that should be explicitly BLOCKED
DISALLOWED_DOCKER_SERVICES = {
    'caddy', 'server', 'huey_worker', 'livekit', 'voice_agent',
    'gotenberg', 'code-executor', 'searxng', 'cycletls-server',
    'steel-browser-api', 'headless-browser', 'local-proxy',
    'skyvern', 'skyvern-ui', 'postgres', 'redis'
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

    def _is_internal_url(self, url: str) -> bool:
        """Check if URL points to internal/localhost addresses or service names."""
        try:
            self._validate_url_security(url)
            return False  # If validation passes, it's not internal
        except ValueError:
            return True  # If validation fails, it's considered internal/unsafe
        
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
        extraction_instructions: str, 
        output_schema: dict
    ) -> Dict[str, Any]:
        """
        Fetches content from a URL using CycleTLS and returns clean text.
        
        Args:
            url: The URL to fetch content from.
            extraction_instructions (str): LLM instructions for data extraction using GPT-OSS model.
                                         Must be specific about what data to extract and how to format it.
            output_schema (dict): Required JSON schema defining the expected output structure.
                                Must be a valid JSON schema with type definitions and properties.
                                Example: {"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}}
        """
        self._before_execute()
        
        # Security check: validate URL security
        try:
            self._validate_url_security(url)
        except ValueError as e:
            return {
                "success": False,
                "error": f"URL security validation failed: {str(e)}"
            }
        try:
            # Ensure schema is a valid JSON schema structure
            if not isinstance(output_schema, dict):
                raise ValueError("output_schema must be a dictionary")

            # Basic schema validation - check for required fields
            if "type" not in output_schema:
                raise ValueError("output_schema must include 'type' field")

            # Test schema validity by creating a validator
            jsonschema.Draft7Validator.check_schema(output_schema)
            logger.info(f"Output schema validation passed: {output_schema}")

        except jsonschema.SchemaError as e:
            logger.error(f"Invalid output schema provided: {e}")
            raise ValueError(f"Invalid schema: {str(e)}")
        
        try:
            # Make GET request using CycleTLS to bypass bot detection
            content = self.client.get(url)
            
            if not content:
                return {
                    "success": False,
                    "error": "Failed to fetch content from URL. The server may be blocking requests."
                }
            # Convert HTML to text
            text_content = self._html_to_text(content)
            
            logger.info(f"Successfully fetched content from {url} ({len(text_content)} characters)")
            
            llm_config = LLMConfig(
                provider="openai/gpt-oss-120b",
                api_token=config.TOGETHER_API_KEY,
                base_url=config.TOGETHER_BASE_URL,
            )

            extraction_kwargs = {
                "llm_config": llm_config,
                "extraction_type": "schema",
                "instruction": extraction_instructions,
                "schema": output_schema,
                "overlap_rate": 0.1,
                "chunk_token_threshold": 10000,
                "apply_chunking": True,
                "input_format": "markdown",
                "reasoning_effort": "low",
            }

            llm_strategy = LLMExtractionStrategy(**extraction_kwargs)

            # Split the text content into chunks of 6000 characters each with an overlap of 1000 characters
            chunk_size = 6000
            overlap = 1000
            chunks = [
                text_content[i:i + chunk_size]
                for i in range(0, len(text_content), chunk_size - overlap)
            ]
            
            extracted_data = llm_strategy.run(url=url, sections=chunks)
            
            result = {
                "success": True,
                "url": url,
                "message": f"Successfully fetched and processed content from {url}",
                "data": extracted_data
            }
        
            return result
            
        except Exception as e:
            logger.error(f"Unexpected error fetching URL {url}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
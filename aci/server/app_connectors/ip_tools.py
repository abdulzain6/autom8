import whois
import dns.resolver
import geocoder
import requests
import ssl
import socket
import time
import ipaddress
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any

from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

logger = get_logger(__name__)

# Known Docker Swarm services that should be explicitly BLOCKED
DISALLOWED_DOCKER_SERVICES = {
    'caddy', 'server', 'huey_worker', 'livekit', 'voice_agent',
    'gotenberg', 'code-executor', 'searxng', 'cycletls-server',
    'steel-browser-api', 'headless-browser', 'local-proxy',
    'postgres', 'redis'
}


class IpTools(AppConnectorBase):
    """
    A connector for performing IP address and DNS-related lookups.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """
        Initializes the IPTools connector.
        """
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        logger.info("IPTools connector initialized.")

    def _before_execute(self) -> None:
        pass

    def _is_private_or_reserved_ip(self, ip_str: str) -> bool:
        """
        Checks if an IP address is private, reserved, or in a restricted range.
        
        Args:
            ip_str: IP address string to check
            
        Returns:
            True if the IP should be blocked, False if it's safe to access
        """
        try:
            ip = ipaddress.ip_address(ip_str)
            
            # Block private networks
            if ip.is_private:
                return True
                
            # Block reserved ranges
            if ip.is_reserved:
                return True
                
            # Block loopback
            if ip.is_loopback:
                return True
                
            # Block link-local
            if ip.is_link_local:
                return True
                
            # Block multicast
            if ip.is_multicast:
                return True
                
            # Block unspecified (0.0.0.0 or ::)
            if ip.is_unspecified:
                return True
                
            # Additional explicit blocks for common internal ranges
            if isinstance(ip, ipaddress.IPv4Address):
                # Block additional RFC 1918 ranges and other internal ranges
                blocked_networks = [
                    ipaddress.IPv4Network('10.0.0.0/8'),      # Private Class A
                    ipaddress.IPv4Network('172.16.0.0/12'),   # Private Class B  
                    ipaddress.IPv4Network('192.168.0.0/16'),  # Private Class C
                    ipaddress.IPv4Network('127.0.0.0/8'),     # Loopback
                    ipaddress.IPv4Network('169.254.0.0/16'),  # Link-local
                    ipaddress.IPv4Network('224.0.0.0/4'),     # Multicast
                    ipaddress.IPv4Network('240.0.0.0/4'),     # Reserved
                    ipaddress.IPv4Network('0.0.0.0/8'),       # "This network"
                ]
                
                for network in blocked_networks:
                    if ip in network:
                        return True
                        
            return False
            
        except ValueError:
            # If we can't parse the IP, block it for safety
            return True

    def _resolve_and_validate_hostname(self, hostname: str) -> str:
        """
        Resolves a hostname to IP and validates it's not internal.
        
        Args:
            hostname: Hostname to resolve and validate
            
        Returns:
            The resolved IP address if safe
            
        Raises:
            Exception: If hostname resolves to private/internal IP
        """
        try:
            # Remove protocol and path if present
            if '://' in hostname:
                hostname = urlparse(hostname).netloc or hostname
                
            # Extract hostname without port
            if ':' in hostname and not hostname.startswith('['):
                hostname = hostname.split(':')[0]
                
            # Resolve hostname to IP
            resolved_ip = socket.gethostbyname(hostname)
            
            # Check if resolved IP is private/internal
            if self._is_private_or_reserved_ip(resolved_ip):
                raise Exception(f"Access denied: {hostname} resolves to internal/private IP {resolved_ip}")
                
            return resolved_ip
            
        except socket.gaierror as e:
            raise Exception(f"Could not resolve hostname {hostname}: {str(e)}")
        except Exception as e:
            if "Access denied" in str(e):
                raise
            raise Exception(f"Hostname validation failed for {hostname}: {str(e)}")

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

    def get_ip_info(self, ip_address: str) -> Dict[str, Any]:
        """
        Retrieves geolocation and WHOIS information for a given IP address.

        Args:
            ip_address: The IP address to look up (e.g., '8.8.8.8').

        Returns:
            A dictionary containing location and ownership details.
        """
        self._before_execute()
        logger.info(f"Fetching info for IP address: {ip_address}")

        # Validate IP is not private/internal
        if self._is_private_or_reserved_ip(ip_address):
            raise Exception(f"Access denied: Cannot lookup private/internal IP address {ip_address}")

        try:
            # Geolocation lookup
            geo = geocoder.ip(ip_address)
            location_info = (
                geo.json
                if geo.ok
                else {"error": "Could not retrieve geolocation data."}
            )

            # WHOIS lookup
            try:
                whois_info = str(whois.whois(ip_address))
            except Exception as e:
                whois_info = {"error": f"WHOIS lookup failed: {str(e)}"}

            return {
                "ip_address": ip_address,
                "geolocation": location_info,
                "whois": whois_info,
            }
        except Exception as e:
            logger.error(
                f"An unexpected error occurred in get_ip_info for {ip_address}: {e}"
            )
            raise Exception(
                f"Failed to get information for IP {ip_address}: {e}"
            ) from e

    def dns_lookup(self, domain: str, record_types: List[str]) -> Dict[str, Any]:
        """
        Performs DNS lookups for a given domain and specified record types.

        Args:
            domain: The domain name to query (e.g., 'google.com').
            record_types: A list of DNS record types to look up (e.g., ['A', 'MX', 'TXT']).

        Returns:
            A dictionary with the results for each requested record type.
        """
        self._before_execute()
        logger.info(
            f"Performing DNS lookup for domain: {domain} (Types: {record_types})"
        )

        results = {"domain": domain, "records": {}}
        valid_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME"]

        for record_type in record_types:
            if record_type.upper() not in valid_types:
                results["records"][record_type] = ["Invalid record type requested."]
                continue

            try:
                answers = dns.resolver.resolve(domain, record_type.upper())
                results["records"][record_type] = [str(rdata) for rdata in answers]
            except dns.resolver.NoAnswer:
                results["records"][record_type] = []
            except dns.resolver.NXDOMAIN:
                results["records"][record_type] = [f"Domain '{domain}' does not exist."]
                break  # Stop if the domain doesn't exist
            except Exception as e:
                logger.error(f"DNS lookup for {record_type} on {domain} failed: {e}")
                results["records"][record_type] = [f"An error occurred: {str(e)}"]

        return results

    def check_website_status(self, url: str, timeout: int = 10) -> Dict[str, Any]:
        """
        Checks the HTTP status and response time of a website.

        Args:
            url: The website URL to check (e.g., 'https://example.com').
            timeout: Request timeout in seconds (default: 10).

        Returns:
            A dictionary containing status code, response time, and availability info.
        """
        self._before_execute()
        logger.info(f"Checking website status for: {url}")

        # Comprehensive URL security validation
        try:
            self._validate_url_security(url)
        except ValueError as e:
            return {
                "url": url,
                "status": "BLOCKED",
                "error": f"URL security validation failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }

        try:
            start_time = time.time()
            response = requests.get(url, timeout=timeout, allow_redirects=True)
            end_time = time.time()
            
            response_time = round((end_time - start_time) * 1000, 2)  # Convert to milliseconds
            
            return {
                "url": url,
                "status_code": response.status_code,
                "status": "UP" if 200 <= response.status_code < 400 else "DOWN",
                "response_time_ms": response_time,
                "response_size_bytes": len(response.content),
                "final_url": response.url,
                "headers": dict(response.headers),
                "timestamp": datetime.now().isoformat()
            }
        except requests.exceptions.Timeout:
            return {
                "url": url,
                "status": "TIMEOUT",
                "error": f"Request timed out after {timeout} seconds",
                "timestamp": datetime.now().isoformat()
            }
        except requests.exceptions.ConnectionError:
            return {
                "url": url,
                "status": "CONNECTION_ERROR",
                "error": "Could not connect to the website",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Website check failed for {url}: {e}")
            return {
                "url": url,
                "status": "ERROR",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def check_ssl_certificate(self, hostname: str, port: int = 443) -> Dict[str, Any]:
        """
        Checks SSL certificate information for a domain.

        Args:
            hostname: The hostname to check (e.g., 'example.com').
            port: The port to connect to (default: 443).

        Returns:
            A dictionary containing SSL certificate details and expiration info.
        """
        self._before_execute()
        logger.info(f"Checking SSL certificate for: {hostname}:{port}")

        # Validate hostname doesn't resolve to internal IPs
        try:
            self._resolve_and_validate_hostname(hostname)
        except Exception as e:
            return {
                "hostname": hostname,
                "port": port,
                "valid": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

        try:
            # Create SSL context
            context = ssl.create_default_context()
            
            # Connect and get certificate
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
            
            if not cert:
                return {
                    "hostname": hostname,
                    "port": port,
                    "valid": False,
                    "error": "No certificate found",
                    "timestamp": datetime.now().isoformat()
                }
            
            # Parse certificate dates
            not_before_str = cert.get('notBefore', '')
            not_after_str = cert.get('notAfter', '')
            
            if not_before_str and not_after_str and isinstance(not_before_str, str) and isinstance(not_after_str, str):
                not_before = datetime.strptime(not_before_str, '%b %d %H:%M:%S %Y %Z')
                not_after = datetime.strptime(not_after_str, '%b %d %H:%M:%S %Y %Z')
                days_until_expiry = (not_after - datetime.now()).days
            else:
                not_before = None
                not_after = None
                days_until_expiry = -1
            
            # Extract certificate details safely
            subject_dict = {}
            if cert.get('subject'):
                for item in cert['subject']:
                    if isinstance(item, tuple) and len(item) >= 2:
                        for pair in item:
                            if isinstance(pair, tuple) and len(pair) >= 2:
                                subject_dict[pair[0]] = pair[1]
            
            issuer_dict = {}
            if cert.get('issuer'):
                for item in cert['issuer']:
                    if isinstance(item, tuple) and len(item) >= 2:
                        for pair in item:
                            if isinstance(pair, tuple) and len(pair) >= 2:
                                issuer_dict[pair[0]] = pair[1]
            
            return {
                "hostname": hostname,
                "port": port,
                "valid": True,
                "subject_common_name": subject_dict.get('commonName', 'N/A'),
                "issuer": issuer_dict.get('organizationName', issuer_dict.get('commonName', 'N/A')),
                "issued_date": not_before.isoformat() if not_before else 'N/A',
                "expiry_date": not_after.isoformat() if not_after else 'N/A',
                "days_until_expiry": days_until_expiry,
                "expires_soon": days_until_expiry <= 30 and days_until_expiry >= 0,
                "serial_number": cert.get('serialNumber', 'N/A'),
                "version": str(cert.get('version', 'N/A')),
                "timestamp": datetime.now().isoformat()
            }
        except socket.gaierror:
            return {
                "hostname": hostname,
                "port": port,
                "valid": False,
                "error": "Hostname could not be resolved",
                "timestamp": datetime.now().isoformat()
            }
        except socket.timeout:
            return {
                "hostname": hostname,
                "port": port,
                "valid": False,
                "error": "Connection timed out",
                "timestamp": datetime.now().isoformat()
            }
        except ssl.SSLError as e:
            return {
                "hostname": hostname,
                "port": port,
                "valid": False,
                "error": f"SSL Error: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"SSL certificate check failed for {hostname}: {e}")
            return {
                "hostname": hostname,
                "port": port,
                "valid": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def ping_host(self, hostname: str, count: int = 4) -> Dict[str, Any]:
        """
        Performs a ping test to check host reachability and response times.

        Args:
            hostname: The hostname or IP to ping.
            count: Number of ping attempts (default: 4).

        Returns:
            A dictionary containing ping statistics and response times.
        """
        self._before_execute()
        logger.info(f"Pinging host: {hostname} ({count} times)")

        # Validate target is not internal/private
        try:
            # Check if it's already an IP address
            try:
                ipaddress.ip_address(hostname)
                if self._is_private_or_reserved_ip(hostname):
                    raise Exception(f"Access denied: Cannot ping private/internal IP {hostname}")
            except ValueError:
                # It's a hostname, resolve and validate
                self._resolve_and_validate_hostname(hostname)
        except Exception as e:
            return {
                "hostname": hostname,
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

        import subprocess
        import platform

        try:
            # Determine ping command based on OS
            param = "-n" if platform.system().lower() == "windows" else "-c"
            command = ["ping", param, str(count), hostname]

            # Execute ping command
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30
            )

            output = result.stdout
            success = result.returncode == 0

            # Parse basic statistics (simplified)
            packet_loss = "0%" if success else "100%"
            if "% packet loss" in output or "% loss" in output:
                import re
                loss_match = re.search(r'(\d+)%.*loss', output)
                if loss_match:
                    packet_loss = f"{loss_match.group(1)}%"

            return {
                "hostname": hostname,
                "success": success,
                "packet_loss": packet_loss,
                "output": output,
                "error": result.stderr if result.stderr else None,
                "timestamp": datetime.now().isoformat()
            }
        except subprocess.TimeoutExpired:
            return {
                "hostname": hostname,
                "success": False,
                "error": "Ping command timed out",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Ping failed for {hostname}: {e}")
            return {
                "hostname": hostname,
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def get_domain_info(self, domain: str) -> Dict[str, Any]:
        """
        Retrieves WHOIS registration information for a given domain name.

        Args:
            domain: The domain name to look up (e.g., 'example.com').

        Returns:
            A dictionary containing domain registration details.
        """
        self._before_execute()
        logger.info(f"Fetching WHOIS info for domain: {domain}")

        # Basic domain validation
        if not domain or not isinstance(domain, str):
            raise Exception("Domain must be a non-empty string")

        # Remove protocol if present
        domain = domain.replace('http://', '').replace('https://', '').replace('www.', '')
        if '/' in domain:
            domain = domain.split('/')[0]

        try:
            # WHOIS lookup for domain
            whois_info = whois.whois(domain)

            # Convert to dict if it's not already
            if not isinstance(whois_info, dict):
                whois_info = dict(whois_info) if hasattr(whois_info, '__dict__') else {"raw": str(whois_info)}

            # Calculate domain age if creation date is available
            domain_age_days = None
            domain_age_years = None
            if whois_info.get('creation_date'):
                creation_date = whois_info['creation_date']
                if isinstance(creation_date, list):
                    creation_date = creation_date[0]
                if isinstance(creation_date, str):
                    try:
                        creation_date = datetime.fromisoformat(creation_date.replace('Z', '+00:00'))
                    except:
                        try:
                            creation_date = datetime.strptime(creation_date, '%Y-%m-%d %H:%M:%S')
                        except:
                            creation_date = None

                if creation_date and isinstance(creation_date, datetime):
                    domain_age_days = (datetime.now() - creation_date).days
                    domain_age_years = round(domain_age_days / 365.25, 1)

            return {
                "domain": domain,
                "whois": whois_info,
                "domain_age_days": domain_age_days,
                "domain_age_years": domain_age_years,
                "is_recent": domain_age_days is not None and domain_age_days < 180,  # Less than 6 months
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(
                f"An unexpected error occurred in get_domain_info for {domain}: {e}"
            )
            return {
                "domain": domain,
                "error": f"WHOIS lookup failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }

# cycle_tls_client.py

import requests
from typing import Optional, Dict, Any
from urllib.parse import urlparse

# --- HTTP Client for Scraping ---

DEFAULT_TLS_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Special headers for quote/content sites
QUOTE_SITE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "sec-ch-ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

class CycleTlsServerClient:
    """
    A client to interact with a CycleTLS server for making HTTP requests.
    This class encapsulates the logic for sending requests with specific TLS/JA3
    fingerprints to avoid being blocked by web servers.
    """
    def __init__(self, server_url: str, proxy: Optional[str] = None):
        """
        Initializes the CycleTlsServerClient.

        Args:
            server_url: The base URL of the running CycleTLS server instance.
            proxy: Optional proxy string to be used for requests.
        """
        self.server_url = server_url
        self.default_args = {
            "ja3": "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,17513-11-45-23-27-51-5-18-10-65037-13-43-35-16-65281-0,29-23-24,0",
            "userAgent": DEFAULT_TLS_HEADERS["user-agent"],
            "headers": DEFAULT_TLS_HEADERS,
            "proxy": proxy,
        }

    def _get_headers_for_url(self, url: str) -> Dict[str, str]:
        """Choose appropriate headers based on the URL domain."""
        domain = urlparse(url).netloc.lower()
        
        # Use special headers for quote sites and similar content sites
        if any(site in domain for site in ['brainyquote.com', 'quotes.com', 'goodreads.com', 'quotegarden.com']):
            headers = QUOTE_SITE_HEADERS.copy()
            headers["authority"] = domain
            return headers
        
        # For other sites, use default headers with dynamic authority
        headers = DEFAULT_TLS_HEADERS.copy()
        headers["authority"] = domain
        return headers

    def get(self, url: str) -> str:
        """
        Sends a GET request to the specified URL via the CycleTLS server.

        Args:
            url: The URL to fetch.

        Returns:
            The HTML body of the response as a string, or an empty string on failure.
        """
        # Get appropriate headers for this URL
        headers = self._get_headers_for_url(url)
        
        payload = {
            "url": url, 
            "args": {
                **self.default_args, 
                "headers": headers,
                "body": ""
            }
        }
        
        try:
            # The server URL should be the endpoint that processes requests, e.g., http://localhost:8080/request
            response = requests.post(self.server_url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("body", "")
        except requests.RequestException as e:
            # In a real application, you'd use a logger here.
            print(f"Error making request to {url} via CycleTLS server: {e}")
            return ""


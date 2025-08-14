# cycle_tls_client.py

import requests
from typing import Optional, Dict, Any

# --- HTTP Client for Scraping ---

DEFAULT_TLS_HEADERS = {
    "authority": "www.amazon.com",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="116", "Not)A;Brand";v="24", "Google Chrome";v="116"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
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

    def get(self, url: str) -> str:
        """
        Sends a GET request to the specified URL via the CycleTLS server.

        Args:
            url: The URL to fetch.

        Returns:
            The HTML body of the response as a string, or an empty string on failure.
        """
        payload = {"url": url, "args": {**self.default_args, "body": ""}}
        try:
            # The server URL should be the endpoint that processes requests, e.g., http://localhost:8080/request
            response = requests.post(self.server_url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("body", "")
        except requests.RequestException as e:
            # In a real application, you'd use a logger here.
            print(f"Error making request to {url} via CycleTLS server: {e}")
            return ""


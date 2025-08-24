import requests
from typing import Dict, Any, Optional
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import SEARXNG_INSTANCE_URL

logger = get_logger(__name__)


class Searxng(AppConnectorBase):
    """
    An AI-friendly connector for the SearXNG metasearch engine.
    This allows agents to perform general and category-specific web searches.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """
        Initializes the SearxngConnector.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        self.base_url = SEARXNG_INSTANCE_URL.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ACI-SearxngConnector/1.0"})
        logger.info(f"SearxngConnector initialized for instance: {self.base_url}")

    def _before_execute(self) -> None:
        return None

    def _search(
        self,
        query: str,
        categories: str,
        page_number: int = 1,
        time_range: Optional[str] = None,
        safesearch: int = 1,
    ) -> Dict[str, Any]:
        """
        Internal search method to query the SearXNG API.

        Args:
            query: The search term.
            categories: The SearXNG categories to search in (e.g., 'general', 'images').
            page_number: The page number of the results.
            time_range: Time filter (e.g., 'day', 'week', 'month', 'year').
            safesearch: Safe search level (0=off, 1=moderate, 2=strict).

        Returns:
            A dictionary containing the parsed JSON response from the API.
        """
        params = {
            "q": query,
            "categories": categories,
            "pageno": page_number,
            "safesearch": safesearch,
            "format": "json",  # Crucial for getting a machine-readable response
        }
        if time_range:
            params["time_range"] = time_range

        try:
            response = self.session.get(
                f"{self.base_url}/search", params=params, timeout=15
            )
            # Raise an exception for HTTP errors (e.g., 404, 500)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"SearXNG API request failed: {e}")
            raise Exception(
                f"Failed to communicate with the SearXNG instance: {e}"
            ) from e

    # --- AI-Agent Friendly Methods ---

    def search_general(
        self, query: str, time_range: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs a general web search.

        Args:
            query: The search term.
            time_range: Optional time filter ('day', 'week', 'month', 'year').

        Returns:
            Search results including web pages, infoboxes, and answers.
        """
        logger.info(f"Performing general search for: '{query}'")
        return self._search(query, categories="general", time_range=time_range)

    def search_images(self, query: str) -> Dict[str, Any]:
        """
        Performs an image-only search.

        Args:
            query: The search term for images.

        Returns:
            A list of image results.
        """
        logger.info(f"Performing image search for: '{query}'")
        return self._search(query, categories="images")

    def search_videos(self, query: str) -> Dict[str, Any]:
        """
        Performs a video-only search.

        Args:
            query: The search term for videos.

        Returns:
            A list of video results.
        """
        logger.info(f"Performing video search for: '{query}'")
        return self._search(query, categories="videos")

    def search_news(
        self, query: str, time_range: Optional[str] = "month"
    ) -> Dict[str, Any]:
        """
        Performs a news-only search.

        Args:
            query: The search term for news articles.
            time_range: Optional time filter ('day', 'week', 'month', 'year'). Defaults to 'month'.

        Returns:
            A list of news article results.
        """
        logger.info(f"Performing news search for: '{query}'")
        return self._search(query, categories="news", time_range=time_range)


# --- Main function for testing ---
if __name__ == "__main__":
    print("--- SearxngConnector Test ---")

    mock_scheme = NoAuthScheme()
    mock_creds = NoAuthSchemeCredentials()

    try:
        connector = Searxng(
            linked_account=None,  # type: ignore
            security_scheme=mock_scheme,
            security_credentials=mock_creds,
        )
        print("✅ Connector initialized successfully.")

        # 1. Test general search
        print("\n--- Testing search_general(query='Python programming language') ---")
        general_results = connector.search_general(query="Python programming language")
        if general_results.get("results"):
            first_result = general_results["results"][0]
            print(
                f"✅ General search successful. Found {len(general_results['results'])} results."
            )
            print(f"  Top result title: '{first_result.get('title')}'")
            print(f"  URL: {first_result.get('url')}")
        else:
            print("❌ No general results found.")

        # 2. Test image search
        print("\n--- Testing search_images(query='Golden Gate Bridge') ---")
        image_results = connector.search_images(query="Golden Gate Bridge")
        if image_results.get("results"):
            first_image = image_results["results"][0]
            print(
                f"✅ Image search successful. Found {len(image_results['results'])} images."
            )
            print(f"  Top image source: '{first_image.get('img_src')}'")
        else:
            print("❌ No image results found.")

    except Exception as e:
        print(f"\n❌ An error occurred during testing: {e}")

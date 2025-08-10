from aci.common.db.sql_models import LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from pygooglenews import GoogleNews as PyGoogleNews
from typing import List, Dict, Any, Optional

from aci.common.logging_setup import get_logger

logger = get_logger(__name__)


class GoogleNews(AppConnectorBase):
    """
    Connector for fetching news articles from Google News using the pygooglenews library.
    This provides key news data for an AI agent without requiring an API key.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
    ):
        """
        Initializes the GoogleNews connector.
        """
        super().__init__(linked_account, security_scheme, security_credentials)
        logger.info("GoogleNews connector initialized.")

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic. Not required for pygooglenews.
        """
        pass

    def _format_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Helper function to parse the raw feed entries into a clean list of dictionaries.
        """
        formatted_news = []
        for entry in entries:
            formatted_news.append(
                {
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "published": entry.get("published"),
                    "summary": entry.get("summary"),
                    "source": entry.get("source", {}).get("title"),
                }
            )
        return formatted_news

    def get_top_headlines(
        self, country: str = "PK", lang: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Fetches the top news headlines for a specific country and language.

        Args:
            country: The two-letter ISO 3166-1 code for the country (e.g., 'PK' for Pakistan, 'US' for USA).
            lang: The two-letter ISO 639-1 code for the language (e.g., 'en' for English, 'ur' for Urdu).

        Returns:
            A list of dictionaries, each representing a top news article.
        """
        logger.info(f"Fetching top headlines for country='{country}', lang='{lang}'")
        try:
            gn = PyGoogleNews(lang=lang, country=country)
            stories = gn.top_news()
            return self._format_entries(stories["entries"]) # type: ignore
        except Exception as e:
            logger.error(f"Failed to get top headlines: {e}")
            raise Exception(f"Failed to retrieve top headlines: {e}") from e


    def search_topic(
        self, query: str, period: Optional[str] = "7d", country: str = "US", lang: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Searches for news articles related to a specific topic within a given time frame.

        Args:
            query: The search term or topic (e.g., "artificial intelligence").
            period: The time frame to search within (e.g., '7d' for 7 days, '1m' for 1 month, '1y' for 1 year).
            country: The two-letter ISO 3166-1 code for the country to search in (e.g., 'PK', 'US').
            lang: The two-letter ISO 639-1 code for the language (e.g., 'en', 'ur').

        Returns:
            A list of dictionaries, each representing a news article related to the topic.
        """
        self._before_execute()
        logger.info(f"Searching for topic: '{query}' in country '{country}' within period: '{period}'")
        try:
            # --- THE FIX IS HERE ---
            # Now uses the provided lang and country parameters instead of being locked to US/en.
            gn = PyGoogleNews(lang=lang, country=country)
            search_result = gn.search(query, when=period)
            return self._format_entries(search_result["entries"]) # type: ignore
        except Exception as e:
            logger.error(f"Failed to search for topic '{query}': {e}")
            raise Exception(f"Failed to search for topic '{query}': {e}") from e

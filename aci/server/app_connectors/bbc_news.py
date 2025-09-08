import bbc_feeds
import requests
import html2text
from typing import Iterable, Optional, List, Dict, Any, cast
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import HTTP_PROXY

logger = get_logger(__name__)


class BbcNews(AppConnectorBase):
    """
    A reliable connector for fetching news articles from BBC News
    using the bbc-feeds PyPI package.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the BBCNewsConnector."""
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        self.news_client = bbc_feeds.news()
        self.http_session = requests.Session()
        
        # Configure proxy if available
        if HTTP_PROXY:
            proxy_config = {
                'http': HTTP_PROXY,
                'https': HTTP_PROXY
            }
            self.http_session.proxies.update(proxy_config)
            logger.info(f"BBC News connector configured with proxy: {HTTP_PROXY}")
        
        self.html_converter = html2text.HTML2Text()
        logger.info("BBCNewsConnector initialized using bbc-feeds.")

    def _before_execute(self) -> None:
        pass

    def _format_story(self, story) -> Dict[str, Any]:
        """Helper to convert the library's story object into a clean dictionary."""
        return {
            "title": story.title,
            "summary": story.summary,
            "url": story.link,
            "published": story.published,
        }

    def get_top_headlines(self, limit: int = 10, edition: str = 'int') -> List[Dict[str, Any]]:
        """
        Fetches the top headline articles from the BBC News homepage.

        Args:
            limit: The maximum number of headlines to return.
            edition: The regional edition to use ('uk', 'us', or 'int' for international).
        """
        logger.info(f"Fetching top {limit} headlines for '{edition}' edition.")
        if HTTP_PROXY:
            logger.info(f"Using proxy for BBC News headlines request: {HTTP_PROXY}")
        try:
            stories = self.news_client.top_stories(limit=limit, edition=edition) # type: ignore
            return [self._format_story(story) for story in stories]
        except Exception as e:
            logger.error(f"Error fetching top headlines: {e}", exc_info=True)
            raise Exception(f"Failed to retrieve top headlines: {e}") from e

    def get_articles_by_category(self, category: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches the latest articles from a specific category (e.g., 'world', 'technology').

        Args:
            category: The news category to fetch articles from.
            limit: The maximum number of articles to return.
        """        
        category_method = getattr(self.news_client, category.lower(), None)
        if not callable(category_method):
            raise ValueError(f"Invalid category: '{category}'.")

        logger.info(f"Fetching {limit} articles from category '{category}'.")
        if HTTP_PROXY:
            logger.info(f"Using proxy for BBC News category request: {HTTP_PROXY}")
        try:
            stories = cast(Iterable, category_method(limit=limit))
            return [self._format_story(story) for story in stories]
        except Exception as e:
            logger.error(f"Error fetching articles for category '{category}': {e}", exc_info=True)
            raise Exception(f"Failed to retrieve articles for category '{category}': {e}") from e

    def get_article_content(self, url: str) -> Dict[str, Any]:
        """
        Fetches the HTML of a BBC News article and converts it to Markdown.

        Args:
            url: The full URL of the BBC News article to read.

        Returns:
            A dictionary containing the article's URL and its full content in Markdown format.
        """
        logger.info(f"Fetching and converting article content from: {url}")
        if HTTP_PROXY:
            logger.info(f"Using proxy for BBC News article content request: {HTTP_PROXY}")
        try:
            response = self.http_session.get(url, timeout=15)
            response.raise_for_status()
            
            # Convert the raw HTML to clean Markdown text
            markdown_content = self.html_converter.handle(response.text)
            
            return {
                "url": url,
                "markdown_content": markdown_content
            }
        except requests.RequestException as e:
            logger.error(f"Failed to fetch article HTML from {url}: {e}", exc_info=True)
            raise Exception(f"Could not retrieve article from URL: {e}") from e
        except Exception as e:
            logger.error(f"Failed to convert article to Markdown: {e}", exc_info=True)
            raise Exception(f"An error occurred during Markdown conversion: {e}") from e

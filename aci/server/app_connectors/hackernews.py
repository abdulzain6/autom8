import datetime
import requests
from typing import List, Dict, Any
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

logger = get_logger(__name__)


class Hackernews(AppConnectorBase):
    """
    Connector for fetching stories and user data from Hacker News using direct API calls.
    This provides structured data from Hacker News for an AI agent without requiring an API key.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """
        Initializes the HackerNewsConnector.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        self.api_base_url = "https://hacker-news.firebaseio.com/v0"
        logger.info("HackerNews connector initialized (direct API mode).")

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic. Not required for this connector.
        """
        pass

    def _format_story(self, story_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Helper function to parse the raw story JSON data into a clean dictionary.
        """
        if not story_data:
            return {}

        # Safely convert Unix timestamp to ISO 8601 string
        post_time = story_data.get('time')
        iso_time = datetime.datetime.fromtimestamp(post_time).isoformat() if post_time else None

        return {
            "id": story_data.get('id'),
            "title": story_data.get('title'),
            "url": story_data.get('url'),
            "author": story_data.get('by'),
            "score": story_data.get('score', 0),
            "comments_count": story_data.get('descendants', 0),
            "time_posted": iso_time,
            "type": story_data.get('type'),
            "hacker_news_url": f"https://news.ycombinator.com/item?id={story_data.get('id')}"
        }

    def get_top_stories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches a list of top stories from Hacker News, returning full details for each.
        """
        self._before_execute()
        logger.info(f"Fetching {limit} 'top' stories from Hacker News API.")
        try:
            # 1. Get the list of top story IDs
            top_stories_url = f"{self.api_base_url}/topstories.json"
            response = requests.get(top_stories_url, timeout=10)
            response.raise_for_status()  # Raise an exception for bad status codes
            story_ids = response.json()

            # 2. Fetch details for each story up to the limit
            stories = []
            for story_id in story_ids[:limit]:
                item_url = f"{self.api_base_url}/item/{story_id}.json"
                item_response = requests.get(item_url, timeout=10)
                item_response.raise_for_status()
                story_data = item_response.json()
                if story_data and story_data.get("type") == "story":
                    stories.append(self._format_story(story_data))
            
            return stories
        except requests.RequestException as e:
            logger.error(f"API request failed while getting stories: {e}")
            raise Exception(f"Failed to retrieve Hacker News stories from API: {e}") from e

    def get_user_details(self, username: str) -> Dict[str, Any]:
        """
        Fetches public details for a specific Hacker News user.
        """
        self._before_execute()
        logger.info(f"Fetching details for Hacker News user: '{username}'")
        try:
            user_url = f"{self.api_base_url}/user/{username}.json"
            response = requests.get(user_url, timeout=10)
            response.raise_for_status()
            user_data = response.json()

            if not user_data:
                raise Exception(f"User '{username}' not found.")

            # Safely convert Unix timestamp to ISO 8601 string
            created_time = user_data.get('created')
            iso_time = datetime.datetime.fromtimestamp(created_time).isoformat() if created_time else None

            return {
                "username": user_data.get('id'),
                "karma": user_data.get('karma'),
                "created_at": iso_time,
                "about": user_data.get('about', 'No description provided.'),
                "profile_url": f"https://news.ycombinator.com/user?id={user_data.get('id')}"
            }
        except requests.RequestException as e:
            logger.error(f"API request failed for user '{username}': {e}")
            raise Exception(f"Failed to get user details for '{username}': {e}") from e

    def get_stories(self, story_type: str = "top", limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches a list of stories from Hacker News based on type.

        Args:
            story_type: Can be 'top', 'new', 'best', 'ask', 'show', or 'job'.
            limit: The maximum number of stories to return.
        """
        # Map the user-friendly type to the actual API endpoint name
        endpoint_map = {
            "top": "topstories",
            "new": "newstories",
            "best": "beststories",
            "ask": "askstories",
            "show": "showstories",
            "job": "jobstories"
        }

        story_endpoint = endpoint_map.get(story_type.lower())
        if not story_endpoint:
            raise ValueError(f"Invalid story_type: '{story_type}'.")

        logger.info(f"Fetching {limit} '{story_type}' stories from Hacker News API.")
        try:
            # Use the mapped endpoint in the URL
            stories_url = f"{self.api_base_url}/{story_endpoint}.json"
            response = requests.get(stories_url, timeout=10)
            response.raise_for_status()
            story_ids = response.json()

            stories = []
            for story_id in story_ids[:limit]:
                item_url = f"{self.api_base_url}/item/{story_id}.json"
                item_response = requests.get(item_url, timeout=10)
                item_response.raise_for_status()
                story_data = item_response.json()
                if story_data:
                    stories.append(self._format_story(story_data))
            
            return stories
        except requests.RequestException as e:
            logger.error(f"API request failed while getting stories: {e}")
            raise Exception(f"Failed to retrieve Hacker News stories from API: {e}") from e
        
    def search_stories(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches Hacker News using the official Algolia Search API.

        Args:
            query: The keyword or phrase to search for.

        Returns:
            A list of stories matching the search query.
        """
        logger.info(f"Searching for '{query}' using Algolia API.")
        search_url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": query,
            "tags": "story"  # To search only for stories, not comments
        }
        
        try:
            response = requests.get(search_url, params=params, timeout=10)
            response.raise_for_status()
            results = response.json()
            
            # The story data is in the 'hits' key of the response
            return results.get('hits', [])
            
        except requests.RequestException as e:
            logger.error(f"Algolia API search failed for query '{query}': {e}")
            raise Exception(f"Search failed for query '{query}': {e}") from e
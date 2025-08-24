from aci.common.db.sql_models import LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from the_python_bay import tpb
from typing import List, Dict, Any

from aci.common.logging_setup import get_logger

logger = get_logger(__name__)


class ThePirateBay(AppConnectorBase):
    """
    Connector for searching and retrieving data from The Pirate Bay using the the-python-bay library.
    This connector provides access to torrents without requiring an API key.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """
        Initializes the ThePirateBay connector.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        logger.info("ThePirateBay connector initialized.")

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic. Not required for this connector.
        """
        pass

    def _format_torrents(self, torrents: List[Any]) -> List[Dict[str, Any]]:
        """
        Helper function to parse the raw torrent objects into a clean list of dictionaries.

        Args:
            torrents: A list of Torrent objects from the the-python-bay library.

        Returns:
            A list of dictionaries, where each dictionary represents a torrent.
        """
        formatted_torrents = []
        for torrent in torrents:
            # The .to_dict property returns a dictionary with all torrent attributes
            torrent_data = torrent.to_dict
            # The magnet link is included in the dictionary and is clickable
            formatted_torrents.append(torrent_data)
        return formatted_torrents

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches The Pirate Bay for torrents related to a specific query.

        Args:
            query: The search term or topic (e.g., "ubuntu").

        Returns:
            A list of dictionaries, each representing a torrent that matches the query.
        """
        logger.info(f"Searching The Pirate Bay for query: '{query}'")
        try:
            results = tpb.search(query)
            return self._format_torrents(results)
        except Exception as e:
            logger.error(f"Failed to search for query '{query}': {e}")
            raise Exception(f"Failed to perform search for '{query}': {e}") from e

    def get_top_movies(self) -> List[Dict[str, Any]]:
        """
        Retrieves the current top 100 movies from The Pirate Bay.

        Returns:
            A list of dictionaries, each representing a top movie torrent.
        """
        logger.info("Fetching top movies from The Pirate Bay.")
        try:
            results = tpb.top_movies()
            return self._format_torrents(results)
        except Exception as e:
            logger.error(f"Failed to get top movies: {e}")
            raise Exception(f"Failed to retrieve top movies: {e}") from e

    def get_top_tv_shows(self) -> List[Dict[str, Any]]:
        """
        Retrieves the current top 100 TV shows from The Pirate Bay.

        Returns:
            A list of dictionaries, each representing a top TV show torrent.
        """
        logger.info("Fetching top TV shows from The Pirate Bay.")
        try:
            results = tpb.top_tv()
            return self._format_torrents(results)
        except Exception as e:
            logger.error(f"Failed to get top TV shows: {e}")
            raise Exception(f"Failed to retrieve top TV shows: {e}") from e
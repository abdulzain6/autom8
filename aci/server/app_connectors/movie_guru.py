from typing import Optional, List, Dict, Any
from imdb import Cinemagoer, IMDbError
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
import os

logger = get_logger(__name__)


class MovieGuru(AppConnectorBase):
    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        self.ia = Cinemagoer()
        self._imdb_base = os.getenv("IMDB_BASE_URL", "https://www.imdb.com")
        logger.info("MovieGuru initialized.")

    def _before_execute(self) -> None:
        pass

    def search_movie(self, title: str, limit: int = 5) -> List[Dict[str, Any]]:
        self._before_execute()
        logger.info(f"Searching for '{title}' (limit={limit})")
        try:
            results = self.ia.search_movie(title)[: max(limit, 0)]
            summaries = []
            for m in results:
                try:
                    self.ia.update(m, "main")
                except IMDbError:
                    pass
                summaries.append(self._movie_summary(m))
            return summaries
        except IMDbError as e:
            logger.error(f"Search error: {e}", exc_info=True)
            raise

    def get_top_250_movies(self, limit: int = 10) -> List[Dict[str, Any]]:
        self._before_execute()
        logger.info(f"Fetching Top 250 (limit={limit})")
        try:
            results = self.ia.get_top250_movies() or []
            if not results:
                logger.warning("Empty Top 250, falling back to popular100")
                results = self.ia.get_popular100_movies() or []
            if not results:
                raise RuntimeError("No movies found for top or popular lists")
            return [self._movie_summary(m) for m in results[: max(limit, 0)]]
        except IMDbError as e:
            logger.error(f"Error fetching top movies: {e}", exc_info=True)
            raise

    def get_upcoming_movies(self, limit: int = 10) -> List[Dict[str, Any]]:
        self._before_execute()
        logger.info(f"Fetching upcoming (popular100) (limit={limit})")
        try:
            results = self.ia.get_popular100_movies()[: max(limit, 0)]
            return [self._movie_summary(m) for m in results]
        except IMDbError as e:
            logger.error(f"Error fetching upcoming movies: {e}", exc_info=True)
            raise

    def get_movie_details(self, movie_id: str, info_sets: Optional[List[str]] = None) -> Dict[str, Any]:
        self._before_execute()
        logger.info(f"Fetching details for ID {movie_id} (info_sets={info_sets})")
        try:
            movie = self.ia.get_movie(str(movie_id))
            if not movie:
                raise ValueError(f"Movie {movie_id} not found")
            sets_to_fetch = info_sets or ["main"]
            for info in sets_to_fetch:
                try:
                    self.ia.update(movie, info)
                except IMDbError:
                    logger.debug(f"Info-set '{info}' failed, skipping")
            return self._movie_full(movie)
        except IMDbError as e:
            logger.error(f"Detail error: {e}", exc_info=True)
            raise

    def _movie_summary(self, movie: Any) -> Dict[str, Any]:
        mid = movie.movieID
        return {
            "id": mid,
            "imdb_id": f"tt{mid}" if mid else None,
            "imdb_url": f"{self._imdb_base}/title/tt{mid}/" if mid else None,
            "title": movie.get("title"),
            "year": movie.get("year"),
            "kind": movie.get("kind"),
            "rating": movie.get("rating"),
            "votes": movie.get("votes"),
            "cover_url": movie.get("cover url"),
        }

    def _movie_full(self, movie: Any) -> Dict[str, Any]:
        # Expose full dict, with keys fetched by update()
        mid = movie.movieID
        full = {
            "id": mid,
            "imdb_id": f"tt{mid}" if mid else None,
            "imdb_url": f"{self._imdb_base}/title/tt{mid}/" if mid else None,
        }
        full.update({k: movie.get(k) for k in movie.keys()})
        return full

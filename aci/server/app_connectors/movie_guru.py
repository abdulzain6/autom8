import os
import html2text
import re
import requests
from imdb import Cinemagoer, IMDbError
from typing import Optional, List, Dict, Any
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import CYCLE_TLS_SERVER_URL, HTTP_PROXY

try:
    from aci.server.cycletls_client import CycleTlsServerClient
    CYCLETLS_AVAILABLE = True
except ImportError:
    CYCLETLS_AVAILABLE = False
    logger = get_logger(__name__)
    logger.warning("CycleTLS client not available, using direct HTTP requests")

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
        
        # Configure Cinemagoer with proxy if available
        if HTTP_PROXY:
            # Cinemagoer expects a string proxy URL, not a dictionary
            self.ia = Cinemagoer(proxy=HTTP_PROXY)
            logger.info(f"MovieGuru configured with proxy: {HTTP_PROXY}")
        else:
            self.ia = Cinemagoer()
            logger.info("MovieGuru initialized without proxy.")
        
        # Set up HTTP client for web scraping fallback
        self.use_cycletls = False
        self.session = None
        
        if CYCLETLS_AVAILABLE:
            try:
                if HTTP_PROXY:
                    self.client = CycleTlsServerClient(server_url=CYCLE_TLS_SERVER_URL, proxy=HTTP_PROXY)
                else:
                    self.client = CycleTlsServerClient(server_url=CYCLE_TLS_SERVER_URL)
                self.use_cycletls = True
                logger.info("MovieGuru using CycleTLS for web scraping")
            except Exception as e:
                logger.warning(f"CycleTLS initialization failed: {e}, falling back to requests")
                self.use_cycletls = False
        
        # Always set up requests session as fallback
        self.session = requests.Session()
        if HTTP_PROXY:
            proxy_config = {
                'http': HTTP_PROXY,
                'https': HTTP_PROXY
            }
            self.session.proxies.update(proxy_config)
        
        # Set a user agent to avoid being blocked
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        if not self.use_cycletls:
            logger.info("MovieGuru using direct HTTP requests for web scraping")
            
        self._imdb_base = os.getenv("IMDB_BASE_URL", "https://www.imdb.com")
        logger.info("MovieGuru initialized with web scraping fallback.")

    def _get_web_content(self, url: str) -> Optional[str]:
        """Get web content using CycleTLS or direct HTTP as fallback."""
        try:
            if self.use_cycletls and hasattr(self, 'client'):
                content = self.client.get(url)
                if content:
                    return content
                else:
                    logger.warning("CycleTLS request failed, trying direct HTTP")
            
            # Use direct HTTP (either as primary method or fallback)
            if self.session:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return response.text
            else:
                logger.error("No HTTP client available")
                return None
                
        except Exception as e:
            logger.error(f"Failed to fetch content from {url}: {e}")
            return None

    def _before_execute(self) -> None:
        pass

    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to clean text using html2text."""
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

    def search_movie(self, title: str, limit: int = 5) -> List[Dict[str, Any]]:
        self._before_execute()
        logger.info(f"Searching for '{title}' (limit={limit})")
        if HTTP_PROXY:
            logger.info(f"Using proxy for MovieGuru search request: {HTTP_PROXY}")
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
        if HTTP_PROXY:
            logger.info(f"Using proxy for MovieGuru Top 250 request: {HTTP_PROXY}")
        try:
            # Try the original Cinemagoer method first
            results = self.ia.get_top250_movies() or []
            if results:
                return [self._movie_summary(m) for m in results[: max(limit, 0)]]
            
            logger.warning("Cinemagoer Top 250 failed, falling back to web scraping")
            # Fallback: Get HTML content and return as text for LLM processing
            url = "https://www.imdb.com/chart/top/"
            content = self._get_web_content(url)
            
            if content:
                # Convert HTML to clean text using html2text
                text_content = self._html_to_text(content)
                
                # Return as a single "movie" entry with the full text content
                return [{
                    "id": "imdb_top_250_list",
                    "imdb_id": "top250",
                    "imdb_url": url,
                    "title": "IMDb Top 250 Movies List",
                    "year": None,
                    "kind": "list",
                    "rating": None,
                    "votes": None,
                    "cover_url": None,
                    "content": text_content,
                    "message": f"Retrieved IMDb Top 250 movies list content for LLM processing"
                }]
            
            raise RuntimeError("No movies found using any available method")
        except IMDbError as e:
            logger.error(f"Error fetching top movies: {e}", exc_info=True)
            raise

    def get_upcoming_movies(self, limit: int = 10) -> List[Dict[str, Any]]:
        self._before_execute()
        logger.info(f"Fetching upcoming (popular) movies (limit={limit})")
        if HTTP_PROXY:
            logger.info(f"Using proxy for MovieGuru upcoming movies request: {HTTP_PROXY}")
        try:
            # Try the original Cinemagoer method first
            results = self.ia.get_popular100_movies() or []
            if results:
                return [self._movie_summary(m) for m in results[: max(limit, 0)]]
            
            logger.warning("Cinemagoer popular movies failed, falling back to web scraping")
            # Fallback: Get HTML content and return as text for LLM processing
            url = "https://www.imdb.com/chart/moviemeter/"
            content = self._get_web_content(url)
            
            if content:
                # Convert HTML to clean text using html2text
                text_content = self._html_to_text(content)
                
                # Return as a single "movie" entry with the full text content
                return [{
                    "id": "imdb_popular_movies_list",
                    "imdb_id": "popular",
                    "imdb_url": url,
                    "title": "IMDb Most Popular Movies List",
                    "year": None,
                    "kind": "list",
                    "rating": None,
                    "votes": None,
                    "cover_url": None,
                    "content": text_content,
                    "message": f"Retrieved IMDb popular movies list content for LLM processing"
                }]
            
            raise RuntimeError("No movies found using any available method")
        except IMDbError as e:
            logger.error(f"Error fetching upcoming movies: {e}", exc_info=True)
            raise

    def get_movie_details(self, movie_id: str, info_sets: Optional[List[str]] = None) -> Dict[str, Any]:
        self._before_execute()
        logger.info(f"Fetching details for ID {movie_id} (info_sets={info_sets})")
        if HTTP_PROXY:
            logger.info(f"Using proxy for MovieGuru movie details request: {HTTP_PROXY}")
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

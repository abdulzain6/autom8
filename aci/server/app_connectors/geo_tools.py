from functools import lru_cache
from typing import Optional, Dict, Any, Tuple
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import time

from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase


logger = get_logger(__name__)


class GeoTools(AppConnectorBase):
    """
    A connector for geocoding utilities - converting city and country names to coordinates.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the GeoTools connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id

    def _before_execute(self) -> None:
        pass

    @lru_cache(maxsize=512)
    def _get_coordinates_cached(self, city: str, country: str) -> Optional[Tuple[float, float]]:
        """
        Cached geocoding function that converts city and country into coordinates.
        Retries up to 3 times on timeout/unavailable errors.
        """
        geolocator = Nominatim(user_agent="Autom8/1.0")
        query = f"{city}, {country}"
        for attempt in range(3):  # retry up to 3 times
            try:
                location = geolocator.geocode(query, timeout=10) # type: ignore
                if location:
                    return (location.latitude, location.longitude) # type: ignore
                return None
            except (GeocoderTimedOut, GeocoderUnavailable) as e:
                logger.warning(f"Geocoding attempt {attempt + 1} failed for '{query}': {e}")
                if attempt < 2:  # don't sleep on the last attempt
                    time.sleep(1)
        return None

    def get_coordinates(self, city: str, country: str) -> Dict[str, Any]:
        """
        Converts a city and country name into geographic coordinates.

        Args:
            city: The name of the city to geocode.
            country: The name of the country.
        """
        self._before_execute()

        try:
            coordinates = self._get_coordinates_cached(city, country)
            if coordinates:
                latitude, longitude = coordinates
                logger.info(f"Successfully geocoded '{city}, {country}' to coordinates: ({latitude}, {longitude})")
                return {
                    "latitude": latitude,
                    "longitude": longitude,
                    "coordinates": [latitude, longitude]
                }
            else:
                logger.warning(f"Could not geocode '{city}, {country}' - no results found")
                return {
                    "error": f"Could not find coordinates for '{city}, {country}'. Please check the city and country names and try again."
                }
        except Exception as e:
            logger.error(f"Unexpected error during geocoding for '{city}, {country}': {e}", exc_info=True)
            return {
                "error": f"An unexpected error occurred during geocoding: {str(e)}"
            }
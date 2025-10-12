from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import HTTP_PROXY

from fli.models import (
    Airport,
    SeatType,
    MaxStops,
    SortBy,
    FlightSearchFilters,
    FlightSegment
)
from fli.search import SearchFlights
from fli.models import PassengerInfo

logger = get_logger(__name__)


class GoogleFlights(AppConnectorBase):
    """
    A connector for Google Flights using the fli library.
    Provides comprehensive flight search capabilities with complex filtering options.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """
        Initializes the Google Flights connector.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)

        self.search_client = SearchFlights()
        
        # Configure HTTP session with proxy if available
        if HTTP_PROXY:
            proxy_config = {
                'http': HTTP_PROXY,
                'https': HTTP_PROXY
            }
            # Configure proxy on the curl_cffi session
            self.search_client.client._client.proxies.update(proxy_config)  # type: ignore
            logger.info(f"Google Flights connector configured with proxy: {HTTP_PROXY}")
        
        logger.info("Google Flights connector initialized with fli library")

    def _before_execute(self) -> None:
        """Setup before executing any method."""
        pass

    def _parse_airports(self, airport_codes: str) -> List[List]:
        """
        Parse comma-separated airport codes into fli format.

        Args:
            airport_codes: Comma-separated airport codes like "JFK,LAX"

        Returns:
            List of airport lists for fli
        """
        codes = [code.strip().upper() for code in airport_codes.split(",")]
        airports = []
        for code in codes:
            try:
                airport = getattr(Airport, code, None)
                if airport:
                    airports.append([airport, 0])
                else:
                    logger.warning(f"Unknown airport code: {code}")
            except AttributeError:
                logger.warning(f"Invalid airport code: {code}")
        return airports if airports else [[Airport.JFK, 0]]  # Default fallback

    def _parse_seat_type(self, seat_type: str) -> SeatType:
        """Parse seat type string to fli SeatType enum."""
        seat_map = {
            "economy": SeatType.ECONOMY,
            "premium_economy": SeatType.PREMIUM_ECONOMY,
            "business": SeatType.BUSINESS,
            "first": SeatType.FIRST,
        }
        return seat_map.get(seat_type.lower(), SeatType.ECONOMY)

    def _parse_max_stops(self, stops: str) -> MaxStops:
        """Parse stops string to fli MaxStops enum."""
        stops_map = {
            "non_stop": MaxStops.NON_STOP,
            "one_stop": MaxStops.ONE_STOP_OR_FEWER,
            "two_stops": MaxStops.TWO_OR_FEWER_STOPS,
        }
        return stops_map.get(stops.lower(), MaxStops.ANY)

    def _parse_sort_by(self, sort_by: str) -> SortBy:
        """Parse sort criteria to fli SortBy enum."""
        sort_map = {
            "cheapest": SortBy.CHEAPEST,
            "duration": SortBy.DURATION,
            "departure_time": SortBy.DEPARTURE_TIME,
            "arrival_time": SortBy.ARRIVAL_TIME,
        }
        return sort_map.get(sort_by.lower(), SortBy.CHEAPEST)

    def search_flights(
        self,
        departure_airports: str,
        arrival_airports: str,
        departure_date: str,
        return_date: Optional[str] = None,
        adults: int = 1,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_on_lap: int = 0,
        seat_type: str = "economy",
        max_stops: str = "any_stops",
        sort_by: str = "cheapest",
        max_results: int = 20
    ) -> Dict[str, Any]:
        """
        Search for flights with comprehensive filtering options.

        Args:
            departure_airports: Comma-separated departure airport codes (e.g., "JFK,LAX")
            arrival_airports: Comma-separated arrival airport codes (e.g., "LHR,CDG")
            departure_date: Departure date in YYYY-MM-DD format
            return_date: Optional return date for round-trip in YYYY-MM-DD format
            adults: Number of adult passengers
            children: Number of child passengers
            infants_in_seat: Number of infants in seats
            infants_on_lap: Number of infants on laps
            seat_type: Seat class ("economy", "premium_economy", "business", "first")
            max_stops: Maximum stops ("non_stop", "one_stop", "two_stops", "any_stops")
            sort_by: Sort criteria ("cheapest", "duration", "departure_time", "arrival_time")
            max_results: Maximum number of results to return

        Returns:
            Dictionary containing flight search results
        """
        try:
            # Parse passenger information
            passenger_info = PassengerInfo(
                adults=adults,
                children=children,
                infants_in_seat=infants_in_seat,
                infants_on_lap=infants_on_lap
            )

            # Parse airports
            dep_airports = self._parse_airports(departure_airports)
            arr_airports = self._parse_airports(arrival_airports)

            # Create flight segments
            segments = [
                FlightSegment(
                    departure_airport=dep_airports,
                    arrival_airport=arr_airports,
                    travel_date=departure_date,
                )
            ]

            # Add return segment if round-trip
            if return_date:
                segments.append(
                    FlightSegment(
                        departure_airport=arr_airports,
                        arrival_airport=dep_airports,
                        travel_date=return_date,
                    )
                )

            # Create search filters
            filters = FlightSearchFilters(
                passenger_info=passenger_info,
                flight_segments=segments,
                seat_type=self._parse_seat_type(seat_type),
                stops=self._parse_max_stops(max_stops),
                sort_by=self._parse_sort_by(sort_by),
            )

            # Perform search
            logger.info(f"Searching flights: {departure_airports} -> {arrival_airports} on {departure_date}")
            flights = self.search_client.search(filters)

            if not flights:
                return {
                    "success": True,
                    "flights": [],
                    "total_results": 0,
                    "search_criteria": {
                        "departure_airports": departure_airports,
                        "arrival_airports": arrival_airports,
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "passengers": f"{adults} adults, {children} children, {infants_in_seat} infants in seat, {infants_on_lap} infants on lap",
                        "seat_type": seat_type,
                        "max_stops": max_stops,
                        "sort_by": sort_by
                    }
                }

            # Process and limit results
            processed_flights = []
            for flight in flights[:max_results]:
                # Handle both single FlightResult and tuple of FlightResults
                if isinstance(flight, tuple):
                    # For round-trip flights, flight is a tuple (outbound, return)
                    outbound, return_flight = flight
                    # Use outbound for main flight info
                    flight_data = outbound
                else:
                    # Single flight
                    flight_data = flight

                processed_flight = {
                    "price": flight_data.price,
                    "duration_minutes": flight_data.duration,
                    "stops": flight_data.stops,
                    "legs": []
                }

                # Process flight legs
                legs = flight_data.legs
                for leg in legs:
                    processed_leg = {
                        "airline": leg.airline.value,
                        "flight_number": leg.flight_number,
                        "departure_airport": leg.departure_airport.value,
                        "arrival_airport": leg.arrival_airport.value,
                        "departure_datetime": leg.departure_datetime,
                        "arrival_datetime": leg.arrival_datetime,
                        "duration_minutes": leg.duration,
                    }
                    processed_flight["legs"].append(processed_leg)

                processed_flights.append(processed_flight)

            return {
                "success": True,
                "flights": processed_flights,
                "total_results": len(processed_flights),
                "search_criteria": {
                    "departure_airports": departure_airports,
                    "arrival_airports": arrival_airports,
                    "departure_date": departure_date,
                    "return_date": return_date,
                    "passengers": f"{adults} adults, {children} children, {infants_in_seat} infants in seat, {infants_on_lap} infants on lap",
                    "seat_type": seat_type,
                    "max_stops": max_stops,
                    "sort_by": sort_by
                }
            }

        except Exception as e:
            logger.error(f"Error searching flights: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Flight search failed: {str(e)}"
            }

    def search_cheap_flights(
        self,
        departure_airports: str,
        arrival_airports: str,
        start_date: str,
        end_date: str,
        adults: int = 1,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_on_lap: int = 0,
        seat_type: str = "economy",
        max_stops: str = "any_stops",
        max_results: int = 10
    ) -> Dict[str, Any]:
        """
        Find the cheapest flight dates within a date range.

        Args:
            departure_airports: Comma-separated departure airport codes
            arrival_airports: Comma-separated arrival airport codes
            start_date: Start date for search range in YYYY-MM-DD format
            end_date: End date for search range in YYYY-MM-DD format
            adults: Number of adult passengers
            children: Number of child passengers
            infants_in_seat: Number of infants in seats
            infants_on_lap: Number of infants on laps
            seat_type: Seat class ("economy", "premium_economy", "business", "first")
            max_stops: Maximum stops ("non_stop", "one_stop", "two_stops", "any_stops")
            max_results: Maximum number of results to return

        Returns:
            Dictionary containing cheapest flight dates and prices
        """
        try:
            # Parse passenger information
            passenger_info = PassengerInfo(
                adults=adults,
                children=children,
                infants_in_seat=infants_in_seat,
                infants_on_lap=infants_on_lap
            )

            # Parse airports
            dep_airports = self._parse_airports(departure_airports)
            arr_airports = self._parse_airports(arrival_airports)

            # Parse date range
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")

            cheapest_flights = []

            # Search each date in the range (limit to avoid too many requests)
            current_date = start
            days_searched = 0
            max_days = 30  # Limit search to 30 days to avoid rate limits

            while current_date <= end and days_searched < max_days:
                try:
                    # Create flight segment for this date
                    segments = [
                        FlightSegment(
                            departure_airport=dep_airports,
                            arrival_airport=arr_airports,
                            travel_date=current_date.strftime("%Y-%m-%d"),
                        )
                    ]

                    # Create search filters
                    filters = FlightSearchFilters(
                        passenger_info=passenger_info,
                        flight_segments=segments,
                        seat_type=self._parse_seat_type(seat_type),
                        stops=self._parse_max_stops(max_stops),
                        sort_by=SortBy.CHEAPEST,
                    )

                    # Search flights for this date
                    flights = self.search_client.search(filters)

                    if flights:
                        # Handle both single FlightResult and tuple of FlightResults
                        first_flight = flights[0]
                        if isinstance(first_flight, tuple):
                            # For round-trip flights, use outbound flight
                            cheapest_flight = first_flight[0]
                        else:
                            # Single flight
                            cheapest_flight = first_flight

                        cheapest_flights.append({
                            "date": current_date.strftime("%Y-%m-%d"),
                            "price": getattr(cheapest_flight, 'price', 0),
                            "currency": getattr(cheapest_flight, 'currency', 'USD'),
                            "stops": getattr(cheapest_flight, 'stops', 0),
                            "duration_minutes": getattr(cheapest_flight, 'duration', 0),
                            "airline": getattr(getattr(cheapest_flight.legs[0], 'airline', None), 'value', str(getattr(cheapest_flight.legs[0], 'airline', 'Unknown'))) if getattr(cheapest_flight, 'legs', None) and len(cheapest_flight.legs) > 0 else 'Unknown'
                        })

                    days_searched += 1
                    current_date += timedelta(days=1)

                except Exception as e:
                    logger.warning(f"Failed to search date {current_date.strftime('%Y-%m-%d')}: {e}")
                    current_date += timedelta(days=1)
                    continue

            # Sort by price and return top results
            cheapest_flights.sort(key=lambda x: x["price"])
            results = cheapest_flights[:max_results]

            return {
                "success": True,
                "cheapest_flights": results,
                "total_dates_searched": days_searched,
                "date_range": f"{start_date} to {end_date}",
                "search_criteria": {
                    "departure_airports": departure_airports,
                    "arrival_airports": arrival_airports,
                    "passengers": f"{adults} adults, {children} children, {infants_in_seat} infants in seat, {infants_on_lap} infants on lap",
                    "seat_type": seat_type,
                    "max_stops": max_stops
                }
            }

        except Exception as e:
            logger.error(f"Error searching cheap flights: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Cheap flights search failed: {str(e)}"
            }

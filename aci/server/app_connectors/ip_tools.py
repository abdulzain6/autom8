import whois
import dns.resolver
import geocoder
from typing import Optional, List, Dict, Any

from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase

logger = get_logger(__name__)


class IpTools(AppConnectorBase):
    """
    A connector for performing IP address and DNS-related lookups.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """
        Initializes the IPTools connector.
        """
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        logger.info("IPTools connector initialized.")

    def _before_execute(self) -> None:
        pass

    def get_ip_info(self, ip_address: str) -> Dict[str, Any]:
        """
        Retrieves geolocation and WHOIS information for a given IP address.

        Args:
            ip_address: The IP address to look up (e.g., '8.8.8.8').

        Returns:
            A dictionary containing location and ownership details.
        """
        self._before_execute()
        logger.info(f"Fetching info for IP address: {ip_address}")

        try:
            # Geolocation lookup
            geo = geocoder.ip(ip_address)
            location_info = (
                geo.json
                if geo.ok
                else {"error": "Could not retrieve geolocation data."}
            )

            # WHOIS lookup
            try:
                whois_info = str(whois.whois(ip_address))
            except Exception as e:
                whois_info = {"error": f"WHOIS lookup failed: {str(e)}"}

            return {
                "ip_address": ip_address,
                "geolocation": location_info,
                "whois": whois_info,
            }
        except Exception as e:
            logger.error(
                f"An unexpected error occurred in get_ip_info for {ip_address}: {e}"
            )
            raise Exception(
                f"Failed to get information for IP {ip_address}: {e}"
            ) from e

    def dns_lookup(self, domain: str, record_types: List[str]) -> Dict[str, Any]:
        """
        Performs DNS lookups for a given domain and specified record types.

        Args:
            domain: The domain name to query (e.g., 'google.com').
            record_types: A list of DNS record types to look up (e.g., ['A', 'MX', 'TXT']).

        Returns:
            A dictionary with the results for each requested record type.
        """
        self._before_execute()
        logger.info(
            f"Performing DNS lookup for domain: {domain} (Types: {record_types})"
        )

        results = {"domain": domain, "records": {}}
        valid_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME"]

        for record_type in record_types:
            if record_type.upper() not in valid_types:
                results["records"][record_type] = ["Invalid record type requested."]
                continue

            try:
                answers = dns.resolver.resolve(domain, record_type.upper())
                results["records"][record_type] = [str(rdata) for rdata in answers]
            except dns.resolver.NoAnswer:
                results["records"][record_type] = []
            except dns.resolver.NXDOMAIN:
                results["records"][record_type] = [f"Domain '{domain}' does not exist."]
                break  # Stop if the domain doesn't exist
            except Exception as e:
                logger.error(f"DNS lookup for {record_type} on {domain} failed: {e}")
                results["records"][record_type] = [f"An error occurred: {str(e)}"]

        return results

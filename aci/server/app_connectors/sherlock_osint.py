import time
from typing import Optional, Dict, Any, List
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server import config
from sherlock_project import sherlock
from sherlock_project.sites import SitesInformation
from sherlock_project.notify import QueryNotifyPrint
from sherlock_project.result import QueryResult

logger = get_logger(__name__)


class SherlockOsint(AppConnectorBase):
    """
    A connector for OSINT (Open Source Intelligence) username investigations using Sherlock.
    Searches for usernames across multiple social media platforms and websites.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """Initializes the Sherlock OSINT connector."""
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        self.user_id = linked_account.user_id
        
        # Load site data for Sherlock
        # We need both the SiteInformation objects for metadata and the raw dict for sherlock function
        self.sites_info = SitesInformation()
        self.site_objects = {site.name: site for site in self.sites_info}
        
        # Get the raw site data that Sherlock function expects
        # We need to recreate this from the SiteInformation objects since Sherlock converts the raw JSON
        self.site_data = {}
        for site in self.sites_info:
            self.site_data[site.name] = {
                'urlMain': site.url_home,
                'url': site.url_username_format,
                'username_claimed': site.username_claimed,
                'username_unclaimed': getattr(site, 'username_unclaimed', 'noonewouldeverusethis7'),
                'isNSFW': site.is_nsfw,
                # Add other fields from site.information if available
                **getattr(site, 'information', {})
            }
        
        logger.info(f"Loaded {len(self.site_data)} sites for Sherlock OSINT")
            

    def _before_execute(self) -> None:
        pass

    def investigate_username(
        self,
        username: str,
        timeout: int = 60,
        include_nsfw: bool = False,
        max_sites: Optional[int] = None,
        site_names: Optional[List[str]] = None,
        strict_filtering: bool = True,
        only_positive: bool = True
    ) -> Dict[str, Any]:
        """
        Investigate a username across multiple social media platforms and websites.
        
        Args:
            username: The username to investigate
            timeout: Timeout in seconds for each site check (default: 60)
            include_nsfw: Whether to include NSFW/adult sites in the search (default: False)
            max_sites: Maximum number of sites to check (default: None, checks all sites)
            site_names: List of specific site names to check (default: None, checks all sites)
            strict_filtering: Whether to apply strict filtering to reduce false positives (default: True)
            only_positive: Whether to only return claimed accounts and skip negative results (default: True)
        """
        self._before_execute()
        
        if not username or not username.strip():
            return {
                "success": False,
                "error": "Username cannot be empty"
            }
        
        username = username.strip()
        
        if not self.site_data:
            return {
                "success": False,
                "error": "Sherlock site data not available"
            }
        
        try:
            # Get the raw site data dictionary that Sherlock expects
            filtered_sites = dict(self.site_data)
            
            # Filter by specific site names if provided
            if site_names:
                # Convert site names to lowercase for case-insensitive matching
                site_names_lower = [name.lower() for name in site_names]
                filtered_sites = {
                    name: data for name, data in filtered_sites.items()
                    if name.lower() in site_names_lower
                }
                
                # Log if any requested sites were not found
                found_sites = set(filtered_sites.keys())
                requested_sites = set(site_names)
                missing_sites = requested_sites - found_sites
                if missing_sites:
                    logger.warning(f"Requested sites not found: {missing_sites}")
            
            # Remove NSFW sites if not requested
            if not include_nsfw:
                filtered_sites = {
                    name: data for name, data in filtered_sites.items()
                    if not data.get('isNSFW', False)
                }
            
            # Limit number of sites if specified (after filtering)
            if max_sites and max_sites > 0:
                site_items = list(filtered_sites.items())[:max_sites]
                filtered_sites = dict(site_items)
            
            logger.info(f"Starting Sherlock investigation for username '{username}' across {len(filtered_sites)} sites")
            
            # Log some debug info about the sites being checked
            if len(filtered_sites) < 50:  # Only log site names for small investigations
                logger.info(f"Sites to check: {list(filtered_sites.keys())}")
            
            # Create a simple query notifier
            query_notify = QueryNotifyPrint()
            
            # Configure proxy if available
            proxy_url = None
            if config.HTTP_PROXY:
                proxy_url = config.HTTP_PROXY
                logger.info(f"Using proxy for Sherlock investigation: {proxy_url}")
            
            # Run Sherlock analysis
            start_time = time.time()
            results = sherlock.sherlock(
                username=username,
                site_data=filtered_sites,
                query_notify=query_notify,
                tor=False,  # Don't use Tor for now
                unique_tor=False,
                proxy=proxy_url,
                timeout=timeout
            )
            
            execution_time = time.time() - start_time
            
            # Check if we got results for all expected sites
            expected_sites = set(filtered_sites.keys())
            actual_sites = set(results.keys())
            missing_sites = expected_sites - actual_sites
            
            if missing_sites:
                logger.warning(f"Missing results for {len(missing_sites)} sites: {list(missing_sites)[:10]}{'...' if len(missing_sites) > 10 else ''}")
            
            # Convert QueryResult objects to serializable dictionaries with enhanced filtering
            serializable_results = {}
            claimed_count = 0
            available_count = 0
            error_count = 0
            uncertain_count = 0
            waf_count = 0
            illegal_count = 0
            
            for site_name, site_result in results.items():
                status_obj = site_result.get('status')
                if isinstance(status_obj, QueryResult):
                    status_name = status_obj.status.name if hasattr(status_obj.status, 'name') else str(status_obj.status)
                    
                    # Enhanced filtering logic based on Sherlock's actual detection methods
                    should_include = False
                    result_category = 'uncertain'
                    
                    if status_name == 'CLAIMED':
                        url = status_obj.site_url_user
                        context = status_obj.context or ''
                        response_text = site_result.get('response_text', b'')
                        http_status = site_result.get('http_status', '')
                        
                        # Convert response_text to string if it's bytes
                        if isinstance(response_text, bytes):
                            try:
                                response_text_str = response_text.decode('utf-8', errors='ignore')
                            except:
                                response_text_str = str(response_text)
                        else:
                            response_text_str = str(response_text) if response_text else ''
                        
                        if strict_filtering:
                            # Check for WAF/security blocks first (highest priority)
                            waf_indicators = [
                                '.loading-spinner{visibility:hidden}body.no-js .challenge-running{display:none}',
                                'challenge-error-text',
                                'AwsWafIntegration.forceRefreshToken',
                                'perimeterxIdentifiers',
                                'cloudflare', 'captcha', 'security check'
                            ]
                            
                            # Check for error pages and false positives
                            false_positive_indicators = [
                                'bad username', 'invalid username', 'user not found',
                                'page not found', '404', 'error occurred', 'something went wrong',
                                'profile not found', 'account not found', 'user does not exist',
                                'no such user', 'username not available', 'not available',
                                'forbidden', 'access denied', 'suspended', 'deleted',
                                'private profile', 'profile unavailable', 'account suspended',
                                'user banned', 'temporarily unavailable', 'service unavailable'
                            ]
                            
                            # Check response content for issues
                            content_to_check = (response_text_str + ' ' + context).lower()
                            
                            has_waf_block = any(indicator.lower() in content_to_check for indicator in waf_indicators)
                            has_false_positive = any(indicator in content_to_check for indicator in false_positive_indicators)
                            
                            # Additional checks for suspicious responses
                            suspicious_conditions = [
                                http_status in [403, 404, 500, 502, 503],  # Error status codes
                                len(response_text_str) < 100,  # Very short responses
                                not url or url == 'http://',  # Invalid URLs
                                'redirect' in content_to_check and len(response_text_str) < 500  # Suspicious redirects
                            ]
                            
                            if has_waf_block:
                                result_category = 'waf_blocked'
                                waf_count += 1
                            elif has_false_positive or any(suspicious_conditions):
                                result_category = 'uncertain'
                                uncertain_count += 1
                            else:
                                # High confidence claimed account
                                result_category = 'claimed'
                                claimed_count += 1
                        else:
                            # Without strict filtering, trust Sherlock's detection
                            result_category = 'claimed'
                            claimed_count += 1
                    
                    elif status_name == 'AVAILABLE':
                        available_count += 1
                        result_category = 'available'
                    
                    elif status_name == 'WAF':
                        waf_count += 1
                        result_category = 'waf_blocked'
                    
                    elif status_name == 'ILLEGAL':
                        illegal_count += 1
                        result_category = 'illegal_username'
                    
                    else:
                        # Unknown status (UNKNOWN, etc.)
                        error_count += 1
                        result_category = 'error'
                    
                                        # Include result based on flags and category
                    if only_positive:
                        # Include claimed and uncertain accounts when only_positive is True
                        should_include = result_category == 'claimed' or result_category == 'uncertain' or result_category == 'uncertain'
                    else:
                        # Include claimed, uncertain, WAF blocked, and errors for analysis
                        should_include = result_category in ['claimed', 'uncertain', 'waf_blocked', 'error']
                    
                    # Create minimal result data to prevent truncation
                    if should_include:
                        result_data = {
                            'site': status_obj.site_name,
                            'url': status_obj.site_url_user,
                            'status': status_name,
                            'category': result_category
                        }
                        
                        # Add optional fields only if not only_positive or if they're important
                        if not only_positive or result_category != 'claimed':
                            result_data.update({
                                'http_status': site_result.get('http_status', ''),
                                'query_time': (
                                    status_obj.query_time.total_seconds() 
                                    if status_obj.query_time and hasattr(status_obj.query_time, 'total_seconds') 
                                    else status_obj.query_time if isinstance(status_obj.query_time, (int, float)) 
                                    else None
                                )
                            })
                        
                        # Add context only for uncertain/error cases to help with analysis
                        if result_category in ['uncertain', 'error', 'waf_blocked'] and status_obj.context:
                            result_data['context_snippet'] = status_obj.context[:100]  # Limit context length
                        
                        serializable_results[site_name] = result_data
                else:
                    # Handle cases where status is not QueryResult
                    error_count += 1
                    if not only_positive:  # Only include errors if showing all results
                        serializable_results[site_name] = {
                            'site': site_name,
                            'status': str(status_obj) if status_obj else 'Unknown',
                            'category': 'error',
                            'error': 'Invalid result format'
                        }
            
            # Return just the results
            return serializable_results
            
        except Exception as e:
            logger.error(f"Sherlock investigation failed for username '{username}': {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Investigation failed: {str(e)}"
            }

    def get_supported_sites(self, include_nsfw: bool = False) -> Dict[str, Any]:
        """
        Get list of all supported sites for username investigation.
        
        Args:
            include_nsfw: Whether to include NSFW/adult sites (default: False)
        """
        self._before_execute()
        
        if not self.site_data:
            return {
                "success": False,
                "error": "Sherlock site data not available"
            }
        
        try:
            sites = []
            for site_name, site_info in self.site_objects.items():
                if not include_nsfw and site_info.is_nsfw:
                    continue
                
                sites.append({
                    'name': site_info.name,
                    'url': site_info.url_home,
                    'is_nsfw': site_info.is_nsfw
                })
            
            # Sort sites alphabetically
            sites.sort(key=lambda x: x['name'].lower())
            
            return {
                'success': True,
                'total_sites': len(sites),
                'sites': sites,
                'site_names': [site['name'] for site in sites],
                'message': f"Retrieved {len(sites)} supported sites for username investigation"
            }
            
        except Exception as e:
            logger.error(f"Failed to get supported sites: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to get supported sites: {str(e)}"
            }
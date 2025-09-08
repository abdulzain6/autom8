import pandas as pd
from typing import List, Dict, Any
from pytrends.request import TrendReq
from aci.common.db.sql_models import LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import HTTP_PROXY
from aci.common.logging_setup import get_logger

logger = get_logger(__name__)


class GoogleTrends(AppConnectorBase):
    """
    Connector for fetching and analyzing data from Google Trends.
    Provides a structured, AI-friendly interface to explore search interest and trending topics.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """
        Initializes the GoogleTrends connector.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        
        # Configure proxy settings if available
        if HTTP_PROXY:
            logger.info(f"Google Trends connector configured with proxy: {HTTP_PROXY}")
            self.pytrends = TrendReq(hl='en-US', tz=360, proxies=HTTP_PROXY)
        else:
            self.pytrends = TrendReq(hl='en-US', tz=360)
        logger.info("Google Trends connector initialized.")

    def _before_execute(self) -> None:
        """
        A hook for pre-execution logic. Not required for pygooglenews.
        """
        pass
    def _format_dataframe(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Helper function to convert a pandas DataFrame into a list of dictionaries.
        """
        if df.empty:
            return []
        # Reset index to make the date/region a column, then convert to dicts
        return df.reset_index().to_dict('records')

    def get_interest_over_time(
        self,
        keywords: List[str],
        timeframe: str = 'today 3-m',
        geo: str = ''
    ) -> List[Dict[str, Any]]:
        """
        Fetches the interest for a list of keywords over a specified time period.
        The interest is represented on a scale of 0 to 100.

        Args:
            keywords: A list of up to 5 keywords to compare (e.g., ['Python', 'JavaScript']).
            timeframe: The time period to analyze. Defaults to 'today 3-m' (last 3 months).
                       Examples: 'now 7-d', '2020-01-01 2020-12-31'.
            geo: The geographical region for the trends, as a two-letter country code (e.g., 'US', 'GB').
                 Defaults to worldwide.

        Returns:
            A list of dictionaries, where each dictionary represents a time point and the interest for each keyword.
        """
        logger.info(f"Getting interest over time for keywords: {keywords}, timeframe: {timeframe}, geo: {geo}")
        if HTTP_PROXY:
            logger.info(f"Using proxy for Google Trends interest over time request: {HTTP_PROXY}")
        self.pytrends.build_payload(kw_list=keywords, cat=0, timeframe=timeframe, geo=geo, gprop='')
        interest_df = self.pytrends.interest_over_time()
        if 'isPartial' in interest_df.columns:
            interest_df = interest_df.drop(columns=['isPartial'])
        return self._format_dataframe(interest_df)

    def get_trending_searches(self, country_code: str = 'US', max_results: int = 10) -> List[str]:
        """
        Fetches the top daily trending search queries for a specific country.

        Args:
            country_code: The two-letter country code (e.g., 'US', 'GB', 'PK'). Defaults to 'US'.
            max_results: The maximum number of trending searches to return. Defaults to 10.

        Returns:
            A list of the top trending search terms.
        """
        logger.info(f"Getting trending searches for country: {country_code}, max_results: {max_results}")
        if HTTP_PROXY:
            logger.info(f"Using proxy for Google Trends trending searches request: {HTTP_PROXY}")
        try:
            trending_df = self.pytrends.trending_searches(pn=country_code.lower())
            return trending_df[0].tolist()[:max_results]
        except Exception:
            return []

    def get_related_queries(self, keyword: str, max_results: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """
        Finds queries related to a given keyword, categorized as 'top' and 'rising'.

        Args:
            keyword: The keyword to find related queries for (e.g., 'artificial intelligence').
            max_results: The maximum number of results to return for both 'top' and 'rising' categories. Defaults to 10.

        Returns:
            A dictionary with two keys, 'top' and 'rising', each containing a list of related queries.
        """
        logger.info(f"Getting related queries for keyword: {keyword}, max_results: {max_results}")
        if HTTP_PROXY:
            logger.info(f"Using proxy for Google Trends related queries request: {HTTP_PROXY}")
        self.pytrends.build_payload(kw_list=[keyword])
        related_queries_dict = self.pytrends.related_queries()
        
        result = {
            "top": [],
            "rising": []
        }

        # The result is nested under the keyword itself
        keyword_data = related_queries_dict.get(keyword, {})
        
        if 'top' in keyword_data and keyword_data['top'] is not None:
            result['top'] = keyword_data['top'].head(max_results).to_dict('records')
            
        if 'rising' in keyword_data and keyword_data['rising'] is not None:
            result['rising'] = keyword_data['rising'].head(max_results).to_dict('records')
            
        return result

    def get_interest_by_region(
        self,
        keywords: List[str],
        timeframe: str = 'today 3-m',
        resolution: str = 'COUNTRY',
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Fetches and compares search interest for keywords by geographical region.

        Args:
            keywords: A list of keywords to compare.
            timeframe: The time period to analyze. Defaults to 'today 3-m' (last 3 months).
            resolution: The level of regional detail. Can be 'COUNTRY', 'REGION', 'CITY', or 'DMA'.
            max_results: The maximum number of regions to return. Defaults to 20.

        Returns:
            A list of dictionaries, each showing the interest for the keywords in a specific region.
        """
        logger.info(f"Getting interest by region for keywords: {keywords}, timeframe: {timeframe}, resolution: {resolution}")
        if HTTP_PROXY:
            logger.info(f"Using proxy for Google Trends interest by region request: {HTTP_PROXY}")
        self.pytrends.build_payload(kw_list=keywords, timeframe=timeframe)
        region_df = self.pytrends.interest_by_region(resolution=resolution, inc_low_vol=True, inc_geo_code=False)
        
        if not region_df.empty and len(keywords) > 0:
            # Sort by the interest of the first keyword to get the most relevant regions
            sorted_df = region_df.sort_values(by=keywords[0], ascending=False)
            limited_df = sorted_df.head(max_results)
            return self._format_dataframe(limited_df)
            
        return self._format_dataframe(region_df)

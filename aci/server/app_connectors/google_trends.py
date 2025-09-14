import time
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

        This constructor configures pytrends to use custom headers and a proxy
        via the 'requests_args' parameter to ensure reliable communication with Google Trends.
        """
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )

        # Define arguments to be passed into every requests call made by pytrends.
        requests_args = {
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.5",
            },
            # Pass proxies directly to the requests library through this argument
            "proxies": (
                {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None
            ),
        }

        if HTTP_PROXY:
            logger.info("Configuring Google Trends connector with proxy.")
        else:
            logger.info("Initializing Google Trends connector without proxy.")

        try:
            self.pytrends = TrendReq(
                hl="en-US",
                tz=360,
                timeout=(15, 30),
                # Use requests_args to pass our custom configuration
                requests_args=requests_args,
            )
            # Perform a small test request on initialization to fail fast if the connection is bad.
            self.pytrends.build_payload(kw_list=["Google"], timeframe="now 1-H")
            logger.info(
                "Google Trends connector initialized and connection verified successfully."
            )
        except Exception as e:
            logger.error(f"Failed to initialize Google Trends connector. Error: {e}")
            raise ConnectionError(
                f"Could not connect to Google Trends. Check proxy/network. Error: {e}"
            )

    def _before_execute(self) -> None:
        pass

    def _format_dataframe(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df.empty:
            return []
        return df.reset_index().to_dict("records")  # type: ignore

    def _execute_with_retry(self, func, max_retries=3, delay=2):
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                error_str = str(e).lower()
                if (
                    "429" in error_str
                    or "rate limit" in error_str
                    or "timeout" in error_str
                ):
                    if attempt < max_retries - 1:
                        wait_time = delay * (2**attempt)
                        logger.warning(
                            f"Rate limit/timeout detected. Retrying in {wait_time}s (Attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(wait_time)
                        continue
                if "400" in error_str:
                    logger.error(
                        f"HTTP 400 Bad Request. The request is malformed. Error: {e}"
                    )
                    raise
                if attempt == max_retries - 1:
                    logger.error(f"All {max_retries} attempts failed.")
                    raise
                else:
                    time.sleep(delay)

    def get_interest_over_time(
        self, keywords: List[str], timeframe: str = "today 3-m", geo: str = ""
    ) -> List[Dict[str, Any]]:
        logger.info(
            f"Getting interest over time for keywords: {keywords}, timeframe: {timeframe}, geo: {geo}"
        )

        def _fetch():
            self.pytrends.build_payload(
                kw_list=keywords, cat=0, timeframe=timeframe, geo=geo
            )
            df = self.pytrends.interest_over_time()
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            return df

        result_df = self._execute_with_retry(_fetch)
        return self._format_dataframe(result_df) if result_df is not None else []

    def get_trending_searches(
        self, country_code: str = "PK", max_results: int = 10
    ) -> List[str]:
        logger.info(
            f"Getting trending searches for country: {country_code}, max_results: {max_results}"
        )

        def _fetch():
            df = self.pytrends.trending_searches(pn=country_code.lower())
            return df[0].tolist()[:max_results]

        try:
            return self._execute_with_retry(_fetch) or []
        except Exception as e:
            logger.warning(f"Could not get trending searches after retries: {e}")
            return []

    def get_related_queries(
        self, keyword: str, max_results: int = 10
    ) -> Dict[str, List[Dict[str, Any]]]:
        logger.info(
            f"Getting related queries for keyword: {keyword}, max_results: {max_results}"
        )

        def _fetch():
            self.pytrends.build_payload(kw_list=[keyword])
            data = self.pytrends.related_queries().get(keyword)
            result = {"top": [], "rising": []}
            if data is not None:
                if "top" in data and data["top"] is not None:
                    result["top"] = data["top"].head(max_results).to_dict("records")
                if "rising" in data and data["rising"] is not None:
                    result["rising"] = (
                        data["rising"].head(max_results).to_dict("records")
                    )
            return result

        return self._execute_with_retry(_fetch) or {"top": [], "rising": []}

    def get_interest_by_region(
        self,
        keywords: List[str],
        timeframe: str = "today 3-m",
        resolution: str = "COUNTRY",
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        logger.info(
            f"Getting interest by region for keywords: {keywords}, timeframe: {timeframe}, resolution: {resolution}"
        )

        def _fetch():
            self.pytrends.build_payload(kw_list=keywords, timeframe=timeframe)
            df = self.pytrends.interest_by_region(
                resolution=resolution, inc_low_vol=True
            )
            if not df.empty and keywords:
                return df.sort_values(by=keywords[0], ascending=False).head(max_results)
            return df

        result_df = self._execute_with_retry(_fetch)
        return self._format_dataframe(result_df) if result_df is not None else []


def test_google_trends_functions():
    """
    Test function to verify all Google Trends connector functions work properly
    by instantiating and using the GoogleTrends class.
    """
    print("\n" + "=" * 50)
    print("🚀 Starting Google Trends Connector Test 🚀")
    print("=" * 50)

    try:
        print("\n--> Step 1: Initializing the connector...")
        # Create a default instance of LinkedAccount for the test.
        connector = GoogleTrends(
            linked_account=None,  # type: ignore
            security_scheme=NoAuthScheme(),
            security_credentials=NoAuthSchemeCredentials(),
        )
        print("    ✅ Connector initialized successfully.")
    except Exception as e:
        print(f"    ❌ FATAL: Connector initialization failed: {e}")
        print(
            "\n💡 Please check your proxy configuration (HTTP_PROXY) and network connection."
        )
        return

    print("\n--> Step 2: Testing get_interest_over_time...")
    try:
        keywords = ["Cricket", "Football"]
        data = connector.get_interest_over_time(
            keywords=keywords, timeframe="today 1-m", geo="PK"
        )
        if data:
            print(
                f"    ✅ Success! Returned {len(data)} data points for {keywords} in Pakistan."
            )
        else:
            print("    ⚠️  Call successful, but no data returned.")
    except Exception as e:
        print(f"    ❌ Test failed: {e}")

    print("\n--> Step 3: Testing get_trending_searches...")
    try:
        data = connector.get_trending_searches(country_code="PK", max_results=5)
        if data:
            print(f"    ✅ Success! Top 5 trending topics in Pakistan:")
            for i, topic in enumerate(data, 1):
                print(f"      {i}. {topic}")
        else:
            print("    ⚠️  Call successful, but no trending topics returned.")
    except Exception as e:
        print(f"    ❌ Test failed: {e}")

    print("\n" + "=" * 50)
    print("🎉 All connector tests completed. 🎉")
    print("=" * 50)


def main():
    """Main function to run the tests."""
    try:
        test_google_trends_functions()
    except Exception as e:
        print(f"\n❌ Testing failed with an unexpected error: {e}")


if __name__ == "__main__":
    main()

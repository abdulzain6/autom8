import pandas as pd
from aci.common.db.sql_models import LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
import yfinance as yf
from typing import List, Dict, Any
from datetime import datetime

from aci.common.logging_setup import get_logger


logger = get_logger(__name__)

class YahooFinance(AppConnectorBase):
    """
    Connector for fetching financial data from Yahoo Finance using the yfinance library.
    This connector is designed to be used by an AI agent, providing structured
    and relevant financial information.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
    ):
        """
        Initializes the YahooFinance connector.
        Note: Authentication details are not required for yfinance.
        """
        super().__init__(linked_account, security_scheme, security_credentials)
        logger.info("YahooFinance connector initialized.")

    def _before_execute(self) -> None:
        """
        A hook that can be used for pre-execution logic, like checking token validity.
        Not required for yfinance, but included for structural consistency.
        """
        pass


    def get_current_stock_price(self, tickers: List[str]) -> Dict[str, Any]:
        """
        Retrieves the latest market price and key daily stats for one or more stocks.

        Args:
            tickers: A list of stock symbols (e.g., ["AAPL", "MSFT"]).

        Returns:
            A dictionary where each key is a ticker and the value contains
            current price, day high, day low, and volume.
        """
        logger.info(f"Fetching current stock prices for tickers: {tickers}")
        results = {}
        try:
            ticker_data = yf.Tickers(" ".join(tickers))
            
            if not ticker_data.tickers:
                raise Exception("No data returned from yfinance Tickers call.")

            # --- THE FIX IS HERE ---
            # Iterate over the .values() of the tickers dictionary to get the
            # Ticker objects, not the string keys.
            for ticker_obj in ticker_data.tickers.values():
                info = ticker_obj.info
                ticker_symbol = info.get("symbol")
                if not ticker_symbol:
                    logger.warning(f"Could not resolve info for one of the tickers.")
                    continue

                if info.get("regularMarketPrice") is None:
                    logger.warning(f"No market data found for ticker: {ticker_symbol}. It may be delisted or invalid.")
                    results[ticker_symbol] = {"error": "No market data found. The ticker may be invalid."}
                else:
                    results[ticker_symbol] = {
                        "currentPrice": info.get("regularMarketPrice"),
                        "dayHigh": info.get("dayHigh"),
                        "dayLow": info.get("dayLow"),
                        "volume": info.get("regularMarketVolume"),
                        "currency": info.get("currency")
                    }
            
            if not results:
                 raise Exception(f"Could not retrieve valid data for any of the tickers: {tickers}")

            return results

        except Exception as e:
            logger.error(f"Failed to get current stock prices for {tickers}: {e}")
            raise Exception(f"Failed to retrieve current stock prices: {e}") from e


    def get_historical_stock_data(self, ticker: str, period: str = "10y") -> List[Dict[str, Any]]:
        """
        Fetches historical market data for a stock with a yearly interval to keep
        the data volume manageable for an AI.

        Args:
            ticker: The stock symbol (e.g., "NVDA").
            period: The time frame (e.g., "5y", "10y"). Max is "20y". Defaults to "10y".

        Returns:
            A list of dictionaries, each representing one year of historical data.
        """
        logger.info(f"Fetching monthly historical data for {ticker} over period {period}")
        
        valid_periods = [f"{i}y" for i in range(1, 21)]
        if period not in valid_periods:
            raise ValueError(f"Invalid period '{period}'. Please use a value like '1y', '5y', up to '20y'.")

        try:
            stock = yf.Ticker(ticker)
            hist_df = stock.history(period=period, interval="1mo")

            if hist_df.empty:
                logger.warning(f"No historical data found for ticker: {ticker}")
                return []

            hist_df = hist_df.reset_index()
            hist_df['Date'] = pd.to_datetime(hist_df['Date'])
            hist_df['Date'] = hist_df['Date'].dt.strftime('%Y-%m-%d')
            
            for col in ['Open', 'High', 'Low', 'Close']:
                if col in hist_df.columns:
                    hist_df[col] = hist_df[col].round(2)

            return hist_df.to_dict('records') # type: ignore

        except Exception as e:
            logger.error(f"Failed to get historical data for {ticker}: {e}")
            raise Exception(f"Failed to retrieve historical data for {ticker}: {e}") from e


    def get_company_fundamental_info(self, ticker: str) -> Dict[str, Any]:
        """
        Retrieves key fundamental information about a company.

        Args:
            ticker: The stock symbol (e.g., "TSLA").

        Returns:
            A dictionary containing company name, sector, industry, business summary,
            P/E ratio, and market capitalization.
        """
        logger.info(f"Fetching fundamental info for ticker: {ticker}")
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            if not info or info.get('longName') is None:
                 raise Exception(f"Could not retrieve valid info for ticker: {ticker}. It may be invalid.")

            return {
                "symbol": info.get("symbol"),
                "companyName": info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "businessSummary": info.get("longBusinessSummary"),
                "marketCap": info.get("marketCap"),
                "trailingPE": info.get("trailingPE"),
                "forwardPE": info.get("forwardPE"),
                "dividendYield": info.get("dividendYield")
            }
        except Exception as e:
            logger.error(f"Failed to get fundamental info for {ticker}: {e}")
            raise Exception(f"Failed to retrieve fundamental info for {ticker}: {e}") from e


    def get_latest_company_news(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Fetches the latest news headlines for a specific company.

        Args:
            ticker: The stock symbol (e.g., "AMZN").

        Returns:
            A list of dictionaries, where each dictionary represents a news article
            with its title, publisher, and link.
        """
        logger.info(f"Fetching latest news for ticker: {ticker}")
        try:
            stock = yf.Ticker(ticker)
            news = stock.news

            if not news:
                logger.warning(f"No news found for ticker: {ticker}")
                return []
            
            formatted_news = []
            for article in news:
                content = article.get('content', {})
                if not content:
                    continue

                formatted_news.append({
                    "title": content.get("title"),
                    "summary": content.get("summary"),
                    "publisher": content.get("provider", {}).get("displayName"),
                    "link": content.get("canonicalUrl", {}).get("url"),
                    "publishDate": content.get("pubDate")
                })
            
            return formatted_news

        except Exception as e:
            logger.error(f"Failed to get news for {ticker}: {e}")
            raise Exception(f"Failed to retrieve news for {ticker}: {e}") from e
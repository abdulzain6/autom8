import re
import html2text
from typing import Any, List, Callable, Optional
from enum import Enum
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from bs4 import BeautifulSoup, Tag
from aci.common.db.sql_models import Base, LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import CYCLE_TLS_SERVER_URL, HTTP_PROXY
from pydantic import BaseModel, HttpUrl, Field
from ..cycletls_client import CycleTlsServerClient

# --- Pydantic Models for Structured Data Output ---


class Product(BaseModel):
    """Represents a product item in a search result list."""

    title: str
    url: HttpUrl
    price: str
    rating: float = 0.0
    review_count: int = 0
    asin: str
    image_url: HttpUrl


class DetailedProduct(BaseModel):
    """Represents detailed information for a single product page."""

    title: str
    price: str
    rating: float = 0.0
    review_count: int = 0
    description: str
    image_url: Optional[HttpUrl] = None
    product_info: str
    features: str
    seller_name: str
    seller_link: Optional[HttpUrl] = None
    video_urls: List[HttpUrl] = Field(default_factory=list)


# --- Configuration for Search Sorting ---


class SortOptions(Enum):
    """Enumeration for Amazon search result sorting options."""

    FEATURED = "relevanceblender"
    PRICE_LOW_TO_HIGH = "price-asc-rank"
    PRICE_HIGH_TO_LOW = "price-desc-rank"
    REVIEW_RANK = "review-rank"
    NEWEST_ARRIVALS = "date-desc-rank"
    BEST_SELLERS = "exact-aware-popularity-rank"


# --- Utility Functions ---


def transform_to_affiliate_link(product_url: str, affiliate_tag: str) -> str:
    """Transforms an Amazon product URL into an affiliate link."""
    if not product_url or not affiliate_tag:
        raise ValueError("Product URL and affiliate tag must be provided.")

    asin_match = re.search(r"/dp/([A-Z0-9]{10})", product_url)
    if asin_match:
        asin = asin_match.group(1)
        return f"https://www.amazon.com/dp/{asin}/?tag={affiliate_tag}"

    parsed_url = urlparse(product_url)
    query_params = parse_qs(parsed_url.query)
    query_params["tag"] = [affiliate_tag]
    new_query_string = urlencode(query_params, doseq=True)
    return urlunparse(parsed_url._replace(query=new_query_string))


# --- Main Amazon Scraper Connector Class ---


class Amazon(AppConnectorBase):
    """
    Connector for scraping product data from Amazon.
    This provides structured product search and detail lookup capabilities
    for an AI agent, using CycleTLS to bypass scraping protection.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
    ):
        """
        Initializes the Amazon connector.
        """
        super().__init__(linked_account, security_scheme, security_credentials)
        self.base_url = "https://www.amazon.com"
        self.client = CycleTlsServerClient(server_url=CYCLE_TLS_SERVER_URL, proxy=HTTP_PROXY)
        self.affiliate_tag = None
        self.text_converter = html2text.HTML2Text()
        self.text_converter.ignore_links = True
        self.text_converter.ignore_images = True

    def _safe_extract(self, func: Callable, default: Any = None) -> Any:
        """Safely executes a parsing function, returning a default value on failure."""
        try:
            result = func()
            return result if result is not None else default
        except (AttributeError, ValueError, TypeError, IndexError):
            return default

    # --- Public Methods (Agent Capabilities) ---

    def _before_execute(self) -> None:
        return super()._before_execute()

    def search_products(
        self,
        keyword: str,
        num_results: int = 20,
        sort_by: SortOptions = SortOptions.FEATURED,
        find_deals: bool = False,
    ) -> List[Product]:
        """
        Searches Amazon for a keyword and returns a list of products.

        Args:
            keyword: The search term (e.g., "wireless headphones").
            num_results: The desired number of products to return. Defaults to 20.
            sort_by: The sorting order for the results. Defaults to FEATURED.
            find_deals: Whether to search within "Today's Deals". Defaults to False.

        Returns:
            A list of Product objects, each representing a found item.
        """
        all_products = []
        page_number = 1
        while len(all_products) < num_results:
            params = {"k": keyword, "s": sort_by.value, "page": page_number}
            if find_deals:
                params["i"] = "todays-deals"

            search_url = f"{self.base_url}/s?{urlencode(params)}"
            html_content = self.client.get(search_url)

            if not html_content:
                break

            soup = BeautifulSoup(html_content, "html.parser")
            page_products = self._extract_products_from_page(soup)

            if not page_products:
                break

            all_products.extend(page_products)
            page_number += 1

        return all_products[:num_results]

    def get_product_details(self, product_url: str) -> Optional[DetailedProduct]:
        """
        Fetches and parses detailed information from a single product page.

        Args:
            product_url: The full URL of the Amazon product page.

        Returns:
            A DetailedProduct object containing comprehensive data, or None if scraping fails.
        """
        html = self.client.get(product_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        return self._parse_detailed_product_page(soup)

    # --- Internal Parsing Logic ---

    def _extract_products_from_page(self, soup: BeautifulSoup) -> List[Product]:
        """Extracts all product data from a single search result page."""
        product_divs = soup.find_all("div", {"data-component-type": "s-search-result"})
        products = [self._parse_product_div(div) for div in product_divs]
        return [p for p in products if p is not None]  # Filter out failed parses

    def _parse_product_div(self, div: Tag) -> Optional[Product]:
        """Parses a single product div from a search results page."""
        asin = self._safe_extract(lambda: div.get("data-asin"), "")
        if not asin:
            return None

        relative_url = self._safe_extract(
            lambda: div.find("a", class_="a-link-normal")["href"]
        )
        full_url = f"{self.base_url}{relative_url}" if relative_url else ""
        if self.affiliate_tag and full_url:
            full_url = transform_to_affiliate_link(full_url, self.affiliate_tag)

        rating_text = self._safe_extract(
            lambda: div.find("span", class_="a-icon-alt").text.split()[0], "0.0"
        )
        review_count_text = self._safe_extract(
            lambda: div.find("span", {"class": "a-size-base", "dir": "auto"}).text, "0"
        )

        try:
            return Product(
                title=self._safe_extract(
                    lambda: div.find("h2").get_text(strip=True), "N/A"
                ),
                url=full_url,
                price=self._safe_extract(
                    lambda: div.find("span", class_="a-offscreen").text, "N/A"
                ),
                rating=float(rating_text),
                review_count=int(re.sub(r"[^\d]", "", review_count_text)),
                asin=asin,
                image_url=self._safe_extract(
                    lambda: div.find("img", class_="s-image")["src"], ""
                ),
            )
        except (ValueError, TypeError):
            return None

    def _parse_detailed_product_page(self, soup: BeautifulSoup) -> DetailedProduct:
        """Parses the main content of a detailed product page."""
        review_count_text = self._safe_extract(
            lambda: soup.find(id="acrCustomerReviewText").get_text(strip=True), "0"
        )
        review_count_match = re.search(r"[\d,]+", review_count_text)

        return DetailedProduct(
            title=self._safe_extract(
                lambda: soup.find(id="productTitle").get_text(strip=True), ""
            ),
            price=self._safe_extract(
                lambda: soup.find(class_="priceToPay").get_text(strip=True), "N/A"
            ),
            rating=self._safe_extract(
                lambda: float(
                    soup.select_one(
                        "#acrPopover .a-size-base.a-color-base"
                    ).text.split()[0]
                ),
                0.0,
            ),
            review_count=(
                int(review_count_match.group().replace(",", ""))
                if review_count_match
                else 0
            ),
            description=self._safe_extract(
                lambda: soup.find(id="productDescription").get_text(strip=True), ""
            ),
            image_url=self._safe_extract(lambda: soup.find(id="landingImage")["src"]),
            product_info=self._safe_extract(
                lambda: self.text_converter.handle(
                    str(soup.find(id="centerCol"))
                ).strip(),
                "",
            ),
            features=self._safe_extract(
                lambda: self.text_converter.handle(
                    str(soup.find(id="aplus_feature_div"))
                ).strip(),
                "",
            ),
            seller_name=self._safe_extract(
                lambda: soup.find(id="bylineInfo").get_text(strip=True), "" # type: ignore
            ),
            seller_link=self._safe_extract(
                lambda: f"{self.base_url}{soup.find(id='bylineInfo')['href']}" # type: ignore
            ),
            video_urls=self._safe_extract(
                lambda: [
                    a["href"]
                    for a in soup.select("#vse-cards-vw-dp a")
                    if "vdp" in a.get("href", "") # type: ignore
                ],
                [],
            ),
        )
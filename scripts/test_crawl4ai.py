import asyncio
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig
from crawl4ai import UndetectedAdapter
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy

async def main():
    # Create an instance of AsyncWebCrawler
    undetected_adapter = UndetectedAdapter()
    browser_config = BrowserConfig(
        cdp_url="",
        browser_mode="cdp",
        proxy="",
        enable_stealth=True,
    )
    crawler_strategy = AsyncPlaywrightCrawlerStrategy(
        browser_config=browser_config,
        browser_adapter=undetected_adapter
    )

    async with AsyncWebCrawler(
        crawler_strategy=crawler_strategy,
        config=browser_config
    ) as crawler:
        # Run the crawler on a URL
        result = await crawler.arun(url="https://flightaware.com/live/flight/PIA306/")

        # Print the extracted content
        print(result.markdown)

# Run the async main function
asyncio.run(main())

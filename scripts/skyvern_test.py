from skyvern import Skyvern



async def main():
    skyvern = Skyvern(base_url="http://localhost:8000", api_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjQ5MDQxMjcxMzIsInN1YiI6Im9fNDQ0NDYwMTA3NTE1MTI3ODY4In0.XEEEGwigS7Rp4DnHh3JCxUUU8_fyJYdq9S4lgwOTXcM")

    task = await skyvern.run_task(prompt="Find the top post on hackernews today", wait_for_completion=True, max_steps=10)
    print(task)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
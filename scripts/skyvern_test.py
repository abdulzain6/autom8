from skyvern import Skyvern


async def main():
    skyvern = Skyvern(
        base_url="http://localhost:8000", 
        api_key=""
    )

    task = await skyvern.run_task(
        prompt="Find the top post on hackernews today", 
        wait_for_completion=True, 
        max_steps=10,
        model={"reasoning" : {"enabled" : False}}
    )

    print(task.output)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
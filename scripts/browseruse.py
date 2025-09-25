"""
AI-powered browser automation using browser-use library with Steel browsers.
https://github.com/steel-dev/steel-cookbook/tree/main/examples/steel-browser-use-starter
"""




import os
import time
import asyncio
from dotenv import load_dotenv
from steel import Steel
from browser_use import Agent, BrowserSession
from browser_use.llm import ChatOpenAI

load_dotenv()

# Replace with your own task
TASK = os.getenv("TASK") or "goto https://tankionline.com/en/news/ and tell me all events and news u see there, make me a report with all information from the page."

# Instructions for the AI agent to be quick and efficient
INSTRUCTIONS = "Be super quick and use as few actions as possible. Complete the task efficiently with minimal steps."


async def main():
    print("🚀 Steel + Browser Use Assistant")
    print("=" * 60)

    print("\nStarting Steel browser session...")

    client = Steel(base_url="http://localhost:3000")

    try:
        session = client.sessions.create()

        cdp_url = f"ws://localhost:3000?sessionId={session.id}"

        model = ChatOpenAI(
            model="Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
            temperature=0.3,
            api_key=os.getenv("TOGETHER_API_KEY"),
            base_url="https://api.together.xyz/v1"
        )
        agent = Agent(
            task=TASK,
            llm=model,
            browser_session=BrowserSession(cdp_url=cdp_url),
            flash_mode=True,
            extend_system_message=INSTRUCTIONS,
            max_actions_per_step=1,
            use_thinking=False,
            use_vision=False
        )

        start_time = time.time()

        print(f"🎯 Executing task: {TASK}")
        print("=" * 60)

        try:
            result = await agent.run(max_steps=10)

            duration = f"{(time.time() - start_time):.1f}"

            print("\n" + "=" * 60)
            print("🎉 TASK EXECUTION COMPLETED")
            print("=" * 60)
            print(f"⏱️  Duration: {duration} seconds")
            print(f"🎯 Task: {TASK}")
            if result:
                print(f"📋 Result:\n{result.final_result()}")
            print("=" * 60)

        except Exception as e:
            print(f"❌ Task execution failed: {e}")
        finally:
            if session:
                print("Releasing Steel session...")
                client.sessions.release(session.id)
                print(f"Session completed. View replay at {session.session_viewer_url}")
            print("Done!")

    except Exception as e:
        print(f"❌ Failed to start Steel browser: {e}")
        print("Please check your STEEL_API_KEY and internet connection.")


if __name__ == "__main__":
    asyncio.run(main())
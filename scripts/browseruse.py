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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "your-openai-api-key-here"

# Replace with your own task
TASK = os.getenv("TASK") or "check pakistan cricket team match vs sl today and if they are winning or losing on google"


async def main():
    print("🚀 Steel + Browser Use Assistant")
    print("=" * 60)

    print("\nStarting Steel browser session...")

    client = Steel(base_url="http://localhost:3000")

    try:
        session = client.sessions.create()

        cdp_url = f"ws://localhost:3000?sessionId={session.id}"

        model = ChatOpenAI(model="gpt-4o", temperature=0.3, api_key=OPENAI_API_KEY)
        agent = Agent(task=TASK, llm=model, browser_session=BrowserSession(cdp_url=cdp_url))

        start_time = time.time()

        print(f"🎯 Executing task: {TASK}")
        print("=" * 60)

        try:
            result = await agent.run()

            duration = f"{(time.time() - start_time):.1f}"

            print("\n" + "=" * 60)
            print("🎉 TASK EXECUTION COMPLETED")
            print("=" * 60)
            print(f"⏱️  Duration: {duration} seconds")
            print(f"🎯 Task: {TASK}")
            if result:
                print(f"📋 Result:\n{result}")
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
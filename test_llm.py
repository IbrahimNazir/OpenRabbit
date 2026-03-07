#!/usr/bin/env python3
"""Test LLM client."""

import asyncio
import sys
import os

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.llm.client import LLMClient

async def test_llm():
    # Test DeepSeek directly since Gemini is quota limited
    import os
    os.environ['LLM_PROVIDER'] = 'deepseek'
    
    client = LLMClient()
    try:
        print("Testing DeepSeek LLM client...")
        result, cost = await asyncio.wait_for(
            client.complete_with_json('{"test": "hello"}', system='You are a helpful assistant.'),
            timeout=30.0  # 30 second timeout
        )
        print(f'LLM test successful: {result}, cost: {cost}')
    except asyncio.TimeoutError:
        print("LLM test timed out")
    except Exception as e:
        print(f'LLM test failed: {e}')
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_llm())
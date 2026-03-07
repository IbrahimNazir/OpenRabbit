#!/usr/bin/env python
"""Direct test of POST connectivity to GitHub API."""

import asyncio
import httpx

async def test_github_post():
    """Test direct POST to GitHub API without retry logic"""
    url = "https://api.github.com/app/installations/113171699/access_tokens"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print(f"Testing POST to {url}")
            print("Sending request...")
            response = await client.post(
                url,
                headers={
                    "Authorization": "Bearer dummy_jwt_for_test",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30.0,
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text[:200]}")
        except httpx.ConnectError as e:
            print(f"ConnectError: {type(e).__name__}: {e}")
        except httpx.TimeoutException as e:
            print(f"TimeoutException: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"Other Exception ({type(e).__name__}): {e}")

if __name__ == "__main__":
    asyncio.run(test_github_post())

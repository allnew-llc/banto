#!/usr/bin/env python3
"""
Example 2: Image generation with per-image budget tracking.

Setup:
    banto store openai
    banto init

Usage:
    pip install openai
    python examples/02_image_generation.py
"""

from banto import SecureVault, BudgetExceededError, KeyNotFoundError

vault = SecureVault(caller="example-imagegen")


def generate_image(prompt: str, n: int = 1, quality: str = "standard", size: str = "1024x1024"):
    # Budget check + key retrieval
    api_key = vault.get_key(
        model="dall-e-3",
        n=n,
        quality=quality,
        size=size,
    )

    import openai

    client = openai.OpenAI(api_key=api_key)
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        n=n,
        quality=quality,
        size=size,
    )

    # Record usage
    vault.record_usage(
        model="dall-e-3",
        n=n,
        quality=quality,
        size=size,
        provider="openai",
        operation="image",
    )

    return [img.url for img in response.data]


def main():
    try:
        # Check cost before committing
        cost = vault.estimate_cost("dall-e-3", n=2, quality="hd", size="1024x1024")
        print(f"Estimated cost for 2 HD images: ${cost:.3f}")

        status = vault.get_budget_status()
        print(f"Remaining budget: ${status['remaining_usd']:.2f}")

        # Generate
        urls = generate_image("A bantō managing a merchant storehouse in Edo Japan", n=1)
        for url in urls:
            print(f"Image: {url}")

    except BudgetExceededError as e:
        print(f"Cannot generate: ${e.requested:.3f} requested, ${e.remaining:.2f} remaining")

    except KeyNotFoundError as e:
        print(f"Run: banto store {e.provider}")


if __name__ == "__main__":
    main()

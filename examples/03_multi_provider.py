#!/usr/bin/env python3
"""
Example 3: Multi-provider routing with shared budget.

Shows how a single vault manages keys for multiple providers
while tracking all costs against one monthly budget.

Setup:
    banto store openai
    banto store google
    banto init

Usage:
    pip install openai google-genai
    python examples/03_multi_provider.py
"""

from banto import SecureVault, BudgetExceededError, KeyNotFoundError

vault = SecureVault(caller="example-multi")


def openai_chat(prompt: str) -> str:
    """Chat via OpenAI -- vault auto-resolves provider from model name."""
    api_key = vault.get_key(
        model="gpt-4o-mini",
        input_tokens=len(prompt) // 4,
        output_tokens=300,
    )

    import openai

    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
    )

    vault.record_usage(
        model="gpt-4o-mini",
        input_tokens=resp.usage.prompt_tokens,
        output_tokens=resp.usage.completion_tokens,
        provider="openai",
        operation="chat",
    )
    return resp.choices[0].message.content


def google_image(prompt: str) -> str:
    """Generate image via Google -- same vault, same budget."""
    api_key = vault.get_key(
        model="imagen-4.0-generate-001",
        n=1,
    )

    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_images(
        model="imagen-4.0-generate-001",
        prompt=prompt,
    )

    vault.record_usage(
        model="imagen-4.0-generate-001",
        n=1,
        provider="google",
        operation="image",
    )
    return f"Generated {len(resp.generated_images)} image(s)"


def main():
    status = vault.get_budget_status()
    print(f"Budget: ${status['used_usd']:.2f} / ${status['monthly_limit_usd']:.2f}\n")

    try:
        # Both providers draw from the same monthly budget
        text = openai_chat("Describe a traditional Japanese storehouse in one sentence.")
        print(f"[OpenAI]  {text}\n")

        result = google_image("Traditional Japanese kura storehouse, watercolor style")
        print(f"[Google]  {result}\n")

    except BudgetExceededError as e:
        print(f"Shared budget exceeded: ${e.remaining:.2f} remaining")

    except KeyNotFoundError as e:
        print(f"Missing key. Run: banto store {e.provider}")

    # Final status -- both providers' costs are tracked together
    status = vault.get_budget_status()
    print(f"Budget after: ${status['used_usd']:.2f} / ${status['monthly_limit_usd']:.2f}")


if __name__ == "__main__":
    main()

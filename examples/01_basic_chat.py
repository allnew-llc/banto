#!/usr/bin/env python3
"""
Example 1: Basic LLM chat with budget-gated key access.

Setup:
    banto store openai
    banto init

Usage:
    pip install openai
    python examples/01_basic_chat.py
"""

from banto import SecureVault, BudgetExceededError, KeyNotFoundError

vault = SecureVault(caller="example-chat")


def chat(prompt: str) -> str:
    # Rough token estimate: 1 token ≈ 4 chars
    est_input = len(prompt) // 4
    est_output = 500  # conservative estimate

    # Budget hold + key retrieval (estimated cost reserved upfront)
    api_key = vault.get_key(
        model="gpt-4o",
        input_tokens=est_input,
        output_tokens=est_output,
    )

    # --- API call (key only exists in this scope) ---
    import openai

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=est_output,
    )

    # Settle hold with actual usage (frees surplus budget)
    vault.record_usage(
        model="gpt-4o",
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        provider="openai",
        operation="chat",
    )

    return response.choices[0].message.content


def main():
    try:
        answer = chat("What is the Edo period in Japan? Answer in 2 sentences.")
        print(answer)

        # Show remaining budget
        status = vault.get_budget_status()
        print(f"\n--- Budget: ${status['used_usd']:.2f} / ${status['monthly_limit_usd']:.2f} ---")

    except BudgetExceededError as e:
        print(f"Budget exceeded: ${e.remaining:.2f} remaining of ${e.limit:.2f} limit")

    except KeyNotFoundError as e:
        print(f"No API key found. Run: banto store {e.provider}")


if __name__ == "__main__":
    main()

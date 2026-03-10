#!/usr/bin/env python3
"""
Example 4: MCP server integration pattern.

Shows how to integrate banto into an MCP server so that every
tool call goes through budget-gated key access.

This is a simplified skeleton -- adapt to your MCP framework.

Setup:
    banto store openai
    banto init
"""

from banto import SecureVault, BudgetExceededError, KeyNotFoundError

# Initialize once at server startup
vault = SecureVault(caller="my-mcp-server")


def handle_generate_image(params: dict) -> dict:
    """MCP tool handler: generate an image."""
    model = params.get("model", "dall-e-3")
    prompt = params["prompt"]
    quality = params.get("quality", "standard")
    size = params.get("size", "1024x1024")
    n = params.get("n", 1)

    try:
        # Budget hold + key retrieval (cost reserved upfront)
        api_key = vault.get_key(
            model=model,
            n=n,
            quality=quality,
            size=size,
        )
    except BudgetExceededError as e:
        # Return structured error to the LLM
        return {
            "error": "budget_exceeded",
            "message": f"Monthly budget exceeded. ${e.remaining:.2f} remaining.",
            "remaining_usd": e.remaining,
            "limit_usd": e.limit,
        }
    except KeyNotFoundError as e:
        return {
            "error": "key_not_found",
            "message": f"No API key for '{e.provider}'. Run: banto store {e.provider}",
        }

    # --- Call the API ---
    import openai

    client = openai.OpenAI(api_key=api_key)
    response = client.images.generate(
        model=model,
        prompt=prompt,
        n=n,
        quality=quality,
        size=size,
    )

    # Settle hold with actual usage
    vault.record_usage(
        model=model,
        n=n,
        quality=quality,
        size=size,
        provider="openai",
        operation="image",
    )

    return {
        "urls": [img.url for img in response.data],
        "cost_usd": vault.estimate_cost(model, n=n, quality=quality, size=size),
    }


def handle_budget_status(_params: dict) -> dict:
    """MCP tool handler: check budget status."""
    return vault.get_budget_status()


# --- Example MCP tool registration (pseudo-code) ---
#
# server.register_tool("generate_image", handle_generate_image)
# server.register_tool("budget_status", handle_budget_status)


if __name__ == "__main__":
    # Simulate a tool call
    print("=== Simulating MCP tool call ===\n")

    result = handle_generate_image({
        "prompt": "A bantō at work in a merchant house",
        "quality": "standard",
        "size": "1024x1024",
    })

    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))

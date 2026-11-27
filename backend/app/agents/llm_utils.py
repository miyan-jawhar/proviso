"""Small compatibility helpers for responses returned by supported LLM providers."""

from typing import Any


def response_text(response: Any) -> str:
    """Return the textual portion of a LangChain chat-model response.

    OpenAI and Ollama normally return ``AIMessage.content`` as a string. Gemini
    can return a list of typed content blocks, so agents should normalize the
    response before parsing JSON, SQL, or prose.
    """
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_blocks: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_blocks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text_blocks.append(block["text"])
            elif isinstance(getattr(block, "text", None), str):
                text_blocks.append(block.text)
        if text_blocks:
            return "\n".join(text_blocks)

    fallback = getattr(response, "text", None)
    if isinstance(fallback, str):
        return fallback
    return str(content)

import sys
import anthropic


MODEL = "claude-sonnet-4-6"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_llm(prompt: str, system_prompt: str | None = None) -> str:
    kwargs = {
        "model": MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = _get_client().messages.create(**kwargs)

    usage = response.usage
    print(
        f"[LLM] tokens: {usage.input_tokens} in / {usage.output_tokens} out",
        file=sys.stderr,
    )

    return response.content[0].text

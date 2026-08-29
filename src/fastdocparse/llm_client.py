"""OpenAI-compatible LLM client for document extraction."""

import time
from typing import List, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam


class LLMClientError(Exception):
    """Raised when a call to the LLM endpoint fails (bad auth, unreachable, or exhausted retries)."""


class LLMClient:
    """A thin adapter over any OpenAI-compatible endpoint."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the LLM client.

        Args:
            base_url: The base URL of the OpenAI-compatible API. Defaults to OpenAI if None.
            api_key: The API key. Can be dummy for local endpoints like Ollama.
            model: The model string to use for completions.
        """
        # If not provided, it will fallback to standard environment variables if used.
        self.base_url = base_url
        self.api_key = api_key or "dummy-key-for-local"
        self.model = model or "gpt-4o-mini"

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=60.0
        )

    def _call(self, messages: List[ChatCompletionMessageParam], temperature: float, max_tokens: int, retries: int = 2, backoff: float = 1.0) -> str:
        """Run a chat completion, retrying transient failures and raising LLMClientError on exhaustion."""
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except AuthenticationError as e:
                raise LLMClientError(f"Authentication failed for model '{self.model}'. Check your API key.") from e
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_error = e
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    continue
            except Exception as e:
                raise LLMClientError(f"LLM call to '{self.model}' failed: {e}") from e

        raise LLMClientError(
            f"Could not reach the LLM endpoint at {self.base_url or 'https://api.openai.com'} "
            f"after {retries + 1} attempts: {last_error}"
        )

    def extract(self, prompt: str, document_text: str, temperature: float = 0.0, max_tokens: int = 4096) -> str:
        """Run the prompt against the LLM to get the structured extraction."""
        final_prompt = prompt.replace("{document_text}", document_text)
        return self._call(
            [
                {"role": "system", "content": "You are a ultra-fast document extractor. Output strict JSON."},
                {"role": "user", "content": final_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete(self, prompt: str, temperature: float = 0.0, max_tokens: int = 2048) -> str:
        """Run a single free-form prompt against the LLM (no document-extraction framing)."""
        return self._call([{"role": "user", "content": prompt}], temperature=temperature, max_tokens=max_tokens)

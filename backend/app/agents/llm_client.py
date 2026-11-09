import os
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


class LLMConfigurationError(RuntimeError):
    """Raised when the OpenAI model configuration is incomplete."""


class StructuredChatModel(Protocol):
    def with_structured_output(self, schema: type[BaseModel]) -> Any: ...


@dataclass(frozen=True)
class AgentModelBundle:
    chat_model: StructuredChatModel
    provider: str
    model_name: str


def create_openai_agent_models() -> AgentModelBundle:
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    model_name = os.environ.get('OPENAI_MODEL', '').strip()
    if not api_key or not model_name:
        raise LLMConfigurationError(
            'OPENAI_API_KEY and OPENAI_MODEL must be configured for live agent runs.'
        )

    from langchain_openai import ChatOpenAI

    chat_model = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        temperature=0,
        timeout=15,
        max_retries=0,
    )
    return AgentModelBundle(
        chat_model=chat_model,
        provider='openai',
        model_name=model_name,
    )

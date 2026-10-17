"""GPT-6 model setup for the Billify accounts-payable agent."""

import os

from langchain_openai import ChatOpenAI
from prismtrace import PRISMtraceCallbackHandler


def create_llm() -> ChatOpenAI:
    """Create the agent's model with PRISM tracing enabled.

    Load NICRRON_API_KEY and PRISMTRACE_API_KEY into the environment before
    calling this function. No network requests are made until the model is used.
    """
    for name in ("NICRRON_API_KEY", "PRISMTRACE_API_KEY"):
        if not os.environ.get(name):
            raise ValueError(f"Set the {name} environment variable before running the agent.")

    handler = PRISMtraceCallbackHandler(
        api_key=os.environ["PRISMTRACE_API_KEY"],
        project_id="091ed2f5-9be8-4f04-8ec7-1a174f59ae30",
        agent_name="Billify AP Agent",
    )
    return ChatOpenAI(
        model="openai/gpt-6-astra",
        base_url="https://api.nicrron.ai/v1",
        api_key=os.environ["NICRRON_API_KEY"],
        callbacks=[handler],
    )

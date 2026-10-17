"""Anthropic model setup for the Billify accounts-payable agent."""

import os

from langchain_anthropic import ChatAnthropic
from prismtrace import PRISMtraceCallbackHandler


def create_llm() -> ChatAnthropic:
    """Create the agent's model with PRISM tracing enabled.

    Load ANTHROPIC_API_KEY and PRISMTRACE_API_KEY into the environment before
    calling this function. No network requests are made until the model is used.
    """
    for name in ("ANTHROPIC_API_KEY", "PRISMTRACE_API_KEY"):
        if not os.environ.get(name):
            raise ValueError(f"Set the {name} environment variable before running the agent.")

    handler = PRISMtraceCallbackHandler(
        api_key=os.environ["PRISMTRACE_API_KEY"],
        project_id="091ed2f5-9be8-4f04-8ec7-1a174f59ae30",
        agent_name="Billify AP Agent",
    )
    return ChatAnthropic(model="claude-sonnet-4-6", callbacks=[handler])

from dataclasses import dataclass


@dataclass
class ToolResult:
    call_id: str
    status: str
    output: dict


@dataclass
class ReadOnlyAnswer:
    content: str
    sources: list[dict]
    steps: list[dict]
    model: str
    tokens_input: int = 0
    tokens_output: int = 0


class AgentExecutionError(ValueError):
    """An incomplete read-only run, never saved as a completed chat answer."""
    def __init__(self, code: str, steps: list[dict] | None = None):
        super().__init__(code)
        self.code=code
        self.steps=steps or []

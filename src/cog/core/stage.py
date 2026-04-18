from collections.abc import Callable
from dataclasses import dataclass

from cog.core.context import ExecutionContext
from cog.core.runner import AgentRunner


@dataclass(frozen=True)
class Stage:
    name: str
    prompt_source: Callable[[ExecutionContext], str]
    model: str
    runner: AgentRunner
    interactive: bool = False
    # True → failures stored in StageResult.error; executor continues to next stage
    tolerate_failure: bool = False


def static_prompt(resource: str) -> Callable[[ExecutionContext], str]:
    """Returns a prompt_source that loads `resource` via importlib.resources from
    cog.prompts and ignores ctx. Use for static template files."""

    def _load(_ctx: ExecutionContext) -> str:
        import importlib.resources

        pkg = importlib.resources.files("cog.prompts")
        return pkg.joinpath(resource).read_text(encoding="utf-8")

    return _load

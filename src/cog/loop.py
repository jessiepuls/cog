import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from cog.core.context import ExecutionContext


@dataclass
class LoopState:
    """Cross-iteration accumulators (counter, cumulative cost)."""

    iteration: int = 0
    cumulative_cost_usd: float = 0.0


def fresh_iteration_context(
    base: ExecutionContext, *, preserve_item: bool = False
) -> ExecutionContext:
    """New ExecutionContext for the next iteration.

    Preserves project_dir / state_cache / telemetry / event_sink / input_provider / headless.
    Resets work_branch. Creates a new tmp_dir.
    Resets item unless `preserve_item=True` — used on the first iteration when the caller
    pre-populated `base.item` (e.g. `cog --item N` or a main-menu picker selection).
    """
    new_tmp = Path(tempfile.mkdtemp(prefix="cog-"))
    if preserve_item:
        return replace(base, tmp_dir=new_tmp, work_branch=None)
    return replace(base, tmp_dir=new_tmp, item=None, work_branch=None)

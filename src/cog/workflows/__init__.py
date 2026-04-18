from cog.core.workflow import Workflow
from cog.workflows.ralph import RalphWorkflow

# Concrete workflows append themselves here when they land.
# DummyWorkflow is intentionally excluded — it's a test harness, not a real workflow.
WORKFLOWS: list[type[Workflow]] = [RalphWorkflow]
# RefineWorkflow appended in #18/#19

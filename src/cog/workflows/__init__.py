from cog.core.workflow import Workflow

# Concrete workflows append themselves here when they land.
# DummyWorkflow is intentionally excluded — it's a test harness, not a real workflow.
WORKFLOWS: list[type[Workflow]] = []

from cog.core.workflow import Workflow
from cog.workflows.ralph import RalphWorkflow
from cog.workflows.refine import RefineWorkflow

WORKFLOWS: list[type[Workflow]] = [RalphWorkflow, RefineWorkflow]

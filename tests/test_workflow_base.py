"""Tests for Workflow base class ClassVar defaults."""

from cog.core.workflow import Workflow
from cog.workflows.dummy import DummyWorkflow
from cog.workflows.ralph import RalphWorkflow
from cog.workflows.refine import RefineWorkflow


def test_workflow_needs_item_picker_defaults_false():
    assert Workflow.needs_item_picker is False


def test_ralph_workflow_needs_item_picker_false():
    assert RalphWorkflow.needs_item_picker is False


def test_dummy_workflow_needs_item_picker_false():
    assert DummyWorkflow.needs_item_picker is False


def test_refine_workflow_needs_item_picker_true():
    assert RefineWorkflow.needs_item_picker is True

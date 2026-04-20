"""CogShellScreen — persistent sidebar + content shell (#121, #122).

Replaces the old screen-stack root (`MainMenuScreen`) with a single screen
that hosts all top-level views as mounted-but-toggled widgets. Workers
living on a view keep running when the user flips to another view, which
unlocks multitasking (e.g. start a ralph run, flip to refine, return to
ralph and see progress).

Real view content lands in follow-up sub-issues (#123-#125). This file
stubs the three views so the navigation shell is reviewable on its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from cog.core.tracker import IssueTracker
from cog.ui.messages import ViewAttention
from cog.ui.views.dashboard import DashboardView
from cog.ui.views.ralph import RalphView
from cog.ui.views.refine import RefineView


@dataclass(frozen=True)
class ShellView:
    """One top-level view displayed in the shell."""

    id: str  # DOM id and view key (e.g. "dashboard")
    label: str  # human label shown in the sidebar (e.g. "Dashboard")
    keybind: str  # key that activates this view (e.g. "ctrl+1")


# Order here == sidebar order + keybind assignment (Ctrl+1..N).
_VIEWS: tuple[ShellView, ...] = (
    ShellView(id="dashboard", label="Dashboard", keybind="ctrl+1"),
    ShellView(id="refine", label="Refine", keybind="ctrl+2"),
    ShellView(id="ralph", label="Ralph", keybind="ctrl+3"),
)


class _StubView(Widget):
    """Placeholder view widget — replaced with real content in #123-#125."""

    def __init__(self, view: ShellView) -> None:
        super().__init__(id=f"view-{view.id}")
        self._view = view

    def compose(self) -> ComposeResult:
        yield Static(
            f"[dim]{self._view.label} view — coming in a follow-up issue.[/dim]",
            id=f"stub-{self._view.id}",
        )


class Sidebar(Widget):
    """Left sidebar listing views with keybind hints."""

    DEFAULT_CSS = """
    Sidebar {
        width: 24;
        border-right: solid $primary;
        background: $surface;
    }
    Sidebar #sidebar-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: $text-muted;
    }
    Sidebar ListView {
        background: $surface;
    }
    Sidebar ListItem {
        padding: 0 1;
    }
    Sidebar ListItem.-active {
        background: $accent;
        color: $text;
    }
    """

    def __init__(self, views: Iterable[ShellView]) -> None:
        super().__init__()
        self._views = tuple(views)
        self._attention: set[str] = set()

    def _label_for(self, v: ShellView) -> str:
        keybind = f"[dim]{v.keybind.replace('ctrl+', '^')}[/dim]"
        dot = "[yellow]●[/yellow] " if v.id in self._attention else "  "
        return f"{keybind} {dot}{v.label}"

    def compose(self) -> ComposeResult:
        yield Static("cog", id="sidebar-title")
        yield ListView(
            *(
                ListItem(
                    Label(self._label_for(v)),
                    id=f"nav-{v.id}",
                )
                for v in self._views
            ),
            id="sidebar-nav",
        )

    def set_attention(self, view_id: str, on: bool) -> None:
        if on:
            if view_id in self._attention:
                return
            self._attention.add(view_id)
        else:
            if view_id not in self._attention:
                return
            self._attention.discard(view_id)
        self._rerender_row(view_id)

    def _rerender_row(self, view_id: str) -> None:
        target = next((v for v in self._views if v.id == view_id), None)
        if target is None:
            return
        try:
            row = self.query_one(f"#nav-{view_id}", ListItem)
        except Exception:  # noqa: BLE001 — not yet mounted
            return
        label = row.query_one(Label)
        label.update(self._label_for(target))


class CogShellScreen(Screen):
    """Persistent shell with sidebar navigation and mounted-always content views.

    All views are mounted on startup; switching toggles `display`. Workers
    owned by a view widget keep running when the view is inactive — widgets
    stay in the tree.
    """

    DEFAULT_CSS = """
    CogShellScreen {
        layout: vertical;
    }
    CogShellScreen #shell-body {
        height: 1fr;
    }
    CogShellScreen #content-area {
        height: 1fr;
        padding: 1;
    }
    """

    BINDINGS = [
        *(Binding(v.keybind, f"switch_to('{v.id}')", v.label) for v in _VIEWS),
        Binding("ctrl+q", "quit_app", "Quit"),
    ]

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker
        self._active_view_id: str = _VIEWS[0].id

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="shell-body"):
            yield Sidebar(_VIEWS)
            with Container(id="content-area"):
                yield DashboardView(self._project_dir, self._tracker)
                yield RefineView(self._project_dir, self._tracker)
                yield RalphView(self._project_dir, self._tracker)
        yield Footer()

    def on_mount(self) -> None:
        self._apply_active_view()
        self._highlight_sidebar_row(self._active_view_id)

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_switch_to(self, view_id: str) -> None:
        if view_id == self._active_view_id:
            return
        if view_id not in {v.id for v in _VIEWS}:
            return
        self._active_view_id = view_id
        self._apply_active_view()
        self._highlight_sidebar_row(view_id)
        self._clear_attention(view_id)

    def on_view_attention(self, event: ViewAttention) -> None:
        # Don't mark the currently-active view — user is already looking at it.
        if event.view_id == self._active_view_id:
            return
        try:
            sidebar = self.query_one(Sidebar)
        except Exception:  # noqa: BLE001 — not yet mounted
            return
        sidebar.set_attention(event.view_id, True)
        if event.reason:
            self.app.notify(f"{event.view_id}: {event.reason}", timeout=4)

    def _clear_attention(self, view_id: str) -> None:
        try:
            sidebar = self.query_one(Sidebar)
        except Exception:  # noqa: BLE001
            return
        sidebar.set_attention(view_id, False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        chosen_id = (event.item.id or "").removeprefix("nav-")
        if chosen_id:
            self.action_switch_to(chosen_id)

    def _apply_active_view(self) -> None:
        for v in _VIEWS:
            widget = self.query_one(f"#view-{v.id}")
            widget.display = v.id == self._active_view_id
            if v.id == self._active_view_id and hasattr(widget, "focus_content"):
                # Defer focus until the layout pass finishes; otherwise the
                # target widget's focusable state may not be settled yet.
                self.call_after_refresh(widget.focus_content)

    def _highlight_sidebar_row(self, view_id: str) -> None:
        list_view = self.query_one("#sidebar-nav", ListView)
        for i, v in enumerate(_VIEWS):
            row = list_view.children[i]
            if v.id == view_id:
                row.add_class("-active")
                list_view.index = i
            else:
                row.remove_class("-active")

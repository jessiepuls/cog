"""CogShellScreen — persistent sidebar + content shell (#121, #122, #192).

Replaces the old screen-stack root (`MainMenuScreen`) with a single screen
that hosts all top-level views as mounted-but-toggled widgets. Workers
living on a view keep running when the user flips to another view, which
unlocks multitasking (e.g. start a ralph run, flip to refine, return to
ralph and see progress).

Dynamic slots (#192): parallel workflow runs launched from the Issues view
are appended below the static rows in the sidebar and numbered Ctrl+6 onward.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.events import Key
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListItem, ListView

from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.ui.dynamic_slots import DynamicSlot, DynamicSlotRegistry, max_concurrent_implements
from cog.ui.messages import LaunchSlotRequest, SlotDismissed, SlotStateChanged, ViewAttention
from cog.ui.views.chat import ChatView
from cog.ui.views.dashboard import DashboardView
from cog.ui.views.issues import IssuesView
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
    ShellView(id="issues", label="Issues", keybind="ctrl+2"),
    ShellView(id="refine", label="Refine", keybind="ctrl+3"),
    ShellView(id="ralph", label="Ralph", keybind="ctrl+4"),
    ShellView(id="chat", label="Chat", keybind="ctrl+5"),
)

_STATIC_COUNT = len(_VIEWS)


class Sidebar(Widget):
    """Left sidebar listing views with keybind hints.

    Static rows are rendered once and updated in-place. Dynamic rows
    (slots) are rebuilt asynchronously whenever the registry changes.
    """

    _SIDEBAR_WIDTH = 28
    # Content width inside a row = sidebar width - ListView scroll/padding
    # reservations. Tuned empirically.
    _ROW_CONTENT_WIDTH = 22

    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        border-right: solid $primary;
        background: $surface;
        padding-top: 1;
    }
    Sidebar ListView {
        background: $surface;
    }
    Sidebar ListItem {
        padding: 0 1;
        border-left: blank;
        height: 1;
    }
    Sidebar ListItem:hover {
        background: $surface-lighten-1;
    }
    Sidebar ListItem.-active {
        border-left: thick $accent;
        background: $surface-lighten-1;
        text-style: bold;
    }
    Sidebar ListItem.-divider {
        color: $text-muted;
    }
    """

    def __init__(self, views: Iterable[ShellView]) -> None:
        super().__init__()
        self._views = tuple(views)
        self._attention: set[str] = set()
        self._counts: dict[str, int | None] = {}

    def set_count(self, view_id: str, count: int | None) -> None:
        self._counts[view_id] = count
        self._rerender_row(view_id)

    def _label_for(self, v: ShellView) -> str:
        # Layout: [keybind][space][name + dot][padding][count slot (3)]
        dot = " [yellow]●[/yellow]" if v.id in self._attention else "  "
        name = f"{v.label}{dot}"
        keybind = v.keybind.replace("ctrl+", "^")
        visible_name_len = len(v.label) + 2  # name + dot/spacer

        count = self._counts.get(v.id)
        count_slot = f"[dim]{count:>3}[/dim]" if count is not None else "   "

        # Reserve len(keybind) + 1 (gap) on the left, 3 for count slot on the right.
        pad = max(1, self._ROW_CONTENT_WIDTH - len(keybind) - 1 - visible_name_len - 3)
        return f"[dim]{keybind}[/dim] {name}{' ' * pad}{count_slot}"

    def compose(self) -> ComposeResult:
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

    async def update_dynamic_slots(self, slots: list[DynamicSlot]) -> None:
        """Rebuild the dynamic section (divider + slot rows) below the static rows."""
        list_view = self.query_one("#sidebar-nav", ListView)
        # Remove all children after the static rows
        children = list(list_view.children)
        for child in children[_STATIC_COUNT:]:
            await child.remove()

        if not slots:
            return

        # Divider (non-selectable)
        await list_view.append(
            ListItem(
                Label("[dim]──────────[/dim]"),
                id="nav-divider",
                disabled=True,
                classes="-divider",
            )
        )
        # Slot rows
        for i, slot in enumerate(slots):
            keybind = f"ctrl+{_STATIC_COUNT + 1 + i}"
            await list_view.append(
                ListItem(
                    Label(slot.sidebar_label(keybind)),
                    id=f"nav-slot-{slot.run_id}",
                )
            )

    def rerender_slot_row(self, slot: DynamicSlot, index: int) -> None:
        """Update an existing dynamic slot row label in-place (no DOM changes)."""
        try:
            row = self.query_one(f"#nav-slot-{slot.run_id}", ListItem)
        except Exception:  # noqa: BLE001
            return
        keybind = f"ctrl+{_STATIC_COUNT + 1 + index}"
        row.query_one(Label).update(slot.sidebar_label(keybind))


class CogShellScreen(Screen):
    """Persistent shell with sidebar navigation and mounted-always content views.

    All static views are mounted on startup; switching toggles `display`.
    Dynamic slot views (#192) are mounted at launch time and removed on dismiss.
    Workers owned by a view widget keep running when the view is inactive.
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
        self._registry = DynamicSlotRegistry(on_change=self._on_registry_changed)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="shell-body"):
            yield Sidebar(_VIEWS)
            with Container(id="content-area"):
                yield DashboardView(self._project_dir, self._tracker)
                yield IssuesView(self._project_dir, self._tracker)
                yield RefineView(self._project_dir, self._tracker)
                yield RalphView(self._project_dir, self._tracker)
                yield ChatView(self._project_dir)
        yield Footer()

    def on_mount(self) -> None:
        self._apply_active_view()
        self._highlight_sidebar_row(self._active_view_id)
        ralph_view = self.query_one(RalphView)
        refine_view = self.query_one(RefineView)
        self.watch(ralph_view, "queue_count", self._on_ralph_count_changed)
        self.watch(refine_view, "queue_count", self._on_refine_count_changed)

    def _on_ralph_count_changed(self, count: int | None) -> None:
        try:
            self.query_one(Sidebar).set_count("ralph", count)
        except Exception:  # noqa: BLE001 — not yet mounted
            pass

    def _on_refine_count_changed(self, count: int | None) -> None:
        try:
            self.query_one(Sidebar).set_count("refine", count)
        except Exception:  # noqa: BLE001 — not yet mounted
            pass

    # -------------------------------------------------------------------------
    # Dynamic slot management
    # -------------------------------------------------------------------------

    def _on_registry_changed(self) -> None:
        """Called synchronously by the registry; schedule async sidebar rebuild.

        Pass the callable (not the coroutine) so that if the worker is
        cancelled before it runs, no "coroutine was never awaited" warning fires.
        """
        self.run_worker(
            self._rebuild_sidebar_dynamic,  # type: ignore[arg-type]
            exclusive=True,
            group="sidebar-dynamic",
        )

    async def _rebuild_sidebar_dynamic(self) -> None:
        try:
            sidebar = self.query_one(Sidebar)
        except Exception:  # noqa: BLE001
            return
        await sidebar.update_dynamic_slots(self._registry.active_slots)
        # Re-apply active highlight (the row may have been re-created).
        self._highlight_sidebar_row(self._active_view_id)

    def on_slot_state_changed(self, msg: SlotStateChanged) -> None:
        self._registry.update_state(msg.run_id, msg.state, errored=msg.errored)
        self._registry.update_stage(msg.run_id, msg.stage)
        # In-place label update for the affected slot (avoids full rebuild)
        active = self._registry.active_slots
        try:
            idx = next(i for i, s in enumerate(active) if s.run_id == msg.run_id)
            slot = active[idx]
            try:
                self.query_one(Sidebar).rerender_slot_row(slot, idx)
            except Exception:  # noqa: BLE001
                pass
        except StopIteration:
            pass

    def on_slot_dismissed(self, msg: SlotDismissed) -> None:
        self._registry.remove(msg.run_id)
        try:
            view = self.query_one(f"#view-slot-{msg.run_id}")
            view.remove()
        except Exception:  # noqa: BLE001
            pass
        # If this was the active view, fall back to dashboard
        if self._active_view_id == f"slot-{msg.run_id}":
            self._active_view_id = _VIEWS[0].id
            self._apply_active_view()
            self._highlight_sidebar_row(self._active_view_id)

    def on_launch_slot_request(self, msg: LaunchSlotRequest) -> None:
        self.run_worker(
            self._launch_slot(msg.workflow, msg.item),
            exclusive=False,
            group="launch-slot",
        )

    async def _launch_slot(
        self,
        workflow: str,
        item: Item,  # SlotWorkflow but str avoids circular
    ) -> None:
        from cog.ui.dynamic_slots import SlotWorkflow
        from cog.ui.widgets.dynamic_slot_view import DynamicSlotView

        wf: SlotWorkflow = workflow  # type: ignore[assignment]

        # Dedup: if a slot for this (workflow, item_id) already exists, focus it.
        existing = self._registry.get(wf, item.item_id)
        if existing is not None:
            self._switch_to_slot(existing.run_id)
            return

        # Concurrency cap for implement.
        if wf == "implement":
            cap = max_concurrent_implements()
            if self._registry.active_count("implement") >= cap:
                self.app.notify(
                    f"{cap} implement run(s) active; dismiss one to start another",
                    severity="warning",
                )
                return

        run_id = DynamicSlotRegistry.new_run_id()
        slot = DynamicSlot(run_id=run_id, workflow=wf, item_id=item.item_id)
        view = DynamicSlotView(slot, self._project_dir, self._tracker, item, self._registry)

        content_area = self.query_one("#content-area")
        await content_area.mount(view)
        view.display = False

        # Add to registry (triggers sidebar rebuild)
        self._registry.add(slot)

        view.start_run()

        # Toast with Ctrl-N hint (slot was just added, so count it)
        static_count = _STATIC_COUNT
        slot_idx = len(self._registry.active_slots) - 1
        ctrl_n = f"Ctrl+{static_count + 1 + slot_idx}"
        self.app.notify(f"Started {wf} #{item.item_id} ({ctrl_n} to view)", timeout=5)

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def on_key(self, event: Key) -> None:
        """Intercept Ctrl+6..N to activate dynamic slots."""
        key = event.key
        if not key.startswith("ctrl+"):
            return
        try:
            n = int(key.removeprefix("ctrl+"))
        except ValueError:
            return
        if n <= _STATIC_COUNT:
            return  # handled by BINDINGS
        slot_idx = n - _STATIC_COUNT - 1  # 0-based
        slots = self._registry.active_slots
        if 0 <= slot_idx < len(slots):
            event.stop()
            self._switch_to_slot(slots[slot_idx].run_id)

    def _switch_to_slot(self, run_id: str) -> None:
        view_id = f"slot-{run_id}"
        if self._active_view_id == view_id:
            return
        self._active_view_id = view_id
        self._apply_active_view()
        self._highlight_sidebar_row(view_id)

    def action_quit_app(self) -> None:
        busy: list[str] = []
        for v in _VIEWS:
            try:
                widget = self.query_one(f"#view-{v.id}")
            except Exception:  # noqa: BLE001 — not mounted yet
                continue
            if not hasattr(widget, "busy_description"):
                continue
            desc = widget.busy_description()
            if desc:
                busy.append(desc)
        # Count active dynamic slots (running or awaiting dismiss)
        for slot in self._registry.active_slots:
            try:
                view = self.query_one(f"#view-slot-{slot.run_id}")
            except Exception:  # noqa: BLE001
                continue
            if hasattr(view, "busy_description"):
                desc = view.busy_description()
                if desc:
                    busy.append(desc)
        n_dynamic = self._registry.active_count()
        if n_dynamic > 0 and not busy:
            busy.append(
                f"{n_dynamic} active run(s). Active worktrees and branches will remain on disk."
            )
        if not busy:
            self.app.exit()
            return
        from cog.ui.screens.quit_confirm import QuitConfirmScreen

        self.app.push_screen(QuitConfirmScreen(busy), self._on_quit_confirmed)

    def _on_quit_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.app.exit()

    def action_switch_to(self, view_id: str) -> None:
        if view_id == self._active_view_id:
            return
        if view_id not in {v.id for v in _VIEWS}:
            return
        previous_id = self._active_view_id
        self._active_view_id = view_id
        self._apply_active_view()
        self._highlight_sidebar_row(view_id)
        # Target view just became active — clear its attention (user is looking).
        self._clear_attention(view_id)
        # Previous view just became inactive — if it's in an attention-worthy
        # state (e.g. refine chat pending) re-mark the sidebar so the user
        # sees they need to come back.
        self._refresh_attention_for(previous_id)

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

    def _refresh_attention_for(self, view_id: str) -> None:
        """Poll a view's current needs_attention() and update the sidebar."""
        try:
            widget = self.query_one(f"#view-{view_id}")
            sidebar = self.query_one(Sidebar)
        except Exception:  # noqa: BLE001
            return
        if not hasattr(widget, "needs_attention"):
            return
        state = widget.needs_attention()
        sidebar.set_attention(view_id, state is not None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("nav-slot-"):
            run_id = item_id.removeprefix("nav-slot-")
            self._switch_to_slot(run_id)
            return
        chosen_id = item_id.removeprefix("nav-")
        if chosen_id and chosen_id != "divider":
            self.action_switch_to(chosen_id)

    def _apply_active_view(self) -> None:
        active = self._active_view_id
        # Static views
        for v in _VIEWS:
            try:
                widget = self.query_one(f"#view-{v.id}")
            except Exception:  # noqa: BLE001
                continue
            widget.display = v.id == active
            if v.id == active and hasattr(widget, "focus_content"):
                self.call_after_refresh(widget.focus_content)
        # Dynamic slot views
        for slot in self._registry.active_slots:
            try:
                view = self.query_one(f"#view-slot-{slot.run_id}")
            except Exception:  # noqa: BLE001
                continue
            view.display = f"slot-{slot.run_id}" == active
            if f"slot-{slot.run_id}" == active and hasattr(view, "focus_content"):
                self.call_after_refresh(view.focus_content)

    def _highlight_sidebar_row(self, view_id: str) -> None:
        """Set the -active class on nav-{view_id}, clear all others."""
        try:
            list_view = self.query_one("#sidebar-nav", ListView)
        except Exception:  # noqa: BLE001
            return
        target_id = f"nav-{view_id}"
        for i, child in enumerate(list_view.children):
            if child.id == target_id:
                child.add_class("-active")
                list_view.index = i
            else:
                child.remove_class("-active")

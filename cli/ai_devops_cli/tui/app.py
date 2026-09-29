"""The interactive dashboard.

Three tables over the same data the CLI prints: pending proposals (with the
confirm/reject flow), AI findings, and recent commits. It talks to the same client
as the CLI, so nothing here is a second implementation of the API.

All network work runs in a thread worker and is marshalled back with
``call_from_thread`` — fetching on the UI thread would freeze the interface for the
duration of every request.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Static, TabbedContent, TabPane

from ai_devops_cli import context
from ai_devops_cli.client import ApiError, Client
from ai_devops_cli.errors import CliError

_CSS = """
Screen { layout: vertical; }
#status { height: 1; padding: 0 1; color: $text-muted; }
DataTable { height: 1fr; }

ConfirmApprove { align: center middle; }
#confirm-dialog {
    width: 64;
    height: auto;
    padding: 1 2;
    background: $panel;
    border: thick $warning;
}
#confirm-question { padding: 1 0; }
#confirm-dialog Button { margin-top: 1; }
"""

#: Textual declares ``App.BINDINGS`` as a list of this union; matching it keeps the
#: override type-correct (a plain ``list[Binding]`` is invariant-incompatible).
BindingType = Binding | tuple[str, str] | tuple[str, str, str]


class ConfirmApprove(ModalScreen[bool]):
    """Asks before approving, because approving runs an executor and writes rows.

    Rejecting is safe and needs no prompt; approving is the one action in this UI that
    changes state, so it gets an explicit yes.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "approve", "Approve"),
        Binding("n", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(f"Apply this proposal?\n\n{self._title}", id="confirm-question")
            yield Button("Approve", variant="primary", id="confirm-yes")
            yield Button("Cancel", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CopilotApp(App[None]):
    """A small, keyboard-driven view of the project's state."""

    CSS = _CSS
    TITLE = "AI DevOps / QA Copilot"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "refresh", "Refresh"),
        Binding("a", "approve", "Approve"),
        Binding("x", "reject", "Reject"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, client: Client, *, subtitle: str = "") -> None:
        super().__init__()
        self._client = client
        self.sub_title = subtitle
        self._proposal_ids: list[str] = []
        self._pending_approval: str = ""
        self._status = Static("Loading…", id="status")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane("Proposals", id="tab-proposals"):
                yield DataTable(id="proposals")
            with TabPane("Findings", id="tab-findings"):
                yield DataTable(id="findings")
            with TabPane("Commits", id="tab-commits"):
                yield DataTable(id="commits")
        yield self._status
        yield Footer()

    def on_mount(self) -> None:
        proposals = self.query_one("#proposals", DataTable)
        proposals.add_columns("TYPE", "TITLE", "RISK", "STATUS")
        proposals.cursor_type = "row"

        findings = self.query_one("#findings", DataTable)
        findings.add_columns("SEVERITY", "AGENT", "TITLE", "CONF")
        findings.cursor_type = "row"

        commits = self.query_one("#commits", DataTable)
        commits.add_columns("SHA", "WHEN", "MESSAGE", "AUTHOR")
        commits.cursor_type = "row"

        self.refresh_data()

    # -- data ---------------------------------------------------------------
    @work(thread=True, exclusive=True)
    def refresh_data(self) -> None:
        """Load every tab off the UI thread, then hand the rows back to it."""
        base = context.project_base()
        try:
            proposals = self._client.results(
                f"{base}/ai/recommendations", limit=100, status="pending"
            )
            findings = self._client.results(f"{base}/ai/findings", limit=100)
            commits = self._client.results(
                f"{base}/commits", limit=100, ordering="-committed_at"
            )
        except ApiError as exc:
            self.call_from_thread(self._set_status, f"[red]{exc.message}[/red]")
            return
        self.call_from_thread(self._populate, proposals, findings, commits)

    def _populate(
        self,
        proposals: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        commits: list[dict[str, Any]],
    ) -> None:
        self._proposal_ids = [str(row["id"]) for row in proposals]

        proposals_table = self.query_one("#proposals", DataTable)
        proposals_table.clear()
        for row in proposals:
            proposals_table.add_row(
                row["type"], row["title"], row["risk_level"], row["status"], key=str(row["id"])
            )

        findings_table = self.query_one("#findings", DataTable)
        findings_table.clear()
        for row in findings:
            findings_table.add_row(
                row["severity"],
                row["agent_code"],
                row["title"],
                f"{row['confidence'] * 100:.0f}%",
                key=str(row["id"]),
            )

        commits_table = self.query_one("#commits", DataTable)
        commits_table.clear()
        for row in commits:
            commits_table.add_row(
                row["short_sha"],
                str(row["committed_at"])[:16].replace("T", " "),
                (row["message"] or "").splitlines()[0],
                row["author_name"],
                key=str(row["id"]),
            )

        self._set_status(
            f"{len(proposals)} pending | {len(findings)} findings | {len(commits)} commits"
        )

    def _set_status(self, text: str) -> None:
        self._status.update(text)

    def _selected_proposal(self) -> str | None:
        table = self.query_one("#proposals", DataTable)
        index = table.cursor_row
        if index is None or index < 0 or index >= len(self._proposal_ids):
            return None
        return self._proposal_ids[index]

    def _selected_title(self) -> str:
        table = self.query_one("#proposals", DataTable)
        index = table.cursor_row
        if index is None or index < 0:
            return ""
        return str(table.get_row_at(index)[1])

    # -- actions ------------------------------------------------------------
    def action_refresh(self) -> None:
        self._set_status("Refreshing…")
        self.refresh_data()

    def action_approve(self) -> None:
        self._request_decision("confirm")

    def action_reject(self) -> None:
        self._request_decision("reject")

    def _request_decision(self, decision: str) -> None:
        recommendation_id = self._selected_proposal()
        if recommendation_id is None:
            self._set_status("[yellow]Select a proposal first.[/yellow]")
            return

        if decision == "reject":
            # Declining writes nothing, so it does not need a confirmation step.
            self._decide("reject", recommendation_id)
            return

        self._pending_approval = recommendation_id
        self.push_screen(ConfirmApprove(self._selected_title()), self._on_confirm)

    def _on_confirm(self, approved: bool | None) -> None:
        recommendation_id = self._pending_approval
        self._pending_approval = ""
        if approved and recommendation_id:
            self._decide("confirm", recommendation_id)

    @work(thread=True, exclusive=True)
    def _decide(self, decision: str, recommendation_id: str) -> None:
        """Run the confirmation flow for one proposal."""
        base = context.project_base()
        try:
            if decision == "confirm":
                self._client.post(
                    f"{base}/ai/recommendations/{recommendation_id}/confirm", json={}
                )
                self.call_from_thread(self._set_status, "[green]Approved.[/green]")
            else:
                self._client.post(
                    f"{base}/ai/recommendations/{recommendation_id}/reject", json={"reason": ""}
                )
                self.call_from_thread(self._set_status, "[green]Rejected. Nothing changed.[/green]")
        except ApiError as exc:
            self.call_from_thread(self._set_status, f"[red]{exc.message}[/red]")
            return
        self.call_from_thread(self.refresh_data)


def run() -> None:
    """Validate the session, then hand the terminal to the app."""
    settings = context.settings()
    if not settings.logged_in:
        raise CliError("Not signed in. Run: copilot login")
    if not settings.has_project:
        raise CliError("No project selected. Run 'copilot projects', then 'copilot use <slug>'.")

    CopilotApp(context.client(), subtitle=context.project_label()).run()

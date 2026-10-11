"""The reusable resource table.

A thin, typed wrapper around Textual's ``DataTable`` that knows how to render
:class:`ResourceRecord` objects for the network and file views.

Long paths and endpoints stay fully searchable: the *underlying* value is never
truncated, only the on-screen cell is elided, and every record remains reachable
through the search box and the detail panel.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Input, Static
from textual.widgets.data_table import RowKey

from ..config import current_theme, with_tokens
from ..models import ResourceCategory, ResourceRecord
from ..services.deduplication import dedup_key

__all__ = ["ResourceTable"]


NETWORK_COLUMNS: list[tuple[str, int]] = [
    ("FD", 8),
    ("FAMILY", 7),
    ("PROTO", 5),
    ("LOCAL ENDPOINT", 30),
    ("REMOTE ENDPOINT", 30),
    ("STATE", 12),
]

FILE_COLUMNS: list[tuple[str, int]] = [
    ("FD", 8),
    ("TYPE", 8),
    ("MODE", 10),
    ("RESOURCE", 64),
]

ALL_COLUMNS: list[tuple[str, int]] = [
    ("FD", 8),
    ("TYPE", 9),
    ("PROTO", 5),
    ("RESOURCE", 46),
    ("STATE", 12),
]


def _cell(text: str, style: str) -> Text:
    return Text(text, style=style)


class ResourceTable(Static):
    """Searchable, sortable table of open resources for one process."""

    DEFAULT_CSS = with_tokens("""
    ResourceTable {
        height: 1fr;
    }
    ResourceTable > .resource-search {
        height: 3;
        margin: 0 0 1 0;
        display: none;
    }
    ResourceTable.-searching > .resource-search {
        display: block;
    }
    ResourceTable DataTable {
        height: 1fr;
    }
    ResourceTable .resource-empty {
        height: auto;
        padding: 1 2;
        display: none;
    }
    ResourceTable.-empty .resource-empty {
        display: block;
    }
    """)

    def __init__(
        self,
        *,
        category: ResourceCategory = ResourceCategory.NETWORK,
        id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(id=id, **kwargs)
        self.category = category
        self._records: list[ResourceRecord] = []
        self._filtered: list[ResourceRecord] = []
        self._searching = False

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="Filter: text, or port=8080 / state=LISTEN / proto=TCP",
            id="resource-filter",
            classes="resource-search",
        )
        yield DataTable(id="resource-grid", zebra_stripes=True, cursor_type="row")
        yield Static("", classes="resource-empty", id="resource-empty")

    def on_mount(self) -> None:
        self._build_columns()

    def _build_columns(self) -> None:
        table = self.query_one("#resource-grid", DataTable)
        table.clear(columns=True)
        columns = self._columns()
        # Add each column as an explicit (label, key) pair. Plain labels get an
        # auto-generated key of None, which in-place cell updates cannot address.
        table.add_columns(*[(label, label) for label, _ in columns])

        for column, (_, width) in zip(table.columns.values(), columns, strict=False):
            column.width = width

    def set_records(self, records: list[ResourceRecord], *, keep_query: bool = True) -> None:
        self._records = records
        query = ""
        if keep_query:
            query = self._current_query()
        self._apply(query)

    def _current_query(self) -> str:
        if self._searching:
            try:
                return self.query_one("#resource-filter", Input).value
            except Exception:
                return ""
        return ""

    def _apply(self, query: str) -> None:
        from ..services.resource_search import search_resources

        self._filtered = search_resources(self._records, query) if query else list(self._records)
        self._populate()

    def _populate(self) -> None:
        table = self.query_one("#resource-grid", DataTable)
        keys = [self._row_key(record) for record in self._filtered]

        # Compare against the keys the table actually holds rather than a cached
        # copy, so anything that clears the table out from under us (a theme
        # change, a column rebuild) falls through to the rebuild path.
        if [row.key.value for row in table.rows.values()] == keys:
            # Same rows in the same order. Repaint cells in place instead of
            # clearing and re-adding every row on each refresh tick, which would
            # throw away the user's cursor position and make the table flicker.
            for record, key in zip(self._filtered, keys, strict=True):
                cells = self._row_cells(record)
                for (label, _width), cell in zip(self._columns(), cells, strict=True):
                    table.update_cell(key, label, cell)
        else:
            previous = table.cursor_row
            table.clear()
            for record, key in zip(self._filtered, keys, strict=True):
                table.add_row(*self._row_cells(record), key=key)
            self._restore_cursor(table, previous)

        empty = self.query_one("#resource-empty", Static)
        if not self._filtered:
            self.add_class("-empty")
            empty.update(self._empty_message())
        else:
            self.remove_class("-empty")
            empty.update("")

        self.refresh_count()

    def _columns(self) -> list[tuple[str, int]]:
        """The column spec this table was built with, in display order."""
        match self.category:
            case ResourceCategory.NETWORK | ResourceCategory.LISTENERS:
                return NETWORK_COLUMNS
            case ResourceCategory.FILES | ResourceCategory.UNIX:
                return FILE_COLUMNS
            case _:
                return ALL_COLUMNS

    @staticmethod
    def _row_key(record: ResourceRecord) -> str:
        """A row key that survives a re-collection.

        This used to be ``str(id(record))``, which changes on every lsof re-run
        because new objects are built each time. That forced a full rebuild on
        every refresh and lost the cursor position. The dedup key is stable for a
        given resource across collections, which is exactly the row's identity.
        """
        return "\x1f".join(str(part) for part in dedup_key(record))

    def _restore_cursor(self, table: DataTable, previous: int | None) -> None:
        """Keep the highlighted row in place across a rebuild."""
        if not self._filtered:
            return
        if previous is None:
            table.move_cursor(row=0)
            return
        table.move_cursor(row=min(max(previous, 0), len(self._filtered) - 1))

    def _empty_message(self) -> Text:
        if not self._records:
            return Text(
                "No resources were collected for this process.",
                style=current_theme().muted,
            )
        return Text(
            "No resources match the current filter.",
            style=current_theme().muted,
        )

    def _row_cells(self, record: ResourceRecord) -> list[Text]:
        match self.category:
            case ResourceCategory.NETWORK | ResourceCategory.LISTENERS:
                return self._network_cells(record)
            case ResourceCategory.FILES | ResourceCategory.UNIX:
                return self._file_cells(record)
            case _:
                return self._all_cells(record)

    def _network_cells(self, record: ResourceRecord) -> list[Text]:
        local = str(record.local) or record.raw_name or record.name
        remote = str(record.remote)
        state = record.state
        return [
            _cell(record.fd, current_theme().caramel),
            _cell(record.resource_type, current_theme().cyan),
            _cell(record.protocol, current_theme().cotton),
            _cell(local, current_theme().cotton),
            _cell(remote or "—", current_theme().muted if not remote else current_theme().blue),
            _cell(state, current_theme().state_color(state)),
        ]

    def _file_cells(self, record: ResourceRecord) -> list[Text]:
        return [
            _cell(record.fd, current_theme().caramel),
            _cell(record.resource_type, current_theme().purple),
            _cell(record.mode or "—", current_theme().muted),
            _cell(record.name, current_theme().cotton),
        ]

    def _all_cells(self, record: ResourceRecord) -> list[Text]:
        state = record.state
        state_style = (
            current_theme().state_color(state) if record.is_inet else current_theme().muted
        )
        return [
            _cell(record.fd, current_theme().caramel),
            _cell(record.resource_type, current_theme().purple),
            _cell(record.protocol, current_theme().muted),
            _cell(record.display_name, current_theme().cotton),
            _cell(state, state_style),
        ]

    def refresh_count(self) -> None:
        """Update the screen-level count line; overridden via message bubbling."""
        self.post_message(self.RecordCountChanged(len(self._filtered), len(self._records)))

    def toggle_search(self) -> None:
        self._searching = not self._searching
        self.set_class(self._searching, "-searching")
        if self._searching:
            self.query_one("#resource-filter", Input).focus()

    def close_search(self) -> bool:
        """Exit search mode and clear the query. Returns True if it was active."""
        if not self._searching:
            return False
        self._searching = False
        self.remove_class("-searching")
        search = self.query_one("#resource-filter", Input)
        search.value = ""
        self._apply("")
        self.query_one("#resource-grid", DataTable).focus()
        return True

    def selected_record(self) -> ResourceRecord | None:
        table = self.query_one("#resource-grid", DataTable)
        row_key = table.cursor_row
        if row_key is None or row_key >= len(self._filtered):
            return None
        return self._filtered[row_key]

    def focus_table(self) -> None:
        self.query_one("#resource-grid", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "resource-filter":
            event.stop()
            self._apply(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "resource-filter":
            event.stop()
            self.focus_table()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "resource-grid":
            event.stop()

    from textual.message import Message

    class RecordCountChanged(Message):
        """Bubble up so the parent screen can update its status line."""

        def __init__(self, shown: int, total: int) -> None:
            super().__init__()
            self.shown = shown
            self.total = total


def make_row_key(record: ResourceRecord) -> RowKey:
    return RowKey(str(id(record)))

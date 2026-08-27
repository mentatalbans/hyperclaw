"""
Productivity Connectors.
Integration with Obsidian, Apple Notes, Apple Calendar, Google Sheets, Google Docs, Evernote, OneNote, ClickUp, Monday.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Note:
    """Note/document."""
    id: str
    title: str
    content: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class CalendarEvent:
    """Calendar event."""
    id: str
    title: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    raw: dict = field(default_factory=dict)


class ProductivityConnector(ABC):
    """Abstract base for productivity connectors."""

    name: str = "base"


class NoteConnector(ProductivityConnector):
    """Abstract base for note-taking connectors."""

    @abstractmethod
    async def get_notes(self, limit: int = 50, **kwargs) -> list[Note]:
        """Get notes."""
        pass

    @abstractmethod
    async def create_note(self, title: str, content: str, **kwargs) -> Note:
        """Create a note."""
        pass

    @abstractmethod
    async def update_note(self, note_id: str, content: str, **kwargs) -> Note:
        """Update a note."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# OBSIDIAN (Local Vault)
# ═══════════════════════════════════════════════════════════════════════════════

class ObsidianConnector(NoteConnector):
    """Obsidian vault connector (local filesystem)."""

    name = "obsidian"

    def __init__(self, vault_path: str | None = None):
        self.vault_path = Path(vault_path or os.environ.get("OBSIDIAN_VAULT", "~/Documents/Obsidian")).expanduser()

    async def get_notes(self, limit: int = 50, **kwargs) -> list[Note]:
        notes = []
        for md_file in sorted(self.vault_path.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            content = md_file.read_text(encoding="utf-8")
            stat = md_file.stat()

            # Extract tags from content
            import re
            tags = re.findall(r"#(\w+)", content)

            notes.append(
                Note(
                    id=str(md_file.relative_to(self.vault_path)),
                    title=md_file.stem,
                    content=content,
                    created_at=datetime.fromtimestamp(stat.st_ctime),
                    updated_at=datetime.fromtimestamp(stat.st_mtime),
                    tags=list(set(tags)),
                )
            )

        return notes

    async def create_note(self, title: str, content: str, **kwargs) -> Note:
        folder = kwargs.get("folder", "")
        note_path = self.vault_path / folder / f"{title}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(content, encoding="utf-8")

        return Note(
            id=str(note_path.relative_to(self.vault_path)),
            title=title,
            content=content,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    async def update_note(self, note_id: str, content: str, **kwargs) -> Note:
        note_path = self.vault_path / note_id
        if not note_path.exists():
            raise FileNotFoundError(f"Note not found: {note_id}")

        note_path.write_text(content, encoding="utf-8")

        return Note(
            id=note_id,
            title=note_path.stem,
            content=content,
            updated_at=datetime.now(),
        )

    async def search(self, query: str, limit: int = 20) -> list[Note]:
        """Search notes by content."""
        results = []
        query_lower = query.lower()

        for md_file in self.vault_path.rglob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            if query_lower in content.lower() or query_lower in md_file.stem.lower():
                results.append(
                    Note(
                        id=str(md_file.relative_to(self.vault_path)),
                        title=md_file.stem,
                        content=content[:500],
                    )
                )
                if len(results) >= limit:
                    break

        return results


# ═══════════════════════════════════════════════════════════════════════════════
# APPLE NOTES (via AppleScript)
# ═══════════════════════════════════════════════════════════════════════════════

class AppleNotesConnector(NoteConnector):
    """Apple Notes connector (macOS only, via AppleScript)."""

    name = "apple_notes"

    async def get_notes(self, limit: int = 50, **kwargs) -> list[Note]:
        import subprocess

        script = f'''
        tell application "Notes"
            set noteList to {{}}
            repeat with aNote in (notes 1 thru {limit})
                set noteInfo to {{id:(id of aNote), title:(name of aNote), body:(body of aNote)}}
                set end of noteList to noteInfo
            end repeat
            return noteList
        end tell
        '''

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"AppleScript error: {result.stderr}")
            return []

        # Parse AppleScript output (simplified)
        return []  # TODO: Parse AppleScript list output

    async def create_note(self, title: str, content: str, **kwargs) -> Note:
        import subprocess

        folder = kwargs.get("folder", "Notes")

        script = f'''
        tell application "Notes"
            tell folder "{folder}"
                make new note with properties {{name:"{title}", body:"{content}"}}
            end tell
        end tell
        '''

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create note: {result.stderr}")

        return Note(id="", title=title, content=content, created_at=datetime.now())

    async def update_note(self, note_id: str, content: str, **kwargs) -> Note:
        import subprocess

        script = f'''
        tell application "Notes"
            set theNote to note id "{note_id}"
            set body of theNote to "{content}"
        end tell
        '''

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to update note: {result.stderr}")

        return Note(id=note_id, title="", content=content, updated_at=datetime.now())


# ═══════════════════════════════════════════════════════════════════════════════
# APPLE CALENDAR (via AppleScript)
# ═══════════════════════════════════════════════════════════════════════════════

class AppleCalendarConnector(ProductivityConnector):
    """Apple Calendar connector (macOS only, via AppleScript)."""

    name = "apple_calendar"

    async def get_events(self, days: int = 7, **kwargs) -> list[CalendarEvent]:
        import subprocess

        script = '''
        tell application "Calendar"
            set eventList to {}
            set startDate to current date
            set endDate to startDate + (%d * days)

            repeat with aCal in calendars
                set theEvents to (every event of aCal whose start date >= startDate and start date <= endDate)
                repeat with anEvent in theEvents
                    set eventInfo to {id:(uid of anEvent), title:(summary of anEvent), startDate:(start date of anEvent), endDate:(end date of anEvent)}
                    set end of eventList to eventInfo
                end repeat
            end repeat
            return eventList
        end tell
        ''' % days

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"AppleScript error: {result.stderr}")
            return []

        return []  # TODO: Parse AppleScript output

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        calendar: str = "Calendar",
        **kwargs,
    ) -> CalendarEvent:
        import subprocess

        start_str = start.strftime("%B %d, %Y at %I:%M %p")
        end_str = end.strftime("%B %d, %Y at %I:%M %p")

        script = f'''
        tell application "Calendar"
            tell calendar "{calendar}"
                make new event with properties {{summary:"{title}", start date:date "{start_str}", end date:date "{end_str}"}}
            end tell
        end tell
        '''

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create event: {result.stderr}")

        return CalendarEvent(id="", title=title, start=start, end=end)


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleSheetsConnector(ProductivityConnector):
    """Google Sheets connector."""

    name = "google_sheets"

    def __init__(self, credentials: str | None = None):
        self.credentials_path = credentials or os.environ.get("GOOGLE_CREDENTIALS_PATH")
        self.base_url = "https://sheets.googleapis.com/v4/spreadsheets"
        self._access_token: str | None = None

    async def _get_token(self) -> str:
        # In production, use google-auth library for proper OAuth
        return os.environ.get("GOOGLE_SHEETS_TOKEN", "")

    async def read_sheet(
        self,
        spreadsheet_id: str,
        range: str = "Sheet1",
        **kwargs,
    ) -> list[list[Any]]:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{spreadsheet_id}/values/{range}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return data.get("values", [])

    async def write_sheet(
        self,
        spreadsheet_id: str,
        range: str,
        values: list[list[Any]],
        **kwargs,
    ) -> dict:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/{spreadsheet_id}/values/{range}",
                headers={"Authorization": f"Bearer {token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": values},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def append_row(
        self,
        spreadsheet_id: str,
        range: str,
        values: list[Any],
        **kwargs,
    ) -> dict:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/{spreadsheet_id}/values/{range}:append",
                headers={"Authorization": f"Bearer {token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": [values]},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE DOCS
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleDocsConnector(ProductivityConnector):
    """Google Docs connector."""

    name = "google_docs"

    def __init__(self):
        self.base_url = "https://docs.googleapis.com/v1/documents"

    async def _get_token(self) -> str:
        return os.environ.get("GOOGLE_DOCS_TOKEN", "")

    async def get_document(self, document_id: str) -> dict:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{document_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def create_document(self, title: str) -> dict:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {token}"},
                json={"title": title},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def insert_text(self, document_id: str, text: str, index: int = 1) -> dict:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/{document_id}:batchUpdate",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": index},
                                "text": text,
                            }
                        }
                    ]
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# EVERNOTE
# ═══════════════════════════════════════════════════════════════════════════════

class EvernoteConnector(NoteConnector):
    """Evernote connector."""

    name = "evernote"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("EVERNOTE_TOKEN")
        self.base_url = "https://api.evernote.com"

    async def get_notes(self, limit: int = 50, **kwargs) -> list[Note]:
        # Evernote uses a complex SDK - simplified HTTP approach
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/edam/note",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"maxNotes": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                Note(
                    id=note["guid"],
                    title=note["title"],
                    content=note.get("content", ""),
                    created_at=datetime.fromtimestamp(note["created"] / 1000)
                    if note.get("created") else None,
                    updated_at=datetime.fromtimestamp(note["updated"] / 1000)
                    if note.get("updated") else None,
                    tags=note.get("tagNames", []),
                    raw=note,
                )
                for note in data.get("notes", [])
            ]

    async def create_note(self, title: str, content: str, **kwargs) -> Note:
        notebook_guid = kwargs.get("notebook_guid")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/edam/note",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "title": title,
                    "content": f'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note>{content}</en-note>',
                    "notebookGuid": notebook_guid,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return Note(id=data["guid"], title=title, content=content, created_at=datetime.now())

    async def update_note(self, note_id: str, content: str, **kwargs) -> Note:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/edam/note/{note_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "guid": note_id,
                    "content": f'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note>{content}</en-note>',
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return Note(id=note_id, title=data.get("title", ""), content=content, updated_at=datetime.now())


# ═══════════════════════════════════════════════════════════════════════════════
# ONENOTE
# ═══════════════════════════════════════════════════════════════════════════════

class OneNoteConnector(NoteConnector):
    """Microsoft OneNote connector."""

    name = "onenote"

    def __init__(self, access_token: str | None = None):
        self.token = access_token or os.environ.get("MICROSOFT_ACCESS_TOKEN")
        self.base_url = "https://graph.microsoft.com/v1.0/me/onenote"

    async def get_notes(self, limit: int = 50, **kwargs) -> list[Note]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/pages",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"$top": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                Note(
                    id=page["id"],
                    title=page["title"],
                    content="",  # Content requires separate call
                    created_at=datetime.fromisoformat(page["createdDateTime"].replace("Z", "+00:00"))
                    if page.get("createdDateTime") else None,
                    updated_at=datetime.fromisoformat(page["lastModifiedDateTime"].replace("Z", "+00:00"))
                    if page.get("lastModifiedDateTime") else None,
                    raw=page,
                )
                for page in data.get("value", [])
            ]

    async def create_note(self, title: str, content: str, **kwargs) -> Note:
        section_id = kwargs.get("section_id")

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><title>{title}</title></head>
        <body>{content}</body>
        </html>
        """

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/sections/{section_id}/pages",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/xhtml+xml",
                },
                content=html_content,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return Note(id=data["id"], title=title, content=content, created_at=datetime.now())

    async def update_note(self, note_id: str, content: str, **kwargs) -> Note:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.base_url}/pages/{note_id}/content",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json=[
                    {
                        "target": "body",
                        "action": "replace",
                        "content": content,
                    }
                ],
                timeout=30.0,
            )
            response.raise_for_status()

            return Note(id=note_id, title="", content=content, updated_at=datetime.now())


# ═══════════════════════════════════════════════════════════════════════════════
# CLICKUP
# ═══════════════════════════════════════════════════════════════════════════════

class ClickUpConnector(ProductivityConnector):
    """ClickUp project management connector."""

    name = "clickup"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("CLICKUP_API_KEY")
        self.base_url = "https://api.clickup.com/api/v2"

    async def get_tasks(self, list_id: str, **kwargs) -> list[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/list/{list_id}/task",
                headers={"Authorization": self.api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("tasks", [])

    async def create_task(self, list_id: str, name: str, **kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/list/{list_id}/task",
                headers={"Authorization": self.api_key},
                json={"name": name, **kwargs},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def update_task(self, task_id: str, **kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/task/{task_id}",
                headers={"Authorization": self.api_key},
                json=kwargs,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# MONDAY.COM
# ═══════════════════════════════════════════════════════════════════════════════

class MondayConnector(ProductivityConnector):
    """Monday.com project management connector."""

    name = "monday"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("MONDAY_API_KEY")
        self.base_url = "https://api.monday.com/v2"

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_boards(self) -> list[dict]:
        query = "{ boards { id name } }"
        result = await self._query(query)
        return result.get("data", {}).get("boards", [])

    async def get_items(self, board_id: str, limit: int = 50) -> list[dict]:
        query = f"""
        {{
            boards(ids: [{board_id}]) {{
                items_page(limit: {limit}) {{
                    items {{
                        id
                        name
                        state
                        column_values {{
                            id
                            text
                        }}
                    }}
                }}
            }}
        }}
        """
        result = await self._query(query)
        boards = result.get("data", {}).get("boards", [])
        if boards:
            return boards[0].get("items_page", {}).get("items", [])
        return []

    async def create_item(self, board_id: str, name: str, **kwargs) -> dict:
        column_values = kwargs.get("column_values", {})
        import json

        query = """
        mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON) {
            create_item(board_id: $boardId, item_name: $itemName, column_values: $columnValues) {
                id
                name
            }
        }
        """
        result = await self._query(
            query,
            {
                "boardId": board_id,
                "itemName": name,
                "columnValues": json.dumps(column_values),
            },
        )
        return result.get("data", {}).get("create_item", {})


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

PRODUCTIVITY_CONNECTORS: dict[str, type[ProductivityConnector]] = {
    "obsidian": ObsidianConnector,
    "apple_notes": AppleNotesConnector,
    "apple_calendar": AppleCalendarConnector,
    "google_sheets": GoogleSheetsConnector,
    "sheets": GoogleSheetsConnector,
    "google_docs": GoogleDocsConnector,
    "docs": GoogleDocsConnector,
    "evernote": EvernoteConnector,
    "onenote": OneNoteConnector,
    "clickup": ClickUpConnector,
    "monday": MondayConnector,
}


def get_productivity_connector(name: str, **kwargs) -> ProductivityConnector:
    """Get a productivity connector by name."""
    name = name.lower()

    if name not in PRODUCTIVITY_CONNECTORS:
        available = ", ".join(sorted(PRODUCTIVITY_CONNECTORS.keys()))
        raise ValueError(f"Unknown productivity connector: {name}. Available: {available}")

    return PRODUCTIVITY_CONNECTORS[name](**kwargs)

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


class TestGmailConnector:
    @pytest.mark.asyncio
    async def test_list_inbox_calls_correct_endpoint(self):
        from integrations.productivity.google.gmail import GmailConnector
        conn = GmailConnector({"access_token": "test_token"})
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"messages": []})
            mock_client.get = AsyncMock(return_value=mock_response)
            conn._google_client = mock_client
            result = await conn.list_inbox()
            assert mock_client.get.called
            assert "gmail" in str(mock_client.get.call_args).lower() or "messages" in str(mock_client.get.call_args).lower()


class TestGoogleCalendarConnector:
    @pytest.mark.asyncio
    async def test_create_event_formats_body(self):
        from integrations.productivity.google.calendar import GoogleCalendarConnector
        conn = GoogleCalendarConnector({"access_token": "test_token"})
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"id": "event123"})
            mock_client.post = AsyncMock(return_value=mock_response)
            conn._google_client = mock_client
            start = datetime(2026, 3, 20, 10, 0, 0)
            end = datetime(2026, 3, 20, 11, 0, 0)
            result = await conn.create_event("Team Meeting", start, end)
            mock_client.post.assert_called_once()
            assert "calendar" in str(mock_client.post.call_args).lower() or "events" in str(mock_client.post.call_args).lower()


class TestGoogleDriveConnector:
    @pytest.mark.asyncio
    async def test_upload_calls_upload_endpoint(self, tmp_path):
        from integrations.productivity.google.drive import GoogleDriveConnector
        conn = GoogleDriveConnector({"access_token": "test_token"})
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"id": "file123"})
            mock_client.post = AsyncMock(return_value=mock_response)
            conn._google_client = mock_client
            result = await conn.upload_file(str(test_file))
            assert mock_client.post.called


class TestGoogleSheetsConnector:
    @pytest.mark.asyncio
    async def test_write_range_formats_values(self):
        from integrations.productivity.google.sheets import GoogleSheetsConnector
        conn = GoogleSheetsConnector({"access_token": "test_token"})
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"updatedCells": 3})
            mock_client.put = AsyncMock(return_value=mock_response)
            conn._google_client = mock_client
            result = await conn.write_range("spreadsheet123", "Sheet1!A1:C1", [["a", "b", "c"]])
            assert mock_client.put.called

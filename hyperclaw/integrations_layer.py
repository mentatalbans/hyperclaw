"""
Assistant Integration Layer
Unified interface for Gmail, Calendar, iMessage, Supabase, Telegram
All wired into HyperClaw agent tool execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(str(Path.home() / '.hyperclaw/.env'))

logger = logging.getLogger("hyperclaw.integrations_layer")

GMAIL_TOKEN_FILE = Path.home() / '.hyperclaw/workspace/secrets/gmail_token.json'
GOOGLE_CREDS_FILE = Path(os.getenv('GOOGLE_CREDENTIALS_FILE', ''))

# ─────────────────────────────────────────────
# TOKEN MANAGER
# ─────────────────────────────────────────────

class GoogleTokenManager:
    """Manages Google OAuth tokens with auto-refresh."""
    
    _token_data: dict = {}
    _last_loaded: float = 0
    
    @classmethod
    def load(cls) -> dict:
        """Load token from disk, refresh if expired."""
        import time
        # Reload every 5 minutes or if not loaded
        if time.time() - cls._last_loaded > 300:
            if GMAIL_TOKEN_FILE.exists():
                with open(GMAIL_TOKEN_FILE) as f:
                    cls._token_data = json.load(f)
                cls._last_loaded = time.time()
        return cls._token_data
    
    @classmethod
    def get_access_token(cls) -> str:
        """Get valid access token, refreshing if needed."""
        data = cls.load()
        
        # Check if expired
        expiry_str = data.get('expiry')
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if now >= expiry - timedelta(minutes=5):
                    # Refresh
                    cls._refresh()
                    data = cls.load()
            except Exception as e:
                logger.warning(f"Expiry check failed: {e}")
        
        return data.get('token', '')
    
    @classmethod
    def _refresh(cls) -> None:
        """Refresh the access token."""
        data = cls._token_data
        try:
            import urllib.request, urllib.parse
            params = {
                'client_id': data.get('client_id', os.getenv('GMAIL_CLIENT_ID', '')),
                'client_secret': data.get('client_secret', os.getenv('GMAIL_CLIENT_SECRET', '')),
                'refresh_token': data.get('refresh_token', os.getenv('GMAIL_REFRESH_TOKEN', '')),
                'grant_type': 'refresh_token',
            }
            req = urllib.request.Request(
                'https://oauth2.googleapis.com/token',
                data=urllib.parse.urlencode(params).encode(),
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            
            if 'access_token' in result:
                cls._token_data['token'] = result['access_token']
                if 'expiry' in result:
                    cls._token_data['expiry'] = result['expiry']
                else:
                    from datetime import datetime, timezone, timedelta
                    expires_in = result.get('expires_in', 3600)
                    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    cls._token_data['expiry'] = expiry.isoformat()
                
                # Save back to disk
                with open(GMAIL_TOKEN_FILE, 'w') as f:
                    json.dump(cls._token_data, f, indent=2)
                cls._last_loaded = 0  # Force reload
                logger.info("Google token refreshed ✅")
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")


# ─────────────────────────────────────────────
# GMAIL
# ─────────────────────────────────────────────

def gmail_list_inbox(max_results: int = 20, query: str = 'in:inbox') -> dict:
    """List inbox messages."""
    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}'}
    
    with httpx.Client(timeout=30) as client:
        r = client.get(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages',
            headers=headers,
            params={'q': query, 'maxResults': max_results}
        )
    
    if r.status_code != 200:
        return {'error': f'Gmail list failed: {r.status_code} {r.text[:200]}'}
    
    return r.json()


def gmail_get_message(message_id: str, format: str = 'full') -> dict:
    """Get a specific message."""
    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}'}
    
    with httpx.Client(timeout=30) as client:
        r = client.get(
            f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}',
            headers=headers,
            params={'format': format}
        )
    
    return r.json() if r.status_code == 200 else {'error': r.text[:200]}


def gmail_send(to: str, subject: str, body: str, cc: str = '', reply_to_id: str = '') -> dict:
    """Send an email via Gmail."""
    import base64
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    
    # Always CC the configured ASSISTANT_CC_EMAIL address per email policy
    cc_list = [os.environ.get('ASSISTANT_CC_EMAIL', 'cc@example.com')]
    if cc and cc != os.environ.get('ASSISTANT_CC_EMAIL', ''):
        cc_list.append(cc)
    
    msg = MIMEMultipart('alternative')
    msg['to'] = to
    msg['subject'] = subject
    msg['cc'] = ', '.join(cc_list)
    msg.attach(MIMEText(body, 'plain'))
    
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {'raw': raw}
    if reply_to_id:
        payload['threadId'] = reply_to_id
    
    with httpx.Client(timeout=30) as client:
        r = client.post(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers=headers,
            json=payload
        )
    
    if r.status_code in (200, 201):
        return {'status': 'sent', 'id': r.json().get('id', '')}
    return {'error': f'Send failed: {r.status_code} {r.text[:200]}'}


def gmail_read_message_text(message_id: str) -> str:
    """Get full readable text of a message."""
    import base64
    msg = gmail_get_message(message_id, format='full')
    if 'error' in msg:
        return f"Error: {msg['error']}"
    
    payload = msg.get('payload', {})
    
    def extract_body(payload):
        if payload.get('mimeType', '').startswith('text/plain'):
            data = payload.get('body', {}).get('data', '')
            if data:
                return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
        
        for part in payload.get('parts', []):
            result = extract_body(part)
            if result:
                return result
        return ''
    
    headers = {h['name']: h['value'] for h in payload.get('headers', [])}
    body = extract_body(payload)
    
    return f"""From: {headers.get('From', 'Unknown')}
Date: {headers.get('Date', 'Unknown')}
Subject: {headers.get('Subject', 'No subject')}

{body[:3000]}"""


# ─────────────────────────────────────────────
# CALENDAR
# ─────────────────────────────────────────────

def calendar_get_events(days_ahead: int = 7) -> list:
    """Get upcoming calendar events."""
    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}'}
    
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    
    with httpx.Client(timeout=30) as client:
        r = client.get(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers=headers,
            params={
                'timeMin': now.isoformat(),
                'timeMax': end.isoformat(),
                'maxResults': 25,
                'singleEvents': True,
                'orderBy': 'startTime'
            }
        )
    
    if r.status_code != 200:
        return [{'error': f'Calendar failed: {r.status_code}'}]
    
    events = r.json().get('items', [])
    result = []
    for e in events:
        start = e.get('start', {})
        result.append({
            'summary': e.get('summary', 'Untitled'),
            'start': start.get('dateTime', start.get('date', '')),
            'end': e.get('end', {}).get('dateTime', ''),
            'location': e.get('location', ''),
            'description': e.get('description', '')[:200],
            'attendees': [a.get('email') for a in e.get('attendees', [])],
        })
    return result


def calendar_create_event(title: str, start_iso: str, end_iso: str, 
                          description: str = '', attendees: list = None) -> dict:
    """Create a calendar event."""
    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    
    body = {
        'summary': title,
        'start': {'dateTime': start_iso, 'timeZone': 'America/Los_Angeles'},
        'end': {'dateTime': end_iso, 'timeZone': 'America/Los_Angeles'},
        'description': description,
    }
    if attendees:
        body['attendees'] = [{'email': a} for a in attendees]
    
    with httpx.Client(timeout=30) as client:
        r = client.post(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers=headers,
            json=body
        )
    
    if r.status_code in (200, 201):
        event = r.json()
        return {'status': 'created', 'id': event.get('id'), 'link': event.get('htmlLink')}
    return {'error': f'Create failed: {r.status_code} {r.text[:200]}'}


# ─────────────────────────────────────────────
# iMESSAGE (Native AppleScript)
# ─────────────────────────────────────────────

def imessage_send(recipient: str, message: str) -> dict:
    """Send an iMessage via AppleScript."""
    if sys.platform != 'darwin':
        return {'error': 'iMessage requires macOS'}
    
    # Escape for AppleScript
    content = message.replace('\\', '\\\\').replace('"', '\\"')
    recipient_clean = recipient.replace('"', '\\"')
    
    script = f'''
    tell application "Messages"
        set targetService to first service whose service type = iMessage
        set targetBuddy to buddy "{recipient_clean}" of targetService
        send "{content}" to targetBuddy
    end tell
    '''
    
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return {'status': 'sent', 'recipient': recipient}
        else:
            # Try SMS fallback
            script_sms = f'''
            tell application "Messages"
                set targetService to first service whose service type = SMS
                set targetBuddy to buddy "{recipient_clean}" of targetService
                send "{content}" to targetBuddy
            end tell
            '''
            result2 = subprocess.run(
                ['osascript', '-e', script_sms],
                capture_output=True, text=True, timeout=15
            )
            if result2.returncode == 0:
                return {'status': 'sent_sms', 'recipient': recipient}
            return {'error': f'AppleScript error: {result.stderr.strip()}'}
    except subprocess.TimeoutExpired:
        return {'error': 'iMessage send timed out'}
    except Exception as e:
        return {'error': str(e)}


def imessage_get_recent(contact: str = '', limit: int = 10) -> list:
    """Get recent iMessages (requires Full Disk Access for Messages DB)."""
    db_path = Path.home() / 'Library/Messages/chat.db'
    
    if not db_path.exists():
        return [{'error': 'Messages DB not accessible — Full Disk Access required'}]
    
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        query = '''
            SELECT 
                m.rowid,
                m.text,
                m.date / 1000000000 + 978307200 as unix_time,
                m.is_from_me,
                h.id as handle
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.rowid
            WHERE m.text IS NOT NULL
        '''
        
        if contact:
            query += f" AND h.id LIKE '%{contact}%'"
        
        query += f" ORDER BY m.date DESC LIMIT {limit}"
        
        rows = conn.execute(query).fetchall()
        conn.close()
        
        messages = []
        for row in rows:
            messages.append({
                'text': row['text'],
                'from_me': bool(row['is_from_me']),
                'handle': row['handle'] or 'Unknown',
                'time': datetime.fromtimestamp(row['unix_time']).strftime('%Y-%m-%d %H:%M'),
            })
        return messages
    except Exception as e:
        return [{'error': f'DB read failed: {e}'}]


# ─────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_KEY', '')

def supabase_query(table: str, limit: int = 50, filters: dict = None) -> list:
    """Query a Supabase table."""
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    params = {'limit': limit}
    if filters:
        for k, v in filters.items():
            params[f'eq.{k}'] = v
    
    with httpx.Client(timeout=30) as client:
        r = client.get(f'{SUPABASE_URL}/rest/v1/{table}', headers=headers, params=params)
    
    return r.json() if r.status_code == 200 else [{'error': r.text[:200]}]


def supabase_insert(table: str, data: dict) -> dict:
    """Insert a row into a Supabase table."""
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }
    
    with httpx.Client(timeout=30) as client:
        r = client.post(f'{SUPABASE_URL}/rest/v1/{table}', headers=headers, json=data)
    
    if r.status_code in (200, 201):
        return {'status': 'inserted', 'data': r.json()}
    return {'error': f'{r.status_code} {r.text[:200]}'}


def supabase_store_memory(content: str, memory_type: str = 'episodic', 
                           tags: list = None, importance: float = 0.5) -> dict:
    """Store a memory in Supabase episodic_memories table."""
    return supabase_insert('episodic_memories', {
        'content': content,
        'memory_type': memory_type,
        'tags': tags or [],
        'importance': importance,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })


# ─────────────────────────────────────────────
# INTEGRATION STATUS
# ─────────────────────────────────────────────

def get_integration_status() -> dict:
    """Get status of all integrations."""
    status = {}
    
    # Gmail / Google
    try:
        token = GoogleTokenManager.get_access_token()
        status['gmail'] = '✅ connected' if token else '❌ no token'
        status['calendar'] = '✅ connected' if token else '❌ no token'
    except Exception as e:
        status['gmail'] = f'❌ {e}'
        status['calendar'] = f'❌ {e}'
    
    # iMessage
    status['imessage'] = '✅ native AppleScript (macOS)' if sys.platform == 'darwin' else '❌ not macOS'
    
    # Supabase
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(
                f'{SUPABASE_URL}/rest/v1/episodic_memories?limit=1',
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
            )
        status['supabase'] = '✅ connected' if r.status_code == 200 else f'❌ {r.status_code}'
    except Exception as e:
        status['supabase'] = f'❌ {e}'
    
    # Telegram (check .env)
    tg_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    status['telegram'] = '✅ configured' if tg_token else '❌ no token'
    
    # WhatsApp
    wa_token = os.getenv('WHATSAPP_ACCESS_TOKEN', '')
    status['whatsapp'] = '✅ configured' if wa_token else '⚠️ credentials needed'
    
    return status


if __name__ == '__main__':
    print("Integration Status Check:")
    status = get_integration_status()
    for k, v in status.items():
        print(f"  {k:15} {v}")
    
    print("\nCalendar Events (next 7 days):")
    events = calendar_get_events(7)
    if events:
        for e in events:
            print(f"  - {e.get('summary')} @ {e.get('start')}")
    else:
        print("  (none)")
    
    print("\nGmail Inbox (5 most recent):")
    inbox = gmail_list_inbox(5)
    msgs = inbox.get('messages', [])
    print(f"  {len(msgs)} messages retrieved")

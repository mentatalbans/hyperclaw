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

# Resolve token file path using os.path.realpath to avoid symlink/nested-dir confusion
_token_path_raw = str(Path.home() / '.hyperclaw/workspace/secrets/gmail_token.json')
GMAIL_TOKEN_FILE = Path(os.path.realpath(_token_path_raw)) if os.path.exists(_token_path_raw) else Path(_token_path_raw)
GOOGLE_CREDS_FILE = Path(os.getenv('GOOGLE_CREDENTIALS_FILE', ''))

# ─────────────────────────────────────────────
# TOKEN MANAGER
# ─────────────────────────────────────────────

class GoogleTokenManager:
    """Manages Google OAuth tokens with auto-refresh.
    
    Hardened version: always checks expiry, auto-refreshes on 401,
    reloads from disk every 30s (not 5min), and retries with env fallbacks.
    Self-heals by syncing refresh_token from .env if token file's is revoked.
    """
    
    _token_data: dict = {}
    _last_loaded: float = 0
    _refresh_failures: int = 0
    # Circuit breaker: stop hammering Google once the grant is dead/revoked.
    MAX_FAILURES: int = 5
    _circuit_open_until: float = 0.0   # epoch seconds; while now < this, _refresh() is a no-op
    _alerted: bool = False             # ensures only ONE reauth alert per outage
    _auth_revoked: bool = False        # terminal state: refresh token returned invalid_grant

    @classmethod
    def load(cls, force: bool = False) -> dict:
        """Load token from disk. Reloads every 30s or on force."""
        import time
        if force or (time.time() - cls._last_loaded > 30):
            if GMAIL_TOKEN_FILE.exists():
                try:
                    with open(GMAIL_TOKEN_FILE) as f:
                        cls._token_data = json.load(f)
                    cls._last_loaded = time.time()
                    logger.debug(f"Loaded token from {GMAIL_TOKEN_FILE} (refresh: {cls._token_data.get('refresh_token','')[:20]}...)")
                except Exception as e:
                    logger.error(f"Failed to load token file {GMAIL_TOKEN_FILE}: {e}")
            else:
                logger.warning(f"Token file not found: {GMAIL_TOKEN_FILE}")
        return cls._token_data
    
    @classmethod
    def _is_expired(cls, data: dict) -> bool:
        """Check if token is expired or will expire within 10 minutes."""
        expiry_str = data.get('expiry')
        if not expiry_str:
            return True  # No expiry = assume expired
        try:
            expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return now >= expiry - timedelta(minutes=10)
        except Exception:
            return True  # Can't parse = assume expired
    
    @classmethod
    def get_access_token(cls) -> str:
        """Get valid access token, refreshing if needed."""
        data = cls.load()
        
        # Support both 'token' and 'access_token' keys
        token = data.get('token', '') or data.get('access_token', '')
        
        # If token is empty, force reload from disk
        if not token:
            data = cls.load(force=True)
            token = data.get('token', '') or data.get('access_token', '')
        
        # If still empty or expired, refresh
        if not token or cls._is_expired(data):
            cls._refresh()
            data = cls._token_data
            token = data.get('token', '') or data.get('access_token', '')
        
        return token
    
    @classmethod
    def handle_401(cls) -> str:
        """Called when a Gmail API call returns 401. Force refresh and return new token."""
        logger.warning("Got 401 from Gmail API, force-refreshing token...")
        cls._last_loaded = 0  # Force disk reload first
        cls.load(force=True)
        cls._refresh()
        data = cls._token_data
        return data.get('token', '') or data.get('access_token', '')

    @classmethod
    def is_healthy(cls) -> bool:
        """True when Gmail/Calendar auth is usable. Used to gate email send/bump/escalation so
        The assistant does not generate work (bumps, SLA breaches) it cannot deliver while the token is dead."""
        if cls._auth_revoked:
            return False
        try:
            tok = cls.get_access_token()
            return bool(tok) and cls._refresh_failures == 0 and not cls._is_expired(cls._token_data)
        except Exception:
            return False

    @classmethod
    def _refresh(cls) -> None:
        """Refresh the access token. Tries token file creds, then env var fallbacks.
        
        Self-healing: if the token file's refresh_token is revoked but the env var's works,
        automatically update the token file with the working refresh_token.
        """
        import time
        # Circuit breaker: after repeated failures (or a revoked grant) we back off instead of
        # retrying on every single Gmail/Calendar call. This is what stopped the 8000+ retries.
        if time.time() < cls._circuit_open_until:
            return
        data = cls._token_data
        
        # Build credential pairs to try (token file first, then env vars)
        cred_sets = []
        file_refresh = data.get('refresh_token', '')
        
        # From token file
        if data.get('client_id') and file_refresh:
            cred_sets.append({
                'client_id': data['client_id'],
                'client_secret': data.get('client_secret', ''),
                'refresh_token': file_refresh,
                'source': 'token_file',
            })
        
        # From env vars (as fallback)
        env_client_id = os.getenv('GMAIL_CLIENT_ID', '')
        env_client_secret = os.getenv('GMAIL_CLIENT_SECRET', '')
        env_refresh = os.getenv('GMAIL_REFRESH_TOKEN', '')
        if env_client_id and env_refresh and env_refresh != file_refresh:
            cred_sets.append({
                'client_id': env_client_id,
                'client_secret': env_client_secret,
                'refresh_token': env_refresh,
                'source': 'env_vars',
            })
        
        for i, creds in enumerate(cred_sets):
            source = creds.pop('source', f'set_{i}')
            try:
                params = {**creds, 'grant_type': 'refresh_token'}
                with httpx.Client(timeout=15) as client:
                    resp = client.post('https://oauth2.googleapis.com/token', data=params)
                result = resp.json()
                
                if 'access_token' in result:
                    new_token = result['access_token']
                    expires_in = result.get('expires_in', 3600)
                    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    
                    cls._token_data['token'] = new_token
                    cls._token_data['access_token'] = new_token
                    cls._token_data['expiry'] = expiry.isoformat()
                    
                    # Self-heal: if env var worked but file didn't, sync the working token to file
                    if source == 'env_vars' and creds['refresh_token'] != file_refresh:
                        logger.warning("Self-healing: updating token file with working refresh_token from env")
                        cls._token_data['refresh_token'] = creds['refresh_token']
                        cls._token_data['client_id'] = creds['client_id']
                        cls._token_data['client_secret'] = creds['client_secret']
                    
                    # Handle token rotation (Google may issue new refresh tokens)
                    if result.get('refresh_token'):
                        cls._token_data['refresh_token'] = result['refresh_token']
                        logger.info("Google returned a rotated refresh_token, saved.")
                    
                    # Persist to disk
                    try:
                        with open(GMAIL_TOKEN_FILE, 'w') as f:
                            json.dump(cls._token_data, f, indent=2)
                    except Exception as e:
                        logger.error(f"Failed to persist token to disk: {e}")
                    
                    cls._last_loaded = 0  # Force reload next time
                    cls._refresh_failures = 0
                    cls._alerted = False
                    cls._circuit_open_until = 0.0
                    cls._auth_revoked = False
                    logger.info(f"Google token refreshed (source: {source}), expires {expiry.isoformat()}")
                    return
                else:
                    error_code = str(result.get('error', '')).lower()
                    error_desc = result.get('error_description', result.get('error', 'unknown'))
                    logger.warning(f"Refresh attempt {i} ({source}) failed: {error_desc}")
                    # invalid_grant / unauthorized_client are PERMANENT (revoked refresh token or
                    # wrong client). Trying more creds or retrying later is futile until reauth.
                    if error_code in ('invalid_grant', 'unauthorized_client'):
                        cls._auth_revoked = True
                        break
            except Exception as e:
                logger.warning(f"Token refresh attempt {i} ({source}) failed: {e}")
        
        cls._refresh_failures += 1
        logger.error(f"All token refresh attempts failed (failure #{cls._refresh_failures})")
        # Trip the circuit once we hit the ceiling or the grant is terminally revoked, and alert
        # the owner exactly once (instead of thousands of silent failures filling the logs).
        if cls._auth_revoked or cls._refresh_failures >= cls.MAX_FAILURES:
            cooldown = 3600 if cls._auth_revoked else 900  # revoked needs human; transient backs off
            cls._circuit_open_until = time.time() + cooldown
            if not cls._alerted:
                cls._alerted = True
                _send_gmail_reauth_alert(revoked=cls._auth_revoked)


def _send_gmail_reauth_alert(revoked: bool = True) -> None:
    """Post a Gmail-reauth Telegram alert, at most once per hour ACROSS ALL processes.
    Best-effort, never raises. The file-based cooldown prevents every daemon (hermes, briefing,
    server, CLI) from each firing its own alert during the same outage."""
    try:
        import time as _t
        stamp = Path.home() / '.hyperclaw/data/.gmail_alert_sent'
        try:
            if stamp.exists() and (_t.time() - stamp.stat().st_mtime) < 3600:
                return  # another process already alerted within the last hour
        except Exception:
            pass

        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        if not token or not chat_id:
            return
        if revoked:
            text = ("Gmail auth is down. The OAuth refresh token is revoked (invalid_grant). "
                    "Email send and follow-ups are paused until reauth.\n"
                    "Fix: run  python3 ~/.hyperclaw/scripts/gmail_reauth.py  on the Mac, then publish "
                    "the OAuth app to Production in Google Cloud so it stops expiring weekly.")
        else:
            text = ("Gmail token refresh has failed repeatedly; email send/follow-ups may be "
                    "degraded. If it persists, run  python3 ~/.hyperclaw/scripts/gmail_reauth.py")
        with httpx.Client(timeout=10) as client:
            client.post(f'https://api.telegram.org/bot{token}/sendMessage',
                        data={'chat_id': chat_id, 'text': text})
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(int(_t.time())))
        except Exception:
            pass
        logger.error("Sent one-time Gmail reauth alert to Telegram")
    except Exception as e:
        logger.warning(f"Failed to send Gmail reauth alert: {e}")


# ─────────────────────────────────────────────
# GMAIL
# ─────────────────────────────────────────────

def _gmail_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make a Gmail API request with automatic 401 retry."""
    token = GoogleTokenManager.get_access_token()
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = f'Bearer {token}'
    
    with httpx.Client(timeout=30) as client:
        r = getattr(client, method)(url, headers=headers, **kwargs)
    
    # Auto-retry on 401
    if r.status_code == 401:
        logger.warning("Gmail 401 - auto-refreshing token and retrying...")
        new_token = GoogleTokenManager.handle_401()
        if new_token:
            headers['Authorization'] = f'Bearer {new_token}'
            with httpx.Client(timeout=30) as client:
                r = getattr(client, method)(url, headers=headers, **kwargs)
    
    return r


def gmail_list_inbox(max_results: int = 20, query: str = 'in:inbox') -> dict:
    """List inbox messages."""
    r = _gmail_request(
        'get',
        'https://gmail.googleapis.com/gmail/v1/users/me/messages',
        params={'q': query, 'maxResults': max_results}
    )
    
    if r.status_code != 200:
        return {'error': f'Gmail list failed: {r.status_code} {r.text[:200]}'}
    
    return r.json()


def gmail_get_message(message_id: str, format: str = 'full') -> dict:
    """Get a specific message."""
    r = _gmail_request(
        'get',
        f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}',
        params={'format': format}
    )
    
    return r.json() if r.status_code == 200 else {'error': r.text[:200]}


import re as _re_attach
# Forward-looking phrases that mean "a file is attached" (not benign uses of 'attached').
_ATTACH_PROMISE_RE = _re_attach.compile(
    r"(please find attached|see attached|find attached|attached (is|are|please|herewith|you will find)|"
    r"i[' ]?ve attached|i have attached|i am attaching|i'm attaching|attaching the|the attached (file|document|"
    r"deck|report|spreadsheet|pdf|doc)|enclosed (is|are|please)|\bpfa\b|attachment:)",
    _re_attach.IGNORECASE,
)


def _body_promises_attachment(body: str) -> bool:
    """True if the body clearly promises an attached file."""
    return bool(_ATTACH_PROMISE_RE.search(body or ""))


def gmail_send(to: str, subject: str, body: str, cc: str = '', reply_to_id: str = '', attachments: list = None, in_reply_to: str = None, references: str = None, bcc: str = '', allow_unattached: bool = False, body_html: str = None) -> dict:
    """Send an email via Gmail with optional attachments.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        body: Email body text
        cc: Additional CC recipients (comma-separated)
        bcc: BCC recipients (comma-separated) - use for a private side-channel to yourself
        reply_to_id: Thread ID to reply to (keeps email in same thread)
        attachments: List of file paths to attach
        in_reply_to: Message-ID to reply to (for In-Reply-To header)
        references: Full References header chain (defaults to in_reply_to)
        allow_unattached: bypass the attachment-promise guard (only if intentionally no file)

    Returns: {status, id, threadId, to, cc, bcc} on success or {error} on failure

    Threading: when reply_to_id (a Gmail threadId) is given but in_reply_to is not, this
    auto-resolves the parent Message-ID + References chain from the thread and forces the subject
    to match the thread. Without those headers replies fork into a NEW thread - which was the bug.
    """
    import base64
    import mimetypes
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders

    token = GoogleTokenManager.get_access_token()
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    # --- Make replies thread correctly for ALL callers (the new-thread-on-reply fix) ---
    if reply_to_id and not in_reply_to:
        try:
            tr = _gmail_request(
                'get', f'https://gmail.googleapis.com/gmail/v1/users/me/threads/{reply_to_id}',
                params={'format': 'metadata', 'metadataHeaders': ['Message-ID', 'References', 'Subject']}
            )
            if tr.status_code == 200:
                tmsgs = tr.json().get('messages', [])
                if tmsgs:
                    lh = {h['name'].lower(): h['value'] for h in tmsgs[-1].get('payload', {}).get('headers', [])}
                    in_reply_to = lh.get('message-id', '') or in_reply_to
                    parent_refs = lh.get('references', '')
                    if in_reply_to and not references:
                        references = (parent_refs + ' ' + in_reply_to).strip() if parent_refs else in_reply_to
                    # Gmail requires the subject to match the thread for a threadId send to stick.
                    tsubj = lh.get('subject', '')
                    if tsubj:
                        import re as _re
                        _norm = lambda s: _re.sub(r'^\s*(re|fwd|fw)\s*:\s*', '', (s or '').strip(),
                                                  flags=_re.IGNORECASE).strip().lower()
                        if _norm(subject) != _norm(tsubj):
                            subject = tsubj if tsubj.lower().startswith('re:') else f"Re: {tsubj}"
        except Exception as e:
            logger.debug(f"Reply threading resolve failed: {e}")

    # Pre-send attachment guard: never send a message that promises a file it doesn't carry.
    if not attachments and not allow_unattached and _body_promises_attachment(body):
        return {'error': "BLOCKED_NO_ATTACHMENT: the body references an attached file but no file is "
                         "attached. Attach the file (attachments=[...] or email_send_with_attachment), "
                         "or reword the body, then resend. If intentional, pass allow_unattached=True."}

    # Optionally CC a configured address on every outbound email (CC_EMAIL env). Empty = no auto-CC.
    _auto_cc = os.environ.get('CC_EMAIL', '').strip()
    cc_list = [_auto_cc] if _auto_cc else []
    if cc and cc != _auto_cc:
        cc_list.append(cc)
    bcc_list = [b.strip() for b in (bcc or '').split(',') if b.strip()]

    # Use mixed multipart for attachments/html, alternative for plain email
    if attachments or body_html:
        msg = MIMEMultipart('mixed')
    else:
        msg = MIMEMultipart('alternative')

    msg['to'] = to
    msg['subject'] = subject
    msg['cc'] = ', '.join(cc_list)
    if bcc_list:
        msg['bcc'] = ', '.join(bcc_list)

    # Add threading headers for replies
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = references or in_reply_to

    # Body: plain always; when body_html is given, send a proper multipart/alternative
    # so modern clients render rich formatting and plain-text clients still work.
    if body_html:
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body, 'plain'))
        alt.attach(MIMEText(body_html, 'html'))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, 'plain'))

    # Attach files
    if attachments:
        for file_path in attachments:
            path = Path(file_path)
            if not path.exists():
                return {'error': f'Attachment not found: {file_path}'}

            mime_type, _ = mimetypes.guess_type(str(path))
            if mime_type is None:
                mime_type = 'application/octet-stream'

            main_type, sub_type = mime_type.split('/', 1)

            with open(path, 'rb') as f:
                file_data = f.read()

            part = MIMEBase(main_type, sub_type)
            part.set_payload(file_data)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=path.name)
            msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {'raw': raw}
    if reply_to_id:
        payload['threadId'] = reply_to_id

    with httpx.Client(timeout=60) as client:
        r = client.post(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers=headers,
            json=payload
        )

    # Auto-retry on 401
    if r.status_code == 401:
        logger.warning("Gmail send 401 - auto-refreshing token and retrying...")
        new_token = GoogleTokenManager.handle_401()
        if new_token:
            headers['Authorization'] = f'Bearer {new_token}'
            with httpx.Client(timeout=60) as client:
                r = client.post(
                    'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                    headers=headers,
                    json=payload
                )

    if r.status_code in (200, 201):
        j = r.json()
        return {'status': 'sent', 'id': j.get('id', ''), 'threadId': j.get('threadId', ''),
                'attachments': len(attachments or []),
                'to': to, 'cc': ', '.join(cc_list), 'bcc': ', '.join(bcc_list)}
    return {'error': f'Send failed: {r.status_code} {r.text[:200]}'}


def gmail_reply(message_id: str, body: str, cc: str = '', attachments: list = None,
                reply_all: bool = False) -> dict:
    """Reply to a specific message, guaranteed in-thread.

    Resolves the recipient, subject (Re:), threadId and In-Reply-To/References from the original
    message, so callers only need the message_id + body. Use this for replies instead of gmail_send.
    """
    orig = gmail_get_message(message_id, format='metadata')
    if 'error' in orig:
        orig = gmail_get_message(message_id, format='full')
    if 'error' in orig:
        return {'error': f"Could not load message {message_id}: {orig.get('error')}"}

    hdrs = {h['name'].lower(): h['value'] for h in orig.get('payload', {}).get('headers', [])}
    thread_id = orig.get('threadId', '')

    def _bare(addr: str) -> str:
        addr = addr or ''
        if '<' in addr and '>' in addr:
            return addr[addr.find('<') + 1:addr.find('>')].strip()
        return addr.strip()

    to_addr = _bare(hdrs.get('reply-to') or hdrs.get('from', ''))
    cc_arg = cc
    if reply_all:
        extras = [hdrs.get('to', ''), hdrs.get('cc', '')]
        cc_arg = ', '.join([x for x in ([cc] + extras) if x])

    # gmail_send auto-resolves In-Reply-To/References/subject from the threadId.
    return gmail_send(to=to_addr, subject=hdrs.get('subject', ''), body=body, cc=cc_arg,
                      reply_to_id=thread_id, attachments=attachments)


def _html_to_text(html: str) -> str:
    """Convert HTML email to clean readable text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')

        # Remove script/style elements
        for tag in soup(['script', 'style', 'head', 'meta', 'link']):
            tag.decompose()

        # Get text and clean up whitespace
        text = soup.get_text(separator='\n')
        lines = [line.strip() for line in text.splitlines()]
        text = '\n'.join(line for line in lines if line)
        return text
    except ImportError:
        # Fallback: basic regex strip
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


def _extract_body_enhanced(payload: dict, message_id: str = None) -> str:
    """Extract readable text from an email payload.

    Robust to the things that used to return a blank body: incorrect base64 padding, bodies
    delivered via attachmentId (large/forwarded emails - the #1 cause of "forwarded message is
    blank"), deeply nested multipart, and forwarded message/rfc822 parts. Accumulates ALL text
    parts so forwarded/quoted content is preserved. Prefers text/plain, falls back to HTML->text.
    """
    import base64

    def _b64(data: str) -> str:
        # Pad to a multiple of 4 with the CORRECT count of '=' (the old '+ ==' hack corrupted some).
        return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')

    def _fetch_attachment_body(att_id: str) -> str:
        if not message_id or not att_id:
            return ''
        try:
            r = _gmail_request(
                'get',
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{att_id}'
            )
            if r.status_code == 200:
                return _b64(r.json().get('data', ''))
        except Exception as e:
            logger.debug(f"Body attachment fetch failed: {e}")
        return ''

    plain_parts, html_parts = [], []

    def walk(p):
        mime = (p.get('mimeType', '') or '')
        body = p.get('body', {}) or {}
        data = body.get('data', '')
        att_id = body.get('attachmentId', '')
        text = ''
        if data:
            try:
                text = _b64(data)
            except Exception as e:
                logger.debug(f"Body base64 decode failed for {mime}: {e}")
        elif att_id and mime.startswith('text/'):
            # Gmail returns large/forwarded bodies as attachments, not inline data.
            text = _fetch_attachment_body(att_id)
        if text:
            if mime.startswith('text/plain'):
                plain_parts.append(text)
            elif mime.startswith('text/html'):
                html_parts.append(text)
        # Recurse: multipart/* and forwarded message/rfc822 both carry nested parts.
        for part in (p.get('parts', []) or []):
            walk(part)

    walk(payload)

    if plain_parts:
        return '\n'.join(plain_parts).strip()
    if html_parts:
        return _html_to_text('\n'.join(html_parts)).strip()
    return ''


def gmail_read_message_text(message_id: str) -> str:
    """Get full readable text of a message with thread info for replies."""
    msg = gmail_get_message(message_id, format='full')
    if 'error' in msg:
        return f"Error: {msg['error']}"

    payload = msg.get('payload', {})
    headers = {h['name']: h['value'] for h in payload.get('headers', [])}
    body = _extract_body_enhanced(payload, message_id)

    # Include IDs needed for replying
    thread_id = msg.get('threadId', '')
    msg_id_header = headers.get('Message-ID', headers.get('Message-Id', ''))

    return f"""From: {headers.get('From', 'Unknown')}
To: {headers.get('To', 'Unknown')}
Date: {headers.get('Date', 'Unknown')}
Subject: {headers.get('Subject', 'No subject')}
[Thread-ID: {thread_id}] [Message-ID: {message_id}]

{body[:5000]}"""


def gmail_list_sent(max_results: int = 20) -> dict:
    """List sent emails."""
    return gmail_list_inbox(max_results=max_results, query='in:sent')


def gmail_list_all(max_results: int = 20, query: str = '') -> dict:
    """List all emails (inbox + sent + all labels).

    Args:
        max_results: Maximum number of results
        query: Gmail search query (e.g., 'from:someone@example.com', 'subject:hello', 'after:2024/01/01')
    """
    params = {'maxResults': max_results}
    if query:
        params['q'] = query

    r = _gmail_request(
        'get',
        'https://gmail.googleapis.com/gmail/v1/users/me/messages',
        params=params
    )

    if r.status_code != 200:
        return {'error': f'Gmail list failed: {r.status_code} {r.text[:200]}'}

    return r.json()


def gmail_search(query: str, max_results: int = 20) -> list:
    """Search all emails with a query.

    Query examples:
        'from:john@example.com' - emails from John
        'to:jane@example.com' - emails to Jane
        'subject:meeting' - emails with 'meeting' in subject
        'in:sent' - sent emails
        'in:inbox' - inbox emails
        'is:unread' - unread emails
        'after:2024/01/01 before:2024/12/31' - date range
        'has:attachment' - emails with attachments
        'larger:5M' - emails larger than 5MB

    Returns list of parsed email summaries.
    """
    result = gmail_list_all(max_results=max_results, query=query)

    if 'error' in result:
        return [{'error': result['error']}]

    messages = result.get('messages', [])
    emails = []

    for msg_ref in messages[:max_results]:
        msg = gmail_get_message(msg_ref['id'], format='metadata')
        if 'error' in msg:
            continue

        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        labels = msg.get('labelIds', [])

        emails.append({
            'id': msg.get('id'),
            'thread_id': msg.get('threadId'),
            'from': headers.get('From', ''),
            'to': headers.get('To', ''),
            'subject': headers.get('Subject', '(no subject)'),
            'date': headers.get('Date', ''),
            'snippet': msg.get('snippet', '')[:100],
            'labels': labels,
            'is_sent': 'SENT' in labels,
            'is_unread': 'UNREAD' in labels,
        })

    return emails


def gmail_get_thread(thread_id: str, parse_quotes: bool = True) -> dict:
    """Get all messages in an email thread with enhanced parsing.

    Args:
        thread_id: The Gmail thread ID
        parse_quotes: If True, separate new content from quoted replies

    Returns dict with:
        - messages: list of messages in chronological order
        - participants: unique list of all participants
        - subject: thread subject
        - summary: quick overview
    """
    r = _gmail_request(
        'get',
        f'https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}',
        params={'format': 'full'}
    )

    if r.status_code != 200:
        return {'error': f'Thread fetch failed: {r.status_code}', 'messages': []}

    thread = r.json()
    messages = []
    participants = set()
    subject = ''

    for msg in thread.get('messages', []):
        payload = msg.get('payload', {})
        hdrs = {h['name']: h['value'] for h in payload.get('headers', [])}

        # Track participants
        from_addr = hdrs.get('From', '')
        to_addr = hdrs.get('To', '')
        participants.add(from_addr)
        if to_addr:
            for addr in to_addr.split(','):
                participants.add(addr.strip())

        # Get subject from first message
        if not subject:
            subject = hdrs.get('Subject', '')

        # Extract body with HTML support
        body = _extract_body_enhanced(payload, msg.get('id'))
        labels = msg.get('labelIds', [])

        # Parse quotes if requested
        if parse_quotes and body:
            parsed = parse_email_quotes(body)
            new_content = parsed['new_content']
            quoted = parsed['quoted']
        else:
            new_content = body
            quoted = []

        # Check for attachments
        attachments = gmail_list_attachments(msg.get('id', ''))
        has_attachments = bool(attachments and 'error' not in attachments[0])

        messages.append({
            'id': msg.get('id'),
            'from': from_addr,
            'to': to_addr,
            'cc': hdrs.get('Cc', ''),
            'date': hdrs.get('Date', ''),
            'subject': hdrs.get('Subject', ''),
            'new_content': new_content[:3000] if parse_quotes else None,
            'body': body[:3000] if not parse_quotes else None,
            'quoted_sections': len(quoted),
            'is_sent': 'SENT' in labels,
            'has_attachments': has_attachments,
            'attachment_count': len(attachments) if has_attachments else 0,
        })

    return {
        'thread_id': thread_id,
        'subject': subject,
        'message_count': len(messages),
        'participants': list(participants),
        'messages': messages,
    }


def gmail_get_recent_activity(hours: int = 24, max_results: int = 50) -> dict:
    """Get recent email activity (sent and received) in the last N hours.

    Returns summary of recent emails grouped by sent/received.
    """
    from datetime import datetime, timedelta

    # Calculate date for query
    after_date = (datetime.now() - timedelta(hours=hours)).strftime('%Y/%m/%d')

    # Get all recent emails
    all_emails = gmail_search(f'after:{after_date}', max_results=max_results)

    if all_emails and 'error' in all_emails[0]:
        return {'error': all_emails[0]['error']}

    sent = [e for e in all_emails if e.get('is_sent')]
    received = [e for e in all_emails if not e.get('is_sent')]

    return {
        'period_hours': hours,
        'total': len(all_emails),
        'sent_count': len(sent),
        'received_count': len(received),
        'sent': sent[:10],  # Last 10 sent
        'received': received[:10],  # Last 10 received
    }


# ─────────────────────────────────────────────
# EMAIL ATTACHMENTS
# ─────────────────────────────────────────────

DOWNLOADS_DIR = Path.home() / '.hyperclaw' / 'downloads'
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def gmail_list_attachments(message_id: str) -> list:
    """List all attachments in an email.

    Returns list of {filename, mime_type, size, attachment_id, part_id}
    """
    msg = gmail_get_message(message_id, format='full')
    if 'error' in msg:
        return [{'error': msg['error']}]

    attachments = []

    def find_attachments(part, part_path=''):
        filename = part.get('filename', '')
        attachment_id = part.get('body', {}).get('attachmentId')

        if filename and attachment_id:
            attachments.append({
                'filename': filename,
                'mime_type': part.get('mimeType', 'application/octet-stream'),
                'size': part.get('body', {}).get('size', 0),
                'attachment_id': attachment_id,
                'part_id': part_path,
            })

        for i, sub_part in enumerate(part.get('parts', [])):
            find_attachments(sub_part, f"{part_path}.{i}" if part_path else str(i))

    find_attachments(msg.get('payload', {}))
    return attachments


def gmail_download_attachment(message_id: str, attachment_id: str, filename: str = None) -> dict:
    """Download an email attachment.

    Args:
        message_id: The email message ID
        attachment_id: The attachment ID from gmail_list_attachments()
        filename: Optional filename to save as (defaults to original)

    Returns: {path, size, filename} on success or {error} on failure
    """
    import base64

    r = _gmail_request(
        'get',
        f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}',
    )

    if r.status_code != 200:
        return {'error': f'Download failed: {r.status_code} {r.text[:200]}'}

    data = r.json().get('data', '')
    if not data:
        return {'error': 'No attachment data returned'}

    # Decode base64
    file_data = base64.urlsafe_b64decode(data + '==')

    # Determine filename
    if not filename:
        # Try to get from attachment list
        attachments = gmail_list_attachments(message_id)
        for att in attachments:
            if att.get('attachment_id') == attachment_id:
                filename = att.get('filename', f'attachment_{attachment_id[:8]}')
                break
        else:
            filename = f'attachment_{attachment_id[:8]}'

    # Save file
    save_path = DOWNLOADS_DIR / filename
    save_path.write_bytes(file_data)

    return {
        'path': str(save_path),
        'size': len(file_data),
        'filename': filename,
    }


def gmail_download_all_attachments(message_id: str) -> list:
    """Download all attachments from an email.

    Returns list of download results.
    """
    attachments = gmail_list_attachments(message_id)
    if attachments and 'error' in attachments[0]:
        return attachments

    results = []
    for att in attachments:
        result = gmail_download_attachment(
            message_id,
            att['attachment_id'],
            att['filename']
        )
        result['original_filename'] = att['filename']
        results.append(result)

    return results



def gmail_forward(message_id: str, to: str, note: str = '', include_attachments: bool = True) -> dict:
    """Forward an email (body + attachments) to someone, with an optional note on top."""
    meta = gmail_get_message(message_id)
    if 'error' in meta:
        return meta
    headers = {h['name'].lower(): h['value'] for h in meta.get('payload', {}).get('headers', [])}
    orig_subject = headers.get('subject', '(no subject)')
    orig_from = headers.get('from', 'unknown')
    orig_date = headers.get('date', '')
    body_text = gmail_read_message_text(message_id)

    fwd_body = ''
    if note:
        fwd_body += note + '\n\n'
    fwd_body += f"---------- Forwarded message ----------\nFrom: {orig_from}\nDate: {orig_date}\nSubject: {orig_subject}\n\n{body_text}"

    att_paths = []
    if include_attachments:
        try:
            for saved in gmail_download_all_attachments(message_id):
                path = saved.get('path') if isinstance(saved, dict) else saved
                if path:
                    att_paths.append(path)
        except Exception as e:
            logger.warning(f"Forward: attachment download failed: {e}")

    subject = orig_subject if orig_subject.lower().startswith('fwd:') else f'Fwd: {orig_subject}'
    return gmail_send(to=to, subject=subject, body=fwd_body,
                      attachments=att_paths or None, allow_unattached=True)


def gmail_create_draft(to: str, subject: str, body: str, cc: str = '',
                       attachments: list = None, body_html: str = None,
                       thread_id: str = '') -> dict:
    """Create a Gmail DRAFT (visible in the account's Drafts folder) instead of sending.
    The approval-queue path: the assistant prepares, the owner hits send."""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders
    import mimetypes
    import base64

    token = GoogleTokenManager.get_access_token()
    if not token:
        return {'error': 'Gmail auth unavailable'}

    msg = MIMEMultipart('mixed') if (attachments or body_html) else MIMEMultipart('alternative')
    msg['to'] = to
    msg['subject'] = subject
    if cc:
        msg['cc'] = cc
    if body_html:
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body, 'plain'))
        alt.attach(MIMEText(body_html, 'html'))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, 'plain'))
    for file_path in (attachments or []):
        path = Path(file_path)
        if not path.exists():
            return {'error': f'Attachment not found: {file_path}'}
        mime_type, _ = mimetypes.guess_type(str(path))
        main_type, sub_type = (mime_type or 'application/octet-stream').split('/', 1)
        part = MIMEBase(main_type, sub_type)
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=path.name)
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {'message': {'raw': raw}}
    if thread_id:
        payload['message']['threadId'] = thread_id
    r = _gmail_request('post', 'https://gmail.googleapis.com/gmail/v1/users/me/drafts', json=payload)
    if r.status_code in (200, 201):
        j = r.json()
        return {'status': 'draft_created', 'draft_id': j.get('id', ''), 'to': to, 'subject': subject,
                'note': 'Draft is in the Drafts folder awaiting review/send.'}
    return {'error': f'Draft create failed: {r.status_code} {r.text[:200]}'}


def gmail_modify(message_id: str, mark_read: bool = None, archive: bool = None,
                 star: bool = None, add_labels: list = None, remove_labels: list = None) -> dict:
    """Inbox management: mark read/unread, archive, star/unstar, arbitrary labels."""
    add, remove = list(add_labels or []), list(remove_labels or [])
    if mark_read is True:
        remove.append('UNREAD')
    elif mark_read is False:
        add.append('UNREAD')
    if archive is True:
        remove.append('INBOX')
    elif archive is False:
        add.append('INBOX')
    if star is True:
        add.append('STARRED')
    elif star is False:
        remove.append('STARRED')
    if not add and not remove:
        return {'error': 'Nothing to modify'}
    r = _gmail_request(
        'post', f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify',
        json={'addLabelIds': add, 'removeLabelIds': remove})
    if r.status_code == 200:
        return {'status': 'modified', 'added': add, 'removed': remove}
    return {'error': f'Modify failed: {r.status_code} {r.text[:200]}'}

def strip_email_signature(body: str) -> dict:
    """Detect and strip email signature from body.

    Returns: {content: str, signature: str}
    """
    import re

    # Common signature patterns
    sig_patterns = [
        r'\n--\s*\n',                           # Standard -- delimiter
        r'\nBest,?\s*\n',                       # Best,
        r'\nRegards,?\s*\n',                    # Regards,
        r'\nThanks,?\s*\n',                     # Thanks,
        r'\nCheers,?\s*\n',                     # Cheers,
        r'\nSincerely,?\s*\n',                  # Sincerely,
        r'\nSent from my iPhone',              # iOS
        r'\nSent from my iPad',                # iPad
        r'\nGet Outlook for',                  # Outlook mobile
        r'\n_{3,}',                             # ___ separator
        r'\n-{3,}',                             # --- separator
    ]

    earliest_match = len(body)
    matched_pattern = None

    for pattern in sig_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match and match.start() < earliest_match:
            earliest_match = match.start()
            matched_pattern = pattern

    if matched_pattern and earliest_match < len(body) - 50:
        return {
            'content': body[:earliest_match].strip(),
            'signature': body[earliest_match:].strip()
        }

    return {'content': body.strip(), 'signature': ''}


def parse_email_quotes(body: str) -> dict:
    """Parse email body to separate new content from quoted replies.

    Returns: {new_content: str, quoted: list[{from, content}]}
    """
    import re

    lines = body.split('\n')
    new_content = []
    quoted_sections = []
    current_quote = []
    quote_attribution = ''
    in_quote = False

    for line in lines:
        # Check for quote attribution line
        quote_start = re.match(r'^On .+ wrote:$', line.strip())
        if quote_start:
            if current_quote:
                quoted_sections.append({
                    'from': quote_attribution,
                    'content': '\n'.join(current_quote)
                })
            quote_attribution = line.strip()
            current_quote = []
            in_quote = True
            continue

        # Check for quoted line (starts with >)
        if line.strip().startswith('>'):
            in_quote = True
            current_quote.append(line.lstrip('> ').strip())
        elif in_quote and line.strip() == '':
            current_quote.append('')
        elif in_quote:
            # End of quoted section
            if current_quote:
                quoted_sections.append({
                    'from': quote_attribution,
                    'content': '\n'.join(current_quote)
                })
            current_quote = []
            in_quote = False
            quote_attribution = ''
            new_content.append(line)
        else:
            new_content.append(line)

    # Handle remaining quote
    if current_quote:
        quoted_sections.append({
            'from': quote_attribution,
            'content': '\n'.join(current_quote)
        })

    return {
        'new_content': '\n'.join(new_content).strip(),
        'quoted': quoted_sections
    }


# ─────────────────────────────────────────────
# CALENDAR
# ─────────────────────────────────────────────

def calendar_get_events(days_ahead: int = 7) -> list:
    """Get upcoming calendar events."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    
    r = _gmail_request(
        'get',
        'https://www.googleapis.com/calendar/v3/calendars/primary/events',
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
    body = {
        'summary': title,
        'start': {'dateTime': start_iso, 'timeZone': 'America/Los_Angeles'},
        'end': {'dateTime': end_iso, 'timeZone': 'America/Los_Angeles'},
        'description': description,
    }
    if attendees:
        body['attendees'] = [{'email': a} for a in attendees]
    
    r = _gmail_request(
        'post',
        'https://www.googleapis.com/calendar/v3/calendars/primary/events',
        headers={'Content-Type': 'application/json'},
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
        status['gmail'] = 'connected' if token else 'no token'
        status['calendar'] = 'connected' if token else 'no token'
    except Exception as e:
        status['gmail'] = f'error: {e}'
        status['calendar'] = f'error: {e}'

    # iMessage
    status['imessage'] = 'native AppleScript (macOS)' if sys.platform == 'darwin' else 'not macOS'

    # Supabase
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(
                f'{SUPABASE_URL}/rest/v1/episodic_memories?limit=1',
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
            )
        status['supabase'] = 'connected' if r.status_code == 200 else f'error: {r.status_code}'
    except Exception as e:
        status['supabase'] = f'error: {e}'

    # Telegram (check .env)
    tg_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    status['telegram'] = 'configured' if tg_token else 'no token'

    # WhatsApp
    wa_token = os.getenv('WHATSAPP_ACCESS_TOKEN', '')
    status['whatsapp'] = 'configured' if wa_token else 'credentials needed'
    
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

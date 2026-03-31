from pathlib import Path
import os
#!/usr/bin/env python3
"""
Assistant Gmail Reader — production inbox tool
Usage: python3 gmail_read.py [--count N] [--unread] [--search "query"]
"""

import argparse
import json
import warnings
import base64
import re
warnings.filterwarnings("ignore")

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = str(Path.home() / ".hyperclaw/workspace/secrets/gmail_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def decode_body(payload):
    """Extract readable text from email payload."""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return body.strip()

def list_emails(count=20, unread_only=False, search=None):
    service = get_service()
    
    params = {"userId": "me", "maxResults": count, "labelIds": ["INBOX"]}
    if unread_only:
        params["labelIds"] = ["INBOX", "UNREAD"]
    if search:
        params["q"] = search
        params.pop("labelIds", None)

    results = service.users().messages().list(**params).execute()
    messages = results.get("messages", [])

    if not messages:
        print("📭 No messages found.")
        return

    print(f"📬 {len(messages)} message(s):\n{'='*70}\n")
    
    for i, msg_ref in enumerate(messages, 1):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        labels = msg.get("labelIds", [])
        is_unread = "UNREAD" in labels
        
        body = decode_body(msg.get("payload", {}))
        body_preview = body[:300].replace("\n", " ").strip() if body else msg.get("snippet", "")[:200]
        
        marker = "🔴 " if is_unread else "   "
        print(f"{marker}[{i}] {headers.get('Subject', '(no subject)')}")
        print(f"     FROM: {headers.get('From', '?')}")
        print(f"     DATE: {headers.get('Date', '?')}")
        print(f"     ID:   {msg_ref['id']}")
        print(f"     {body_preview}...")
        print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--unread", action="store_true")
    parser.add_argument("--search", type=str, default=None)
    args = parser.parse_args()
    
    list_emails(count=args.count, unread_only=args.unread, search=args.search)

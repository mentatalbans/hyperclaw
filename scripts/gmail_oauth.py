#!/usr/bin/env python3
"""
Assistant Gmail OAuth Flow
Generates a token.json for Gmail API access.
Stores credentials in HyperClaw secrets dir.
"""

import json
import os
from pathlib import Path
import sys

CREDENTIALS_FILE = str(Path.home() / "Downloads/GOOGLE_OAUTH_CLIENT_ID_REDACTED.json")
TOKEN_FILE = str(Path.home() / ".hyperclaw/workspace/secrets/gmail_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]

os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("ERROR: google-auth-oauthlib not installed")
    sys.exit(1)

creds = None

# Load existing token if it exists
if os.path.exists(TOKEN_FILE):
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

# If no valid creds, do the OAuth flow
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        print("Refreshing expired token...")
        creds.refresh(Request())
    else:
        print("Starting OAuth flow — browser will open...")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=8085, prompt="consent", open_browser=True)

    # Save the token
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"✅ Token saved to {TOKEN_FILE}")

# Test it — pull Gmail profile
from googleapiclient.discovery import build
service = build("gmail", "v1", credentials=creds)
profile = service.users().getProfile(userId="me").execute()
print(f"\n✅ Gmail connected: {profile.get('emailAddress')}")
print(f"   Total messages: {profile.get('messagesTotal')}")
print(f"   Threads: {profile.get('threadsTotal')}")

# Extract and print token components for HyperClaw config
token_data = json.loads(creds.to_json())
print(f"\n📋 Token info:")
print(f"   client_id: {token_data.get('client_id', '')[:30]}...")
print(f"   refresh_token: {'PRESENT ✅' if token_data.get('refresh_token') else 'MISSING ❌'}")
print(f"   expiry: {token_data.get('expiry', 'N/A')}")

#!/usr/bin/env python3
"""
iMessage Monitor for the user
Checks for new messages every 30 seconds and alerts via Telegram
"""
import subprocess
import time
import json
import requests
import os
from datetime import datetime

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
USER_PHONE = os.environ.get("IMESSAGE_USER_PHONE", "")

def get_recent_messages():
    """Get recent iMessages using AppleScript"""
    script = f'''
    tell application "Messages"
        set recentMessages to {{}}
        repeat with i from 1 to 5
            try
                set theChat to item i of chats
                set lastMessage to last message of theChat
                set messageText to text of lastMessage
                set messageDate to date sent of lastMessage
                set sender to handle of sender of lastMessage
                set recentMessages to recentMessages & {{messageText, messageDate, sender}}
            end try
        end repeat
        return recentMessages
    end tell
    '''
    
    try:
        result = subprocess.run(['osascript', '-e', script], 
                              capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        print(f"Error getting messages: {e}")
        return ""

def send_telegram_alert(message):
    """Send alert to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': f"📱 NEW iMESSAGE for the user:

{message}",
        'parse_mode': 'Markdown'
    }
    
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")

def main():
    print(f"🔍 iMessage Monitor started at {datetime.now()}")
    print(f"📱 Monitoring messages for the user: {USER_PHONE}")
    
    last_check = ""
    
    while True:
        try:
            current_messages = get_recent_messages()
            
            if current_messages and current_messages != last_check:
                print(f"📨 New message activity detected at {datetime.now()}")
                send_telegram_alert("Message activity detected - check iMessages")
                last_check = current_messages
            
            time.sleep(30)  # Check every 30 seconds
            
        except KeyboardInterrupt:
            print("
🛑 iMessage Monitor stopped")
            break
        except Exception as e:
            print(f"Error in monitoring loop: {e}")
            time.sleep(60)  # Wait longer on error

if __name__ == "__main__":
    main()

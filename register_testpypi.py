import requests
from urllib.parse import urlencode

# TestPyPI account registration
print("Setting up TestPyPI account for HyperClaw package testing...")

# Registration data
account_data = {
    'username': 'hyperclaw-test',
    'email': 'your-email@example.com',
    'password': 'HyperClaw2024!Test',
    'full_name': 'HyperClaw Test Account',
}

print(f"Account details prepared:")
print(f"Username: {account_data['username']}")
print(f"Email: {account_data['email']}")
print(f"Full Name: {account_data['full_name']}")
print("
Next: Manual registration required at https://test.pypi.org/account/register/")

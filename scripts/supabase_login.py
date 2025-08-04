from supabase import create_client, Client
from typing import Optional
import requests
import os

SUPABASE_URL = "https://nrwhxklxcedgscmrmbaw.supabase.co/"
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def upsert_user_and_get_jwt(email: str, password: str) -> Optional[str]:
    # 1. Upsert user using Supabase Admin API
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json"
    }

    url = f"{SUPABASE_URL}/auth/v1/admin/users"
    data = {
        "email": email,
        "password": password,
        "email_confirm": True
    }

    # Try to create user; ignore if already exists
    resp = requests.post(url, json=data, headers=headers)

    # 2. Sign in to get JWT
    auth_url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    auth_payload = {"email": email, "password": password}


    auth_resp = requests.post(auth_url, json=auth_payload, headers={"apikey": SUPABASE_SERVICE_ROLE_KEY})
    if auth_resp.status_code != 200:
        raise Exception(f"Login failed: {auth_resp.text}")
    
    return auth_resp.json()["access_token"]


# Example usage
if __name__ == "__main__":
    token = upsert_user_and_get_jwt("demoo@example.com", "securePassword123")
    print("JWT Token:", token)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from livekit import api
import os
import json
import time
from typing import List, Literal, Dict

app = FastAPI()

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "https://your-livekit-server")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "your_key")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "your_secret")


class TokenRequest(BaseModel):
    identity: str
    name: str
    room: str
    tools: List[str]
    language: str = "en"
    role: Literal["publisher", "subscriber"] = "publisher"


@app.post("/generate-token")
def generate_token(data: TokenRequest) -> Dict[str, str]:
    try:
        meta = json.dumps({"tools": data.tools, "lang": data.language})
        token = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)\
            .with_identity(data.identity)\
            .with_name(data.name)\
            .with_metadata(meta)\
            .with_grants(api.VideoGrants(room_join=True, room=data.room))\
            .to_jwt(ttl=3600)
        return {"token": token}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TranscriptQuery(BaseModel):
    session_id: str


@app.post("/transcription")
def get_transcription(q: TranscriptQuery) -> Dict[str, str]:
    # Replace with your actual DB or in-memory store
    # Simulating response
    return {
        "session_id": q.session_id,
        "transcript": "Hi, how can I help you today?",
        "ai_response": "Here are the top results based on your query."
    }

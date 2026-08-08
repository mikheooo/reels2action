import os, asyncio, pydantic
from pydantic import BaseModel, Field
from typing import List
from enum import Enum
from google import genai
from google.genai import types

class CategoryEnum(str, Enum):
    HEALTH = "HEALTH"

class EvidenceLevelEnum(str, Enum):
    shown = "shown"
    mentioned_only = "mentioned_only"

class PerceptionResult(BaseModel):
    raw_transcript: str
    primary_category: CategoryEnum
    secondary_categories: List[CategoryEnum]
    evidence_level: EvidenceLevelEnum
    ui_elements: str

async def test():
    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    print("Uploading file...")
    try:
        # We need a small dummy file
        with open("/tmp/dummy.txt", "w") as f: f.write("dummy")
        gemini_file = await client.aio.files.upload(file="/tmp/dummy.txt")
        print("Uploaded", gemini_file.name)
        
        print("Generating content...")
        resp = await client.aio.models.generate_content(
            model='gemini-3.6-flash',
            contents=[gemini_file, "Analyze this."],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PerceptionResult,
            )
        )
        print("Success:", resp.text)
    except Exception as e:
        print("ERROR:", type(e).__name__, "-", e)

asyncio.run(test())

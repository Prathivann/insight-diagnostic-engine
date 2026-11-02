"""List the models actually available to THIS Groq account."""
import os
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = Groq(api_key=os.environ["GROQ_API_KEY"])

models = client.models.list()
for m in sorted(models.data, key=lambda x: x.id):
    print(m.id)
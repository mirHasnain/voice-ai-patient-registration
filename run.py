"""Local development server. Vercel uses api/index.py instead."""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print(f"[boot] patient registration API  http://localhost:{port}")
    print(f"[boot] dashboard                 http://localhost:{port}/")
    print(f"[boot] interactive API docs      http://localhost:{port}/docs")
    print(f"[boot] vapi webhook              http://localhost:{port}/webhook/vapi")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)

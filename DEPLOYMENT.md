# Deployment Guide

Two ways to run this project. Pick one.

---

## Option A — Local (Full Stack, FastAPI + Streamlit)

Best for: development, testing the full production architecture.

### Prerequisites
```bash
# macOS
brew install tesseract python@3.11

# Ubuntu / WSL
sudo apt install tesseract-ocr libgl1 python3.11 python3.11-venv

# Windows
Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
Install Python 3.11: https://python.org/downloads
```

### Steps

**Terminal 1 — Backend**
```bash
cd backend
python -m venv .venv

# macOS/Linux:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt

# Create your .env file
cp ../.env.example ../.env
# Open .env and add at least: GOOGLE_API_KEY=your_key_here

uvicorn app.main:app --reload --port 8000
```

You should see:
```
INFO  LLM providers ready: ['gemini-primary', 'gemini-flash']
INFO  AI Code Reviewer v2.0.0 starting up
```

**Terminal 2 — Frontend**
```bash
cd frontend
pip install -r requirements.txt
BACKEND_URL=http://127.0.0.1:8000 streamlit run app.py
```

Open **http://localhost:8501**

---

## Option B — Streamlit Community Cloud (Free, No Server Needed)

Best for: sharing demos, portfolio links, zero infra.

This uses `streamlit_app.py` at the root — it runs the **entire pipeline inside Streamlit** with no FastAPI required.

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/aishwarya2629/ai-code-reviewer.git
git push -u origin main
```

### Step 2 — Deploy on Streamlit Cloud
1. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub
2. Click **New app**
3. Set:
   - **Repository:** `aishwarya2629/ai-code-reviewer`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`  ← important, not `frontend/app.py`
4. Click **Advanced settings → Secrets**
5. Paste your secrets:
   ```toml
   GOOGLE_API_KEY = "your_google_api_key_here"
   ```
6. Click **Deploy**

That's it. Streamlit Cloud reads `packages.txt` automatically and installs Tesseract.

### Getting a free API key
- **Google Gemini (recommended):** [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) — free tier, no credit card
- **Groq (fast, generous free tier):** [console.groq.com/keys](https://console.groq.com/keys)

### What works on Streamlit Cloud

| Feature | Works? | Notes |
|---|---|---|
| Code Review (full 6-node pipeline) | ✅ | |
| Security Scanner (regex + LLM) | ✅ | |
| Problem Solver (4 solutions) | ✅ | |
| Image Upload + OCR | ✅ | Tesseract installed via `packages.txt` |
| Provider fallback chain | ✅ | All configured providers available |
| Session history | ✅ | In-memory per session |

### What is different from local full-stack

| | Local Full-Stack | Streamlit Cloud |
|---|---|---|
| FastAPI backend | ✅ Running | ❌ Not needed |
| Proper HTTP status codes | ✅ | N/A (no HTTP API) |
| Request-ID in headers | ✅ | Shows in UI only |
| Docker | ✅ | ❌ Not used |
| API docs (/docs) | ✅ | ❌ No API server |
| Running tests | ✅ `pytest` | ❌ N/A |

For a **portfolio demo**, Streamlit Cloud is perfect. For a **production system**, use the full-stack with Docker.

---

## Option C — Docker (Full Stack, One Command)

```bash

docker-compose up --build
```

- Frontend: http://localhost:8501
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

---

## Troubleshooting

### "No providers available — running in mock mode"
You haven't set any API keys. Add `GOOGLE_API_KEY` to `.env` (local) or Streamlit Cloud secrets.

### "Backend is not reachable" (local)
The FastAPI backend isn't running. Start it with `uvicorn app.main:app --reload` in the `backend/` directory.

### "Could not extract text from image"
- The image is blurry or has very small text — use a clear, high-resolution screenshot
- Tesseract is not installed — follow the prerequisites above

### "ImportError: No module named X" on Streamlit Cloud
Make sure `requirements.txt` at the **root** of the repo is present (not just `backend/requirements.txt`). Streamlit Cloud reads the root-level file.

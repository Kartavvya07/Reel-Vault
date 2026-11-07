# Reel Vault

Reel Vault is an intelligent personal knowledge base system that ingests, transcribes, visually analyzes, and organizes Instagram Reels and posts into an indexed, searchable library.

Installable as a Progressive Web App (PWA) on mobile devices, it registers directly as an Android Share Target so you can share any Reel or Post directly from Instagram into your private vault.

> **Note**: This application is strictly intended for personal use with your own API keys, accounts, and infrastructure.

## Key Features

- **PWA Share Target**: Share Reels or Posts directly from Instagram to your vault on Android.
- **Multimodal AI Analysis**: Uses Google Gemini to extract speech transcripts, on-screen text, tools/repos, key points, and core takeaways from video.
- **Instant Post Saving**: Saves photo and carousel posts (`/p/`, `/tv/`) immediately as manual entries with 1-tap quick notes and category tags (no login wall issues).
- **Hybrid Search**: Combines SQLite FTS5 keyword full-text search with Gemini semantic vector embeddings.
- **Adaptive Extraction**: Gracefully falls back to video frames for silent or audio-free tutorials.
- **Zero Heavy Infrastructure**: Built with FastAPI, SQLite, and vanilla JS/Tailwind. Deploys effortlessly to Railway.

## Environment Variables

Copy `.env.example` to `.env` and set the following variables:

- `VAULT_AUTH_TOKEN`: Secret token/password protecting your vault.
- `GEMINI_API_KEY`: Google Gemini API key from Google AI Studio.
- `ENABLE_REAL_PIPELINE`: `true` to enable Gemini multimodal extraction.
- `ALLOW_MOCK`: `false` in production (prevents mock data generation).
- `ANALYZE_POSTS`: `false` (default) to save posts as manual notes without hitting login walls.
- `ENVIRONMENT`: `development` or `production`.
- `HOST` and `PORT`: Server bind host and port (default `0.0.0.0:8000`).
- `COOKIES_FILE`: (Optional) path to an authenticated Instagram cookies file if analyzing private posts.

## Local Setup & Development

1. **Clone & install dependencies**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

2. **System Dependencies**:
   Ensure `ffmpeg` is installed and available in your system `PATH`.

3. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your GEMINI_API_KEY and VAULT_AUTH_TOKEN
   ```

4. **Run test suite**:
   ```bash
   python -m pytest tests/
   ```

5. **Start server**:
   ```bash
   uvicorn app.spike.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## License

This project is licensed under the [MIT License](LICENSE).

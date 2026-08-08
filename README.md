# Reels2Action: Instagram Reels & Shorts to Actionable Tasks

An automated Telegram bot and asynchronous background worker that ingests short-form video URLs (Reels/Shorts), processes their content, and converts them into structured tasks and action items.

## Architecture
- **Bot Layer (`bot/`)**: Aiogram-based Telegram bot interface for accepting video submission URLs and delivering status updates.
- **Worker Layer (`worker/`)**: ARQ / Redis async task queue executing video downloading, transcript extraction, and LLM structured prompt processing.
- **Containerization**: Fully Dockerized via `docker-compose.yml` for seamless deployment.

## Features
- Asynchronous task queuing using Redis & ARQ.
- Video ingestion and text extraction.
- Structured LLM prompt processing for converting video ideas into actionable briefs.

## Setup
1. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`, `REDIS_HOST`, etc.
2. Launch with Docker Compose:
   ```bash
   docker-compose up --build -d
   ```

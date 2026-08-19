import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Credentials ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

PORT = int(os.getenv("PORT", 8080))

# --- Simple Post Function ---
async def send_test_deal(bot, chat_to_notify):
    caption = (
        "🔥 <b>TEST LOOT DEAL</b> 🔥\n\n"
        "📦 <b>boAt Airdopes 131 PRO True Wireless Earbuds</b>\n\n"
        "🔻 MRP: <s>₹2,990</s>\n"
        "💥 <b>Offer Price: ₹899</b>\n\n"
        "⚡ <i>Testing baseline setup...</i>"
    )
    
    # Simple direct button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy Now / Test Link", url="https://www.amazon.in/dp/B09N3ZNHTY")]
    ])

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=caption,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await bot.send_message(chat_id=chat_to_notify, text="✅ Success! Channel par test deal post ho gayi hai.")
    except Exception as e:
        await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: {e}")

# --- Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! Bot bilkul active hai.\n\nChannel par test post bhejne ke liye `/test` likhein.")

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Channel par message bhej rahe hain...")
    await send_test_deal(context.bot, update.effective_chat.id)

# --- Keep-Alive Health Server for Render ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Live!")
    def log_message(self, format, *args):
        return

def run_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("test", test_cmd))

    print("Bot started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

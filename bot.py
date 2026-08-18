import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

PORT = int(os.getenv("PORT", 8080))

# Simple Flipkart Deals Pool
DEALS = [
    {
        "title": "boAt Airdopes 131 PRO True Wireless Earbuds (45H Playtime)",
        "price": "₹899",
        "mrp": "₹2,990",
        "discount": "69% OFF",
        "url": "https://www.flipkart.com/boat-airdopes-131-pro-tws-earbuds/p/itmca2bb89e02315",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/p/r/z/airdopes-131-pro-boat-original-imagr767zgzhg9hy.jpeg"
    },
    {
        "title": "Boult Audio Z40 True Wireless Earbuds (60H Playtime, Fast Charging)",
        "price": "₹999",
        "mrp": "₹4,999",
        "discount": "80% OFF",
        "url": "https://www.flipkart.com/boult-audio-z40-true-wireless-earbuds/p/itm535df2a1ad96b",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/headphone/m/u/v/-original-imagp8f4k7fggyhy.jpeg"
    },
    {
        "title": "Noise ColorFit Icon 2 1.8'' Bluetooth Calling Smart Watch",
        "price": "₹1,099",
        "mrp": "₹5,999",
        "discount": "81% OFF",
        "url": "https://www.flipkart.com/noise-colorfit-icon-2-1-8-display-bluetooth-calling-smartwatch/p/itm677c7ecda6173",
        "photo": "https://rukminim2.flixcart.com/image/832/832/xif0q/smartwatch/y/j/0/-original-imagkhe74jhz8hga.jpeg"
    }
]

deal_index = 0

async def post_next_deal(bot, chat_to_notify=None):
    global deal_index
    deal = DEALS[deal_index % len(DEALS)]
    deal_index += 1

    caption = (
        f"🛍️ <b>FLIPKART LOOT DEAL ({deal['discount']})</b> 🛍️\n\n"
        f"📦 <b>{deal['title']}</b>\n\n"
        f"🔻 MRP: <s>{deal['mrp']}</s>\n"
        f"💥 <b>Offer Price: {deal['price']}</b>\n\n"
        f"⚡ <i>Limited Stock Offer!</i>"
    )

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy on Flipkart", url=deal["url"])]])

    try:
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=deal["photo"],
            caption=caption,
            reply_markup=btn,
            parse_mode="HTML"
        )
        if chat_to_notify:
            await bot.send_message(chat_id=chat_to_notify, text="✅ Deal channel par post ho gayi!")
    except Exception as e:
        if chat_to_notify:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: {e}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot active hai! Deal post karne ke liye `/postnow` likhein.")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await post_next_deal(context.bot, chat_to_notify=update.effective_chat.id)

async def auto_post(context: ContextTypes.DEFAULT_TYPE):
    await post_next_deal(context.bot)

# Web Server for Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        return

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthHandler).serve_forever(), daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))

    # Har 5 minute mein 1 post
    app.job_queue.run_repeating(auto_post, interval=300, first=10)

    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

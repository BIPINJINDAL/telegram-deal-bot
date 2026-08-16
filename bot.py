import os
import re
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
admin_env = os.getenv("ADMIN_ID", "0").strip()
ADMIN_ID = int(admin_env) if admin_env.isdigit() else 0
PORT = int(os.getenv("PORT", 8080))

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            title TEXT,
            url TEXT UNIQUE,
            target_price REAL,
            last_price REAL
        )
    """)
    conn.commit()
    conn.close()

# --- Amazon Anti-Bot Bypass Scraper ---
def get_amazon_data(url):
    try:
        # Extract ASIN / Clean Link
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', url)
        if asin_match:
            clean_url = f"https://www.amazon.in/dp/{asin_match.group(1)}"
        else:
            clean_url = url.split('?')[0]

        # Use Free Web Reader Gateway to bypass datacenter IP captcha
        gateway_url = f"https://r.jina.ai/{clean_url}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Target-Selector": "body"
        }
        
        res = requests.get(gateway_url, headers=headers, timeout=25)
        text = res.text

        # Extract Title
        title = "Amazon Product"
        title_match = re.search(r'Title:\s*(.+)', text)
        if title_match:
            title = title_match.group(1).strip()
        else:
            first_line = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('http')]
            if first_line:
                title = first_line[0][:80]

        # Extract Price (Regex matches ₹ 19,990 or Rs. 19990)
        price = None
        price_patterns = [
            r'₹\s*([0-9,]+(?:\.[0-9]{2})?)',
            r'INR\s*([0-9,]+(?:\.[0-9]{2})?)',
            r'Rs\.?\s*([0-9,]+(?:\.[0-9]{2})?)'
        ]

        for pattern in price_patterns:
            matches = re.findall(pattern, text)
            for m in matches:
                clean_val = float(m.replace(',', ''))
                if 10 <= clean_val <= 500000:  # Filters valid product prices
                    price = clean_val
                    break
            if price:
                break

        if price:
            return {"title": title, "price": price, "url": clean_url}

    except Exception as e:
        print(f"Scrape Error: {e}")
    return None

def make_link(url, platform):
    if platform == "amazon":
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={AMAZON_TAG}"
    elif platform == "flipkart":
        return f"https://ekaro.in/enkr?url={url}"
    return url

# --- Telegram Commands ---
async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID != 0 and update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.strip().split()
    if len(text) < 4:
        await update.message.reply_text("Format (single line):\n`/track amazon <URL> <TARGET_PRICE>`", parse_mode="Markdown")
        return

    platform = text[1].lower()
    url = text[2]
    try:
        target_price = float(text[3].replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Target price number format me dalein.")
        return

    status_msg = await update.message.reply_text("⏳ Product verify kiya ja raha hai (Anti-bot bypass)...")

    data = get_amazon_data(url)

    if not data or not data["price"]:
        await status_msg.edit_text("❌ Price fetch nahi hui. URL check karein ya thodi der baad try karein.")
        return

    clean_url = data["url"]
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (platform, title, url, target_price, last_price) VALUES (?, ?, ?, ?, ?)",
                  (platform, data["title"], clean_url, target_price, data["price"]))
        conn.commit()
        await status_msg.edit_text(
            f"✅ **Tracking Started!**\n\n"
            f"📦 **Title:** {data['title'][:65]}...\n"
            f"💰 **Current Price:** ₹{data['price']:,.0f}\n"
            f"🎯 **Target Price:** ₹{target_price:,.0f}\n\n"
            f"⚡ Jaise hi price target par aayegi, bot channel me deal post kar dega."
        )
    except sqlite3.IntegrityError:
        await status_msg.edit_text("⚠️ Ye product already tracking list mein hai.")
    finally:
        conn.close()

# --- Monitor Routine ---
async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price in products:
        data = get_amazon_data(url)
        if not data or not data["price"]:
            continue

        curr_price = data["price"]
        if curr_price < last_price or curr_price <= target_price:
            discount = int(((last_price - curr_price) / last_price) * 100) if last_price > curr_price else 0
            buy_link = make_link(url, platform)

            caption = (
                f"🔥 **PRICE DROP ALERT!** 🔥\n\n"
                f"📦 **{title[:80]}**\n\n"
                f"🔻 Purani Price: ~~₹{last_price:,.0f}~~\n"
                f"💥 **Deal Price: ₹{curr_price:,.0f}** {f'({discount}% OFF)' if discount > 0 else ''}\n\n"
                f"⚡ *Limited Time Deal! Grab Now!*"
            )

            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Check Deal", url=buy_link)]])

            try:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, reply_markup=btn, parse_mode="Markdown")
            except Exception as e:
                print(f"Telegram Broadcast Error: {e}")

            c.execute("UPDATE products SET last_price = ? WHERE id = ?", (curr_price, prod_id))
            conn.commit()

    conn.close()

# --- Health Check Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active 24/7!")

    def log_message(self, format, *args):
        return

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

def main():
    init_db()

    web_thread = threading.Thread(target=run_health_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("track", track_cmd))
    app.job_queue.run_repeating(monitor_deals, interval=900, first=10)

    print("Bot is live on Free Web Service...")
    app.run_polling()

if __name__ == "__main__":
    main()

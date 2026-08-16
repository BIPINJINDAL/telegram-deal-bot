import os
import re
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
admin_env = os.getenv("ADMIN_ID", "0").strip()
ADMIN_ID = int(admin_env) if admin_env.isdigit() else 0
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
            last_price REAL,
            image_url TEXT
        )
    """)
    conn.commit()
    conn.close()

def clean_price(text):
    if not text:
        return None
    val = re.sub(r"[^\d.]", "", text.replace(",", ""))
    return float(val) if val else None

def get_amazon_data(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        title_tag = soup.find("span", {"id": "productTitle"})
        title = title_tag.get_text().strip() if title_tag else "Amazon Product"
        price_tag = soup.find("span", {"class": "a-price-whole"})
        price = clean_price(price_tag.get_text()) if price_tag else None
        img_tag = soup.find("img", {"id": "landingImage"})
        img_url = img_tag.get("src") if img_tag else ""
        return {"title": title, "price": price, "image_url": img_url}
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

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Format: `/track amazon <URL> <TARGET_PRICE>`", parse_mode="Markdown")
        return

    platform, url, target_price = args[0].lower(), args[1], float(args[2])
    data = get_amazon_data(url)

    if not data or not data["price"]:
        await update.message.reply_text("❌ Price fetch nahi hui. URL check karein.")
        return

    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (platform, title, url, target_price, last_price, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                  (platform, data["title"], url, target_price, data["price"], data["image_url"]))
        conn.commit()
        await update.message.reply_text(f"✅ **Tracked:** {data['title'][:50]}...\nCurrent: ₹{data['price']} | Target: ₹{target_price}")
    except sqlite3.IntegrityError:
        await update.message.reply_text("⚠️ Product already tracked.")
    finally:
        conn.close()

async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price, image_url FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price, image_url in products:
        data = get_amazon_data(url)
        if not data or not data["price"]:
            continue

        curr_price = data["price"]
        if curr_price < last_price or curr_price <= target_price:
            discount = int(((last_price - curr_price) / last_price) * 100) if last_price > curr_price else 0
            buy_link = make_link(url, platform)

            caption = (
                f"🔥 **PRICE DROP ALERT!** 🔥\n\n"
                f"📦 **{title[:75]}**\n"
                f"🔻 Purani Price: ~~₹{last_price:,.0f}~~\n"
                f"💥 **Deal Price: ₹{curr_price:,.0f}** {f'({discount}% OFF)' if discount > 0 else ''}\n\n"
                f"⚡ *Limited Time Deal!*"
            )

            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Check Deal", url=buy_link)]])

            try:
                if data.get("image_url"):
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=data["image_url"], caption=caption, reply_markup=btn, parse_mode="Markdown")
                else:
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, reply_markup=btn, parse_mode="Markdown")
            except Exception as e:
                print(f"Telegram Post Error: {e}")

            c.execute("UPDATE products SET last_price = ? WHERE id = ?", (curr_price, prod_id))
            conn.commit()

    conn.close()

# --- Render Port Binding (Daemon Thread) ---
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

    # Web Server for Render
    web_thread = threading.Thread(target=run_health_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("track", track_cmd))
    app.job_queue.run_repeating(monitor_deals, interval=900, first=10)

    print("Bot is live on Free Web Service...")
    app.run_polling()

if __name__ == "__main__":
    main()

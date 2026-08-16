import os
import re
import json
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
admin_env = os.getenv("ADMIN_ID", "0").strip()
ADMIN_ID = int(admin_env) if admin_env.isdigit() else 0
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
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
    val = re.sub(r"[^\d.]", "", str(text).replace(",", ""))
    try:
        f = float(val)
        return f if f > 0 else None
    except:
        return None

# --- Flipkart Scraper Engine ---
def get_flipkart_data(url):
    try:
        session = requests.Session()
        res = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        final_url = res.url.split('?')[0]
        soup = BeautifulSoup(res.text, "html.parser")

        title = "Flipkart Deal"
        price = None
        image_url = ""

        # 1. Price Selectors (Flipkart current classes + fallbacks)
        price_tags = [
            soup.find("div", {"class": "Nx9bqj"}),
            soup.find("div", {"class": "_30jeq3"}),
            soup.find("div", {"class": "hl05eU"}),
            soup.find("div", {"class": "_25b18c"})
        ]
        for tag in price_tags:
            if tag:
                p = clean_price(tag.get_text())
                if p and p > 10:
                    price = p
                    break

        # 2. JSON Embedded Fallback if HTML class changed
        if not price:
            matches = re.findall(r'"pricing":\{"finalPrice":\{"value":([0-9.]+)', res.text)
            if not matches:
                matches = re.findall(r'"price":([0-9]+)', res.text)
            if matches:
                price = float(matches[0])

        # 3. Product Title
        title_tags = [
            soup.find("span", {"class": "VU-ZEz"}),
            soup.find("span", {"class": "B_NuCI"}),
            soup.find("h1", {"class": "_6EBuvT"}),
            soup.find("h1")
        ]
        for t in title_tags:
            if t and t.get_text().strip():
                title = t.get_text().strip()
                break

        # 4. Product Image
        img_tags = [
            soup.find("img", {"class": "DByuf4"}),
            soup.find("img", {"class": "_396cs4"}),
            soup.find("img", {"class": "_2r_T1I"})
        ]
        for img in img_tags:
            if img and img.get("src"):
                image_url = img.get("src")
                break

        if price:
            return {
                "title": title,
                "price": price,
                "image_url": image_url,
                "url": final_url,
                "platform": "flipkart"
            }
    except Exception as e:
        print(f"Flipkart Scrape Error: {e}")
    return None

def make_link(url, platform):
    if platform == "flipkart":
        # EarnKaro / Cuelinks affiliate link template
        return f"https://ekaro.in/enkr?url={requests.utils.quote(url)}"
    elif platform == "amazon":
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={AMAZON_TAG}"
    return url

# --- Bot Commands ---
async def handle_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()

    if ADMIN_ID != 0 and update.effective_user.id != ADMIN_ID:
        return

    url_match = re.search(r'(https?://[^\s]+)', msg_text)
    if not url_match:
        if msg_text.startswith('/start'):
            await update.message.reply_text(
                "👋 **Flipkart & Deals Tracker Bot Active!**\n\n"
                "Track karne ke liye Flipkart link aur target price bhejein:\n"
                "`/track https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4 65000`",
                parse_mode="Markdown"
            )
        return

    url = url_match.group(1)
    
    # Extract Target Price
    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', msg_text.replace(url, ''))
    if not numbers:
        await update.message.reply_text("⚠️ Kripya link ke sath **Target Price** bhi dalein (e.g. `65000`).")
        return

    target_price = float(numbers[-1])
    status_msg = await update.message.reply_text("⏳ Flipkart se price verify ho rahi hai...")

    data = get_flipkart_data(url)

    if not data or not data["price"]:
        await status_msg.edit_text("❌ Price fetch nahi ho saki. Valid Flipkart product link dalein.")
        return

    clean_url = data["url"]
    platform = data["platform"]
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (platform, title, url, target_price, last_price, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                  (platform, data["title"], clean_url, target_price, data["price"], data["image_url"]))
        conn.commit()
        await status_msg.edit_text(
            f"✅ **Tracking Started Successfully!**\n\n"
            f"📦 **Product:** {data['title'][:60]}...\n"
            f"💰 **Current Price:** ₹{data['price']:,.0f}\n"
            f"🎯 **Target Price:** ₹{target_price:,.0f}\n\n"
            f"⚡ Price ₹{target_price:,.0f} ya usse kam hote hi bot channel me post kar dega!"
        )
    except sqlite3.IntegrityError:
        await status_msg.edit_text("⚠️ Ye product already tracking list mein hai.")
    finally:
        conn.close()

# --- Monitor Routine ---
async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price, image_url FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price, img_url in products:
        data = get_flipkart_data(url)
        if not data or not data["price"]:
            continue

        curr_price = data["price"]
        if curr_price < last_price or curr_price <= target_price:
            discount = int(((last_price - curr_price) / last_price) * 100) if last_price > curr_price else 0
            buy_link = make_link(url, platform)

            caption = (
                f"🔥 **FLIPKART PRICE DROP ALERT!** 🔥\n\n"
                f"📦 **{title[:80]}**\n\n"
                f"🔻 Purani Price: ~~₹{last_price:,.0f}~~\n"
                f"💥 **Deal Price: ₹{curr_price:,.0f}** {f'({discount}% OFF)' if discount > 0 else ''}\n\n"
                f"⚡ *Limited Stock Deal! Jaldi order karein.*"
            )

            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Check Deal", url=buy_link)]])

            try:
                display_img = data.get("image_url") or img_url
                if display_img:
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=display_img, caption=caption, reply_markup=btn, parse_mode="Markdown")
                else:
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, reply_markup=btn, parse_mode="Markdown")
            except Exception as e:
                print(f"Telegram Broadcast Error: {e}")

            c.execute("UPDATE products SET last_price = ? WHERE id = ?", (curr_price, prod_id))
            conn.commit()

    conn.close()

# --- Health Check Server for Render ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is live 24/7!")

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
    app.add_handler(CommandHandler("track", handle_tracking))
    app.add_handler(CommandHandler("start", handle_tracking))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tracking))
    
    app.job_queue.run_repeating(monitor_deals, interval=900, first=15)

    print("Bot is live...")
    app.run_polling()

if __name__ == "__main__":
    main()

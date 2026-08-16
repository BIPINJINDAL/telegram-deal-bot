import os
import re
import json
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Config ---
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

# --- 100% Working Amazon & Flipkart Scraper ---
def get_product_data(raw_url):
    platform = "flipkart" if "flipkart" in raw_url.lower() else "amazon"
    
    # 1. Clean Amazon ASIN URL
    clean_url = raw_url
    if platform == "amazon":
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', raw_url)
        if asin_match:
            clean_url = f"https://www.amazon.in/dp/{asin_match.group(1)}"
        else:
            clean_url = raw_url.split('?')[0]

    title = "Product Deal"
    price = None
    image_url = ""

    # Method 1: Microlink Real-Browser Gateway (Bypasses Captchas)
    try:
        api_url = f"https://api.microlink.io?url={requests.utils.quote(clean_url)}&headers[user-agent]=Mozilla/5.0"
        res = requests.get(api_url, timeout=20).json()
        
        if res.get("status") == "success" and "data" in res:
            data = res["data"]
            title = data.get("title", title).split(" : Amazon.in")[0].strip()
            
            if "image" in data and data["image"]:
                image_url = data["image"].get("url", "")

            # Extract price from description or publisher fields
            desc = data.get("description", "")
            matches = re.findall(r'(?:₹|Rs\.?|INR)\s*([0-9,]+(?:\.[0-9]{2})?)', desc, re.IGNORECASE)
            if matches:
                p = clean_price(matches[0])
                if p and p > 10:
                    price = p
    except Exception as e:
        print(f"Microlink Error: {e}")

    # Method 2: Fallback via Jina Reader Engine
    if not price:
        try:
            jina_url = f"https://r.jina.ai/{clean_url}"
            headers = {"User-Agent": "Mozilla/5.0", "X-Target-Selector": "body"}
            res = requests.get(jina_url, headers=headers, timeout=15)
            text = res.text

            t_match = re.search(r'Title:\s*(.+)', text)
            if t_match and title == "Product Deal":
                title = t_match.group(1).split(" : Amazon.in")[0].strip()

            matches = re.findall(r'(?:₹|Rs\.?|INR)\s*([0-9,]+(?:\.[0-9]{2})?)', text, re.IGNORECASE)
            for m in matches:
                p = clean_price(m)
                if p and 50 <= p <= 1000000:
                    price = p
                    break
        except Exception as e:
            print(f"Jina Error: {e}")

    if price:
        return {"title": title, "price": price, "image_url": image_url, "url": clean_url, "platform": platform}
    
    return None

def make_link(url, platform):
    if platform == "amazon":
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={AMAZON_TAG}"
    elif platform == "flipkart":
        return f"https://ekaro.in/enkr?url={url}"
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
                "👋 **Deal Tracker Bot Active!**\n\n"
                "Track karne ke liye link aur target price bhejein:\n"
                "`/track https://www.amazon.in/dp/B0FYGBSKFB 70000`",
                parse_mode="Markdown"
            )
        return

    url = url_match.group(1)
    
    # Extract Target Price
    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', msg_text.replace(url, ''))
    if not numbers:
        await update.message.reply_text("⚠️ Kripya link ke sath **Target Price** bhi likhein (e.g. `70000`).")
        return

    target_price = float(numbers[-1])
    status_msg = await update.message.reply_text("⏳ Amazon price verify ho rahi hai...")

    data = get_product_data(url)

    if not data or not data["price"]:
        await status_msg.edit_text("❌ Price fetch nahi ho saki. Product link check karein.")
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
            f"📦 **Product:** {data['title'][:65]}...\n"
            f"💰 **Current Price:** ₹{data['price']:,.0f}\n"
            f"🎯 **Target Price:** ₹{target_price:,.0f}\n\n"
            f"⚡ Jaise hi price ₹{target_price:,.0f} ya usse kam hogi, bot seedha channel mein post kar dega!"
        )
    except sqlite3.IntegrityError:
        await status_msg.edit_text("⚠️ Ye product already tracking list mein active hai.")
    finally:
        conn.close()

# --- Monitor Routine ---
async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price, image_url FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price, img_url in products:
        data = get_product_data(url)
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
                f"⚡ *Limited Stock! Deal grab karein.*"
            )

            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Check Deal", url=buy_link)]])

            try:
                display_img = data.get("image_url") or img_url
                if display_img:
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=display_img, caption=caption, reply_markup=btn, parse_mode="Markdown")
                else:
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, reply_markup=btn, parse_mode="Markdown")
            except Exception as e:
                print(f"Telegram Post Error: {e}")

            c.execute("UPDATE products SET last_price = ? WHERE id = ?", (curr_price, prod_id))
            conn.commit()

    conn.close()

# --- Render Keep-Alive Server ---
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

    print("Bot is fully running on Render...")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import re
import asyncio
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Environment Variables ---
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
    val = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        f = float(val)
        return f if f > 0 else None
    except:
        return None

# --- Multi-Method Anti-Bot Amazon Scraper ---
def get_amazon_data(raw_url):
    # 1. Extract ASIN
    asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', raw_url)
    asin = asin_match.group(1) if asin_match else None
    clean_url = f"https://www.amazon.in/dp/{asin}" if asin else raw_url.split('?')[0]

    title = "Amazon Product"
    price = None
    image_url = ""

    # Method 1: Direct Mobile Emulation (Bypasses most desktop bot-blocks)
    try:
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        res = requests.get(clean_url, headers=mobile_headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Title
            t_elem = soup.find("span", {"id": "title"}) or soup.find("span", {"id": "productTitle"}) or soup.find("h1")
            if t_elem:
                title = t_elem.get_text().strip()
                
            # Price
            for sel in [".apexPriceToPay .a-offscreen", ".a-price .a-offscreen", ".a-price-whole", "#corePrice_feature_div"]:
                tag = soup.select_one(sel)
                if tag:
                    parsed = clean_price(tag.get_text())
                    if parsed:
                        price = parsed
                        break

            # Image
            img = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"class": "a-dynamic-image"})
            if img:
                image_url = img.get("src", "")
                
            if price:
                return {"title": title, "price": price, "image_url": image_url, "url": clean_url}
    except Exception as e:
        print(f"Direct Scrape Method Failed: {e}")

    # Method 2: Free Cloud Proxy Fallback
    try:
        proxy_url = f"https://r.jina.ai/{clean_url}"
        res = requests.get(proxy_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        text = res.text

        # Extract Title from proxy text
        t_match = re.search(r'Title:\s*(.+)', text)
        if t_match:
            title = t_match.group(1).strip()

        # Extract Price from text
        matches = re.findall(r'(?:₹|Rs\.?|INR)\s*([0-9,]+(?:\.[0-9]{2})?)', text, re.IGNORECASE)
        for m in matches:
            val = float(m.replace(',', ''))
            if 20 <= val <= 1000000:
                price = val
                break

        if price:
            return {"title": title, "price": price, "image_url": image_url, "url": clean_url}
    except Exception as e:
        print(f"Proxy Method Failed: {e}")

    return None

def make_link(url, platform):
    if platform == "amazon":
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={AMAZON_TAG}"
    elif platform == "flipkart":
        return f"https://ekaro.in/enkr?url={url}"
    return url

# --- Bot Message & Command Handler ---
async def handle_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    
    # Check Admin permission
    if ADMIN_ID != 0 and update.effective_user.id != ADMIN_ID:
        return

    # Extract URL from anywhere in the message
    url_match = re.search(r'(https?://[^\s]+)', msg_text)
    if not url_match:
        if msg_text.startswith('/start'):
            await update.message.reply_text(
                "👋 **Deal Tracker Bot Active!**\n\n"
                "Kisi bhi product ko track karne ke liye link aur target price bhejein:\n"
                "`/track amazon https://www.amazon.in/dp/B0863TXGM3 25000`",
                parse_mode="Markdown"
            )
        return

    url = url_match.group(1)
    
    # Extract Platform
    platform = "amazon" if "amazon" in url.lower() else ("flipkart" if "flipkart" in url.lower() else "amazon")

    # Extract Target Price (last number in the message)
    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', msg_text.replace(url, ''))
    if not numbers:
        await update.message.reply_text("⚠️ Kripya link ke sath **Target Price** bhi likhein.\nExample:\n`/track amazon <URL> 25000`")
        return
    
    target_price = float(numbers[-1])

    status_msg = await update.message.reply_text("⏳ Product verify ho raha hai...")

    data = get_amazon_data(url)

    if not data or not data["price"]:
        await status_msg.edit_text("❌ Price fetch nahi ho saki. Kripya check karein ki product stock mein hai ya nahi.")
        return

    clean_url = data["url"]
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (platform, title, url, target_price, last_price, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                  (platform, data["title"], clean_url, target_price, data["price"], data["image_url"]))
        conn.commit()
        await status_msg.edit_text(
            f"✅ **Tracking Started!**\n\n"
            f"📦 **Title:** {data['title'][:65]}...\n"
            f"💰 **Current Price:** ₹{data['price']:,.0f}\n"
            f"🎯 **Target Price:** ₹{target_price:,.0f}\n\n"
            f"⚡ Price target par aate hi bot channel mein post kar dega!"
        )
    except sqlite3.IntegrityError:
        await status_msg.edit_text("⚠️ Ye product pehle se tracking database mein hai.")
    finally:
        conn.close()

# --- Auto Price Monitor ---
async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price, image_url FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price, img_url in products:
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
                f"⚡ *Limited Time Deal! Jaldi order karein.*"
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
    
    # Handlers for both /track command and direct link messages
    app.add_handler(CommandHandler("track", handle_tracking))
    app.add_handler(CommandHandler("start", handle_tracking))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tracking))
    
    app.job_queue.run_repeating(monitor_deals, interval=900, first=15)

    print("Bot is fully running on Render...")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import re
import json
import asyncio
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

# --- Multi-Source Amazon & Flipkart Scraper ---
def fetch_product_data(raw_url):
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

    # Proxy gateways to bypass Render Datacenter IP block
    gateways = [
        {"url": f"https://api.allorigins.win/raw?url={requests.utils.quote(clean_url)}", "type": "html"},
        {"url": f"https://r.jina.ai/{clean_url}", "type": "text"},
        {"url": clean_url, "type": "direct"}
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    for gw in gateways:
        try:
            res = requests.get(gw["url"], headers=headers, timeout=12)
            if res.status_code != 200:
                continue

            content = res.text

            # Method A: Extract from LD+JSON Schema (Highest accuracy)
            soup = BeautifulSoup(content, "html.parser")
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    if isinstance(data, list):
                        data = data[0]
                    if "name" in data:
                        title = data.get("name", title)
                    if "image" in data:
                        image_url = data["image"] if isinstance(data["image"], str) else data["image"][0]
                    if "offers" in data:
                        offers = data["offers"]
                        if isinstance(offers, list):
                            offers = offers[0]
                        p = offers.get("price") or offers.get("lowPrice")
                        parsed = clean_price(p)
                        if parsed:
                            price = parsed
                            break
                except:
                    continue

            if price:
                return {"title": title, "price": price, "image_url": image_url, "url": clean_url, "platform": platform}

            # Method B: Regex extraction from Embedded JS / Data tags
            embedded_price_matches = re.findall(r'(?:"priceAmount"|"price"|"buyingPrice"|"offerPrice"|priceblock_ourprice|priceblock_dealprice)[\s:=]+["\']?([0-9,]+(?:\.[0-9]{2})?)', content)
            for m in embedded_price_matches:
                p = clean_price(m)
                if p and 20 <= p <= 2000000:
                    price = p
                    break

            # Method C: Standard HTML Tag Fallbacks
            if not price:
                tags = [
                    soup.find("span", {"class": "a-price-whole"}),
                    soup.find("div", {"class": "Nx9bqj"}),
                    soup.find("div", {"class": "_30jeq3"})
                ]
                for t in tags:
                    if t:
                        p = clean_price(t.get_text())
                        if p:
                            price = p
                            break

            # Title & Image Fallback
            t_tag = soup.find("span", {"id": "productTitle"}) or soup.find("h1")
            if t_tag:
                title = t_tag.get_text().strip()

            img_tag = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"class": "DByuf4"})
            if img_tag and not image_url:
                image_url = img_tag.get("src", "")

            if price:
                return {"title": title, "price": price, "image_url": image_url, "url": clean_url, "platform": platform}

        except Exception as e:
            print(f"Gateway failed: {e}")
            continue

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
                "`/track https://www.amazon.in/dp/B0863TXGM3 25000`",
                parse_mode="Markdown"
            )
        return

    url = url_match.group(1)
    
    # Extract Target Price
    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', msg_text.replace(url, ''))
    if not numbers:
        await update.message.reply_text("⚠️ Kripya link ke sath **Target Price** bhi likhein (e.g. `25000`).")
        return

    target_price = float(numbers[-1])
    status_msg = await update.message.reply_text("⏳ Product verify kiya ja raha hai...")

    data = fetch_product_data(url)

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
            f"✅ **Tracking Started!**\n\n"
            f"📦 **Title:** {data['title'][:65]}...\n"
            f"💰 **Current Price:** ₹{data['price']:,.0f}\n"
            f"🎯 **Target Price:** ₹{target_price:,.0f}\n\n"
            f"⚡ Jaise hi price drop hogi, channel mein alert post ho jayega!"
        )
    except sqlite3.IntegrityError:
        await status_msg.edit_text("⚠️ Ye product pehle se tracking database mein hai.")
    finally:
        conn.close()

# --- Monitor Routine ---
async def monitor_deals(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id, platform, title, url, target_price, last_price, image_url FROM products")
    products = c.fetchall()

    for prod_id, platform, title, url, target_price, last_price, img_url in products:
        data = fetch_product_data(url)
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

# --- Render Port Binding ---
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

    print("Bot is live on Free Web Service...")
    app.run_polling()

if __name__ == "__main__":
    main()

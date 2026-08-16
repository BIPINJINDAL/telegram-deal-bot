import os
import re
import json
import sqlite3
import threading
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# --- Database (Duplicate Deals Filter) ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE,
            title TEXT,
            url TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (deal_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id, title, url):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO posted_deals (deal_id, title, url) VALUES (?, ?, ?)", (deal_id, title, url))
        conn.commit()
    except:
        pass
    finally:
        conn.close()

# --- Affiliate Link Converter ---
def convert_to_affiliate(original_url):
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url or "fkrt.it" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Live Auto-Deal Feeds Fetcher ---
def fetch_trending_loot_deals():
    deals = []
    
    # Source 1: Live Deals XML Stream
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=12)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:15]:
                deal_url = item.find('link').text.strip() if item.find('link') is not None else ""
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                guid = item.find('guid').text if item.find('guid') is not None else deal_url

                # Extract product image from description
                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""

                # Extract Direct Store Links (Amazon / Flipkart)
                store_link_tag = soup.find("a", href=True)
                target_url = store_link_tag['href'] if store_link_tag else deal_url

                # Price / Discount Detection
                discount_match = re.search(r'([0-9]{2}%|₹\s*[0-9,]+)', title)
                badge = discount_match.group(1) if discount_match else "HOT DEAL"

                if target_url and title:
                    deals.append({
                        "id": guid,
                        "title": title,
                        "url": target_url,
                        "image_url": img_url,
                        "badge": badge
                    })
    except Exception as e:
        print(f"Feed 1 Fetch Error: {e}")

    return deals

# --- Background Auto-Poster Routine ---
async def auto_post_loot_deals(context: ContextTypes.DEFAULT_TYPE):
    deals = fetch_trending_loot_deals()
    
    for deal in deals:
        if is_already_posted(deal["id"]):
            continue

        affiliate_link = convert_to_affiliate(deal["url"])

        caption = (
            f"🔥 **SUPER LOOT / PRICE DROP DEAL** 🔥\n\n"
            f"📦 **{deal['title']}**\n\n"
            f"⚡ **Discount:** `{deal['badge']}`\n"
            f"🚨 *Limited Quantity! Jaldi order karein before price goes up.*"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=affiliate_link)]
        ])

        try:
            if deal.get("image_url") and deal["image_url"].startswith("http"):
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image_url"],
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

            # Mark deal as posted so it never duplicates
            mark_as_posted(deal["id"], deal["title"], deal["url"])

            # Small delay between posts so Telegram rate-limit is avoided
            await asyncio.sleep(5)

        except Exception as e:
            print(f"Telegram Auto-Post Error: {e}")

# --- Render Port Binding (Keep-Alive) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auto Loot Deals Bot is running 24/7!")

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

    # Har 10 minute me auto fetch karke nayi deals channel me post karega
    app.job_queue.run_repeating(auto_post_loot_deals, interval=600, first=5)

    print("Auto Deals Engine Active...")
    app.run_polling()

if __name__ == "__main__":
    main()

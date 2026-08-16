import os
import re
import asyncio
import sqlite3
import threading
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
raw_channel = os.getenv("CHANNEL_ID", "-1003958458010").strip()

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    CHANNEL_ID = raw_channel

AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

# --- Database ---
def init_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()

def is_already_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("SELECT id FROM posted_deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_as_posted(deal_id):
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id) VALUES (?)", (str(deal_id),))
        conn.commit()
    except:
        pass
    finally:
        conn.close()

def clear_db():
    conn = sqlite3.connect("deals.db")
    c = conn.cursor()
    c.execute("DELETE FROM posted_deals")
    conn.commit()
    conn.close()

# --- Affiliate Generator ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"
    if EARNKARO_ID:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}&r={EARNKARO_ID}"
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- Multi-Source Live Scraper ---
def fetch_live_deals():
    deals = []
    
    # 3 High-Speed Active India Loot Feeds
    feed_sources = [
        "https://indiafreestuff.in/feed",
        "https://www.offernloot.com/feed",
        "https://dealhunt.in/feed"
    ]

    for feed_url in feed_sources:
        try:
            res = requests.get(feed_url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for item in root.findall('.//item')[:8]:
                    title = item.find('title').text.strip() if item.find('title') is not None else ""
                    link = item.find('link').text.strip() if item.find('link') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    
                    # Extract image from description or enclosure
                    soup = BeautifulSoup(desc, "html.parser")
                    img_tag = soup.find("img")
                    img_url = img_tag.get("src") if img_tag else ""

                    enclosure = item.find('enclosure')
                    if not img_url and enclosure is not None:
                        img_url = enclosure.get('url', '')

                    # Extract Direct Merchant Link inside post if present
                    store_link = None
                    for a in soup.find_all("a", href=True):
                        href = a['href']
                        if any(dom in href for dom in ["amazon.in", "amzn.to", "flipkart.com", "meesho.com", "myntra.com", "ajio.com"]):
                            store_link = href
                            break

                    target_url = store_link if store_link else link

                    if title and target_url:
                        deals.append({
                            "id": link,
                            "title": title,
                            "url": target_url,
                            "image": img_url
                        })
        except Exception as e:
            print(f"Feed error ({feed_url}): {e}")

    return deals

# --- Posting Engine ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = fetch_live_deals()
    posted_count = 0
    err_message = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])
        caption = (
            f"🔥 **SUPER LOOT DEAL** 🔥\n\n"
            f"📦 **{clean_title}**\n\n"
            f"⚡ *Limited Period Price Drop!*"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            if deal.get("image") and deal["image"].startswith("http"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image"],
                    caption=caption,
                    reply_markup=btn,
                    parse_mode="Markdown"
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    reply_markup=btn,
                    parse_mode="Markdown"
                )
            mark_as_posted(deal["id"])
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 5:
                break
        except Exception as e:
            err_message = str(e)
            print(f"Direct Post Error: {e}")
            break

    if chat_to_notify:
        if err_message:
            await bot.send_message(
                chat_id=chat_to_notify,
                text=f"❌ **Channel Post Error:**\n`{err_message}`",
                parse_mode="Markdown"
            )
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Bilkul nayi live deals post ho chuki hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari trending deals already up-to-date hain. Nayi deal aate hi auto-post ho jayegi.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Loot Deals Engine Active!**\n\n• `/postnow` - Instant 5 fresh deals post karein\n• `/reset` - Deal cache clear karein")

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Internet se bilkul fresh live deals fetch ho rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Cache reset complete! Ab `/postnow` karein.")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Live 24/7!")
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
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    # Background runner every 5 mins
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Bot is polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

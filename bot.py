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
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bipin_loot_deals").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "dealstracker-21").strip()
EARNKARO_ID = os.getenv("EARNKARO_ID", "").strip()
PORT = int(os.getenv("PORT", 8080))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# --- Database ---
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
        c.execute("INSERT OR REPLACE INTO posted_deals (deal_id, title, url) VALUES (?, ?, ?)", (deal_id, title, url))
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

# --- Affiliate Link Generator ---
def convert_to_affiliate(original_url):
    if not original_url:
        return "https://www.amazon.in"

    # EarnKaro Link Conversion
    if EARNKARO_ID:
        encoded_url = requests.utils.quote(original_url)
        return f"https://ekaro.in/enkr?url={encoded_url}&r={EARNKARO_ID}"

    # Fallback to Direct Amazon / Flipkart
    if "amazon.in" in original_url or "amzn.to" in original_url:
        sep = "&" if "?" in original_url else "?"
        return f"{original_url}{sep}tag={AMAZON_TAG}"
    elif "flipkart.com" in original_url:
        return f"https://ekaro.in/enkr?url={requests.utils.quote(original_url)}"
    return original_url

# --- 100% Dynamic Real-Time Deals Scraper ---
def fetch_live_web_deals():
    deals = []
    
    # Source 1: India Deals Feed Stream
    try:
        res = requests.get("https://www.desidime.com/feed", headers=HEADERS, timeout=10)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item')[:10]:
                title = item.find('title').text.strip() if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                link = item.find('link').text.strip() if item.find('link') is not None else ""
                guid = item.find('guid').text if item.find('guid') is not None else link

                soup = BeautifulSoup(desc, "html.parser")
                img_tag = soup.find("img")
                img_url = img_tag.get("src") if img_tag else ""

                store_tag = soup.find("a", href=True)
                target_url = store_tag['href'] if store_tag else link

                # Extract discount/price
                price_match = re.search(r'(?:Rs\.?|₹)\s*([0-9,]+)', title)
                price = f"₹{price_match.group(1)}" if price_match else "Best Offer"

                if title and target_url:
                    deals.append({
                        "id": guid,
                        "title": title,
                        "price": price,
                        "url": target_url,
                        "image_url": img_url
                    })
    except Exception as e:
        print(f"Stream 1 fetch error: {e}")

    # Source 2: Public Deals Hub Scraper (Backup Live Feed)
    if len(deals) < 3:
        try:
            res2 = requests.get("https://dealsheaven.com/", headers=HEADERS, timeout=10)
            if res2.status_code == 200:
                soup = BeautifulSoup(res2.text, "html.parser")
                for card in soup.select(".deal-detail, .deallogo")[:8]:
                    title_elem = card.find("h3") or card.find("a")
                    img_elem = card.find("img")
                    link_elem = card.find("a", href=True)
                    price_elem = card.find("span", class_="deal-price")

                    if title_elem and link_elem:
                        t = title_elem.get_text().strip()
                        u = link_elem["href"]
                        img = img_elem.get("src") if img_elem else ""
                        p = price_elem.get_text().strip() if price_elem else "Hot Deal"

                        deals.append({
                            "id": f"dh_{hash(t)}",
                            "title": t,
                            "price": p,
                            "url": u if u.startswith("http") else f"https://dealsheaven.com{u}",
                            "image_url": img
                        })
        except Exception as e:
            print(f"Stream 2 fetch error: {e}")

    return deals

# --- Posting Function ---
async def post_deals_to_channel(bot, force=False, chat_to_notify=None):
    deals = fetch_live_web_deals()
    posted_count = 0
    err_log = None

    for deal in deals:
        if not force and is_already_posted(deal["id"]):
            continue

        aff_link = convert_to_affiliate(deal["url"])
        clean_title = re.sub(r'[*_`\[\]]', '', deal['title'])

        caption = (
            f"🔥 **SUPER LOOT / PRICE DROP DEAL** 🔥\n\n"
            f"📦 **{clean_title}**\n\n"
            f"💥 **Price / Offer:** `{deal['price']}`\n\n"
            f"⚡ *Limited Stock! Jaldi Grab Karein!*"
        )
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy Now / Loot Deal", url=aff_link)]])

        try:
            if deal.get("image_url") and deal["image_url"].startswith("http"):
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=deal["image_url"],
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

            mark_as_posted(deal["id"], deal["title"], deal["url"])
            posted_count += 1
            await asyncio.sleep(2)

            if force and posted_count >= 5:
                break
        except Exception as e:
            err_log = str(e)
            print(f"Telegram Posting Error: {e}")
            break

    if chat_to_notify:
        if err_log:
            await bot.send_message(chat_id=chat_to_notify, text=f"❌ Error: `{err_log}`", parse_mode="Markdown")
        elif posted_count > 0:
            await bot.send_message(chat_id=chat_to_notify, text=f"✅ {posted_count} Fresh Live Deals channel mein post ho chuki hain!")
        else:
            await bot.send_message(chat_id=chat_to_notify, text="ℹ️ Saari latest deals already posted hain. Nayi deal aate hi auto post ho jayegi.")

# --- Background Scheduled Task ---
async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await post_deals_to_channel(context.bot, force=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Auto Deals Engine Live Hai!**\n\n"
        "Commands:\n"
        "• `/postnow` - Live fresh deals turant channel par bhejein\n"
        "• `/reset` - Deal cache clear karein",
        parse_mode="Markdown"
    )

async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Live internet se fresh deals fetch karke channel par post ki ja rahi hain...")
    await post_deals_to_channel(context.bot, force=True, chat_to_notify=update.effective_chat.id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_db()
    await update.message.reply_text("🧹 Cache reset done! Ab `/postnow` karein.")

# --- Keep-Alive Health Server ---
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
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    # Background runner every 5 minutes
    app.job_queue.run_repeating(auto_job, interval=300, first=5)

    print("Dynamic Deals Engine Running...")
    app.run_polling()

if __name__ == "__main__":
    main()

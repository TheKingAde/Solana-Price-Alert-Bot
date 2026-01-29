
import requests
import time
from rich.console import Console
from rich.table import Table
from datetime import datetime
import sqlite3
import json
from datetime import datetime

first_run = True
alert_count = 0
console = Console()

# Minimal log capture for print messages and errors
import builtins
from collections import deque
import traceback

log_messages = deque(maxlen=100)

def log_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    log_messages.append(msg)

builtins.print = log_print

# Telegram bot token and chat id (set your values here)
TELEGRAM_BOT_TOKEN = '8249556434:AAHAfPZWQpk7BHPx_olnG33H0VlBDXxdhKs'
TELEGRAM_CHAT_ID = '6126141848'

def send_telegram_alert(message):
    global alert_count
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            alert_count += 1
            print("Telegram alert sent.")
        else:
            print(f"Failed to send Telegram alert: {response.text}")
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")

def get_all_token_addresses(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT address FROM tokens')
    addresses = [row[0] for row in cursor.fetchall()]
    return addresses

def fetch_token_data_batches(conn):
    """
    Fetches all token addresses from the database every 5 minutes,
    splits them into batches of 30, and sends requests to the dexscreener API for each batch.
    """

    # Filter addresses by last_updated > 5 minutes ago
    cursor = conn.cursor()
    addresses = get_all_token_addresses(conn)
    filtered_addresses = []
    for addr in addresses:
        cursor.execute('SELECT last_updated FROM tokens WHERE address = ?', (addr,))
        result = cursor.fetchone()
        if result and result[0]:
            try:
                last_updated_dt = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
                if (datetime.now() - last_updated_dt).total_seconds() > 300:
                    filtered_addresses.append(addr)
            except Exception as e:
                print(f"Error parsing last_updated for {addr}: {e}")
        else:
            # If no last_updated, include by default
            filtered_addresses.append(addr)

    batch_size = 30
    batches = [filtered_addresses[i:i+batch_size] for i in range(0, len(filtered_addresses), batch_size)]
    for batch in batches:
        token_addresses_str = ','.join(batch)
        url2 = f"https://api.dexscreener.com/tokens/v1/solana/{token_addresses_str}"
        try:
            response = requests.get(url2)
            response.raise_for_status()
            data = response.json()
            # For each token, if market cap > 100000, delete from DB
            cursor = conn.cursor()
            for item in data:
                # Check if pairCreatedAt is less than 1 hour old, skip if so
                pair_created_at_raw = item.get('pairCreatedAt')
                if pair_created_at_raw:
                    pair_created_at = datetime.fromtimestamp(pair_created_at_raw / 1000)
                    now = datetime.now()
                    if (now - pair_created_at).total_seconds() < 3600:
                        continue

                addr = item['baseToken']['address']
                alerted_conn = sqlite3.connect('alerted_tokens.db')
                alerted_cursor = alerted_conn.cursor()
                alerted_cursor.execute('''CREATE TABLE IF NOT EXISTS tokens (
                    address TEXT PRIMARY KEY,
                    name TEXT,
                    market_cap REAL,
                    url TEXT,
                    pair_created_at INTEGER,
                    price_change TEXT,
                    price_usd TEXT,
                    last_updated TEXT,
                    description TEXT,
                    links TEXT
                )''')
                alerted_conn.commit()
                alerted_cursor.execute('SELECT 1 FROM tokens WHERE address = ?', (addr,))
                already_alerted = alerted_cursor.fetchone()
                alerted_conn.close()
                if already_alerted:
                    continue
                
                mc = item.get('marketCap')
                if mc is not None and mc > 100000:
                    cursor.execute('DELETE FROM tokens WHERE address = ?', (addr,))
                    print(f"Deleted token {addr} from DB due to market cap {mc}")
                    continue

                # Check price change m5 > 25 or h1 > 25, move to alerted_tokens.db if so
                price_change = item.get('priceChange', {})
                m5 = price_change.get('m5')
                h1 = price_change.get('h1')
                trigger_alert = False

                if m5 is not None:
                    try:
                        if float(m5) > 25:
                            trigger_alert = True
                    except Exception:
                        pass
                elif h1 is not None:
                    try:
                        if float(h1) > 25:
                            trigger_alert = True
                    except Exception:
                        pass

                if trigger_alert:
                    # Send alert to telegram with name, address, and url
                    name = item['baseToken'].get('name', 'Unknown')
                    url = item.get('url', '')

                    alert_msg = (
                        f"🚨 <b>Name: {name}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 <b>Address:</b>\n"
                        f"<code>{addr}</code>\n\n"
                        f"🔗 <b>DexScreener:</b>\n"
                        f"<a href='{url}'>View Chart</a>"
                    )

                    # ---- description ----
                    description = cursor.execute(
                        'SELECT description FROM tokens WHERE address = ?', 
                        (addr,)
                    ).fetchone()

                    if description and description[0]:
                        alert_msg += f"\n\n📝 <b>Description</b>\n{description[0]}"
                    else:
                        alert_msg += f"\n\n📝 <b>Description</b>\n<i>Description is not available</i>"

                    # ---- links ----
                    website_link = None
                    twitter_link = None

                    links_row = cursor.execute(
                        'SELECT links FROM tokens WHERE address = ?', 
                        (addr,)
                    ).fetchone()

                    if links_row and links_row[0]:
                        try:
                            links = json.loads(links_row[0])
                            for link in links:
                                if not website_link and link.get('label') == 'Website':
                                    website_link = link.get('url')
                                if not twitter_link and link.get('type') == 'twitter':
                                    twitter_link = link.get('url')
                        except json.JSONDecodeError:
                            pass

                    # ---- Jupiter ----
                    jupiter_url = f"https://jup.ag/tokens/{addr}"

                    # ---- append links ----
                    alert_msg += "\n\n🔗 <b>Links</b>"
                    alert_msg += (
                        f"\n🌐 <a href='{website_link}'>Website</a>"
                        if website_link else
                        "\n🌐 <i>Website is not available</i>"
                    )

                    alert_msg += (
                        f"\n🐦 <a href='{twitter_link}'>X</a>"
                        if twitter_link else
                        "\n🐦 <i>X link is not available</i>"
                    )

                    alert_msg += f"\n🪐 <a href='{jupiter_url}'>Jupiter</a>"
                    send_telegram_alert(alert_msg)

                    # Fetch the full row from tokens
                    cursor.execute('SELECT * FROM tokens WHERE address = ?', (addr,))
                    row = cursor.fetchone()
                    if row:
                        # Open alerted_tokens.db and ensure schema
                        alerted_conn = sqlite3.connect('alerted_tokens.db')
                        alerted_cursor = alerted_conn.cursor()
                        alerted_cursor.execute('''CREATE TABLE IF NOT EXISTS tokens (
                            address TEXT PRIMARY KEY,
                            name TEXT,
                            market_cap REAL,
                            url TEXT,
                            pair_created_at INTEGER,
                            price_change TEXT,
                            price_usd TEXT,
                            last_updated TEXT,
                            description TEXT,
                            links TEXT
                        )''')
                        alerted_conn.commit()
                        # Insert into alerted_tokens.db
                        alerted_cursor.execute('INSERT OR IGNORE INTO tokens (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated, description, links) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', row)
                        alerted_conn.commit()
                        alerted_conn.close()
                        # Remove from main tokens db
                        cursor.execute('DELETE FROM tokens WHERE address = ?', (addr,))
            conn.commit()
        except requests.RequestException as e:
            print(f"Error fetching batch: {batch}\n{e}")

def fetch_and_display_tokens(conn):
    global first_run
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Filter for Solana tokens ending with specified suffixes
        suffixes = ('bonk', 'BAGS', 'pump')
        filtered_tokens = [
            token for token in data
            if token.get('chainId') == 'solana' and token.get('tokenAddress', '').endswith(suffixes)
        ]
        
        # Build profile lookup by token address
        profile_map = {
            token.get("tokenAddress"): {
                "description": token.get("description"),
                "links": json.dumps(token.get("links")) if token.get("links") else None
            }
            for token in data
        }

        # Collect token addresses
        addresses = [token['tokenAddress'] for token in filtered_tokens]
        new_tokens = []
        if addresses:
            # Fetch detailed data
            token_addresses_str = ','.join(addresses)
            url2 = f"https://api.dexscreener.com/tokens/v1/solana/{token_addresses_str}"
            response2 = requests.get(url2)
            response2.raise_for_status()
            data2 = response2.json()
            
            # Process and save to DB
            cursor = conn.cursor()
            # Get existing addresses before insert
            cursor.execute('SELECT address FROM tokens')
            # existing_addresses = set(row[0] for row in cursor.fetchall())
            
            for item in data2:
                mc = item.get('marketCap')
                if mc is not None and mc <= 100000:
                    addr = item['baseToken']['address']
                    # Check if token address is already in alerted_tokens.db and if it is, skip
                    alerted_conn = sqlite3.connect('alerted_tokens.db')
                    alerted_cursor = alerted_conn.cursor()
                    alerted_cursor.execute('''CREATE TABLE IF NOT EXISTS tokens (
                        address TEXT PRIMARY KEY,
                        name TEXT,
                        market_cap REAL,
                        url TEXT,
                        pair_created_at INTEGER,
                        price_change TEXT,
                        price_usd TEXT,
                        last_updated TEXT,
                        description TEXT,
                        links TEXT
                    )''')
                    alerted_conn.commit()
                    alerted_cursor.execute('SELECT 1 FROM tokens WHERE address = ?', (addr,))
                    already_alerted = alerted_cursor.fetchone()
                    alerted_conn.close()
                    if already_alerted:
                        continue

                    name = item['baseToken'].get('name', '')
                    # Pull description & links from profile API
                    profile = profile_map.get(addr, {})
                    description = profile.get("description")
                    links = profile.get("links")
                    url_db = item['url']
                    pair_created_at_raw = item.get('pairCreatedAt')
                    if pair_created_at_raw:
                        # Convert ms to datetime string
                        pair_created_at = datetime.fromtimestamp(pair_created_at_raw / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        pair_created_at = None

                    price_change = item.get('priceChange')
                    price_change_str = json.dumps(price_change) if price_change is not None else None
                    price_usd = item.get('priceUsd')
                    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cursor.execute(
                        '''INSERT OR IGNORE INTO tokens 
                        (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated, description, links) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (
                            addr,
                            name,
                            mc,
                            url_db,
                            pair_created_at,
                            price_change_str,
                            price_usd,
                            last_updated,
                            description,
                            links
                        )
                    )
            conn.commit()
    except requests.RequestException as e:
        print(f"Error fetching data: {e}")

if __name__ == "__main__":
    # Connect to SQLite DB
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS tokens (
        address TEXT PRIMARY KEY,
        name TEXT,
        market_cap REAL,
        url TEXT,
        pair_created_at INTEGER,
        price_change TEXT,
        price_usd TEXT,
        last_updated TEXT,
        description TEXT,
        links TEXT
    )''')
    conn.commit()
    
    # Run in a loop for live updates
    try:
        while True:
            fetch_and_display_tokens(conn)
            fetch_token_data_batches(conn)
            # Display live table with alert count
            table = Table(title="Solana Price Alert Telegram Bot")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="magenta")
            table.add_row("Telegram Alerts Sent", str(alert_count))

            # Live table for logs/errors
            log_table = Table(title="Bot Logs")
            log_table.add_column("Message", style="yellow")
            for msg in list(log_messages):
                log_table.add_row(msg)

            console.clear()
            console.print(table)
            console.print(log_table)
            time.sleep(5)  # Update every minute
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        conn.close()

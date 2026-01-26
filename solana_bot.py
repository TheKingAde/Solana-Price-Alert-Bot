
import requests
import time
from rich.console import Console
from rich.table import Table
from datetime import datetime
import sqlite3
import json
import asyncio
from datetime import datetime

first_run = True
alert_count = 0
console = Console()

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

async def fetch_token_data_batches(conn):
    """
    Asynchronously fetches all token addresses from the database every 5 minutes,
    splits them into batches of 30, and sends requests to the dexscreener API for each batch.
    """
    while True:
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
                        last_updated TEXT
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
                            if abs(float(m5)) > 25:
                                trigger_alert = True
                        except Exception:
                            pass
                    elif h1 is not None:
                        try:
                            if abs(float(h1)) > 25:
                                trigger_alert = True
                        except Exception:
                            pass

                    if trigger_alert:
                        # Send alert to telegram with name, address, and url
                        name = item['baseToken'].get('name', 'Unknown')
                        url = item.get('url', '')
                        alert_msg = f"<b>{name}</b>\nAddress: <code>{addr}</code>\nURL: {url}"
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
                                last_updated TEXT
                            )''')
                            alerted_conn.commit()
                            # Insert into alerted_tokens.db
                            alerted_cursor.execute('INSERT OR IGNORE INTO tokens (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', row)
                            alerted_conn.commit()
                            alerted_conn.close()
                            # Remove from main tokens db
                            cursor.execute('DELETE FROM tokens WHERE address = ?', (addr,))
                            print(f"Moved token {addr} to alerted_tokens.db due to price change alert")
                conn.commit()
                print(f"Fetched and checked data for batch: {batch}")
            except requests.RequestException as e:
                print(f"Error fetching batch: {batch}\n{e}")
        print("Waiting 5 minutes before next batch fetch...")
        await asyncio.sleep(300)

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
                        last_updated TEXT
                    )''')
                    alerted_conn.commit()
                    alerted_cursor.execute('SELECT 1 FROM tokens WHERE address = ?', (addr,))
                    already_alerted = alerted_cursor.fetchone()
                    alerted_conn.close()
                    if already_alerted:
                        continue

                    name = item['baseToken'].get('name', '')
                    url_db = item['url']
                    pair_created_at_raw = item.get('pairCreatedAt')
                    if pair_created_at_raw:
                        # Convert ms to datetime string
                        pair_created_at = datetime.fromtimestamp(pair_created_at_raw / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        pair_created_at = None

                    price_change = item.get('priceChange')
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
                        alert_msg = f"<b>{name}</b>\nAddress: <code>{addr}</code>\nURL: {url_db}"
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
                                last_updated TEXT
                            )''')
                            alerted_conn.commit()
                            # Insert into alerted_tokens.db
                            alerted_cursor.execute('INSERT OR IGNORE INTO tokens (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', row)
                            alerted_conn.commit()
                            alerted_conn.close()
                            # Remove from main tokens db
                            cursor.execute('DELETE FROM tokens WHERE address = ?', (addr,))
                            print(f"Moved token {addr} to alerted_tokens.db due to price change alert")

                    price_change_str = json.dumps(price_change) if price_change is not None else None
                    price_usd = item.get('priceUsd')
                    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cursor.execute('INSERT OR IGNORE INTO tokens (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (addr, name, mc, url_db, pair_created_at, price_change_str, price_usd, last_updated))
            conn.commit()
            
            # Get newly added tokens
            # cursor.execute('SELECT address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated FROM tokens WHERE address NOT IN ({})'.format(','.join('?' for _ in existing_addresses)), list(existing_addresses))
            # new_tokens = cursor.fetchall()
        
        # # Display table of newly added tokens
        # if new_tokens:
        #     if first_run:
        #         print(f"\nLive Token Table - Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        #         print("-" * 240)
        #         print(f"{'Token Address':<50} {'Name':<20} {'Market Cap':<15} {'URL':<60} {'PairCreatedAt':<15} {'PriceChange':<30} {'PriceUsd':<15} {'LastUpdated'}")
        #         print("-" * 240)
        #         first_run = False
        #     for addr, name, mc, url, pair_created_at, price_change, price_usd, last_updated in new_tokens:
        #         addr_str = addr[:49]
        #         name_str = name[:19]
        #         mc_str = str(mc)[:14]
        #         url_str = url[:59]
        #         pair_created_at_str = str(pair_created_at) if pair_created_at is not None else ''
        #         price_change_str = price_change[:29] + '...' if price_change and len(price_change) > 32 else (price_change or '')
        #         price_usd_str = price_usd if price_usd is not None else ''
        #         last_updated_str = last_updated if last_updated is not None else ''
        #         print(f"{addr_str:<50} {name_str:<20} {mc_str:<15} {url_str:<60} {pair_created_at_str:<15} {price_change_str:<30} {price_usd_str:<15} {last_updated_str}")
        #     print("-" * 240)
        
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
        last_updated TEXT
    )''')
    conn.commit()
    
    # Run in a loop for live updates
    try:
        # Start the async batch fetcher in the background
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(fetch_token_data_batches(conn))
        while True:
            fetch_and_display_tokens(conn)
            # Display live table with alert count
            table = Table(title="Solana Price Alert Bot - Live Telegram Alerts")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="magenta")
            table.add_row("Telegram Alerts Sent", str(alert_count))
            console.clear()
            console.print(table)
            time.sleep(5)  # Update every minute
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        conn.close()

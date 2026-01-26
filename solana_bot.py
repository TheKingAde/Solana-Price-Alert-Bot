import requests
import time
from datetime import datetime
import sqlite3
import json
from datetime import datetime

first_run = True

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
            existing_addresses = set(row[0] for row in cursor.fetchall())
            
            for item in data2:
                mc = item.get('marketCap')
                if mc is not None and mc <= 100000:
                    addr = item['baseToken']['address']
                    name = item['baseToken'].get('name', '')
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
                    cursor.execute('INSERT OR IGNORE INTO tokens (address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (addr, name, mc, url_db, pair_created_at, price_change_str, price_usd, last_updated))
            conn.commit()
            
            # Get newly added tokens
            cursor.execute('SELECT address, name, market_cap, url, pair_created_at, price_change, price_usd, last_updated FROM tokens WHERE address NOT IN ({})'.format(','.join('?' for _ in existing_addresses)), list(existing_addresses))
            new_tokens = cursor.fetchall()
        
        # Display table of newly added tokens
        if new_tokens:
            if first_run:
                print(f"\nLive Token Table - Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print("-" * 240)
                print(f"{'Token Address':<50} {'Name':<20} {'Market Cap':<15} {'URL':<60} {'PairCreatedAt':<15} {'PriceChange':<30} {'PriceUsd':<15} {'LastUpdated'}")
                print("-" * 240)
                first_run = False
            for addr, name, mc, url, pair_created_at, price_change, price_usd, last_updated in new_tokens:
                addr_str = addr[:49]
                name_str = name[:19]
                mc_str = str(mc)[:14]
                url_str = url[:59]
                pair_created_at_str = str(pair_created_at) if pair_created_at is not None else ''
                price_change_str = price_change[:29] + '...' if price_change and len(price_change) > 32 else (price_change or '')
                price_usd_str = price_usd if price_usd is not None else ''
                last_updated_str = last_updated if last_updated is not None else ''
                print(f"{addr_str:<50} {name_str:<20} {mc_str:<15} {url_str:<60} {pair_created_at_str:<15} {price_change_str:<30} {price_usd_str:<15} {last_updated_str}")
            print("-" * 240)
        
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
        while True:
            fetch_and_display_tokens(conn)
            time.sleep(5)  # Update every minute
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        conn.close()

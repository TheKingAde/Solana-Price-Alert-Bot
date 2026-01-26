# scrape_bonk_playwright.py
from playwright.sync_api import sync_playwright

def main():
    url = input("Enter the URL to scrape: ").strip()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        selector = 'div.bg-bags-grey-extra-dark\\/50'
        page.wait_for_selector(selector, timeout=15000)

        stats = page.query_selector_all(selector)
        results = {}
        for stat in stats:
            label = stat.query_selector('span')
            if not label:
                continue
            label_text = label.inner_text().strip()
            value_div = stat.query_selector('div.flex.items-center')
            if value_div:
                p_tags = value_div.query_selector_all('p.font-semibold')
                if len(p_tags) >= 2:
                    value = p_tags[1].inner_text().strip()
                else:
                    value = None
            else:
                value = None
            if label_text == "MCAP":
                results["market_cap"] = value
            elif label_text == "24H VOL":
                results["volume"] = value
            elif label_text == "PRICE":
                results["price"] = value

        print("Market Cap:", results.get("market_cap", "N/A"))
        print("24H Volume:", results.get("volume", "N/A"))
        print("Price:", results.get("price", "N/A"))

        browser.close()

if __name__ == "__main__":
    main()

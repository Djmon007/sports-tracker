import json
import os
import re
import time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.mfasportsmahe.com/Products"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


def scrape_products(max_pages: int = 5):
    items = []

    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}?page={page}" if page > 1 else BASE_URL
        print(f"Fetching page {page}: {url}")

        try:
            res = requests.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
        except requests.RequestException as exc:
            print(f"Failed to fetch {url}: {exc}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        # Find product links
        cards = soup.find_all("a", href=re.compile(r"/Product-detail/|/product/"))
        if not cards:
            cards = [
                a
                for a in soup.find_all("a")
                if "view product" in a.get_text().lower()
            ]

        found_on_page = 0
        for card in cards:
            product_url = urljoin(BASE_URL, card.get("href", ""))
            parent = (
                card.find_parent("div", class_=re.compile(r"item|card|col|grid"))
                or card.parent
            )
            raw_text = clean_text(parent.get_text(separator=" "))

            # Extract prices (current and original)
            prices = re.findall(r"₹?\s*(\d{2,5})", raw_text)
            current_price = prices[0] if len(prices) >= 1 else None
            original_price = prices[1] if len(prices) >= 2 else None

            # Clean product title
            title = (
                raw_text.replace("View Product", "")
                .replace("Off", "")
                .replace("%", "")
            )
            for p in prices:
                title = title.replace(p, "")
            title = clean_text(title)

            if title:
                items.append(
                    {
                        "title": title,
                        "current_price": current_price,
                        "original_price": original_price,
                        "url": product_url,
                    }
                )
                found_on_page += 1

        print(f"Extracted {found_on_page} products from page {page}")
        time.sleep(1.5)

    unique_items = {
        item.get("url") or item["title"]: item for item in items
    }.values()
    return list(unique_items)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products(max_pages=5)

    output_file = "data/products.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(results)} items to {output_file}")

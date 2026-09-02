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


def parse_product_card(card):
    # 1. Product Link
    link_tag = card.find("a", href=re.compile(r"/Products?/|/Product-detail/", re.I))
    if not link_tag and card.name == "a":
        link_tag = card

    if not link_tag or not link_tag.get("href"):
        return None

    href = link_tag.get("href", "")
    if href.startswith("#") or "javascript:" in href:
        return None
    product_url = urljoin(BASE_URL, href)

    # 2. Extract Discount (e.g. "(31% Off)")
    full_text = card.get_text(separator=" ")
    off_match = re.search(r"\(\s*(\d+%\s*Off)\s*\)", full_text, re.I)
    if not off_match:
        off_match = re.search(r"(\d+%\s*Off)", full_text, re.I)
    discount_off = f"({off_match.group(1)})" if off_match else None

    # 3. Extract Original Price (strikethrough / del tag)
    original_price = None
    del_tag = card.find(["del", "s", "strike"]) or card.find(
        attrs={"style": re.compile(r"text-decoration:\s*line-through", re.I)}
    )
    if del_tag:
        match = re.search(r"(\d+)", del_tag.get_text())
        if match:
            original_price = match.group(1)

    # 4. Extract Title
    title = ""
    # Check for dedicated header or anchor with the product name
    title_elem = card.find(["h2", "h3", "h4", "h5", "h6"])
    if not title_elem:
        for a in card.find_all("a"):
            txt = clean_text(a.get_text())
            if txt and "view product" not in txt.lower():
                title_elem = a
                break

    if title_elem:
        title = clean_text(title_elem.get_text())
    else:
        # Fallback: extract card lines and take the line preceding prices
        lines = [clean_text(line) for line in card.stripped_strings]
        for line in lines:
            if "view product" in line.lower():
                continue
            if re.search(r"[A-Za-z]{3,}", line) and not re.search(r"^\s*₹?\s*\d+\s*$", line):
                title = line
                break

    # Clean UI artefacts from title
    title = re.sub(r"(?i)\bview product\b", "", title).strip()

    # 5. Extract Current Price (targeted to the price element with ₹)
    current_price = None
    
    # Priority: find price prefixed with ₹ or Rs
    rupee_match = re.search(r"(?:₹|Rs\.?)\s*(\d{2,5})", full_text)
    if rupee_match:
        current_price = rupee_match.group(1)
    else:
        # Find numeric sequence directly before original_price or discount
        price_block_match = re.search(r"(\d{2,5})\s+(?:\d{2,5}\s+)?\(\d+%\s*Off\)", full_text)
        if price_block_match:
            current_price = price_block_match.group(1)

    # Validation: Title and price must be valid
    if not title or title.isdigit() or len(title) < 3:
        return None

    return {
        "title": title,
        "current_price": current_price,
        "original_price": original_price,
        "off": discount_off,
        "url": product_url,
    }


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

        # Get parent product containers
        cards = soup.find_all("div", class_=re.compile(r"product|col-|card|item", re.I))
        candidate_cards = [
            c for c in cards 
            if "view product" in c.get_text().lower() 
            and len(c.find_all(text=re.compile(r"view product", re.I))) == 1
        ]

        found = 0
        for card in candidate_cards:
            parsed = parse_product_card(card)
            if parsed:
                items.append(parsed)
                found += 1

        print(f"Extracted {found} products from page {page}")
        time.sleep(1.5)

    unique_items = {item["url"]: item for item in items if item.get("url")}.values()
    return list(unique_items)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products(max_pages=5)

    output_file = "data/products.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(results)} items to {output_file}")

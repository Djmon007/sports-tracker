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
    """
    Extracts title, prices, discount, and URL accurately from an MFA product card.
    """
    # 1. Target URL
    # Look for View Product anchor or a direct product link
    link_elem = card.find("a", href=re.compile(r"/Products?/|/Product-detail/", re.I))
    if not link_elem and card.name == "a":
        link_elem = card

    if not link_elem or not link_elem.get("href"):
        return None

    raw_href = link_elem.get("href", "")
    if raw_href.startswith("#") or "javascript:" in raw_href:
        return None
    product_url = urljoin(BASE_URL, raw_href)

    # 2. Target Title
    # Product title is usually in an h3, h4, h5, or a link containing product text (not "View Product")
    title = ""
    title_elem = card.find(["h3", "h4", "h5", "h6"])
    if not title_elem:
        for a in card.find_all("a"):
            text = clean_text(a.get_text())
            if text and "view product" not in text.lower():
                title_elem = a
                break

    if title_elem:
        title = clean_text(title_elem.get_text())
    else:
        # Fallback: inspect raw text before price elements
        card_clone = BeautifulSoup(str(card), "html.parser")
        for tag in card_clone.find_all(["del", "s", "strike", "a"]):
            if "view product" in tag.get_text().lower():
                tag.decompose()
        title = clean_text(card_clone.get_text())

    # Ensure unwanted UI keywords don't slip into title
    title = re.sub(r"(?i)\bview product\b", "", title).strip()

    # 3. Original Price (Strikethrough: <del>, <s>, <strike>, or CSS line-through)
    original_price = None
    del_tag = card.find(["del", "s", "strike"]) or card.find(
        attrs={"style": re.compile(r"text-decoration:\s*line-through", re.I)}
    )
    if del_tag:
        del_match = re.search(r"(\d+)", del_tag.get_text())
        if del_match:
            original_price = del_match.group(1)

    # 4. Discount / Off Percentage (e.g., "(31% Off)")
    off_match = re.search(r"(\d+%\s*Off)", card.get_text(), re.I)
    off_text = off_match.group(1) if off_match else None

    # 5. Current Price
    # Find all standalone numbers in the price area (avoiding year numbers in title)
    current_price = None
    # Extract only the text outside of the title container
    price_search_area = card.get_text(separator=" ")
    if title:
        price_search_area = price_search_area.replace(title, "")

    # Look for Indian Rupee symbol or price values adjacent to discount/del
    price_candidates = re.findall(r"(?:₹|\bRs\.?|\bINR)?\s*(\d{2,5})\b", price_search_area)
    
    # Filter out values that match the original strikethrough price or 2026/2027 years
    valid_prices = [
        p for p in price_candidates 
        if p != original_price and p not in ["2026", "2027", "2025", "2024"]
    ]
    
    if valid_prices:
        current_price = valid_prices[0]

    # Clean up empty or corrupted titles
    if not title or title.lower() == "view product":
        return None

    return {
        "title": title,
        "current_price": current_price,
        "original_price": original_price,
        "off": off_text,
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

        # Select individual product card wrappers
        # MFA Sports uses bootstrap columns or product grid wrappers
        cards = soup.find_all("div", class_=re.compile(r"product|col-|card|item", re.I))
        
        # Deduplicate parent containers
        candidate_cards = [
            c for c in cards 
            if "view product" in c.get_text().lower() 
            and len(c.find_all(text=re.compile(r"view product", re.I))) == 1
        ]

        found_on_page = 0
        for card in candidate_cards:
            parsed = parse_product_card(card)
            if parsed:
                items.append(parsed)
                found_on_page += 1

        print(f"Extracted {found_on_page} products from page {page}")
        time.sleep(1.5)

    # Deduplicate entries by URL
    unique_items = {item["url"]: item for item in items if item.get("url")}.values()
    return list(unique_items)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products(max_pages=5)

    output_file = "data/products.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Successfully saved {len(results)} items to {output_file}")

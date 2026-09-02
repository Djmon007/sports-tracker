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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


def parse_card(view_link_elem):
    # Determine the product URL
    href = view_link_elem.get("href", "")
    if not href or href.startswith("#") or "javascript:" in href:
        return None
    product_url = urljoin(BASE_URL, href)

    # Ascend up to the enclosing product container (stops before full body/main container)
    container = view_link_elem
    for _ in range(4):
        if container.parent and container.parent.name not in ["body", "html", "section"]:
            container = container.parent
        else:
            break

    # Extract all text segments cleanly
    raw_strings = [clean_text(s) for s in container.stripped_strings if clean_text(s)]
    
    # Remove UI boilerplate strings
    filtered_strings = [
        s for s in raw_strings 
        if not re.search(r"^(view product|add to cart|buy now|wishlist)$", s, re.I)
    ]

    if not filtered_strings:
        return None

    # 1. Title: The longest text line containing alphabet characters
    # (Product titles like 'CHELSEA HOME 2026-27 IMPORTED KIT' are the primary descriptive line)
    title = ""
    candidate_titles = [
        s for s in filtered_strings 
        if re.search(r"[a-zA-Z]{3,}", s) and not re.search(r"^\(?\d+%\s*Off\)?$", s, re.I)
    ]
    if candidate_titles:
        title = max(candidate_titles, key=len)

    # 2. Discount: Extract pattern like "(31% Off)" or "31% Off"
    discount_off = None
    for s in filtered_strings:
        m = re.search(r"\(?\s*(\d+%\s*Off)\s*\)?", s, re.I)
        if m:
            discount_off = f"({m.group(1).strip()})"
            break

    # 3. Original Price: Check for <del>, <s>, <strike> tags
    original_price = None
    del_tag = container.find(["del", "s", "strike"])
    if del_tag:
        m = re.search(r"(\d+)", del_tag.get_text())
        if m:
            original_price = m.group(1)

    # 4. Extract numeric prices
    # Gather any standalone numbers between 2 and 5 digits (ignoring season years 2024-2027)
    numbers = []
    for s in filtered_strings:
        found_nums = re.findall(r"\b(\d{2,5})\b", s)
        for num in found_nums:
            if num not in ["2024", "2025", "2026", "2027"] and not re.search(r"\b" + num + r"%\b", s):
                numbers.append(num)

    current_price = None
    if numbers:
        current_price = numbers[0]
        # If original_price was not found via <del>, look for a second number
        if not original_price and len(numbers) > 1:
            original_price = numbers[1]
            # Ensure current_price is the lower one if both exist
            if int(current_price) > int(original_price):
                current_price, original_price = original_price, current_price

    if not title:
        return None

    return {
        "title": title,
        "current_price": current_price,
        "original_price": original_price,
        "off": discount_off,
        "url": product_url
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
            print(f"Request error for page {page}: {exc}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        # Directly locate every anchor that represents a product view action
        view_links = [
            a for a in soup.find_all("a") 
            if "view product" in a.get_text().lower() and a.get("href")
        ]

        if not view_links:
            # Fallback if text differs slightly
            view_links = soup.find_all("a", href=re.compile(r"/Products?/[a-zA-Z0-9_-]+", re.I))

        page_count = 0
        for link in view_links:
            data = parse_card(link)
            if data:
                items.append(data)
                page_count += 1

        print(f"Parsed {page_count} items from page {page}")
        time.sleep(1.2)

    # Deduplicate items by URL
    unique_items = list({item["url"]: item for item in items if item.get("url")}.values())
    return unique_items


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products(max_pages=5)

    output_path = "data/products.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Done! Successfully written {len(results)} items to {output_path}")

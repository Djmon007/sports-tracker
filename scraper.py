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
    href = view_link_elem.get("href", "")
    if not href or href.startswith("#") or "javascript:" in href:
        return None
    product_url = urljoin(BASE_URL, href)

    # Climb up to the card wrapper
    container = view_link_elem
    for _ in range(4):
        if container.parent and container.parent.name not in ["body", "html", "section"]:
            container = container.parent
        else:
            break

    raw_strings = [clean_text(s) for s in container.stripped_strings if clean_text(s)]
    filtered_strings = [
        s for s in raw_strings 
        if not re.search(r"^(view product|add to cart|buy now|wishlist)$", s, re.I)
    ]

    if not filtered_strings:
        return None

    # 1. Product Title (the main alphanumeric text line)
    title = ""
    candidate_titles = [
        s for s in filtered_strings 
        if re.search(r"[a-zA-Z]{3,}", s) and not re.search(r"^\(?\d+%\s*Off\)?$", s, re.I)
    ]
    if candidate_titles:
        title = max(candidate_titles, key=len)

    if not title:
        return None

    # 2. Extract Discount (e.g., "(31% Off)")
    full_card_text = clean_text(container.get_text(separator=" "))
    off_match = re.search(r"\(?\s*(\d+%\s*Off)\s*\)?", full_card_text, re.I)
    discount_off = f"({off_match.group(1).strip()})" if off_match else None

    # 3. Price Block Extraction
    # Remove the title from the card text so year numbers (2026-27, 2021-22) aren't read as prices
    price_area = full_card_text.replace(title, "")
    # Remove UI labels
    price_area = re.sub(r"(?i)\bview product\b", "", price_area)

    current_price = None
    original_price = None

    # Primary Pattern: Match two prices followed by (X% Off) -> e.g. "699 999 (31% Off)"
    dual_price_match = re.search(
        r"(?:₹|Rs\.?)?\s*(\d{2,5})\s+(?:₹|Rs\.?)?\s*(\d{2,5})\s*\(?\s*\d+%\s*Off\)?",
        price_area,
        re.I
    )

    if dual_price_match:
        current_price = dual_price_match.group(1)
        original_price = dual_price_match.group(2)
    else:
        # Check explicit strikethrough tags
        del_tag = container.find(["del", "s", "strike"])
        if del_tag:
            m = re.search(r"(\d{2,5})", del_tag.get_text())
            if m:
                original_price = m.group(1)

        # Collect isolated price candidates from the price area
        remaining_numbers = re.findall(r"(?:₹|Rs\.?)?\s*(\d{2,5})\b", price_area)
        # Exclude discount percentages and any common calendar year artifacts
        valid_nums = [
            n for n in remaining_numbers 
            if n not in ["2021", "2022", "2024", "2025", "2026", "2027"]
            and not re.search(r"\b" + n + r"%", price_area)
        ]

        if valid_nums:
            current_price = valid_nums[0]
            if len(valid_nums) > 1 and not original_price:
                original_price = valid_nums[1]

    # Ensure current price is the lower one if both exist
    if current_price and original_price:
        c_val = int(current_price)
        o_val = int(original_price)
        if c_val > o_val:
            current_price, original_price = str(o_val), str(c_val)

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

        view_links = [
            a for a in soup.find_all("a") 
            if "view product" in a.get_text().lower() and a.get("href")
        ]

        if not view_links:
            view_links = soup.find_all("a", href=re.compile(r"/Products?/[a-zA-Z0-9_-]+", re.I))

        page_count = 0
        for link in view_links:
            data = parse_card(link)
            if data:
                items.append(data)
                page_count += 1

        print(f"Parsed {page_count} items from page {page}")
        time.sleep(1.2)

    return list({item["url"]: item for item in items if item.get("url")}.values())


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products(max_pages=5)

    output_path = "data/products.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Done! Successfully written {len(results)} items to {output_path}")

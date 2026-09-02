import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
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

STANDARD_SIZES = ["S", "M", "L", "XL", "XXL", "3XL"]
OUTPUT_PATH = "data/products.json"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


def normalize_size(s: str) -> str:
    s = s.upper().strip()
    return "3XL" if s in ["XXXL", "3XL"] else s


def extract_clean_title(card_soup: BeautifulSoup, href_slug: str) -> str:
    # 1. Try explicit title tag
    for tag in card_soup.find_all(["h2", "h3", "h4", "h5", "h6", "p"]):
        txt = clean_text(tag.get_text())
        if txt and not re.search(r"(view product|add to cart|₹|\boff\b)", txt, re.I) and len(txt) > 5:
            # Strip prefixes like 'Add to Cart -->'
            cleaned = re.sub(r"^(?:add to cart\s*(?:-->|->|-|:)?\s*)+", "", txt, flags=re.I).strip()
            if len(cleaned) > 5:
                return cleaned

    # 2. Derive human-readable name from URL slug if card text is noisy
    if href_slug:
        slug = href_slug.rstrip("/").split("/")[-1]
        slug_title = re.sub(r"[-_]+", " ", slug).strip().upper()
        if len(slug_title) > 5 and not slug_title.isdigit():
            return slug_title

    return "Product"


def parse_sizes_and_stock(product_url: str, session: requests.Session) -> dict:
    """Scrapes the product detail page to read stock under Choose Size."""
    size_stock = {sz: 0 for sz in STANDARD_SIZES}
    try:
        res = session.get(product_url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            return size_stock
        page_html = res.text
        soup = BeautifulSoup(page_html, "html.parser")
    except Exception:
        return size_stock

    # Check for embedded script JSON data
    variant_matches = re.findall(r'\{[^{}]*(?:size|attribute)[^{}]*(?:stock|qty|quantity)[^{}]*\}', page_html, re.I)
    for v_str in variant_matches:
        for size in STANDARD_SIZES:
            target = [size, "XXXL"] if size == "3XL" else [size]
            for ts in target:
                if re.search(rf'["\']?size["\']?\s*:\s*["\']?{ts}["\']?', v_str, re.I):
                    m = re.search(r'["\']?(?:stock|qty|quantity)["\']?\s*:\s*(\d+)', v_str, re.I)
                    if m:
                        size_stock[size] = int(m.group(1))

    # Parse DOM elements in "Choose Size" block
    valid_tokens = set(STANDARD_SIZES) | {"XXXL"}
    elements = soup.find_all(
        lambda tag: tag.name in ["button", "div", "li", "span", "input", "a"]
        and tag.get_text(strip=True).upper() in valid_tokens
    )

    for el in elements:
        sz = normalize_size(el.get_text(strip=True))
        if sz not in STANDARD_SIZES:
            continue

        stock_attr = el.get("data-stock") or el.get("data-qty") or el.get("data-quantity")
        if stock_attr and stock_attr.isdigit():
            size_stock[sz] = int(stock_attr)
            continue

        el_classes = " ".join(el.get("class", [])).lower()
        if any(w in el_classes for w in ["disabled", "out-of-stock", "outofstock", "soldout"]):
            size_stock[sz] = 0
            continue

        # Check surrounding text for 'X in stock' or 'out of stock'
        parent = el.find_parent(["div", "section", "form"]) or el.parent
        p_text = clean_text(parent.get_text(separator=" "))
        m_stock = re.search(r"(\d+)\s+(?:in stock|left|available)", p_text, re.I)
        if m_stock:
            size_stock[sz] = int(m_stock.group(1))
        elif re.search(r"out\s+of\s+stock", p_text, re.I):
            size_stock[sz] = 0
        else:
            # If button exists and is active/not disabled
            if size_stock[sz] == 0:
                size_stock[sz] = 1

    return size_stock


def parse_card(view_link):
    href = view_link.get("href", "")
    if not href or href.startswith("#") or "javascript:" in href:
        return None
    product_url = urljoin(BASE_URL, href)

    # Locate enclosing card container
    card = view_link
    for _ in range(4):
        if card.parent and card.parent.name not in ["body", "html", "section"]:
            card = card.parent
        else:
            break

    full_text = clean_text(card.get_text(separator=" "))
    title = extract_clean_title(card, href)

    # Discount
    off_match = re.search(r"\(?\s*(\d+%\s*Off)\s*\)?", full_text, re.I)
    discount_off = f"({off_match.group(1).strip()})" if off_match else None

    # Prices
    price_area = full_text.replace(title, "")
    price_area = re.sub(r"(?i)\b(?:view product|add to cart)\b", "", price_area)

    current_price, original_price = None, None
    dual = re.search(r"(?:₹|Rs\.?)?\s*(\d{2,5})\s+(?:₹|Rs\.?)?\s*(\d{2,5})\s*\(?\s*\d+%\s*Off\)?", price_area, re.I)

    if dual:
        current_price, original_price = dual.group(1), dual.group(2)
    else:
        del_tag = card.find(["del", "s", "strike"])
        if del_tag:
            m = re.search(r"(\d{2,5})", del_tag.get_text())
            if m:
                original_price = m.group(1)

        nums = re.findall(r"(?:₹|Rs\.?)?\s*(\d{2,5})\b", price_area)
        valid = [n for n in nums if n not in ["2021", "2022", "2024", "2025", "2026", "2027"] and not re.search(r"\b" + n + r"%", price_area)]
        if valid:
            current_price = valid[0]
            if len(valid) > 1 and not original_price:
                original_price = valid[1]

    if current_price and original_price and int(current_price) > int(original_price):
        current_price, original_price = original_price, current_price

    return {
        "title": title,
        "current_price": current_price,
        "original_price": original_price,
        "off": discount_off,
        "url": product_url
    }


def get_total_pages(soup: BeautifulSoup) -> int:
    page_nums = []
    for a in soup.find_all("a", href=re.compile(r"page=\d+")):
        m = re.search(r"page=(\d+)", a.get("href", ""))
        if m:
            page_nums.append(int(m.group(1)))
    for tag in soup.find_all(["a", "span", "li"]):
        txt = tag.get_text().strip()
        if txt.isdigit() and int(txt) < 300:
            page_nums.append(int(txt))
    return max(page_nums) if page_nums else 1


def scrape_all():
    session = requests.Session()
    discovered_cards = {}
    page = 1
    total_pages = None

    print("Step 1: Discovering all catalog items across all pages...")
    while True:
        url = f"{BASE_URL}?page={page}" if page > 1 else BASE_URL
        try:
            res = session.get(url, headers=HEADERS, timeout=15)
            res.raise_for_status()
        except requests.RequestException:
            break

        soup = BeautifulSoup(res.text, "html.parser")
        if total_pages is None:
            total_pages = get_total_pages(soup)
            print(f"Detected {total_pages} total catalog pages.")

        view_links = [
            a for a in soup.find_all("a") 
            if "view product" in a.get_text().lower() and a.get("href")
        ]
        if not view_links:
            break

        for link in view_links:
            card = parse_card(link)
            if card and card["url"] not in discovered_cards:
                discovered_cards[card["url"]] = card

        if total_pages and page >= total_pages:
            break
        page += 1
        time.sleep(0.3)

    print(f"Total discovered products: {len(discovered_cards)}. Step 2: Extracting size stock in parallel...")

    # Load existing URLs to determine hourly delta
    previous_urls = set()
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                old_items = old_data.get("products", []) if isinstance(old_data, dict) else old_data
                previous_urls = {it["url"] for it in old_items if "url" in it}
        except Exception:
            pass

    def worker(item):
        s = requests.Session()
        item["sizes"] = parse_sizes_and_stock(item["url"], s)
        item["is_new"] = bool(previous_urls and item["url"] not in previous_urls)
        return item

    completed = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(worker, item) for item in discovered_cards.values()]
        for f in as_completed(futures):
            completed.append(f.result())

    new_count = sum(1 for it in completed if it.get("is_new"))

    payload = {
        "metadata": {
            "total_products": len(completed),
            "new_products_last_hour": new_count,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        "products": completed
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(completed)} products with full size inventory. Newly added: {new_count}")


if __name__ == "__main__":
    scrape_all()

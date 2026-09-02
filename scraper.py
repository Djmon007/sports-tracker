import json
import os
import re
import time
from urllib.parse import urljoin
from datetime import datetime, timezone
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

# Standard size list including 3XL / XXXL
STANDARD_SIZES = ["S", "M", "L", "XL", "XXL", "3XL"]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


def normalize_size(size_str: str) -> str:
    """Normalizes variations like XXXL to 3XL."""
    s = size_str.upper().strip()
    if s in ["XXXL", "3XL"]:
        return "3XL"
    return s


def parse_sizes_and_stock(product_url: str, session: requests.Session) -> dict:
    size_stock = {size: 0 for size in STANDARD_SIZES}

    try:
        res = session.get(product_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return size_stock
        page_html = res.text
        soup = BeautifulSoup(page_html, "html.parser")
    except Exception:
        return size_stock

    # Method A: Variant JSON matches inside scripts
    variant_matches = re.findall(
        r'\{[^{}]*(?:size|attribute)[^{}]*(?:stock|qty|quantity)[^{}]*\}', 
        page_html, 
        re.I
    )
    for v_str in variant_matches:
        for size in STANDARD_SIZES:
            target_sizes = [size, "XXXL"] if size == "3XL" else [size]
            for ts in target_sizes:
                if re.search(rf'["\']?size["\']?\s*:\s*["\']?{ts}["\']?', v_str, re.I):
                    stock_match = re.search(r'["\']?(?:stock|qty|quantity)["\']?\s*:\s*(\d+)', v_str, re.I)
                    if stock_match:
                        size_stock[size] = int(stock_match.group(1))

    # Method B: Parse DOM size buttons/selectors
    valid_size_tokens = set(STANDARD_SIZES) | {"XXXL"}
    size_elements = soup.find_all(
        lambda tag: tag.name in ["button", "div", "li", "span", "input", "a"]
        and tag.get_text(strip=True).upper() in valid_size_tokens
    )

    for el in size_elements:
        raw_size = el.get_text(strip=True)
        size = normalize_size(raw_size)
        if size not in STANDARD_SIZES:
            continue

        # Check data attributes
        stock_val = el.get("data-stock") or el.get("data-qty") or el.get("data-quantity")
        if stock_val is not None and stock_val.isdigit():
            size_stock[size] = int(stock_val)
            continue

        # Check classes for out-of-stock indicators
        el_classes = " ".join(el.get("class", [])).lower()
        if any(w in el_classes for w in ["disabled", "out-of-stock", "outofstock", "sold-out", "soldout"]):
            size_stock[size] = 0
            continue

        # Check surrounding text for stock count
        parent_block = el.find_parent(["div", "section", "form"]) or el.parent
        parent_text = clean_text(parent_block.get_text(separator=" "))
        
        stock_text_match = re.search(r"(\d+)\s+(?:in stock|left|available)", parent_text, re.I)
        if stock_text_match:
            size_stock[size] = int(stock_text_match.group(1))
        elif re.search(r"out\s+of\s+stock", parent_text, re.I):
            size_stock[size] = 0
        else:
            if size_stock[size] == 0 and "active" in el_classes:
                size_stock[size] = 1

    return size_stock


def parse_card(view_link_elem):
    href = view_link_elem.get("href", "")
    if not href or href.startswith("#") or "javascript:" in href:
        return None
    product_url = urljoin(BASE_URL, href)

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

    # Title
    title = ""
    candidate_titles = [
        s for s in filtered_strings 
        if re.search(r"[a-zA-Z]{3,}", s) and not re.search(r"^\(?\d+%\s*Off\)?$", s, re.I)
    ]
    if candidate_titles:
        title = max(candidate_titles, key=len)

    if not title:
        return None

    # Discount
    full_card_text = clean_text(container.get_text(separator=" "))
    off_match = re.search(r"\(?\s*(\d+%\s*Off)\s*\)?", full_card_text, re.I)
    discount_off = f"({off_match.group(1).strip()})" if off_match else None

    # Price Area
    price_area = full_card_text.replace(title, "")
    price_area = re.sub(r"(?i)\bview product\b", "", price_area)

    current_price = None
    original_price = None

    dual_price_match = re.search(
        r"(?:₹|Rs\.?)?\s*(\d{2,5})\s+(?:₹|Rs\.?)?\s*(\d{2,5})\s*\(?\s*\d+%\s*Off\)?",
        price_area,
        re.I
    )

    if dual_price_match:
        current_price = dual_price_match.group(1)
        original_price = dual_price_match.group(2)
    else:
        del_tag = container.find(["del", "s", "strike"])
        if del_tag:
            m = re.search(r"(\d{2,5})", del_tag.get_text())
            if m:
                original_price = m.group(1)

        remaining_numbers = re.findall(r"(?:₹|Rs\.?)?\s*(\d{2,5})\b", price_area)
        valid_nums = [
            n for n in remaining_numbers 
            if n not in ["2021", "2022", "2024", "2025", "2026", "2027"]
            and not re.search(r"\b" + n + r"%", price_area)
        ]

        if valid_nums:
            current_price = valid_nums[0]
            if len(valid_nums) > 1 and not original_price:
                original_price = valid_nums[1]

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


def get_total_pages(soup: BeautifulSoup) -> int:
    page_numbers = []
    for a in soup.find_all("a", href=re.compile(r"page=\d+")):
        m = re.search(r"page=(\d+)", a.get("href", ""))
        if m:
            page_numbers.append(int(m.group(1)))

    for tag in soup.find_all(["a", "span", "li"]):
        txt = tag.get_text().strip()
        if txt.isdigit() and int(txt) < 300:
            page_numbers.append(int(txt))

    return max(page_numbers) if page_numbers else 1


def scrape_all():
    session = requests.Session()
    items = []
    page = 1
    total_pages = None

    while True:
        url = f"{BASE_URL}?page={page}" if page > 1 else BASE_URL
        print(f"Fetching catalog page {page}{f' of {total_pages}' if total_pages else ''}")

        try:
            res = session.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
        except requests.RequestException as exc:
            print(f"Catalog fetch failed at page {page}: {exc}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        if total_pages is None:
            total_pages = get_total_pages(soup)

        view_links = [
            a for a in soup.find_all("a") 
            if "view product" in a.get_text().lower() and a.get("href")
        ]

        if not view_links:
            break

        for link in view_links:
            product = parse_card(link)
            if product:
                print(f"Scraping sizes for: {product['title'][:30]}...")
                sizes = parse_sizes_and_stock(product["url"], session)
                product["sizes"] = sizes
                items.append(product)
                time.sleep(0.2)

        if total_pages and page >= total_pages:
            break

        page += 1
        time.sleep(0.8)

    return list({item["url"]: item for item in items if item.get("url")}.values())


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    output_path = "data/products.json"

    previous_urls = set()
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                old_items = old_data.get("products", []) if isinstance(old_data, dict) else old_data
                previous_urls = {item["url"] for item in old_items if "url" in item}
        except Exception:
            previous_urls = set()

    all_products = scrape_all()

    new_items_count = 0
    for item in all_products:
        if previous_urls and item["url"] not in previous_urls:
            item["is_new"] = True
            new_items_count += 1
        else:
            item["is_new"] = False

    payload = {
        "metadata": {
            "total_products": len(all_products),
            "new_products_last_hour": new_items_count,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        "products": all_products
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Finished! Processed {len(all_products)} products with sizes.")

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

    # Ascend to the card container
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

    # 1. Product Title
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
    price_area = full_card_text.replace(title, "")
    price_area = re.sub(r"(?i)\bview product\b", "", price_area)

    current_price = None
    original_price = None

    # Primary Pattern: "699 999 (31% Off)"
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

    # Lower value is always current selling price
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
    """Finds the maximum page number from the pagination elements."""
    page_numbers = []
    # Search all links with ?page=X or numeric pagination links
    for a in soup.find_all("a", href=re.compile(r"page=\d+")):
        m = re.search(r"page=(\d+)", a.get("href", ""))
        if m:
            page_numbers.append(int(m.group(1)))

    # Fallback to inspecting plain pagination numbers
    for tag in soup.find_all(["a", "span", "li"]):
        txt = tag.get_text().strip()
        if txt.isdigit() and int(txt) < 300:
            page_numbers.append(int(txt))

    return max(page_numbers) if page_numbers else 1


def scrape_products():
    items = []
    page = 1
    total_pages = None

    while True:
        url = f"{BASE_URL}?page={page}" if page > 1 else BASE_URL
        print(f"Fetching page {page}{f' of {total_pages}' if total_pages else ''}: {url}")

        try:
            res = requests.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
        except requests.RequestException as exc:
            print(f"Request error for page {page}: {exc}")
            break

        soup = BeautifulSoup(res.text, "html.parser")

        # Discover total pages on first request
        if total_pages is None:
            total_pages = get_total_pages(soup)
            print(f"Auto-detected {total_pages} total pages to scrape.")

        view_links = [
            a for a in soup.find_all("a") 
            if "view product" in a.get_text().lower() and a.get("href")
        ]

        if not view_links:
            view_links = soup.find_all("a", href=re.compile(r"/Products?/[a-zA-Z0-9_-]+", re.I))

        # Stop crawling if no products are found on the page
        if not view_links:
            print(f"No products found on page {page}. Finished crawling.")
            break

        page_count = 0
        for link in view_links:
            data = parse_card(link)
            if data:
                items.append(data)
                page_count += 1

        print(f"Parsed {page_count} items from page {page}")

        # Stop if we hit the discovered last page
        if total_pages and page >= total_pages:
            print("Reached the final page.")
            break

        page += 1
        time.sleep(0.8)  # Polite delay to prevent connection timeouts

    return list({item["url"]: item for item in items if item.get("url")}.values())


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    results = scrape_products()

    output_path = "data/products.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Done! Successfully written {len(results)} items to {output_path}")

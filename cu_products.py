"""CU 공식 홈페이지(cu.bgfretail.com) 상품 목록 수집기.

전국 공통 상품 목록이며, 매장별 재고 정보는 포함되지 않는다.
(매장 재고는 포켓CU 앱에서만 조회 가능)
"""
import csv
import html
import re
import sys
import time
import urllib.parse
import urllib.request

AJAX_URL = "https://cu.bgfretail.com/product/productAjax.do"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

# (대분류 코드, 소분류 코드, 홈페이지 분류명)
CATEGORIES = [
    ("10", "1", "간편식사 > 도시락"),
    ("10", "3", "간편식사 > 샌드위치/햄버거"),
    ("10", "2", "간편식사 > 주먹밥/김밥"),
    ("20", "4", "즉석조리 > 튀김류"),
    ("50", "10", "식품 > 안주류"),
    ("50", "12", "식품 > 가공식사"),
]

ITEM_RE = re.compile(
    r'<li class="prod_list.*?view\((\d+)\).*?<img src="([^"]+)".*?'
    r'<div class="name"[^>]*><p>(.*?)</p>.*?<strong>([\d,]+)</strong>(.*?)</li>',
    re.S,
)


def fetch_page(main, sub, page):
    body = urllib.parse.urlencode({
        "pageIndex": page, "searchMainCategory": main, "searchSubCategory": sub,
        "listType": "1", "searchCondition": "setA", "searchUseYn": "", "gdIdx": "0",
        "codeParent": main, "user_id": "", "search1": "", "search2": "", "searchKeyword": "",
    }).encode()
    req = urllib.request.Request(AJAX_URL, data=body, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://cu.bgfretail.com/product/product.do?category=product&depth2=4",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_items(page_html):
    for gd_idx, img, name, price, rest in ITEM_RE.findall(page_html):
        badges = re.findall(r'class="(plus1|plus2)"', rest)
        tags = re.findall(r'class="(new|best)"', rest)
        yield {
            "gdIdx": gd_idx,
            "name": html.unescape(name).strip(),
            "price": int(price.replace(",", "")),
            "event": {"plus1": "1+1", "plus2": "2+1"}.get(badges[0], "") if badges else "",
            "tag": ",".join(t.upper() for t in tags),
            "barcode": img.rsplit("/", 1)[-1].split(".")[0],
            "image": "https:" + img if img.startswith("//") else img,
        }


def collect():
    rows, seen = [], set()
    for main, sub, label in CATEGORIES:
        page = 1
        while True:
            items = list(parse_items(fetch_page(main, sub, page)))
            new = [it for it in items if it["gdIdx"] not in seen]
            for it in new:
                seen.add(it["gdIdx"])
                rows.append({"category": label, **it})
            print(f"{label} p{page}: {len(items)}개", file=sys.stderr)
            if not new:
                break
            page += 1
            time.sleep(0.5)
    return rows


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "cu_products_raw.csv"
    data = collect()
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    print(f"{len(data)}개 저장 -> {out}", file=sys.stderr)

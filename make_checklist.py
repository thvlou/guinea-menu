"""cu_products.py 결과(raw CSV)를 품목별로 분류해 매장 재고 확인표를 만든다.

재고 칸은 비어 있으며, 포켓CU 앱 > 재고조회에서 매장을 선택해 확인한 수량을 채워 넣는 용도.
"""
import csv
import re
import sys

CUP = re.compile(r"컵|사발|왕뚜껑|용기|도시락")
NOT_NOODLE = re.compile(
    r"떡볶이|쫄볶이|밥|컵반|죽|떡국|수프|컵국|누룽지|미트볼|묵사발|찌개|수제비|어묵탕|오뎅|웰팜|오마치|봉지"
)

REQUESTED = ["샌드위치", "햄버거", "핫바", "김밥", "삼각김밥", "컵라면"]
ORDER = REQUESTED + ["도시락", "주먹밥", "핫도그", "즉석튀김"]


def classify(row):
    cat, name = row["category"], row["name"]
    if cat == "간편식사 > 도시락":
        return "도시락"
    if cat == "간편식사 > 샌드위치/햄버거":
        if name.startswith("햄)") or re.search(r"버거|머핀", name):
            return "햄버거"
        if name.startswith("핫)"):
            return "핫도그"
        return "샌드위치"
    if cat == "간편식사 > 주먹밥/김밥":
        if name.startswith(("삼)", "빅삼)")):
            return "삼각김밥"
        if name.startswith("김)") or "꼬마" in name:
            return "김밥"
        return "주먹밥"
    if cat == "즉석조리 > 튀김류":
        return "즉석튀김"
    if "핫바" in name:
        return "핫바"
    if cat == "식품 > 가공식사" and CUP.search(name):
        if not NOT_NOODLE.search(name) or ("라면" in name and "봉지" not in name):
            return "컵라면"
    return None


def main(raw_path, out_path):
    rows = []
    with open(raw_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            kind = classify(r)
            if kind:
                rows.append({
                    "구분": kind,
                    "요청품목": "Y" if kind in REQUESTED else "",
                    "상품명": r["name"],
                    "가격(원)": r["price"],
                    "행사": r["event"],
                    "NEW/BEST": r["tag"],
                    "재고(포켓CU 확인)": "",
                    "바코드": r["barcode"],
                    "홈페이지 분류": r["category"],
                    "이미지": r["image"],
                })
    rows.sort(key=lambda r: (ORDER.index(r["구분"]), r["상품명"]))
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for kind in ORDER:
        print(f"{kind}: {sum(r['구분'] == kind for r in rows)}개", file=sys.stderr)
    print(f"총 {len(rows)}개 -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

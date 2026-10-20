"""피그마 디자인 웹앱(app/) 데이터 빌드.

- 재고확인표 CSV → app/js/menu.js (큰 카테고리 / 상세 상품명)
- 배경 제거한 상품 이미지 → app/img/<바코드>.webp

  python build_app.py [이미지 폴더, 기본 data/cutout]
"""
import csv
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CSV = ROOT / "한신대60주년관점_재고확인표.csv"
SRC_IMG = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "cutout"
APP = ROOT / "app"

# CU 간편식 이름 앞 머리말. 화면에는 떼고, 일부는 큰 카테고리를 정하는 데 쓴다.
TYPE_PREFIX = {"샌", "햄", "핫", "김", "삼", "빅삼", "주", "도", "샐", "면", "튀김", "해동", "오뎅"}
PREFIX_CATEGORY = {"샐": "샐러드", "면": "면요리", "오뎅": "어묵"}
# 디핑소스처럼 한 끼가 아닌 것, 이름 끝이 '제주'인 제주 지역 생산 간편식(오산 매장엔 없음)
EXCLUDE = re.compile(r"소스|제주[0-9N]?$")


def split_name(raw):
    head, name = (raw.split(")", 1) if ")" in raw else ("", raw))
    if head and head not in TYPE_PREFIX:
        head = ""  # 농심), 오뚜기) 같은 제조사 표기
    name = re.sub(r"(?<=[가-힣])[0-9N]$", "", name)  # 끝의 버전 표기(…샌드1, …바N)
    return head, name


def main():
    (APP / "img").mkdir(parents=True, exist_ok=True)
    (APP / "js").mkdir(parents=True, exist_ok=True)
    menu = []
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if EXCLUDE.search(r["상품명"]):
                continue
            src = SRC_IMG / f"{r['바코드']}.webp"
            if not src.exists():
                continue
            prefix, name = split_name(r["상품명"])
            category = PREFIX_CATEGORY.get(prefix, r["구분"])
            shutil.copy2(src, APP / "img" / src.name)
            menu.append([r["바코드"], category, name])

    js = "// build_app.py가 만든 파일. [바코드, 큰 카테고리, 상세 상품명]\n"
    js += "window.MENU = " + json.dumps(menu, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (APP / "js" / "menu.js").write_text(js, encoding="utf-8")
    print(f"메뉴 {len(menu)}개 -> app/js/menu.js, 이미지 -> app/img/ (원본: {SRC_IMG})")


if __name__ == "__main__":
    main()

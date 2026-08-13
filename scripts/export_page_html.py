import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_export_tools.config import get_client, PROJECT_ROOT
from pdf_export_tools.notion_api import extract_id
from pdf_export_tools.html_export import build_page_html


def main():
    parser = argparse.ArgumentParser(description="Notion 페이지를 PDF 추출용 HTML로 변환합니다.")
    parser.add_argument("page_id", help="Notion 페이지 ID 또는 URL")
    parser.add_argument("-o", "--output", default=None, help="출력 HTML 경로")
    args = parser.parse_args()

    notion = get_client()
    page_id = extract_id(args.page_id)

    print(f"블록 수집 중: {page_id}", file=sys.stderr)
    title, html_doc = build_page_html(notion, page_id)

    output_path = args.output or f"{re.sub(r'[^0-9A-Za-z가-힣_ -]', '', title).strip() or 'notion_page'}.html"
    if not os.path.isabs(output_path):
        output_path = os.path.join(PROJECT_ROOT, output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print(f"완료: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

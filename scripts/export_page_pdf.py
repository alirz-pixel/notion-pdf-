import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_export_tools.config import get_client, PROJECT_ROOT
from pdf_export_tools.notion_api import extract_id
from pdf_export_tools.html_export import build_page_html

CHROMIUM_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser(explicit_path=None):
    if explicit_path:
        if os.path.isfile(explicit_path):
            return explicit_path
        raise FileNotFoundError(f"지정한 브라우저 경로를 찾을 수 없습니다: {explicit_path}")

    for path in CHROMIUM_CANDIDATES:
        if os.path.isfile(path):
            return path

    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Chrome 또는 Edge 실행 파일을 찾을 수 없습니다. --browser 옵션으로 경로를 직접 지정해주세요."
    )


def override_font_sizes(html_doc, body_size=None, code_size=None):
    if body_size is None and code_size is None:
        return html_doc

    def repl(match):
        block = match.group(0)
        if body_size is not None:
            block = re.sub(r"--body-font-size:\s*\d+px;", f"--body-font-size: {body_size}px;", block)
        if code_size is not None:
            block = re.sub(r"--code-font-size:\s*\d+px;", f"--code-font-size: {code_size}px;", block)
        return block

    return re.sub(r":root\s*\{[^}]*\}", repl, html_doc, count=1)


def html_to_pdf(html_path, pdf_path, browser_path, timeout=120):
    file_url = "file:///" + html_path.replace("\\", "/")

    # A dedicated --user-data-dir is essential: without it, a headless
    # invocation launched while a normal Chrome window is already open just
    # gets silently forwarded to that existing instance (same default
    # profile) and exits doing nothing — the classic "blank PDF" symptom.
    # We also deliberately do NOT pass --virtual-time-budget together with
    # --print-to-pdf: that combination is known to snapshot the page before
    # layout/paint has actually finished, producing blank output even when
    # everything else is correct. Letting Chrome use its normal
    # page-load-complete signal is what actually works reliably.
    with tempfile.TemporaryDirectory(prefix="notion_pdf_profile_") as profile_dir:
        cmd = [
            browser_path,
            "--headless=new",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            file_url,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )

    if result.returncode != 0 or not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise RuntimeError(
            f"PDF 변환 실패 (exit={result.returncode})\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def main():
    parser = argparse.ArgumentParser(description="Notion 페이지를 바로 PDF로 변환합니다 (HTML은 임시로만 생성됨).")
    parser.add_argument("page_id", help="Notion 페이지 ID 또는 URL")
    parser.add_argument("-o", "--output", default=None, help="출력 PDF 경로")
    parser.add_argument("--browser", default=None, help="Chrome/Edge 실행 파일 경로 (자동 탐지 실패 시 지정)")
    parser.add_argument("--body-size", type=int, default=None, help="본문 글자 크기(px) 오버라이드 (기본 13px)")
    parser.add_argument("--code-size", type=int, default=None, help="코드 블럭 글자 크기(px) 오버라이드 (기본 13px)")
    parser.add_argument("--keep-html", action="store_true", help="변환에 사용한 중간 HTML 파일을 지우지 않고 남겨둠")
    args = parser.parse_args()

    notion = get_client()
    page_id = extract_id(args.page_id)

    print(f"블록 수집 중: {page_id}", file=sys.stderr)
    title, html_doc = build_page_html(notion, page_id)
    html_doc = override_font_sizes(html_doc, args.body_size, args.code_size)

    safe_title = re.sub(r"[^0-9A-Za-z가-힣_ -]", "", title).strip() or "notion_page"
    pdf_path = args.output or f"{safe_title}.pdf"
    if not os.path.isabs(pdf_path):
        pdf_path = os.path.join(PROJECT_ROOT, pdf_path)

    if args.keep_html:
        html_path = os.path.splitext(pdf_path)[0] + ".html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
    else:
        fd, html_path = tempfile.mkstemp(suffix=".html", prefix="notion_export_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_doc)

    try:
        browser_path = find_browser(args.browser)
        print(f"PDF 변환 중 ({os.path.basename(browser_path)} 사용)...", file=sys.stderr)
        html_to_pdf(html_path, pdf_path, browser_path)
    finally:
        if not args.keep_html and os.path.exists(html_path):
            os.remove(html_path)

    print(f"완료: {pdf_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

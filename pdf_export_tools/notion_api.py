import re

ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def extract_id(raw):
    raw = raw.strip()
    m = ID_PATTERN.search(raw)
    return m.group(0) if m else raw


def iter_children(notion, block_id):
    cursor = None
    while True:
        resp = notion.blocks.children.list(block_id=block_id, start_cursor=cursor, page_size=100)
        for block in resp.get("results", []):
            yield block
        if resp.get("has_more"):
            cursor = resp.get("next_cursor")
        else:
            break


def get_page_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            rich_text = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in rich_text) or "(제목 없음)"
    return "(제목 없음)"

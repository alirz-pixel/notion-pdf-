import html
import json
import os
import re

from .config import VENDOR_DIR
from .notion_api import iter_children, get_page_title
from .richtext import rich_text_to_html, code_rich_text_to_data
from .link_metadata import fetch_link_metadata

# Populated by collect_headings() before rendering, consumed by the
# table_of_contents block renderer.
HEADINGS = []

# Notion language name -> a language hljs's "common" bundle actually ships.
# Anything not covered here falls back to 'plaintext' so log dumps and
# unrecognized languages never get auto-detect-guessed into the wrong
# language by highlight.js.
HLJS_LANG_ALIAS = {
    "plain text": "plaintext", "": "plaintext", "plaintext": "plaintext",
    "c++": "cpp", "cpp": "cpp",
    "c#": "csharp", "csharp": "csharp",
    "objective-c": "objectivec", "objectivec": "objectivec",
    "shell": "bash", "sh": "bash", "bash": "bash", "shellscript": "bash",
    "docker": "dockerfile", "dockerfile": "dockerfile",
    "vb.net": "vbnet", "visual basic": "vbnet", "vbnet": "vbnet",
    "markup": "xml", "html": "xml", "xml": "xml",
    "notion formula": "plaintext", "excel formula": "plaintext", "mermaid": "plaintext",
    "protobuf": "plaintext", "coffeescript": "plaintext", "f#": "plaintext",
}
HLJS_KNOWN_LANGS = {
    "sql", "python", "bash", "javascript", "typescript", "java", "json", "yaml",
    "xml", "css", "c", "cpp", "csharp", "go", "rust", "ruby", "php", "kotlin",
    "swift", "dockerfile", "ini", "markdown", "plaintext", "diff", "makefile",
    "r", "scala", "perl", "lua", "powershell", "graphql", "objectivec", "vbnet",
}


def hljs_language_class(notion_language):
    key = (notion_language or "").strip().lower()
    lang = HLJS_LANG_ALIAS.get(key, key)
    if lang not in HLJS_KNOWN_LANGS:
        lang = "plaintext"
    return lang


def heading_anchor_id(block):
    return "h-" + re.sub(r"[^0-9a-fA-F]", "", block["id"])[:32]


def collect_headings(blocks):
    """Walk the block tree in document order and record every heading, so
    a table_of_contents block can render the same list Notion would show."""
    for block in blocks:
        block_type = block.get("type")
        if block_type in ("heading_1", "heading_2", "heading_3"):
            text = rich_text_to_html(block[block_type].get("rich_text", []))
            HEADINGS.append((int(block_type[-1]), text, heading_anchor_id(block)))
        collect_headings(block.get("_children", []))


def fetch_tree(notion, block_id, _depth=0, _seen=None):
    if _seen is None:
        _seen = set()
    blocks = []
    for block in iter_children(notion, block_id):
        block_type = block.get("type")

        if block_type == "synced_block":
            synced_from = block.get("synced_block", {}).get("synced_from")
            source_id = synced_from["block_id"] if synced_from else block["id"]
            if source_id not in _seen:
                _seen.add(source_id)
                block["_children"] = fetch_tree(notion, source_id, _depth + 1, _seen)
            else:
                block["_children"] = []
        elif block_type in ("child_page", "child_database"):
            block["_children"] = []
        elif block.get("has_children"):
            block["_children"] = fetch_tree(notion, block["id"], _depth + 1, _seen)
        else:
            block["_children"] = []

        blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# Block -> HTML
# ---------------------------------------------------------------------------

def render_children(blocks):
    """Render a list of sibling blocks to HTML, grouping consecutive
    list items into <ul>/<ol> and consecutive to_do items into a list."""
    html_parts = []
    i = 0
    n = len(blocks)
    while i < n:
        block = blocks[i]
        block_type = block.get("type")

        if block_type == "bulleted_list_item":
            items = []
            while i < n and blocks[i].get("type") == "bulleted_list_item":
                items.append(render_list_item(blocks[i]))
                i += 1
            html_parts.append(f"<ul>{''.join(items)}</ul>")
            continue

        if block_type == "numbered_list_item":
            items = []
            while i < n and blocks[i].get("type") == "numbered_list_item":
                items.append(render_list_item(blocks[i]))
                i += 1
            html_parts.append(f"<ol>{''.join(items)}</ol>")
            continue

        if block_type == "to_do":
            items = []
            while i < n and blocks[i].get("type") == "to_do":
                items.append(render_todo_item(blocks[i]))
                i += 1
            html_parts.append(f"<ul class=\"todo-list\">{''.join(items)}</ul>")
            continue

        html_parts.append(render_block(block))
        i += 1

    return "".join(html_parts)


def render_list_item(block):
    block_type = block["type"]
    text = rich_text_to_html(block[block_type].get("rich_text", []))
    children_html = render_children(block.get("_children", []))
    return f"<li>{text}{children_html}</li>"


def render_todo_item(block):
    data = block["to_do"]
    checked = "checked" if data.get("checked") else ""
    text = rich_text_to_html(data.get("rich_text", []))
    children_html = render_children(block.get("_children", []))
    return f'<li><label><input type="checkbox" disabled {checked}/> {text}</label>{children_html}</li>'


def render_table(block):
    data = block["table"]
    has_col_header = data.get("has_column_header", False)
    has_row_header = data.get("has_row_header", False)
    rows = block.get("_children", [])

    col_count = data.get("table_width") or (
        len(rows[0].get("table_row", {}).get("cells", [])) if rows else 0
    )

    body_rows = []
    for r_idx, row in enumerate(rows):
        cells = row.get("table_row", {}).get("cells", [])
        cell_html = []
        for c_idx, cell in enumerate(cells):
            content = rich_text_to_html(cell)
            is_header = (has_col_header and r_idx == 0) or (has_row_header and c_idx == 0)
            tag = "th" if is_header else "td"
            # A drag handle on every cell of every row (not just the header)
            # so columns stay resizable even when the table has no header row.
            # The last column gets one too — dragging it just grows/shrinks
            # that column without needing a neighbor to borrow width from.
            handle = '<span class="col-resize-handle"></span>'
            cell_html.append(f"<{tag}>{content}{handle}</{tag}>")
        body_rows.append(f"<tr>{''.join(cell_html)}</tr>")

    colgroup = "".join("<col/>" for _ in range(col_count))
    return (
        f'<div class="table-wrap"><table class="notion-table">'
        f"<colgroup>{colgroup}</colgroup>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div>"
    )


CALLOUT_COLORS = {
    "gray_background": "#f1f1ef", "brown_background": "#f4eeee",
    "orange_background": "#fbecdd", "yellow_background": "#fbf3db",
    "green_background": "#edf3ec", "blue_background": "#e7f3f8",
    "purple_background": "#f6f3f9", "pink_background": "#faf1f5",
    "red_background": "#fdebec", "default": "#f7f6f3",
}


_LINK_METADATA_CACHE = {}


def render_link_card(url, caption_html=""):
    """Bookmark/embed/link_preview blocks only carry a bare URL in the API —
    Notion's own rich card (favicon/title/description) comes from a crawl it
    already did when the link was pasted, which the API doesn't expose. We
    replicate that by fetching the URL ourselves at export time; if that
    fails (unreachable, blocked, non-HTML) we fall back to a plain link."""
    caption_block = f'<div class="code-caption">{caption_html}</div>' if caption_html else ""
    if not url:
        return caption_block

    if url not in _LINK_METADATA_CACHE:
        _LINK_METADATA_CACHE[url] = fetch_link_metadata(url)
    meta = _LINK_METADATA_CACHE[url]

    safe_url = html.escape(url, quote=True)
    if not meta:
        return f'<p class="media-link">🔗 <a href="{safe_url}" target="_blank">{html.escape(url)}</a></p>{caption_block}'

    favicon_html = ""
    if meta.get("favicon"):
        favicon_html = f'<img class="link-card-favicon" src="{html.escape(meta["favicon"], quote=True)}" alt=""/>'
    desc_html = ""
    if meta.get("description"):
        desc_html = f'<div class="link-card-desc">{html.escape(meta["description"])}</div>'
    thumb_html = ""
    if meta.get("image"):
        thumb_html = f'<div class="link-card-thumb"><img src="{html.escape(meta["image"], quote=True)}" alt=""/></div>'

    return (
        f'<a class="link-card" href="{safe_url}" target="_blank" rel="noopener">'
        f'<div class="link-card-body">'
        f'<div class="link-card-title-row">{favicon_html}<span class="link-card-title">{html.escape(meta["title"])}</span></div>'
        f"{desc_html}"
        f'<div class="link-card-url">{html.escape(meta["url"])}</div>'
        f"</div>{thumb_html}</a>{caption_block}"
    )


def render_block(block):
    block_type = block.get("type")
    children = block.get("_children", [])
    children_html = render_children(children)

    if block_type == "paragraph":
        text = rich_text_to_html(block["paragraph"].get("rich_text", []))
        if not text.strip():
            return "<p>&nbsp;</p>"
        return f"<p>{text}{children_html}</p>"

    if block_type in ("heading_1", "heading_2", "heading_3"):
        level = block_type[-1]
        text = rich_text_to_html(block[block_type].get("rich_text", []))
        anchor = heading_anchor_id(block)
        return f'<h{level} id="{anchor}">{text}</h{level}>{children_html}'

    if block_type == "quote":
        text = rich_text_to_html(block["quote"].get("rich_text", []))
        return f"<blockquote>{text}{children_html}</blockquote>"

    if block_type == "callout":
        data = block["callout"]
        text = rich_text_to_html(data.get("rich_text", []))
        icon = data.get("icon") or {}
        emoji = icon.get("emoji", "💡") if icon.get("type") == "emoji" else "💡"
        color = CALLOUT_COLORS.get(data.get("color", "default"), CALLOUT_COLORS["default"])
        return (
            f'<div class="callout" style="background:{color}">'
            f'<div class="callout-icon">{emoji}</div>'
            f'<div class="callout-text">{text}{children_html}</div></div>'
        )

    if block_type == "toggle":
        text = rich_text_to_html(block["toggle"].get("rich_text", []))
        return f"<details><summary>{text}</summary>{children_html}</details>"

    if block_type == "code":
        data = block["code"]
        code_text, fmt_segments = code_rich_text_to_data(data.get("rich_text", []))
        escaped = html.escape(code_text)
        lang_class = hljs_language_class(data.get("language"))
        caption = rich_text_to_html(data.get("caption", []))
        caption_html = f'<div class="code-caption">{caption}</div>' if caption else ""
        fmt_attr = ""
        if fmt_segments:
            fmt_json = html.escape(json.dumps(fmt_segments, ensure_ascii=False), quote=True)
            fmt_attr = f' data-fmt="{fmt_json}"'
        return (
            f'<div class="code-block"><pre><code class="language-{lang_class}"{fmt_attr}>'
            f"{escaped}</code></pre>{caption_html}</div>"
        )

    if block_type == "divider":
        return "<hr/>"

    if block_type == "image":
        data = block["image"]
        url = data.get("external", {}).get("url") or data.get("file", {}).get("url", "")
        caption = rich_text_to_html(data.get("caption", []))
        caption_html = f'<figcaption>{caption}</figcaption>' if caption else ""
        return f'<figure class="notion-image"><img src="{html.escape(url, quote=True)}" loading="lazy"/>{caption_html}</figure>'

    if block_type in ("video", "file", "pdf"):
        data = block[block_type]
        url = data.get("external", {}).get("url") or data.get("file", {}).get("url", "")
        return f'<p class="media-link">📎 <a href="{html.escape(url, quote=True)}" target="_blank">{block_type} 첨부</a></p>'

    if block_type in ("bookmark", "embed", "link_preview"):
        data = block[block_type]
        url = data.get("url", "")
        caption = rich_text_to_html(data.get("caption", [])) if block_type != "link_preview" else ""
        return render_link_card(url, caption)

    if block_type == "equation":
        expression = block["equation"].get("expression", "")
        return f'<pre class="equation-block">{html.escape(expression)}</pre>'

    if block_type == "table":
        return render_table(block)

    if block_type == "column_list":
        columns_html = "".join(f'<div class="notion-column">{render_children(c.get("_children", []))}</div>' for c in children)
        return f'<div class="notion-column-list">{columns_html}</div>'

    if block_type == "column":
        return children_html

    if block_type == "synced_block":
        return children_html

    if block_type == "table_of_contents":
        if not HEADINGS:
            return ""
        min_level = min(lvl for lvl, _, _ in HEADINGS)
        items = "".join(
            f'<div class="toc-item toc-level-{lvl - min_level}">'
            f'<a href="#{anchor}">{text}</a></div>'
            for lvl, text, anchor in HEADINGS
        )
        return f'<div class="toc">{items}</div>'

    if block_type == "child_page":
        title = block.get("child_page", {}).get("title", "하위 페이지")
        return f'<p class="child-page">📄 {html.escape(title)} (하위 페이지 - 내용 미포함)</p>'

    if block_type == "child_database":
        title = block.get("child_database", {}).get("title", "데이터베이스")
        return f'<p class="child-page">🗄 {html.escape(title)} (데이터베이스 - 내용 미포함)</p>'

    if block_type == "breadcrumb":
        return ""

    # fallback: try rich_text if present
    data = block.get(block_type, {})
    if isinstance(data, dict) and "rich_text" in data:
        text = rich_text_to_html(data.get("rich_text", []))
        return f'<p class="unsupported">{text}</p>'

    return f'<p class="unsupported">[지원되지 않는 블록: {html.escape(block_type or "unknown")}]</p>'


# ---------------------------------------------------------------------------
# Vendor assets (highlight.js, bundled offline so the exported HTML needs no
# network access to render or print with syntax highlighting)
# ---------------------------------------------------------------------------

def _read_vendor(filename):
    with open(os.path.join(VENDOR_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def load_highlight_assets():
    return {
        "hljs_js": _read_vendor("highlight.min.js"),
        "hljs_css": _read_vendor("hljs-theme.min.css"),
    }


# ---------------------------------------------------------------------------
# HTML document
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>
  :root {{
    --body-font-size: 13px;
    --code-font-size: 13px;
    --body-font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Segoe UI", Helvetica, Arial, sans-serif;
    --code-font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }}

  * {{ box-sizing: border-box; }}

  /* Notion's own text-selection tint instead of the browser default blue.
     Kept translucent (not opaque) so it layers on top of manual highlight
     backgrounds (.hl-*) rather than blotting them out — the user's own
     formatting stays visible underneath while the selection is shown. */
  ::selection {{ background: rgba(35, 131, 226, 0.28); color: inherit; }}
  ::-moz-selection {{ background: rgba(35, 131, 226, 0.28); color: inherit; }}

  /* Notion links: plain text color (no browser blue), a muted underline,
     and a soft gray hover highlight instead of a color change. */
  #page a {{
    color: inherit;
    text-decoration: underline;
    text-decoration-color: rgba(55, 53, 47, 0.35);
    text-underline-offset: 2px;
    cursor: pointer;
    border-radius: 3px;
    transition: background 100ms ease-in;
  }}
  #page a:hover {{
    background: rgba(55, 53, 47, 0.08);
    text-decoration-color: currentColor;
  }}

  body {{
    font-family: var(--body-font-family);
    color: #37352f;
    background: #fff;
    margin: 0;
    padding: 0;
  }}

  #toolbar {{
    position: sticky;
    top: 0;
    z-index: 100;
    background: #f7f6f3;
    border-bottom: 1px solid #e3e2e0;
    padding: 12px 24px;
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
    align-items: center;
    font-size: 13px;
  }}

  #toolbar .control {{ display: flex; align-items: center; gap: 8px; }}
  #toolbar label {{ font-weight: 600; white-space: nowrap; }}
  #toolbar input[type="range"] {{ width: 140px; }}
  #toolbar input[type="number"] {{ width: 56px; }}
  #toolbar select {{
    font-size: 13px;
    padding: 4px 6px;
    border: 1px solid #e3e2e0;
    border-radius: 4px;
    background: white;
  }}
  #toolbar button {{
    background: #2383e2;
    color: white;
    border: none;
    padding: 8px 18px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }}
  #toolbar button:hover {{ background: #1a6fc4; }}
  .toolbar-link-btn {{
    margin-left: auto;
    background: white;
    color: #37352f;
    border: 1px solid #e3e2e0;
    padding: 8px 18px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    white-space: nowrap;
  }}
  .toolbar-link-btn:hover {{ background: #f1f1ef; }}

  #page {{
    max-width: 900px;
    margin: 0 auto;
    padding: 48px 64px 96px;
    font-size: var(--body-font-size);
    line-height: 1.6;
  }}

  #page h1.page-title {{
    font-size: calc(var(--body-font-size) * 2.2);
    font-weight: 700;
    margin: 0 0 8px;
  }}

  #page h1 {{ font-size: calc(var(--body-font-size) * 1.6); margin: 1.4em 0 0.3em; font-weight: 700; }}
  #page h2 {{ font-size: calc(var(--body-font-size) * 1.35); margin: 1.2em 0 0.3em; font-weight: 700; }}
  #page h3 {{ font-size: calc(var(--body-font-size) * 1.15); margin: 1em 0 0.3em; font-weight: 600; }}

  #page p {{ margin: 0.35em 0; }}
  #page ul, #page ol {{ margin: 0.2em 0; padding-left: 1.6em; }}
  #page li {{ margin: 0.15em 0; }}
  #page .todo-list {{ list-style: none; padding-left: 0.4em; }}

  #page blockquote {{
    border-left: 3px solid #37352f;
    margin: 0.5em 0;
    padding-left: 1em;
    color: #37352f;
  }}

  #page hr {{ border: none; border-top: 1px solid #e3e2e0; margin: 1.5em 0; }}

  #page .callout {{
    display: flex;
    gap: 10px;
    padding: 14px 16px;
    border-radius: 6px;
    margin: 0.6em 0;
  }}
  #page .callout-icon {{ flex-shrink: 0; }}

  #page details {{
    margin: 0.4em 0;
    padding: 4px 0;
  }}
  #page details summary {{
    cursor: pointer;
    font-weight: 500;
  }}
  #page details > *:not(summary) {{ margin-left: 1.4em; }}

  .code-block {{
    background: #f7f6f3;
    border-radius: 6px;
    margin: 0.6em 0;
    overflow: hidden;
  }}
  .code-block pre {{
    margin: 0;
    padding: 16px 20px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .code-block code, .code-block pre {{
    font-family: var(--code-font-family);
    font-size: var(--code-font-size);
    line-height: 1.5;
  }}
  .code-caption {{ padding: 0 20px 12px; font-size: 0.85em; color: #787774; }}
  .inline-code, .inline-eq {{
    font-family: var(--code-font-family);
    font-size: var(--body-font-size);
    background: rgba(135, 131, 120, 0.15);
    color: #eb5757;
    padding: 0.15em 0.4em;
    border-radius: 4px;
  }}
  .inline-eq {{ color: inherit; }}

  /* highlight.js theme (vendored, see wiki_doc_tools/vendor/hljs-theme.min.css)
     is injected above this block; these overrides make it sit inside our
     existing .code-block box instead of drawing its own background/padding. */
  .code-block pre code.hljs {{
    background: transparent;
    padding: 0;
    display: block;
    overflow-x: visible;
  }}

  .notion-image {{ margin: 0.8em 0; }}
  .notion-image img {{ max-width: 100%; border-radius: 4px; }}
  .notion-image figcaption {{ font-size: 0.85em; color: #787774; margin-top: 4px; }}

  .table-wrap {{ position: relative; overflow-x: auto; margin: 0.6em 0; }}
  table.notion-table {{
    border-collapse: collapse;
    table-layout: auto;
    width: auto;
    max-width: 100%;
    margin: 0;
    font-size: var(--body-font-size);
  }}
  table.notion-table.resized {{ table-layout: fixed; }}
  table.notion-table td, table.notion-table th {{
    position: relative;
    border: 1px solid #e3e2e0;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
    white-space: normal;
    overflow-wrap: break-word;
    overflow: hidden;
  }}
  table.notion-table th {{ background: #f7f6f3; font-weight: 600; }}

  .col-resize-handle {{
    position: absolute;
    top: 0;
    right: -3px;
    width: 6px;
    height: 100%;
    cursor: col-resize;
    z-index: 1;
    user-select: none;
  }}
  .col-resize-handle:hover, .col-resize-handle.active {{
    background: #2383e2;
  }}
  .row-resize-handle {{
    position: absolute;
    left: 0;
    height: 6px;
    width: 100%;
    cursor: row-resize;
    z-index: 2;
    user-select: none;
  }}
  .row-resize-handle:hover, .row-resize-handle.active {{
    background: #2383e2;
  }}
  @media print {{
    .col-resize-handle, .row-resize-handle {{ display: none; }}
  }}

  .notion-column-list {{ display: flex; gap: 24px; margin: 0.6em 0; }}
  .notion-column {{ flex: 1; min-width: 0; }}

  .media-link, .unsupported, .child-page {{ color: #787774; font-size: 0.9em; }}
  .equation-block {{ background: #f7f6f3; padding: 10px 14px; border-radius: 6px; font-family: monospace; }}

  .mention-chip {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: rgba(55, 53, 47, 0.08);
    border-radius: 4px;
    padding: 1px 6px 1px 4px;
    text-decoration: none;
    font-weight: 500;
  }}
  a .mention-chip {{ color: inherit; }}
  .mention-chip:hover {{ background: rgba(55, 53, 47, 0.16); }}
  .mention-icon {{ font-size: 0.9em; }}

  .inline-link-with-icon {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    vertical-align: -3px;
  }}
  .link-favicon {{ width: 14px; height: 14px; flex-shrink: 0; }}

  .link-card {{
    display: flex;
    border: 1px solid #e3e2e0;
    border-radius: 6px;
    margin: 0.6em 0;
    overflow: hidden;
    text-decoration: none !important;
    color: inherit;
    break-inside: avoid;
  }}
  .link-card:hover {{ background: #f7f6f3; }}
  .link-card-body {{ flex: 1; min-width: 0; padding: 10px 14px; }}
  .link-card-title-row {{ display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }}
  .link-card-favicon {{ width: 16px; height: 16px; flex-shrink: 0; }}
  .link-card-title {{
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .link-card-desc {{
    font-size: 0.85em;
    color: #787774;
    margin-bottom: 6px;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}
  .link-card-url {{
    font-size: 0.8em;
    color: #9b9a97;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .link-card-thumb {{
    width: 120px;
    flex-shrink: 0;
    background: #f7f6f3;
  }}
  .link-card-thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}

  .toc {{ margin: 0.4em 0; }}
  .toc-item {{ padding: 4px 0; }}
  .toc-item a {{ color: #37352f; text-decoration: none; border-bottom: 1px solid #e3e2e0; }}
  .toc-item a:hover {{ border-bottom-color: #37352f; }}
  .toc-level-0 {{ margin-left: 0; }}
  .toc-level-1 {{ margin-left: 1.4em; }}
  .toc-level-2 {{ margin-left: 2.8em; }}

  .color-gray {{ color: #9b9a97; }}
  .color-brown {{ color: #64473a; }}
  .color-orange {{ color: #d9730d; }}
  .color-yellow {{ color: #dfab01; }}
  .color-green {{ color: #0f7b6c; }}
  .color-blue {{ color: #0b6e99; }}
  .color-purple {{ color: #6940a5; }}
  .color-pink {{ color: #ad1a72; }}
  .color-red {{ color: #e03e3e; }}

  .hl-gray {{ background: #e3e2e0; border-radius: 3px; padding: 0 1px; }}
  .hl-brown {{ background: #e9e5e3; border-radius: 3px; padding: 0 1px; }}
  .hl-orange {{ background: #fadec9; border-radius: 3px; padding: 0 1px; }}
  .hl-yellow {{ background: #fdecc8; border-radius: 3px; padding: 0 1px; }}
  .hl-green {{ background: #dbeddb; border-radius: 3px; padding: 0 1px; }}
  .hl-blue {{ background: #d3e5ef; border-radius: 3px; padding: 0 1px; }}
  .hl-purple {{ background: #e8deee; border-radius: 3px; padding: 0 1px; }}
  .hl-pink {{ background: #f5e0e9; border-radius: 3px; padding: 0 1px; }}
  .hl-red {{ background: #ffe2dd; border-radius: 3px; padding: 0 1px; }}

  html {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
    color-adjust: exact;
  }}

  @media print {{
    #toolbar {{ display: none !important; }}
    #page {{ padding: 0 24px; max-width: none; }}
    a {{ color: inherit; text-decoration: underline; }}

    /* Chrome refuses to paginate a box with overflow:hidden/border-radius —
       it force-pushes the whole element to the next page (huge blank gap)
       and can mis-paint the background at the fragment seam. Long code
       blocks need to be allowed to break across pages, so flatten those
       properties for print and let the block split naturally. */
    .code-block {{
      overflow: visible !important;
      border-radius: 0 !important;
      break-inside: auto !important;
      page-break-inside: auto !important;
    }}
    .code-block pre {{
      overflow: visible !important;
    }}

    /* Small elements are safe (and nicer) to keep intact on one page. */
    table.notion-table, .callout, .link-card {{
      break-inside: avoid;
      page-break-inside: avoid;
    }}

    * {{
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
      color-adjust: exact !important;
    }}
  }}

  {hljs_css}
</style>
</head>
<body>

<div id="toolbar" class="no-print">
  <div class="control">
    <label for="bodySize">본문 글자 크기</label>
    <input type="range" id="bodySize" min="10" max="28" step="1" value="13"/>
    <input type="number" id="bodySizeNum" min="10" max="28" step="1" value="13"/>
    <span>px</span>
  </div>
  <div class="control">
    <label for="codeSize">코드 블럭 글자 크기</label>
    <input type="range" id="codeSize" min="8" max="24" step="1" value="13"/>
    <input type="number" id="codeSizeNum" min="8" max="24" step="1" value="13"/>
    <span>px</span>
  </div>
  <div class="control">
    <label for="bodyFont">본문 폰트</label>
    <select id="bodyFont">
      <option value="-apple-system, &quot;Apple SD Gothic Neo&quot;, &quot;Malgun Gothic&quot;, &quot;Segoe UI&quot;, Helvetica, Arial, sans-serif" selected>시스템 기본 (고딕)</option>
      <option value="&quot;Malgun Gothic&quot;, &quot;맑은 고딕&quot;, sans-serif">맑은 고딕</option>
      <option value="&quot;Nanum Gothic&quot;, &quot;나눔고딕&quot;, sans-serif">나눔고딕</option>
      <option value="&quot;Batang&quot;, &quot;바탕체&quot;, &quot;Nanum Myeongjo&quot;, serif">바탕체 (명조)</option>
      <option value="Georgia, &quot;Times New Roman&quot;, serif">Georgia (세리프)</option>
      <option value="Arial, Helvetica, sans-serif">Arial</option>
    </select>
  </div>
  <div class="control">
    <label for="codeFont">코드 폰트</label>
    <select id="codeFont">
      <option value="&quot;SFMono-Regular&quot;, Consolas, &quot;Liberation Mono&quot;, Menlo, monospace" selected>기본 (Consolas)</option>
      <option value="&quot;D2Coding&quot;, Consolas, monospace">D2Coding</option>
      <option value="&quot;Courier New&quot;, Courier, monospace">Courier New</option>
      <option value="&quot;Cascadia Code&quot;, Consolas, monospace">Cascadia Code</option>
      <option value="&quot;Fira Code&quot;, Consolas, monospace">Fira Code</option>
      <option value="Menlo, Monaco, monospace">Menlo</option>
    </select>
  </div>
  <a id="notionLink" class="toolbar-link-btn" href="{notion_url}" target="_blank" rel="noopener">노션 바로가기</a>
  <button id="exportBtn">PDF 추출</button>
</div>

<div id="page">
  <h1 class="page-title">{title}</h1>
  {body}
</div>

<script>
{hljs_js}
</script>
<script>
  // Overlays manual Notion rich-text formatting (bold/italic/underline/
  // strikethrough/color) on top of the syntax highlighter's own spans, so
  // formatting the user applied by hand inside a code block still shows up.
  // Segments are character-offset ranges into the block's raw text; after
  // hljs rewrites the element's innerHTML, the concatenation of its text
  // nodes in document order still equals that same raw text (hljs only
  // wraps runs in spans, it never adds/removes characters), so we can walk
  // the text nodes with a running offset and slice them against the ranges.
  function applyManualCodeFormatting(el) {{
    const raw = el.getAttribute('data-fmt');
    if (!raw) return;
    let segments;
    try {{ segments = JSON.parse(raw); }} catch (e) {{ return; }}
    if (!segments || !segments.length) return;

    function segAt(pos) {{
      for (const s of segments) {{
        if (pos >= s.s && pos < s.e) return s;
      }}
      return null;
    }}

    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    let node;
    while ((node = walker.nextNode())) textNodes.push(node);

    let offset = 0;
    textNodes.forEach(tn => {{
      const s = tn.nodeValue;
      const start = offset;
      offset += s.length;

      let needsSplit = false;
      for (let k = 0; k < s.length; k++) {{
        if (segAt(start + k)) {{ needsSplit = true; break; }}
      }}
      if (!needsSplit) return;

      const frag = document.createDocumentFragment();
      let i = 0;
      while (i < s.length) {{
        const segHere = segAt(start + i);
        let j = i + 1;
        while (j < s.length && segAt(start + j) === segHere) j++;
        const piece = s.slice(i, j);

        if (!segHere) {{
          frag.appendChild(document.createTextNode(piece));
        }} else {{
          let wrapped = document.createTextNode(piece);
          if (segHere.strikethrough) {{ const w = document.createElement('s'); w.appendChild(wrapped); wrapped = w; }}
          if (segHere.underline) {{ const w = document.createElement('u'); w.appendChild(wrapped); wrapped = w; }}
          if (segHere.italic) {{ const w = document.createElement('em'); w.appendChild(wrapped); wrapped = w; }}
          if (segHere.bold) {{ const w = document.createElement('strong'); w.appendChild(wrapped); wrapped = w; }}
          if (segHere.color) {{
            const w = document.createElement('span');
            w.className = segHere.color.endsWith('_background')
              ? 'hl-' + segHere.color.slice(0, -('_background'.length))
              : 'color-' + segHere.color;
            w.appendChild(wrapped);
            wrapped = w;
          }}
          frag.appendChild(wrapped);
        }}
        i = j;
      }}
      tn.parentNode.replaceChild(frag, tn);
    }});
  }}

  if (window.hljs) {{
    document.querySelectorAll('.code-block pre code').forEach(el => {{
      hljs.highlightElement(el);
      applyManualCodeFormatting(el);
    }});
  }}

  // Keeps row-resize handles aligned with their row's bottom edge. Global
  // (not per-table) so font-size/font-family changes elsewhere can also
  // trigger a reflow-safe reposition.
  function repositionAllRowHandles() {{
    document.querySelectorAll('tr').forEach(tr => {{
      const h = tr._rowHandle;
      if (!h) return;
      const wrap = tr.closest('.table-wrap');
      if (!wrap) return;
      const wrapRect = wrap.getBoundingClientRect();
      const r = tr.getBoundingClientRect();
      h.style.top = (r.bottom - wrapRect.top - 3) + 'px';
    }});
  }}

  // Notion-style draggable column/row resizing for tables.
  (function initResizableTables() {{
    document.querySelectorAll('table.notion-table').forEach(table => {{
      const wrap = table.closest('.table-wrap');
      const cols = table.querySelectorAll('colgroup col');
      const rows = Array.from(table.querySelectorAll('tr'));
      const repositionRowHandles = repositionAllRowHandles;

      // --- column resize ---
      if (cols.length) {{
        table.querySelectorAll('.col-resize-handle').forEach(handle => {{
          handle.addEventListener('mousedown', e => {{
            e.preventDefault();
            const cell = handle.parentElement;
            const colIndex = Array.from(cell.parentElement.children).indexOf(cell);
            const col = cols[colIndex];
            const startX = e.clientX;
            const startWidth = cell.getBoundingClientRect().width;

            table.classList.add('resized');
            if (!col.style.width) {{
              rows.forEach(tr => {{
                const c = tr.children[colIndex];
                if (c) cols[colIndex].style.width = c.getBoundingClientRect().width + 'px';
              }});
            }}

            handle.classList.add('active');

            function onMouseMove(ev) {{
              const newWidth = Math.max(40, startWidth + (ev.clientX - startX));
              col.style.width = newWidth + 'px';
              repositionRowHandles();
            }}
            function onMouseUp() {{
              handle.classList.remove('active');
              document.removeEventListener('mousemove', onMouseMove);
              document.removeEventListener('mouseup', onMouseUp);
            }}
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
          }});
        }});
      }}

      // --- row resize ---
      if (wrap) {{
        rows.slice(0, -1).forEach(tr => {{
          const handle = document.createElement('div');
          handle.className = 'row-resize-handle';
          wrap.appendChild(handle);
          tr._rowHandle = handle;

          handle.addEventListener('mousedown', e => {{
            e.preventDefault();
            const startY = e.clientY;
            const startHeight = tr.getBoundingClientRect().height;
            handle.classList.add('active');

            function onMouseMove(ev) {{
              const newHeight = Math.max(20, startHeight + (ev.clientY - startY));
              tr.style.height = newHeight + 'px';
              Array.from(tr.children).forEach(c => {{ c.style.height = newHeight + 'px'; }});
              repositionRowHandles();
            }}
            function onMouseUp() {{
              handle.classList.remove('active');
              document.removeEventListener('mousemove', onMouseMove);
              document.removeEventListener('mouseup', onMouseUp);
            }}
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
          }});
        }});
        repositionRowHandles();
        window.addEventListener('resize', repositionRowHandles);
      }}
    }});
  }})();

  const root = document.documentElement;
  const bodyRange = document.getElementById('bodySize');
  const bodyNum = document.getElementById('bodySizeNum');
  const codeRange = document.getElementById('codeSize');
  const codeNum = document.getElementById('codeSizeNum');

  function setBodySize(v) {{
    root.style.setProperty('--body-font-size', v + 'px');
    bodyRange.value = v;
    bodyNum.value = v;
    requestAnimationFrame(repositionAllRowHandles);
  }}
  function setCodeSize(v) {{
    root.style.setProperty('--code-font-size', v + 'px');
    codeRange.value = v;
    codeNum.value = v;
    requestAnimationFrame(repositionAllRowHandles);
  }}

  bodyRange.addEventListener('input', e => setBodySize(e.target.value));
  bodyNum.addEventListener('input', e => setBodySize(e.target.value));
  codeRange.addEventListener('input', e => setCodeSize(e.target.value));
  codeNum.addEventListener('input', e => setCodeSize(e.target.value));

  document.getElementById('bodyFont').addEventListener('change', e => {{
    root.style.setProperty('--body-font-family', e.target.value);
    requestAnimationFrame(repositionAllRowHandles);
  }});
  document.getElementById('codeFont').addEventListener('change', e => {{
    root.style.setProperty('--code-font-family', e.target.value);
    requestAnimationFrame(repositionAllRowHandles);
  }});
  document.getElementById('exportBtn').addEventListener('click', () => window.print());
</script>

</body>
</html>
"""


def build_page_html(notion, page_id):
    page = notion.pages.retrieve(page_id=page_id)
    title = get_page_title(page)

    blocks = fetch_tree(notion, page_id)

    HEADINGS.clear()
    collect_headings(blocks)

    body_html = render_children(blocks)
    assets = load_highlight_assets()

    html_doc = PAGE_TEMPLATE.format(
        title=html.escape(title),
        body=body_html,
        hljs_js=assets["hljs_js"],
        hljs_css=assets["hljs_css"],
        notion_url=html.escape(page.get("url", ""), quote=True),
    )
    return title, html_doc

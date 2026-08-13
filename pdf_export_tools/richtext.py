import html


def code_rich_text_to_data(rich_text_list):
    """Flatten a code block's rich_text into plain text, plus a list of
    {s, e, bold, italic, underline, strikethrough, color} segments marking
    any manual formatting the user applied on top of the raw code — so the
    exported HTML can overlay it on top of the syntax highlighter's colors."""
    code_text = ""
    segments = []
    offset = 0
    for rt in rich_text_list or []:
        text = rt.get("plain_text", "")
        ann = rt.get("annotations", {}) or {}
        seg = {}
        if ann.get("bold"):
            seg["bold"] = True
        if ann.get("italic"):
            seg["italic"] = True
        if ann.get("underline"):
            seg["underline"] = True
        if ann.get("strikethrough"):
            seg["strikethrough"] = True
        color = ann.get("color", "default")
        if color and color != "default":
            seg["color"] = color
        if seg and text:
            seg["s"] = offset
            seg["e"] = offset + len(text)
            segments.append(seg)
        code_text += text
        offset += len(text)
    return code_text, segments


def rich_text_to_html(rich_text_list):
    parts = []
    for rt in rich_text_list or []:
        text = rt.get("plain_text", "")
        if rt.get("type") == "equation":
            text = rt.get("equation", {}).get("expression", text)
            escaped = html.escape(text)
            parts.append(f'<code class="inline-eq">{escaped}</code>')
            continue

        escaped = html.escape(text).replace("\n", "<br/>")
        ann = rt.get("annotations", {})
        href = rt.get("href")

        if ann.get("code"):
            escaped = f'<code class="inline-code">{escaped}</code>'
        if ann.get("bold"):
            escaped = f"<strong>{escaped}</strong>"
        if ann.get("italic"):
            escaped = f"<em>{escaped}</em>"
        if ann.get("strikethrough"):
            escaped = f"<s>{escaped}</s>"
        if ann.get("underline"):
            escaped = f"<u>{escaped}</u>"
        color = ann.get("color", "default")
        if color and color != "default":
            if color.endswith("_background"):
                base = color[: -len("_background")]
                escaped = f'<span class="hl-{base}">{escaped}</span>'
            else:
                escaped = f'<span class="color-{color}">{escaped}</span>'
        if href:
            safe_href = html.escape(href, quote=True)
            escaped = f'<a href="{safe_href}" target="_blank" rel="noopener">{escaped}</a>'

        parts.append(escaped)
    return "".join(parts)

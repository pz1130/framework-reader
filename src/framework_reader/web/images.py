"""Brand images uploaded by an administrator: detect raster formats and sanitize SVG."""
import xml.etree.ElementTree as ET

_SVG_FORBID = {
    "script", "foreignObject", "iframe", "object", "embed", "animate",
    "animateTransform", "animateMotion", "set", "handler", "audio", "video",
}
_SVG_CSS_BAD = ("url(", "expression", "@import", "javascript:")


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def looks_like_svg(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf\t\r\n ")
    return head.startswith(b"<")


def sanitize_svg(data: bytes) -> bytes:
    """Strips scripts, event-handler attributes, external references, and dangerous CSS."""
    svg = "http://www.w3.org/2000/svg"
    ET.register_namespace("", svg)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.fromstring(data)
    if root.tag != f"{{{svg}}}svg":
        raise ValueError("not an SVG document")

    def local(name: str) -> str:
        return name.split("}")[-1]

    def scrub_css(value: str) -> str:
        low = value.lower()
        return value if not any(word in low for word in _SVG_CSS_BAD) else ""

    for node in root.iter():
        if local(node.tag) in _SVG_FORBID:
            raise ValueError(f"forbidden element: {local(node.tag)}")
        for name in list(node.attrib):
            attribute = local(name)
            if attribute.lower().startswith("on"):
                del node.attrib[name]
            elif attribute == "href":
                value = str(node.attrib[name]).lstrip()
                allowed = value.startswith("#") or any(
                    value.startswith(prefix) for prefix in (
                        "data:image/png;base64,", "data:image/jpeg;base64,",
                        "data:image/webp;base64,", "data:image/gif;base64,",
                    )
                )
                if not allowed:
                    del node.attrib[name]
            elif attribute == "style":
                node.attrib[name] = scrub_css(node.attrib[name])
        if local(node.tag) == "style":
            node.text = scrub_css(node.text or "")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root)

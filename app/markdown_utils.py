"""Markdown 渲染 + HTML 消毒。所有用户输入必须经 render_markdown() 后才能输出 HTML。
流程：markdown → HTML → bleach 消毒（白名单标签/属性），防 XSS。
"""
import bleach
import markdown

_ALLOWED_TAGS = [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "img", "table", "thead", "tbody",
    "tr", "th", "td", "span",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "code": ["class"],
    "span": ["class"],
    "th": ["align"],
    "td": ["align"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_md = markdown.Markdown(
    extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    output_format="html",
)


def render_markdown(text: str) -> str:
    """Markdown → 消毒后 HTML。任何用户内容入库/出库都必须走这里。"""
    if not text:
        return ""
    _md.reset()
    html = _md.convert(text)
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


def excerpt(text: str, length: int = 160) -> str:
    """生成纯文本摘要（用于 meta description、列表页）。"""
    if not text:
        return ""
    _md.reset()
    plain = bleach.clean(_md.convert(text), tags=[], strip=True)
    plain = " ".join(plain.split())
    return plain[:length] + ("…" if len(plain) > length else "")

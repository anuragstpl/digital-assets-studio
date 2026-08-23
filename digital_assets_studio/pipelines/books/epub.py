"""A dependency-light EPUB 3 builder.

Written by hand rather than pulled from a library so the output is completely
predictable: KDP, Kobo, Apple Books and Google Play all reject slightly
different things, and when one of them complains you need to be able to open the
file and see exactly why.
"""
from __future__ import annotations

import html
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import markdown as md


@dataclass
class Chapter:
    title: str
    markdown: str
    id: str = ""
    in_toc: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-") or "section"
            self.id = slug[:40]


@dataclass
class BookMeta:
    title: str
    author: str
    language: str = "en"
    publisher: str = ""
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    isbn: str = ""
    year: int = field(default_factory=lambda: datetime.now().year)
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))


CSS = """
@page { margin: 0; }
html, body { margin: 0; padding: 0; }
body {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  line-height: 1.55;
  text-align: left;
  margin: 0 5%;
  widows: 2; orphans: 2;
}
h1, h2, h3 {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  line-height: 1.2;
  text-align: left;
  page-break-after: avoid;
  margin-top: 2.2em;
}
h1 { font-size: 1.7em; margin-top: 1em; }
h2 { font-size: 1.35em; }
h3 { font-size: 1.1em; }
p { margin: 0; text-indent: 1.2em; }
p:first-of-type, h1 + p, h2 + p, h3 + p, blockquote p, li p { text-indent: 0; }
p + p { margin-top: 0; }
.noindent p { text-indent: 0; margin-bottom: 0.9em; }
blockquote {
  margin: 1.2em 1.5em; padding-left: 0.9em;
  border-left: 3px solid #bbb; font-style: italic;
}
ul, ol { margin: 1em 0 1em 1.4em; }
li { margin-bottom: 0.4em; }
hr { border: 0; border-top: 1px solid #ccc; margin: 2em 20%; }
code, pre { font-family: "Courier New", monospace; font-size: 0.9em; }
pre { white-space: pre-wrap; background: #f4f4f4; padding: 0.7em; }
img { max-width: 100%; height: auto; }
.titlepage { text-align: center; margin-top: 22%; }
.titlepage h1 { font-size: 2.3em; margin-bottom: 0.2em; }
.titlepage .author { font-size: 1.15em; letter-spacing: 0.08em; text-transform: uppercase; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; max-height: 100%; }
"""

_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><meta charset="utf-8"/><title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>{body}</body></html>
"""


def md_to_xhtml(text: str) -> str:
    body = md.markdown(text, extensions=["extra", "sane_lists", "smarty"])
    # Markdown's output is HTML5; XHTML needs self-closed voids.
    body = re.sub(r"<(br|hr|img)([^>]*?)\s*/?>", r"<\1\2/>", body)
    body = body.replace("&nbsp;", "&#160;").replace("&mdash;", "&#8212;")
    body = body.replace("&ndash;", "&#8211;").replace("&hellip;", "&#8230;")
    body = re.sub(r"&(?!(?:#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos);)", "&amp;", body)
    return body


def build_epub(meta: BookMeta, chapters: list[Chapter], out_path: Path,
               cover_image: bytes | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    esc = html.escape

    docs: list[tuple[str, str, str]] = []   # (filename, title, xhtml)
    manifest: list[str] = []
    spine: list[str] = []

    if cover_image:
        docs.append(("cover.xhtml", "Cover",
                     '<div class="cover"><img src="cover.jpg" alt="Cover"/></div>'))

    for ch in chapters:
        docs.append((f"{ch.id}.xhtml", ch.title, md_to_xhtml(ch.markdown)))

    with zipfile.ZipFile(out_path, "w") as z:
        # mimetype must be first and stored uncompressed
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/style.css", CSS)
        if cover_image:
            z.writestr("OEBPS/cover.jpg", cover_image)
            manifest.append('<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>')

        for fname, title, body in docs:
            z.writestr(f"OEBPS/{fname}", _XHTML.format(lang=meta.language, title=esc(title), body=body))
            item_id = fname.rsplit(".", 1)[0]
            manifest.append(f'<item id="{esc(item_id)}" href="{esc(fname)}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{esc(item_id)}"/>')

        toc_items = "\n".join(
            f'      <li><a href="{esc(ch.id)}.xhtml">{esc(ch.title)}</a></li>'
            for ch in chapters if ch.in_toc
        )
        nav = _XHTML.format(
            lang=meta.language, title="Contents",
            body=('<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n'
                  + toc_items + "\n</ol></nav>"),
        )
        z.writestr("OEBPS/nav.xhtml", nav)
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        manifest.append('<item id="css" href="style.css" media-type="text/css"/>')

        # EPUB 2 fallback NCX keeps older Kindle tooling happy
        points = "\n".join(
            f'    <navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(ch.title)}</text></navLabel>'
            f'<content src="{esc(ch.id)}.xhtml"/></navPoint>'
            for i, ch in enumerate([c for c in chapters if c.in_toc], start=1)
        )
        z.writestr("OEBPS/toc.ncx",
                   f'<?xml version="1.0" encoding="UTF-8"?>\n'
                   f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                   f'<head><meta name="dtb:uid" content="urn:uuid:{meta.uuid}"/></head>\n'
                   f'<docTitle><text>{esc(meta.title)}</text></docTitle>\n'
                   f'<navMap>\n{points}\n</navMap></ncx>')
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

        subjects = "\n".join(f"    <dc:subject>{esc(s)}</dc:subject>" for s in meta.subjects)
        identifier = f"urn:isbn:{meta.isbn}" if meta.isbn else f"urn:uuid:{meta.uuid}"
        opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{esc(identifier)}</dc:identifier>
    <dc:title>{esc(meta.title)}</dc:title>
    <dc:language>{esc(meta.language)}</dc:language>
    <dc:creator id="author">{esc(meta.author)}</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <dc:publisher>{esc(meta.publisher)}</dc:publisher>
    <dc:date>{meta.year}-01-01</dc:date>
    <dc:description>{esc(meta.description)}</dc:description>
{subjects}
    <meta property="dcterms:modified">{now}</meta>
  </metadata>
  <manifest>
    {"".join(chr(10) + "    " + m for m in manifest)}
  </manifest>
  <spine toc="ncx">
    {"".join(chr(10) + "    " + s for s in spine)}
  </spine>
</package>"""
        z.writestr("OEBPS/content.opf", opf)

    return out_path

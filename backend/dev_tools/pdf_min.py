"""极简中文 PDF 写入器，仅用于生成 data/knowledge/ 的种子语料。

为什么不引第三方库：项目依赖里没有 reportlab / fpdf，uv.lock 已冻结，临时加
依赖会破坏「评审 5 分钟跑起来」的承诺。这里直接按 PDF 1.4 规范拼字节，用非嵌入
的 Adobe GB1 标准字体 STSong-Light 配 UniGB-UCS2-H 编码，并附带 ToUnicode CMap，
使 pypdf 能把文字原样提取回来。RAG 只关心提取结果，因此这条路是够用的。

已知取舍：字体不嵌入，评审机器若缺少 Adobe 亚洲字体包，视觉渲染可能回退成
系统替代字体。Windows 中文环境下 PDFium 会用宋体替代，实测可正常显示。

对象编号约定：1 Catalog、2 Pages、3 Type0 字体、4 CIDFont、5 FontDescriptor、
6 ToUnicode，页对象与内容流从 7 开始成对递增。
"""

from __future__ import annotations

from collections.abc import Sequence

#: A4 页面尺寸（pt）
_MEDIA_BOX = "0 0 595 842"
_FIRST_PAGE_OBJ = 7
_BMP_LIMIT = 0x10000


def _hex_text(line: str) -> bytes:
    """UniGB-UCS2-H 下字符码就是 UTF-16BE 码元，直接写成十六进制串。"""
    return b"<" + line.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _content_stream(
    lines: Sequence[str],
    font_size: int,
    leading: int,
    margin_left: int,
    margin_top: int,
) -> bytes:
    parts = [
        b"BT",
        f"/F1 {font_size} Tf".encode("ascii"),
        f"{leading} TL".encode("ascii"),
        f"{margin_left} {margin_top} Td".encode("ascii"),
    ]
    for index, line in enumerate(lines):
        if index:
            parts.append(b"T*")
        if line.strip():
            parts.append(_hex_text(line) + b" Tj")
    parts.append(b"ET")
    return b"\n".join(parts) + b"\n"


def _to_unicode_stream(code_points: Sequence[int]) -> bytes:
    """码位到 Unicode 的恒等映射。

    pypdf 的 build_char_map 遇到 /ToUnicode 会优先走 Unicode 映射并提前返回，
    所以只要这份 CMap 正确，extract_text 就能还原原文，不必依赖阅读器去解析
    UniGB-UCS2-H 预定义编码。bfchar 每块上限 100 条，超出要分块。
    """
    head = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n"
        "<0000> <FFFF>\n"
        "endcodespacerange\n"
    )
    tail = "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ordered = sorted(set(code_points))
    blocks: list[str] = []
    for start in range(0, len(ordered), 100):
        group = ordered[start : start + 100]
        entries = "".join(f"<{point:04X}> <{point:04X}>\n" for point in group)
        blocks.append(f"{len(group)} beginbfchar\n{entries}endbfchar\n")
    return (head + "".join(blocks) + tail).encode("ascii")


def wrap_cjk(paragraph: str, width: int = 44) -> list[str]:
    """按字符数折行。中日韩字符是全角，用 len 近似即可，不需要 East Asian Width 表。"""
    text = paragraph.strip()
    if len(text) <= width:
        return [text]
    return [text[start : start + width] for start in range(0, len(text), width)]


def paginate(lines: Sequence[str], per_page: int = 40) -> list[list[str]]:
    pages = [list(lines[start : start + per_page]) for start in range(0, len(lines), per_page)]
    return pages or [[]]


def build_pdf(
    pages: Sequence[Sequence[str]],
    *,
    font_size: int = 11,
    leading: int = 17,
    margin_left: int = 50,
    margin_top: int = 790,
) -> bytes:
    """把若干页文本行拼成一个可被 pypdf 正确提取的 PDF 字节串。"""
    for page in pages:
        for line in page:
            outside = [ch for ch in set(line) if ord(ch) >= _BMP_LIMIT]
            if outside:
                raise ValueError(f"仅支持基本多文种平面字符，遇到: {outside[:5]}")

    code_points = [ord(ch) for page in pages for line in page for ch in line]
    kids = " ".join(f"{_FIRST_PAGE_OBJ + 2 * index} 0 R" for index in range(len(pages)))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []

    def add(body: bytes) -> None:
        offsets.append(len(out))
        out.extend(f"{len(offsets)} 0 obj\n".encode("ascii") + body + b"\nendobj\n")

    def stream_obj(stream: bytes) -> bytes:
        return (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"))
    add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        b"/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>"
    )
    add(
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        b"/FontDescriptor 5 0 R /DW 1000 >>"
    )
    add(
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
        b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 "
        b"/CapHeight 880 /StemV 93 >>"
    )
    add(stream_obj(_to_unicode_stream(code_points)))

    for page in pages:
        contents_obj = len(offsets) + 2
        add(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [{_MEDIA_BOX}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {contents_obj} 0 R >>".encode(
                "ascii"
            )
        )
        add(stream_obj(_content_stream(page, font_size, leading, margin_left, margin_top)))

    startxref = len(out)
    size = len(offsets) + 1
    out.extend(f"xref\n0 {size}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)

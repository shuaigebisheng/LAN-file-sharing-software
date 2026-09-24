#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网设备互传 · 零依赖单文件版 · GUI 窗口
- 权限控制：上传时指定接收者，只有发送者和接收者可见
- 同一 IP 视为同一设备（同设备开多窗口共享身份）
- 断点续传 + 每个文件可暂停/继续
- 共享目录可动态切换
- 支持多选打包下载（zip）
- 客户端 token 隔离 part 分片，避免跨客户端覆盖
- 无主文件视为服务端所有，默认公开，只有服务端能改权限
- /api/files 支持 ETag + 分页 + 分类/搜索/排序
- 可选集成 Pillow 生成真缩略图（未安装时优雅降级为原图）
- 缩略图未就绪时返回占位图（302），前端自动重试
- Office 文件（docx/xlsx/pptx）预览：
    * 优先：调用外部 LibreOffice（soffice）转 PDF，PDF.js 渲染（高保真）
    * 兜底：纯 Python zipfile + xml.etree 解析（未装 LibreOffice 时）
    * 可通过环境变量 SOFFICE_PATH 指定 soffice 路径

依赖（可选）：
    Pillow      —— 生成图片缩略图（建议安装）
    pillow-heif —— 支持 HEIC 缩略图（可选）
    LibreOffice —— Office 文件高保真预览（可选，未装则自动降级）
必装：
    qr.py
    web/index.html, web/style.css, web/app.js, web/qr.html
用法：
    python transfer.py
    pythonw transfer.py
"""

import os
import re
import sys
import stat
import json
import time
import shutil
import socket
import hashlib
import tempfile
import zipfile
import itertools
import threading
import datetime
import mimetypes
import webbrowser
import collections
import subprocess
import urllib.parse
import posixpath
import html as _html
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tkinter as tk
from tkinter import ttk

from qr import qr_encode, qr_svg


# =====================================================================
#  可选依赖：Pillow（用于生成缩略图）
# =====================================================================

try:
    from PIL import Image, ImageOps
    _HAS_PIL = True

    try:
        Image.MAX_IMAGE_PIXELS = 200 * 1000 * 1000
    except Exception:
        pass

    try:
        _THUMB_RESAMPLE = Image.Resampling.LANCZOS
    except AttributeError:
        _THUMB_RESAMPLE = Image.LANCZOS

    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
        _HAS_HEIF = True
    except Exception:
        _HAS_HEIF = False

except ImportError:
    Image = None
    ImageOps = None
    _HAS_PIL = False
    _HAS_HEIF = False
    _THUMB_RESAMPLE = None


# =====================================================================
#  路径配置
# =====================================================================

def _get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_bundle_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR    = _get_base_dir()
BUNDLE_DIR  = _get_bundle_dir()
WEB_DIR     = os.path.join(BASE_DIR, "web")
SHARED_DIR  = os.path.join(BASE_DIR, "shared")
DATA_DIR    = os.path.join(BASE_DIR, ".data")
PARTIAL_DIR = os.path.join(DATA_DIR, "partial")
THUMB_DIR   = os.path.join(DATA_DIR, "thumbs")
OFFICE_PDF_CACHE_DIR = os.path.join(DATA_DIR, "office_pdf_cache")
PORT  = 8000
CHUNK = 64 * 1024

THUMB_MAX_SIZE = (240, 240)
THUMB_QUALITY  = 78

# 缩略图未就绪时的占位图（内嵌 SVG，几百字节）
_THUMB_PLACEHOLDER_PATH = "/static/thumb-pending.svg"
_THUMB_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" '
    'width="240" height="240">'
    '<circle cx="108" cy="100" r="10" fill="rgba(128,128,128,0.4)"/>'
    '<path d="M64 158l38-42 30 34 18-18 26 26z" '
    'fill="rgba(128,128,128,0.25)"/>'
    '</svg>'
).encode("utf-8")


def read_web_file(name):
    safe = os.path.basename(name)
    candidates = [
        os.path.join(BASE_DIR, "web", safe),
        os.path.join(BUNDLE_DIR, "web", safe),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            continue
    return None


def set_shared_dir(new_dir):
    global SHARED_DIR
    new_dir = os.path.abspath(new_dir)
    if new_dir == SHARED_DIR:
        return True, None
    try:
        os.makedirs(new_dir, exist_ok=True)
        probe = os.path.join(new_dir, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        return False, str(e)
    SHARED_DIR = new_dir

    try:
        if os.path.isdir(THUMB_DIR):
            shutil.rmtree(THUMB_DIR, ignore_errors=True)
    except OSError:
        pass

    try:
        if os.path.isdir(OFFICE_PDF_CACHE_DIR):
            shutil.rmtree(OFFICE_PDF_CACHE_DIR, ignore_errors=True)
    except OSError:
        pass

    return True, None


def _migrate_legacy_data():
    old_meta = os.path.join(SHARED_DIR, ".meta.json")
    new_meta = os.path.join(DATA_DIR, "meta.json")
    try:
        if os.path.isfile(old_meta) and not os.path.exists(new_meta):
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.move(old_meta, new_meta)
    except OSError:
        pass

    old_partial = os.path.join(SHARED_DIR, ".partial")
    if os.path.isdir(old_partial):
        try:
            os.makedirs(PARTIAL_DIR, exist_ok=True)
            for fn in os.listdir(old_partial):
                src = os.path.join(old_partial, fn)
                dst = os.path.join(PARTIAL_DIR, fn)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.move(src, dst)
            try:
                shutil.rmtree(old_partial)
            except OSError:
                pass
        except OSError:
            pass


# =====================================================================
#  客户端 ID（用于 part 文件隔离）
# =====================================================================

_CID_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")


def _normalize_cid(raw):
    if not raw:
        return ""
    s = _CID_SAFE_RE.sub("_", str(raw).strip())
    return s[:80]


def _part_path(name, size, cid=""):
    safe = os.path.basename(name)
    prefix = (cid + ".") if cid else ""
    return os.path.join(PARTIAL_DIR, f"{prefix}{safe}.{size}.part")


# =====================================================================
#  缩略图
# =====================================================================

_THUMB_SEM = threading.Semaphore(2)
_THUMB_FAILED = set()
_THUMB_FAILED_LOCK = threading.Lock()


def _thumb_path(name):
    safe = os.path.basename(name)
    return os.path.join(THUMB_DIR, safe + ".jpg")


def _is_image_file(name):
    ctype = mimetypes.guess_type(name)[0] or ""
    return ctype.startswith("image/")


def _generate_thumbnail_sync(src_path, name):
    if not _HAS_PIL:
        return False

    tmp_path = _thumb_path(name) + ".tmp"
    dst_path = _thumb_path(name)

    try:
        os.makedirs(THUMB_DIR, exist_ok=True)

        with Image.open(src_path) as im:
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass

            if im.mode != "RGB":
                has_alpha = im.mode in ("RGBA", "LA") or \
                            (im.mode == "P" and "transparency" in im.info)
                if has_alpha:
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")

            im.thumbnail(THUMB_MAX_SIZE, _THUMB_RESAMPLE)
            im.save(tmp_path, "JPEG", quality=THUMB_QUALITY, optimize=True)

        os.replace(tmp_path, dst_path)
        return True

    except Exception as e:
        push_log(f"[error] 生成缩略图失败 {name}: {e}")
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        with _THUMB_FAILED_LOCK:
            _THUMB_FAILED.add(name)
        return False


def _generate_thumbnail_async(src_path, name):
    if not _HAS_PIL:
        return
    if not _is_image_file(name):
        return

    with _THUMB_FAILED_LOCK:
        if name in _THUMB_FAILED:
            return

    def _job():
        with _THUMB_SEM:
            ok = _generate_thumbnail_sync(src_path, name)
            if ok:
                push_log(f"🖼 已生成缩略图 {name}")

    t = threading.Thread(target=_job, daemon=True,
                         name="thumb-" + name[:24])
    t.start()


# =====================================================================
#  文件分类（与前端 app.js 保持一致）
# =====================================================================

_CATEGORY_EXT_MAP = {
    "img": {"jpg","jpeg","png","gif","webp","heic","bmp","svg","avif","ico"},
    "vid": {"mp4","mov","mkv","avi","webm","flv","wmv","m4v"},
    "aud": {"mp3","wav","flac","aac","m4a","ogg","opus"},
    "doc": {
        "pdf",
        # Word
        "doc","docx","docm","dot","dotx","dotm","rtf","odt","ott","fodt",
        # Excel
        "xls","xlsx","xlsm","xlsb","xltx","xltm","ods","ots","fods",
        # PowerPoint
        "ppt","pptx","pptm","pps","ppsx","pot","potx","potm","odp","otp","fodp",
        # 其他文档
        "csv","tsv",
        "txt","md","markdown","log",
        "js","mjs","ts","tsx","jsx","py","rb","go","rs","java","kt","swift",
        "c","cc","cpp","h","hpp","cs","php","sh","bash","zsh","sql",
        "html","htm","css","scss","less","xml","json","yaml","yml","toml",
        "ini","conf","cfg","env","vue","svelte",
    },
    "zip": {"zip","rar","7z","tar","gz","bz2","xz"},
}

_VALID_SORTS = (
    "mtime_desc", "mtime_asc",
    "size_desc", "size_asc",
    "name_asc", "name_desc",
)


def _file_category(name):
    idx = name.rfind(".")
    ext = name[idx + 1:].lower() if idx >= 0 else ""
    for cat, exts in _CATEGORY_EXT_MAP.items():
        if ext in exts:
            return cat
    return "other"


# =====================================================================
#  OOXML 预览（docx / xlsx / pptx 纯 Python 解析 · 兜底）
#  这些格式本质是 zip + XML，用标准库即可提取文本/表格
# =====================================================================

# 纯 Python 能解析的 OOXML 后缀（zip + XML）
_OOXML_EXTS = {"docx", "xlsx", "pptx"}

# 全部 Office 后缀（LibreOffice 能处理的范围）
_OFFICE_EXTS = {
    # ---- Word ----
    "docx", "docm", "dotx", "dotm",     # OOXML
    "doc",  "dot",  "rtf",               # 旧版二进制 / RTF
    "odt",  "ott",  "fodt",              # OpenDocument

    # ---- Excel ----
    "xlsx", "xlsm", "xltx", "xltm",      # OOXML
    "xls",  "xlsb",                      # 旧版二进制
    "ods",  "ots",  "fods",              # OpenDocument

    # ---- PowerPoint ----
    "pptx", "pptm", "potx", "potm",      # OOXML
    "ppt",  "pps",  "ppsx", "pot",       # 旧版二进制
    "odp",  "otp",  "fodp",              # OpenDocument
}
_OOXML_MAX_FILE  = 200 * 1024 * 1024   # 整个文件上限 200MB
_OOXML_MAX_PART  = 50  * 1024 * 1024   # 单个 XML 部件上限 50MB
_XLSX_MAX_ROWS   = 500                 # 每张工作表最多渲染行数
_XLSX_MAX_COLS   = 50                  # 每行最多渲染列数


def _xml_local_name(tag):
    """去掉 XML 命名空间前缀，返回本地名称。"""
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        i = tag.find("}")
        return tag[i + 1:] if i >= 0 else tag
    return tag


def _safe_parse_xml(zf, name, max_size=_OOXML_MAX_PART):
    """安全地解析 zip 内某个 XML 部件。返回 ElementTree 或 None。"""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > max_size:
        return None
    try:
        with zf.open(name) as f:
            return ET.parse(f)
    except (ET.ParseError, OSError, ValueError, EOFError):
        return None


# ---------- docx ----------

def _docx_para_text(p):
    parts = []
    for node in p.iter():
        ln = _xml_local_name(node.tag)
        if ln == "t":
            if node.text:
                parts.append(node.text)
        elif ln == "tab":
            parts.append("\t")
        elif ln == "br":
            parts.append("\n")
    return "".join(parts)


def _docx_table_rows(tbl):
    rows = []
    for tr in tbl:
        if _xml_local_name(tr.tag) != "tr":
            continue
        cells = []
        for tc in tr:
            if _xml_local_name(tc.tag) != "tc":
                continue
            paras = []
            for p in tc:
                if _xml_local_name(p.tag) == "p":
                    paras.append(_docx_para_text(p))
            cells.append("\n".join(paras))
        rows.append(cells)
    return rows


def _render_docx(zf):
    tree = _safe_parse_xml(zf, "word/document.xml")
    if tree is None:
        return None
    root = tree.getroot()
    body = None
    for elem in root.iter():
        if _xml_local_name(elem.tag) == "body":
            body = elem
            break
    if body is None:
        body = root

    out = []
    for child in body:
        ln = _xml_local_name(child.tag)
        if ln == "p":
            text = _docx_para_text(child)
            if text:
                out.append("<p>" + _html.escape(text) + "</p>")
            else:
                out.append("<p>&nbsp;</p>")
        elif ln == "tbl":
            rows = _docx_table_rows(child)
            if rows:
                out.append('<table class="ooxml-table">')
                for row in rows:
                    out.append("<tr>")
                    for cell in row:
                        out.append(
                            "<td>" +
                            _html.escape(cell).replace("\n", "<br>") +
                            "</td>"
                        )
                    out.append("</tr>")
                out.append("</table>")
    return "".join(out) if out else None


# ---------- xlsx ----------

def _xlsx_col_index(ref):
    """从单元格引用（如 'AB12'）解析出 0 起始的列索引。"""
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
        elif "a" <= ch <= "z":
            n = n * 26 + (ord(ch) - 96)
        else:
            break
    return max(0, n - 1)


def _xlsx_cell_text(cell, shared):
    t = cell.get("t") or "n"
    v = None
    inline_parts = None
    for child in cell:
        ln = _xml_local_name(child.tag)
        if ln == "v":
            v = child.text or ""
            break
        if ln == "is":
            parts = []
            for n in child.iter():
                if _xml_local_name(n.tag) == "t" and n.text:
                    parts.append(n.text)
            inline_parts = parts
            break
    if inline_parts is not None:
        return "".join(inline_parts)
    if v is None:
        return ""
    if t == "s":
        try:
            idx = int(v)
            return shared[idx] if 0 <= idx < len(shared) else ""
        except (ValueError, IndexError):
            return ""
    if t == "b":
        return "TRUE" if v == "1" else "FALSE"
    if t in ("e", "str"):
        return v
    return v  # 数字或普通字符串


def _xlsx_sheet_rows(root, shared):
    rows = []
    for row_elem in root.iter():
        if _xml_local_name(row_elem.tag) != "row":
            continue
        if len(rows) >= _XLSX_MAX_ROWS:
            break
        row_data = []
        for cell in row_elem:
            if _xml_local_name(cell.tag) != "c":
                continue
            if len(row_data) >= _XLSX_MAX_COLS:
                break
            ref = cell.get("r") or ""
            if ref:
                col = _xlsx_col_index(ref)
                if col >= _XLSX_MAX_COLS:
                    continue
                while len(row_data) < col:
                    row_data.append("")
            row_data.append(_xlsx_cell_text(cell, shared))
        while row_data and row_data[-1] == "":
            row_data.pop()
        rows.append(row_data)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _render_xlsx(zf):
    # 共享字符串表
    shared = []
    tree = _safe_parse_xml(zf, "xl/sharedStrings.xml")
    if tree is not None:
        for si in tree.getroot():
            if _xml_local_name(si.tag) != "si":
                continue
            parts = []
            for n in si.iter():
                if _xml_local_name(n.tag) == "t" and n.text:
                    parts.append(n.text)
            shared.append("".join(parts))

    # workbook.xml：工作表名 + rId
    names_by_rid = {}
    tree = _safe_parse_xml(zf, "xl/workbook.xml")
    if tree is not None:
        for sh in tree.getroot().iter():
            if _xml_local_name(sh.tag) != "sheet":
                continue
            rid = None
            for k, v in sh.attrib.items():
                if _xml_local_name(k) == "id":
                    rid = v
                    break
            name = sh.get("name")
            if rid and name:
                names_by_rid[rid] = name

    # xl/_rels/workbook.xml.rels：rId → 工作表路径
    rid_to_path = {}
    tree = _safe_parse_xml(zf, "xl/_rels/workbook.xml.rels")
    if tree is not None:
        for rel in tree.getroot():
            rid = rel.get("Id")
            target = rel.get("Target")
            if not rid or not target:
                continue
            if target.startswith("/"):
                path = target.lstrip("/")
            else:
                path = "xl/" + target
            path = posixpath.normpath(path)
            rid_to_path[rid] = path

    # 按 workbook 顺序排列
    ordered = []
    for rid, name in names_by_rid.items():
        if rid in rid_to_path:
            ordered.append((name, rid_to_path[rid]))

    if not ordered:
        # 回退：按 sheetN.xml 数字顺序
        cand = []
        for n in zf.namelist():
            if not n.startswith("xl/worksheets/sheet") or not n.endswith(".xml"):
                continue
            tail = n[len("xl/worksheets/"):]
            if "/" in tail:
                continue
            m = re.match(r"sheet(\d+)\.xml$", tail)
            if m:
                cand.append((int(m.group(1)), n))
        cand.sort()
        for i, (_, path) in enumerate(cand):
            ordered.append((f"Sheet{i + 1}", path))

    out = []
    for name, path in ordered:
        tree = _safe_parse_xml(zf, path)
        if tree is None:
            continue
        rows = _xlsx_sheet_rows(tree.getroot(), shared)
        if not rows:
            continue
        out.append('<div class="ooxml-sheet">')
        out.append(f"<h3>{_html.escape(name)}</h3>")
        out.append('<table class="ooxml-table">')
        for row in rows:
            out.append("<tr>")
            for cell in row:
                out.append("<td>" + _html.escape(cell) + "</td>")
            out.append("</tr>")
        out.append("</table></div>")
    return "".join(out) if out else None


# ---------- pptx ----------

def _render_pptx(zf):
    slides = []
    for n in zf.namelist():
        if not n.startswith("ppt/slides/slide") or not n.endswith(".xml"):
            continue
        tail = n[len("ppt/slides/"):]
        if "/" in tail:
            continue
        m = re.match(r"slide(\d+)\.xml$", tail)
        if m:
            slides.append((int(m.group(1)), n))
    slides.sort()
    if not slides:
        return None

    out = []
    for i, (_, path) in enumerate(slides):
        tree = _safe_parse_xml(zf, path)
        if tree is None:
            continue
        paragraphs = []
        for p in tree.getroot().iter():
            if _xml_local_name(p.tag) != "p":
                continue
            parts = []
            for t in p.iter():
                if _xml_local_name(t.tag) == "t" and t.text:
                    parts.append(t.text)
            text = "".join(parts)
            if text:
                paragraphs.append(text)
        if not paragraphs:
            continue
        out.append('<div class="ooxml-slide">')
        out.append(f"<h3>第 {i + 1} 页</h3>")
        out.append("<pre>" + _html.escape("\n".join(paragraphs)) + "</pre>")
        out.append("</div>")
    return "".join(out) if out else None


def _render_ooxml(full_path, ext):
    """对外统一入口：返回 HTML 字符串或 None。"""
    try:
        size = os.path.getsize(full_path)
    except OSError:
        return None
    if size > _OOXML_MAX_FILE:
        return None
    # docx 已由前端 docx-preview 处理，这里不再解析
    if ext == "docx":
        return None
    if not zipfile.is_zipfile(full_path):
        return None
    try:
        with zipfile.ZipFile(full_path, "r") as zf:
            if ext == "xlsx":
                return _render_xlsx(zf)
            if ext == "pptx":
                return _render_pptx(zf)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return None
    return None


# =====================================================================
#  LibreOffice：探测 / 转 PDF / 缓存
# =====================================================================

_SOFFICE_PATH = None        # None = 未探测；"" = 不可用；其他 = 路径
_SOFFICE_LOCK = threading.Lock()
_OFFICE_PDF_LOCK = threading.Lock()


def _find_soffice():
    """返回 soffice 可执行文件路径，未找到返回空字符串。"""
    global _SOFFICE_PATH
    with _SOFFICE_LOCK:
        if _SOFFICE_PATH is not None:
            return _SOFFICE_PATH

        candidates = []

        # 1) 环境变量覆盖
        env = os.environ.get("SOFFICE_PATH")
        if env:
            candidates.append(env)

        # 2) PATH 中查找
        for name in ("soffice", "libreoffice"):
            try:
                p = shutil.which(name)
            except Exception:
                p = None
            if p:
                candidates.append(p)

        # 3) 常见安装位置
        if sys.platform == "win32":
            candidates += [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ]
        elif sys.platform == "darwin":
            candidates.append(
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"
            )

        for path in candidates:
            if path and os.path.isfile(path):
                _SOFFICE_PATH = path
                push_log(f"📄 检测到 LibreOffice：{path}")
                return path

        _SOFFICE_PATH = ""
        push_log("⚠️ 未检测到 LibreOffice，Office 预览将使用纯文本模式")
        return ""


def _office_pdf_cache_path(name, src_path):
    try:
        mtime = int(os.path.getmtime(src_path))
    except OSError:
        return None
    key = hashlib.md5(f"{name}|{mtime}".encode("utf-8")).hexdigest()
    return os.path.join(OFFICE_PDF_CACHE_DIR, key + ".pdf")


def _ensure_office_pdf(name, src_path):
    """转换并缓存 PDF。成功返回 PDF 路径，失败返回 None。"""
    soffice = _find_soffice()
    if not soffice:
        return None

    cached = _office_pdf_cache_path(name, src_path)
    if cached is None:
        return None

    with _OFFICE_PDF_LOCK:
        if os.path.isfile(cached):
            return cached

        try:
            os.makedirs(OFFICE_PDF_CACHE_DIR, exist_ok=True)
        except OSError as e:
            push_log(f"[error] 创建 PDF 缓存目录失败: {e}")
            return None

        try:
            with tempfile.TemporaryDirectory(prefix="oo_") as tmp:
                try:
                    result = subprocess.run(
                        [
                            soffice,
                            "--headless",
                            "--norestore",
                            "--convert-to", "pdf",
                            "--outdir", tmp,
                            src_path,
                        ],
                        capture_output=True,
                        timeout=120,
                    )
                except subprocess.TimeoutExpired:
                    push_log(f"[error] LibreOffice 转换超时: {name}")
                    return None
                except OSError as e:
                    push_log(f"[error] LibreOffice 调用失败: {e}")
                    return None

                if result.returncode != 0:
                    stderr = (result.stderr or b"").decode(
                        "utf-8", "replace")[:400]
                    push_log(f"[error] LibreOffice 转换失败: {name} - {stderr}")
                    return None

                base = os.path.splitext(os.path.basename(src_path))[0]
                produced = os.path.join(tmp, base + ".pdf")
                if not os.path.isfile(produced):
                    pdfs = [f for f in os.listdir(tmp)
                            if f.lower().endswith(".pdf")]
                    if not pdfs:
                        push_log(f"[error] LibreOffice 未产出 PDF: {name}")
                        return None
                    produced = os.path.join(tmp, pdfs[0])

                tmp_target = cached + ".tmp"
                try:
                    shutil.move(produced, tmp_target)
                    os.replace(tmp_target, cached)
                except OSError as e:
                    push_log(f"[error] 写入 PDF 缓存失败: {e}")
                    return None
        except Exception as e:
            # 兜底：任何未预期的异常都不影响主流程，只是本次转换失败
            push_log(f"[error] Office → PDF 转换异常: {name} - {e}")
            return None

        return cached

def _cleanup_office_pdf_cache(max_files=200):
    """简单清理：缓存文件数超过上限时删掉最旧的一批。"""
    try:
        if not os.path.isdir(OFFICE_PDF_CACHE_DIR):
            return
        entries = []
        for fn in os.listdir(OFFICE_PDF_CACHE_DIR):
            if not fn.endswith(".pdf"):
                continue
            p = os.path.join(OFFICE_PDF_CACHE_DIR, fn)
            try:
                entries.append((os.path.getmtime(p), p))
            except OSError:
                continue
        if len(entries) <= max_files:
            return
        entries.sort()
        for _, p in entries[:len(entries) - max_files]:
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


# =====================================================================
#  日志
# =====================================================================

LOG_BUFFER = collections.deque(maxlen=500)
LOG_COUNTER = 0
LOG_LOCK = threading.Lock()


def push_log(msg):
    global LOG_COUNTER
    with LOG_LOCK:
        LOG_COUNTER += 1
        LOG_BUFFER.append({"id": LOG_COUNTER, "t": time.time(), "msg": msg})


def _sanitize_log_text(s):
    if not s:
        return s
    ctrl = sum(1 for ch in s if ord(ch) < 32 and ch not in "\t")
    if ctrl > 3:
        return None
    return "".join(ch if (ch.isprintable() or ch == "\t") else "·" for ch in s)


# =====================================================================
#  User-Agent
# =====================================================================

def parse_ua(ua):
    if not ua:
        return None
    s = ua.lower()

    if "iphone" in s:
        device = "iPhone"
    elif "ipad" in s:
        device = "iPad"
    elif "android" in s:
        device = "Android 手机" if "mobile" in s else "Android 平板"
    elif "windows" in s:
        device = "Windows"
    elif "macintosh" in s or "mac os" in s:
        device = "Mac"
    elif "linux" in s:
        device = "Linux"
    elif "harmony" in s:
        device = "HarmonyOS"
    else:
        device = None

    if "edg/" in s or "edgios/" in s or "edga/" in s:
        browser = "Edge"
    elif "chrome/" in s or "crios/" in s:
        browser = "Chrome"
    elif "firefox/" in s or "fxios/" in s:
        browser = "Firefox"
    elif "safari/" in s:
        browser = "Safari"
    elif "curl/" in s:
        browser = "curl"
    elif "wget/" in s:
        browser = "wget"
    else:
        browser = None

    if device and browser:
        return f"{device} · {browser}"
    return device or browser


# =====================================================================
#  客户端身份（IP 归一化）
# =====================================================================

_LOCAL_KEY = "__local__"
_LOCAL_IPS_CACHE = {"ips": set(), "t": 0}
_LOCAL_IPS_LOCK = threading.Lock()

_CLIENT_KEY_CACHE = {"map": {}, "t": 0.0}
_CLIENT_KEY_CACHE_LOCK = threading.Lock()
_CLIENT_KEY_CACHE_TTL = 60.0


def _local_ips():
    with _LOCAL_IPS_LOCK:
        now = time.time()
        if now - _LOCAL_IPS_CACHE["t"] > 30 or not _LOCAL_IPS_CACHE["ips"]:
            try:
                _LOCAL_IPS_CACHE["ips"] = set(get_lan_ips())
            except Exception:
                pass
            _LOCAL_IPS_CACHE["t"] = now
        return _LOCAL_IPS_CACHE["ips"]


def client_key(ip):
    if not ip:
        return ""
    if ip in ("127.0.0.1", "::1", "localhost"):
        return _LOCAL_KEY

    now = time.time()
    with _CLIENT_KEY_CACHE_LOCK:
        if now - _CLIENT_KEY_CACHE["t"] > _CLIENT_KEY_CACHE_TTL:
            _CLIENT_KEY_CACHE["map"] = {}
            _CLIENT_KEY_CACHE["t"] = now
        cached = _CLIENT_KEY_CACHE["map"].get(ip)
        if cached is not None:
            return cached

    key = ip
    try:
        if ip in _local_ips():
            key = _LOCAL_KEY
    except Exception:
        pass

    with _CLIENT_KEY_CACHE_LOCK:
        _CLIENT_KEY_CACHE["map"][ip] = key
    return key


def _invalidate_client_key_cache():
    with _CLIENT_KEY_CACHE_LOCK:
        _CLIENT_KEY_CACHE["map"] = {}
        _CLIENT_KEY_CACHE["t"] = 0.0


# =====================================================================
#  在线设备
# =====================================================================

CLIENTS = {}
CLIENTS_LOCK = threading.Lock()
CLIENT_TIMEOUT = 120


def touch_client(key, ip, name=None):
    now = time.time()
    with CLIENTS_LOCK:
        rec = CLIENTS.get(key)
        if rec is None:
            CLIENTS[key] = {"ip": ip, "name": name or "", "t": now}
        else:
            rec["t"] = now
            if ip:
                rec["ip"] = ip
            if name:
                rec["name"] = name


def get_client_name(key):
    if not key:
        return None
    with CLIENTS_LOCK:
        rec = CLIENTS.get(key)
        return rec["name"] if rec and rec.get("name") else None


def get_active_clients():
    now = time.time()
    with CLIENTS_LOCK:
        for k in list(CLIENTS.keys()):
            if now - CLIENTS[k]["t"] > CLIENT_TIMEOUT * 4:
                del CLIENTS[k]
        return [
            {"key": k,
             "ip": CLIENTS[k]["ip"],
             "name": CLIENTS[k].get("name") or CLIENTS[k]["ip"]}
            for k in CLIENTS
            if now - CLIENTS[k]["t"] < CLIENT_TIMEOUT
        ]


def _reset_clients_for_new_network():
    with CLIENTS_LOCK:
        removed = 0
        for k in list(CLIENTS.keys()):
            if k != _LOCAL_KEY:
                del CLIENTS[k]
                removed += 1
    _invalidate_client_key_cache()
    return removed


# =====================================================================
#  文件元数据（权限 + 完成标记）
# =====================================================================

META_LOCK = threading.Lock()
_META_CACHE = {"data": None, "lock": threading.Lock()}


def _meta_path():
    return os.path.join(DATA_DIR, "meta.json")


def _load_meta():
    with _META_CACHE["lock"]:
        if _META_CACHE["data"] is not None:
            return _META_CACHE["data"]

    data = {}
    try:
        with open(_meta_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                data = d
    except (OSError, ValueError):
        pass

    with _META_CACHE["lock"]:
        if _META_CACHE["data"] is None:
            _META_CACHE["data"] = data
        return _META_CACHE["data"]


def _save_meta(m):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _meta_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _meta_path())
        with _META_CACHE["lock"]:
            _META_CACHE["data"] = m
    except OSError:
        pass


def _get_to_clients(info):
    if "to_clients" in info:
        v = info.get("to_clients") or []
        if not isinstance(v, list):
            v = [v]
        return [client_key(k) for k in v if k]
    old = info.get("to_client", "")
    if old:
        return [client_key(old)]
    return []


def _get_to_names(info):
    if "to_names" in info:
        v = info.get("to_names") or []
        if not isinstance(v, list):
            v = [v]
        return list(v)
    old = info.get("to_name", "")
    return [old] if old else []


def set_file_meta(name, from_key, to_keys, from_name, to_names, cid=None):
    with META_LOCK:
        m = _load_meta()
        old = m.get(name, {})
        rec = {
            "from_client": from_key or "",
            "from_name":   from_name or "",
            "to_clients":  list(to_keys or []),
            "to_names":    list(to_names or []),
            "uploaded_at": time.time(),
        }
        if cid is not None:
            rec["cid"] = cid
        elif old.get("cid"):
            rec["cid"] = old["cid"]

        if old.get("added_at"):
            rec["added_at"] = old["added_at"]
        else:
            rec["added_at"] = time.time()

        m[name] = rec
        _save_meta(m)


def can_access(name, me_key, meta):
    info = meta.get(name)
    if info is None:
        return True
    from_key = client_key(info.get("from_client", ""))
    if from_key and from_key == me_key:
        return True
    to_keys = _get_to_clients(info)
    if not to_keys:
        return True
    for k in to_keys:
        if k == me_key:
            return True
    return False


def _is_file_mine(info, me_key):
    from_key = client_key(info.get("from_client", "")) if info else ""
    if from_key:
        return from_key == me_key
    return me_key == _LOCAL_KEY


def _compute_files_etag(me_key, file_entries):
    h = hashlib.md5()
    h.update(str(me_key).encode("utf-8", "replace"))
    h.update(b"\x00")
    for name, st, info in file_entries:
        h.update(name.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(str(int(st.st_mtime)).encode())
        h.update(b"\x00")
        h.update(str(st.st_size).encode())
        h.update(b"\x00")
        h.update(str(info.get("added_at", 0)).encode())
        h.update(b"\x00")
        h.update(str(info.get("from_client", "")).encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(str(info.get("from_name", "")).encode("utf-8", "replace"))
        h.update(b"\x00")
        to = info.get("to_clients") or []
        if isinstance(to, list):
            for x in to:
                h.update(str(x).encode("utf-8", "replace"))
                h.update(b",")
        h.update(b"\x00")
        tn = info.get("to_names") or []
        if isinstance(tn, list):
            for x in tn:
                h.update(str(x).encode("utf-8", "replace"))
                h.update(b",")
        h.update(b"\x01")
    return h.hexdigest()


# =====================================================================
#  正在传输
# =====================================================================

TRANSFERS = {}
TRANSFERS_LOCK = threading.Lock()
_FINALIZE_LOCK = threading.Lock()
_TID = itertools.count(1)


def _new_transfer(direction, name, size, offset, client,
                  client_key_val="", cid=""):
    now = time.time()
    with TRANSFERS_LOCK:
        if direction == "up":
            for old_tid, old in list(TRANSFERS.items()):
                if (old["dir"] == "up"
                        and old["name"] == name
                        and old["size"] == size
                        and old.get("cid", "") == cid
                        and not (old["done"] and old["ok"])):
                    old["received"] = offset
                    old["t0"] = now
                    old["last_t"] = now
                    old["last_b"] = offset
                    old["speed"] = 0.0
                    old["done"] = False
                    old["ok"] = False
                    old["paused"] = False
                    old["error"] = None
                    old["client"] = client
                    old["client_key"] = client_key_val
                    old["cid"] = cid
                    return old_tid
        tid = next(_TID)
        TRANSFERS[tid] = {
            "id": tid, "dir": direction, "name": name, "size": size,
            "received": offset, "t0": now, "last_t": now, "last_b": offset,
            "speed": 0.0, "done": False, "ok": False,
            "paused": False, "error": None, "client": client,
            "client_key": client_key_val,
            "cid": cid,
        }
    return tid


def _update_transfer(tid, received):
    now = time.time()
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        t["received"] = received
        dt = now - t["last_t"]
        if dt >= 0.5:
            inst = (received - t["last_b"]) / dt
            if t["speed"] > 0:
                t["speed"] = t["speed"] * 0.6 + inst * 0.4
            else:
                t["speed"] = inst
            t["last_t"] = now
            t["last_b"] = received


def _pause_transfer(tid, reason=None):
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        t["done"] = False
        t["ok"] = False
        t["paused"] = True
        t["error"] = reason
        t["speed"] = 0.0


def _finish_transfer(tid, ok, error=None):
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        t["done"] = True
        t["ok"] = ok
        t["paused"] = False
        t["error"] = error
        t["done_at"] = time.time()
        if ok:
            t["received"] = t["size"]


def _gc_transfers(max_age=5.0):
    now = time.time()
    with TRANSFERS_LOCK:
        for tid in list(TRANSFERS.keys()):
            t = TRANSFERS[tid]
            if t["done"] and now - t.get("done_at", t["t0"]) > max_age:
                del TRANSFERS[tid]


def _cancel_transfer(name, size, me_key, cid):
    part = _part_path(name, size, cid)
    with TRANSFERS_LOCK:
        for tid, t in list(TRANSFERS.items()):
            if (t["dir"] == "up"
                    and t["name"] == name
                    and t["size"] == size
                    and t.get("cid", "") == cid
                    and not (t["done"] and t["ok"])):
                ck = t.get("client_key") or ""
                if ck and ck != me_key:
                    return False
                del TRANSFERS[tid]
                break

    try:
        if os.path.isfile(part):
            os.remove(part)
    except OSError:
        pass

    return True


# =====================================================================
#  工具
# =====================================================================

def fmt_bytes(b):
    b = float(b)
    if b < 1024:
        return f"{int(b)} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024:.1f} MB"
    return f"{b / 1024 / 1024 / 1024:.2f} GB"


def fmt_speed(bps):
    return fmt_bytes(bps) + "/s"


# =====================================================================
#  HTTP
# =====================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "FileDrop/2.9"
    protocol_version = "HTTP/1.1"
    timeout = 60

    # ---------- 身份 ----------

    def _me(self):
        return client_key(self.client_address[0])

    def _me_name(self):
        return get_client_name(self._me()) or self.client_address[0]

    def _resolve_cid(self, qs=None, data=None):
        raw = ""
        if qs:
            raw = (qs.get("cid") or [""])[0]
        if not raw and isinstance(data, dict):
            raw = data.get("cid") or ""
        cid = _normalize_cid(raw)
        if cid:
            return cid
        return client_key(self.client_address[0])

    # ---------- 日志 ----------

    def log_message(self, fmt, *args):
        try:
            try:
                msg = fmt % args if args else str(fmt)
            except Exception:
                return
            msg = _sanitize_log_text(msg)
            if msg is None:
                return
            path = getattr(self, "path", "") or ""
            if "/api/" in path or "/static/" in path:
                return
            push_log("%s - %s" % (self._me_name(), msg))
        except Exception:
            pass

    def log_error(self, fmt, *args):
        try:
            try:
                msg = fmt % args if args else str(fmt)
            except Exception:
                return
            msg = _sanitize_log_text(msg)
            if msg is None:
                return
            low = msg.lower()
            if "timed out" in low or "timeout" in low:
                return
            if ("bad request" in low or "bad http" in low
                    or "code 400" in low or "unsupported method" in low):
                return
            push_log("[error] %s - %s" % (self._me_name(), msg))
        except Exception:
            pass

    def handle_one_request(self):
        try:
            peek = self.connection.recv(1, socket.MSG_PEEK)
            if peek and peek[0] in (0x16, 0x80):
                self.close_connection = True
                return
        except Exception:
            self.close_connection = True
            return

        try:
            ua = self.headers.get("User-Agent", "") if self.headers else ""
            ip = self.client_address[0]
            touch_client(client_key(ip), ip, parse_ua(ua))
        except Exception:
            pass

        try:
            super().handle_one_request()
        except (ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                TimeoutError):
            self.close_connection = True

    # ---------- 底层发送 ----------

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    # ---------------- GET ----------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if path in ("/", "/index.html"):
            html = read_web_file("index.html")
            if html is None:
                self._send(500, "web/index.html 未找到")
                return
            self._send(200, html, "text/html; charset=utf-8")

        elif path == "/favicon.ico":
            self._send(204)

        elif path.startswith("/static/"):
            self._serve_static(path)

        elif path == "/api/qr.svg":
            host = (qs.get("host") or [""])[0].strip()
            if not host:
                ips = get_lan_ips()
                host = f"{ips[0]}:{PORT}" if ips else f"localhost:{PORT}"
            url = f"http://{host}/"
            try:
                svg = qr_svg(qr_encode(url))
            except Exception:
                svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
                       'viewBox="0 0 100 100"><text x="50" y="55" '
                       'text-anchor="middle" font-size="12">生成失败</text></svg>')
            self._send(200, svg, "image/svg+xml; charset=utf-8")

        elif path == "/qr":
            self._serve_qr_page(qs)

        elif path == "/api/files":
            self._handle_files(qs)

        elif path == "/api/clients":
            self._handle_clients()

        elif path == "/api/ooxml":
            self._handle_ooxml(qs)

        elif path == "/api/office-pdf":
            self._handle_office_pdf(qs)

        elif path == "/upload/status":
            self._handle_status(qs)

        elif path.startswith("/thumb/"):
            name = urllib.parse.unquote(path[len("/thumb/"):])
            self._serve_thumb(name)

        elif path.startswith("/preview/"):
            name = urllib.parse.unquote(path[len("/preview/"):])
            self._serve_file(name, inline=True)

        elif path.startswith("/download/"):
            name = urllib.parse.unquote(path[len("/download/"):])
            self._serve_file(name)

        else:
            self._send(404, "Not Found")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/upload":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            self._handle_upload(qs)
        elif parsed.path == "/upload/cancel":
            self._handle_cancel()
        elif parsed.path == "/api/permission":
            self._handle_permission()
        elif parsed.path == "/api/zip":
            self._handle_zip()
        else:
            self._send(404, "Not Found")

    # ---------------- 静态 ----------------

    def _serve_static(self, path):
        rel = path[len("/static/"):]

        # 占位图：直接返回内嵌 SVG，不读磁盘
        if rel == "thumb-pending.svg":
            self._send(200, _THUMB_PLACEHOLDER_SVG,
                       "image/svg+xml; charset=utf-8",
                       extra={"Cache-Control": "public, max-age=86400"})
            return

        content = read_web_file(rel)
        if content is None:
            self._send(404, "Not Found")
            return
        ext = os.path.splitext(rel)[1].lower()
        ctype_map = {
            ".css":  "text/css; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".mjs":  "application/javascript; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".svg":  "image/svg+xml; charset=utf-8",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif":  "image/gif",
            ".webp": "image/webp",
            ".ico":  "image/x-icon",
            ".json": "application/json; charset=utf-8",
            ".woff": "font/woff",
            ".woff2":"font/woff2",
            ".ttf":  "font/ttf",
        }
        ctype = ctype_map.get(ext, "application/octet-stream")
        self._send(200, content, ctype)

    # ---------------- 文件列表（分页 + 过滤 + 排序） ----------------

    def _handle_files(self, qs):
        me = self._me()

        try:
            limit = int((qs.get("limit") or ["60"])[0])
        except (ValueError, TypeError):
            limit = 60
        limit = max(1, min(limit, 200))

        try:
            offset = int((qs.get("offset") or ["0"])[0])
        except (ValueError, TypeError):
            offset = 0
        offset = max(0, offset)

        category = (qs.get("category") or ["all"])[0].strip() or "all"
        if category not in ("all", "img", "vid", "aud", "doc", "zip", "other"):
            category = "all"

        search = (qs.get("search") or [""])[0].strip()

        sort = (qs.get("sort") or ["mtime_desc"])[0].strip()
        if sort not in _VALID_SORTS:
            sort = "mtime_desc"

        with META_LOCK:
            meta = _load_meta()
            dirty = False
            all_entries = []

            try:
                with os.scandir(SHARED_DIR) as it:
                    dir_entries = list(it)
            except OSError:
                dir_entries = []

            for entry in dir_entries:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                name = entry.name
                if not can_access(name, me, meta):
                    continue

                info = meta.get(name)
                if info is None:
                    now = time.time()
                    info = {
                        "from_client": "",
                        "from_name":   "",
                        "to_clients":  [],
                        "to_names":    [],
                        "uploaded_at": now,
                        "added_at":    now,
                    }
                    meta[name] = info
                    dirty = True
                elif "added_at" not in info:
                    info["added_at"] = int(st.st_mtime)
                    dirty = True

                all_entries.append((name, st, info))

            if dirty:
                _save_meta(meta)

        etag_raw = (
            _compute_files_etag(me, all_entries)
            + f"-{limit}-{offset}-{category}-{sort}-{search}"
        )
        etag = '"' + hashlib.md5(etag_raw.encode("utf-8", "replace")).hexdigest() + '"'

        inm = ""
        if self.headers:
            inm = self.headers.get("If-None-Match", "") or ""

        if inm == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        counts = {"all": len(all_entries), "img": 0, "vid": 0,
                  "aud": 0, "doc": 0, "zip": 0, "other": 0}
        for name, st, info in all_entries:
            cat = _file_category(name)
            counts[cat] = counts.get(cat, 0) + 1

        if category != "all":
            all_entries = [e for e in all_entries
                           if _file_category(e[0]) == category]

        if search:
            s = search.lower()
            all_entries = [e for e in all_entries if s in e[0].lower()]

        if sort == "mtime_desc":
            key = lambda x: int(x[2].get("added_at") or x[1].st_mtime)
            reverse = True
        elif sort == "mtime_asc":
            key = lambda x: int(x[2].get("added_at") or x[1].st_mtime)
            reverse = False
        elif sort == "size_desc":
            key = lambda x: x[1].st_size
            reverse = True
        elif sort == "size_asc":
            key = lambda x: x[1].st_size
            reverse = False
        elif sort == "name_asc":
            key = lambda x: x[0].lower()
            reverse = False
        elif sort == "name_desc":
            key = lambda x: x[0].lower()
            reverse = True
        else:
            key = lambda x: int(x[2].get("added_at") or x[1].st_mtime)
            reverse = True

        all_entries.sort(key=key, reverse=reverse)

        total = len(all_entries)
        page_entries = all_entries[offset:offset + limit]

        items = []
        for name, st, info in page_entries:
            to_keys = _get_to_clients(info)
            to_nms  = _get_to_names(info)
            items.append({
                "name": name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "added_at": int(info.get("added_at") or st.st_mtime),
                "from_name": info.get("from_name", "") or "",
                "to_clients": to_keys,
                "to_names": to_nms,
                "is_mine": _is_file_mine(info, me),
            })

        body = json.dumps({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": counts,
        }, ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---------------- 在线设备 ----------------

    def _handle_clients(self):
        me = self._me()
        out = []
        for c in get_active_clients():
            out.append({
                "id": c["key"],
                "ip": c["ip"],
                "name": c["name"],
                "is_self": c["key"] == me,
            })
        out.sort(key=lambda x: (not x["is_self"], x["name"]))
        self._json(out)

    # ---------------- OOXML 预览（docx/xlsx/pptx） ----------------

    def _handle_ooxml(self, qs):
        name = os.path.basename((qs.get("name") or [""])[0].strip())
        if not name:
            self._json({"error": "缺少文件名"}, 400)
            return

        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in _OFFICE_EXTS:
            self._json({"error": "不支持的文件类型"}, 400)
            return

        full = os.path.join(SHARED_DIR, name)
        if not os.path.isfile(full):
            self._json({"error": "文件不存在"}, 404)
            return

        me = self._me()
        meta = _load_meta()
        if not can_access(name, me, meta):
            self._json({"error": "无访问权限"}, 403)
            return

        # ---------- 优先：LibreOffice 转 PDF ----------
        if _find_soffice():
            pdf_path = _ensure_office_pdf(name, full)
            if pdf_path:
                # 顺手做一次轻量 GC
                _cleanup_office_pdf_cache()
                self._json({
                    "mode": "pdf",
                    "url": "/api/office-pdf?name=" + urllib.parse.quote(name),
                })
                return
            # 转换失败 → 落到下面的 HTML 兜底

        # ---------- 兜底：纯 Python 解析（仅限 OOXML 系列） ----------
        if ext in _OOXML_EXTS:
            try:
                html = _render_ooxml(full, ext)
            except Exception as e:
                push_log(f"[error] 解析 {ext} 失败 {name}: {e}")
                html = None

            if html is not None:
                self._json({"mode": "html", "html": html})
                return

        # 走到这里说明：
        # - 没装 LibreOffice（或转换失败）
        # - 又不是 OOXML 系列
        # → 只能提示下载
        self._json({"error": "无法预览该文件（请下载后用 Office 打开）"}, 422)

    # ---------------- Office → PDF（流式下载，支持 Range） ----------------

    def _handle_office_pdf(self, qs):
        name = os.path.basename((qs.get("name") or [""])[0].strip())
        if not name:
            self._send(400, "缺少文件名")
            return

        full = os.path.join(SHARED_DIR, name)
        if not os.path.isfile(full):
            self._send(404, "文件不存在")
            return

        me = self._me()
        meta = _load_meta()
        if not can_access(name, me, meta):
            self._send(403, "无访问权限")
            return

        if not _find_soffice():
            self._send(404, "LibreOffice 不可用")
            return

        pdf_path = _ensure_office_pdf(name, full)
        if not pdf_path or not os.path.isfile(pdf_path):
            self._send(500, "文档转换失败")
            return

        try:
            size = os.path.getsize(pdf_path)
        except OSError:
            self._send(500, "文件读取失败")
            return

        # 解析 Range（PDF.js 会用）
        range_header = (self.headers.get("Range", "") if self.headers else "") or ""
        start, end = 0, size - 1
        is_range = False

        if range_header.startswith("bytes="):
            try:
                spec = range_header[6:].split(",")[0].strip()
                if "-" in spec:
                    s_str, e_str = spec.split("-", 1)
                    s_str = s_str.strip()
                    e_str = e_str.strip()
                    if s_str == "" and e_str:
                        n = int(e_str)
                        if n > 0:
                            start = max(0, size - n)
                            end = size - 1
                            is_range = True
                    elif s_str and e_str == "":
                        start = int(s_str)
                        end = size - 1
                        is_range = True
                    elif s_str and e_str:
                        start = int(s_str)
                        end = int(e_str)
                        is_range = True

                    if is_range:
                        if start < 0 or end >= size or start > end:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
            except (ValueError, IndexError):
                is_range = False
                start, end = 0, size - 1

        length = end - start + 1

        if is_range:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)

        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if self.command == "HEAD":
            return

        try:
            with open(pdf_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- 下载 ----------------

    def _serve_file(self, name, inline=False):
        safe = os.path.basename(name)
        full = os.path.join(SHARED_DIR, safe)
        if not safe or not os.path.isfile(full):
            self._send(404, "File not found")
            return

        me = self._me()
        meta = _load_meta()
        if not can_access(safe, me, meta):
            self._send(403, "Forbidden")
            return

        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        prefix = "inline" if inline else "attachment"
        disp = prefix + "; filename*=UTF-8''" + urllib.parse.quote(safe)

        range_header = (self.headers.get("Range", "") if self.headers else "") or ""
        start, end = 0, size - 1
        is_range = False

        if range_header.startswith("bytes="):
            try:
                spec = range_header[6:].split(",")[0].strip()
                if "-" in spec:
                    s_str, e_str = spec.split("-", 1)
                    s_str = s_str.strip()
                    e_str = e_str.strip()
                    if s_str == "" and e_str:
                        n = int(e_str)
                        if n > 0:
                            start = max(0, size - n)
                            end = size - 1
                            is_range = True
                    elif s_str and e_str == "":
                        start = int(s_str)
                        end = size - 1
                        is_range = True
                    elif s_str and e_str:
                        start = int(s_str)
                        end = int(e_str)
                        is_range = True

                    if is_range:
                        if start < 0 or end >= size or start > end:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
            except (ValueError, IndexError):
                is_range = False
                start, end = 0, size - 1

        length = end - start + 1

        if is_range:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)

        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Disposition", disp)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if self.command == "HEAD":
            return

        tid = None
        if not inline:
            tid = _new_transfer("down", safe, size, start, self._me_name())

        sent = 0
        try:
            with open(full, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
                    remaining -= len(chunk)
                    if tid is not None:
                        _update_transfer(tid, start + sent)

            if tid is not None:
                _finish_transfer(tid, True)
        except (BrokenPipeError, ConnectionResetError):
            if tid is not None:
                _pause_transfer(tid, "客户端断开")
        except Exception as e:
            if tid is not None:
                _pause_transfer(tid, str(e))

    def _serve_thumb(self, name):
        """
        缩略图端点。

        策略：
          - 缩略图已生成 → 返回缩略图（JPEG，几 KB），强缓存
          - 缩略图未生成 + Pillow 可用 → 302 重定向到内嵌占位图，
            同时后台触发生成；前端识别占位图后会自动重试
          - 缩略图未生成 + Pillow 不可用 → 回退原图（唯一例外）
        """
        safe = os.path.basename(name)
        if not safe:
            self._send(404, "Not Found")
            return

        shared_path = os.path.join(SHARED_DIR, safe)
        if not os.path.isfile(shared_path):
            self._send(404, "Not Found")
            return

        me = self._me()
        meta = _load_meta()
        if not can_access(safe, me, meta):
            self._send(403, "Forbidden")
            return

        thumb = _thumb_path(safe)

        if os.path.isfile(thumb):
            # ---- 缩略图就绪 ----
            full = thumb
            ctype = "image/jpeg"
            cache_control = "public, max-age=86400"
            size = os.path.getsize(full)
            mtime = int(os.path.getmtime(full))
            etag = f'W/"{mtime}-{size}"'

        else:
            # ---- 缩略图未就绪 ----
            src_ctype = mimetypes.guess_type(safe)[0] or ""

            if _HAS_PIL and src_ctype.startswith("image/"):
                # 触发后台生成，同时 302 到占位图
                _generate_thumbnail_async(shared_path, safe)

                self.send_response(302)
                self.send_header("Location", _THUMB_PLACEHOLDER_PATH)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            # 没 Pillow：只能返回原图
            full = shared_path
            ctype = src_ctype or "application/octet-stream"
            cache_control = "no-cache"
            size = os.path.getsize(full)
            mtime = int(os.path.getmtime(full))
            etag = f'W/"{mtime}-{size}"'

        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        self.end_headers()

        if self.command == "HEAD":
            return

        try:
            with open(full, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- /qr ----------------

    def _serve_qr_page(self, qs):
        host = self.headers.get("Host", "")
        if not host or host.startswith("127.") or host.startswith("localhost"):
            ips = get_lan_ips()
            host = f"{ips[0]}:{PORT}" if ips else f"localhost:{PORT}"
        url = f"http://{host}/"
        try:
            svg = qr_svg(qr_encode(url))
        except Exception as e:
            svg = f'<div style="color:#b91c1c">二维码生成失败: {e}</div>'

        template = read_web_file("qr.html")
        if template is None:
            self._send(500, "web/qr.html 未找到")
            return
        html = template.replace("{{QR_SVG}}", svg).replace("{{URL}}", url)
        self._send(200, html, "text/html; charset=utf-8")

    # ---------------- 上传状态 ----------------

    def _handle_status(self, qs):
        name = os.path.basename((qs.get("name") or [""])[0].strip())
        try:
            size = int((qs.get("size") or ["0"])[0])
        except ValueError:
            self._json({"error": "bad size"}, 400)
            return
        if not name or size <= 0:
            self._json({"error": "bad params"}, 400)
            return

        cid = self._resolve_cid(qs=qs)
        part = _part_path(name, size, cid)

        meta = _load_meta()
        info = meta.get(name, {})
        final = os.path.join(SHARED_DIR, name)

        if (os.path.isfile(final)
                and os.path.getsize(final) == size
                and info.get("cid") == cid):
            try:
                if os.path.isfile(part):
                    os.remove(part)
                    push_log(f"🧹 清理残留分片 {os.path.basename(part)}")
            except OSError:
                pass
            self._json({"offset": size, "complete": True, "name": name})
            return

        if os.path.isfile(part):
            offset = os.path.getsize(part)
            if offset >= size:
                self._json({"offset": offset, "complete": True, "name": name})
                return
            self._json({"offset": offset, "complete": False})
        else:
            self._json({"offset": 0, "complete": False})

    # ---------------- 上传 ----------------

    def _handle_upload(self, qs):
        name = os.path.basename((qs.get("name") or [""])[0].strip())
        try:
            size = int((qs.get("size") or ["0"])[0])
            offset = int((qs.get("offset") or ["0"])[0])
        except ValueError:
            self._send(400, "Bad params")
            return
        if not name or size <= 0 or offset < 0 or offset > size:
            self._send(400, "Bad params")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, "Bad Content-Length")
            return
        if length < 0:
            self._send(400, "Bad Content-Length")
            return

        to_raw = (qs.get("to") or [""])[0].strip()
        to_clients = [x.strip() for x in to_raw.split(",") if x.strip()]
        from_client = self._me()
        cid = self._resolve_cid(qs=qs)

        os.makedirs(PARTIAL_DIR, exist_ok=True)
        part = _part_path(name, size, cid)

        try:
            if os.path.exists(part):
                cur = os.path.getsize(part)
                if cur > offset:
                    with open(part, "r+b") as f:
                        f.truncate(offset)
                elif cur < offset:
                    with open(part, "r+b") as f:
                        f.seek(0, 2)
                        f.write(b"\x00" * (offset - cur))
            else:
                offset = 0
        except OSError as e:
            self._send(500, f"prepare failed: {e}")
            return

        who = self._me_name()
        tid = _new_transfer("up", name, size, offset, who, from_client, cid)

        received = offset
        try:
            mode = "r+b" if os.path.exists(part) else "w+b"
            with open(part, mode) as f:
                f.seek(offset)
                remaining = length
                while remaining > 0 and received < size:
                    to_read = min(CHUNK, remaining, size - received)
                    chunk = self.rfile.read(to_read)
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
                    received += len(chunk)
                    _update_transfer(tid, received)
        except Exception as e:
            _pause_transfer(tid, str(e) if str(e) else "连接中断")
            try:
                self._send(500, f"write failed: {e}")
            except Exception:
                pass
            return

        if received >= size:
            base, ext = os.path.splitext(name)
            with _FINALIZE_LOCK:
                target = os.path.join(SHARED_DIR, name)
                n = 1
                while os.path.exists(target):
                    target = os.path.join(SHARED_DIR, f"{base}({n}){ext}")
                    n += 1
                try:
                    shutil.move(part, target)
                except OSError as e:
                    _pause_transfer(tid, str(e))
                    self._send(500, f"finalize failed: {e}")
                    return

            final_name = os.path.basename(target)
            from_name = get_client_name(from_client) or from_client
            to_names = [get_client_name(t) or t for t in to_clients]
            set_file_meta(final_name, from_client, to_clients,
                          from_name, to_names, cid)

            _generate_thumbnail_async(target, final_name)

            _finish_transfer(tid, True)
            if to_clients:
                names_str = "、".join(to_names)
                push_log(f"✓ {who} → {names_str} 发送 "
                         f"{final_name} ({fmt_bytes(size)})")
            else:
                push_log(f"✓ 收到 {final_name} ({fmt_bytes(size)}) 来自 {who}")

            self._json({"offset": size, "complete": True, "name": final_name})
        else:
            _pause_transfer(tid, "部分写入，等待续传")
            self._json({"offset": received, "complete": False, "name": name}, 422)

    # ---------------- 取消上传 ----------------

    def _handle_cancel(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "bad length"}, 400)
            return
        if length <= 0 or length > 65536:
            self._json({"error": "bad length"}, 400)
            return

        try:
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        name = os.path.basename((data.get("name") or "").strip())
        try:
            size = int(data.get("size") or 0)
        except (ValueError, TypeError):
            size = 0

        if not name or size <= 0:
            self._json({"error": "bad params"}, 400)
            return

        cid = self._resolve_cid(data=data)
        me = self._me()
        ok = _cancel_transfer(name, size, me, cid)
        if not ok:
            self._json({"error": "not owner"}, 403)
            return

        push_log(f"✗ {self._me_name()} 取消上传 {name}")
        self._json({"ok": True, "name": name, "size": size})

    # ---------------- 多选打包下载 ----------------

    def _handle_zip(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "bad length"}, 400)
            return
        if length <= 0 or length > 1024 * 1024:
            self._json({"error": "bad length"}, 400)
            return

        try:
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        names = data.get("names") if isinstance(data, dict) else None
        if not isinstance(names, list) or not names:
            self._json({"error": "no files"}, 400)
            return
        if len(names) > 200:
            self._json({"error": "too many files (max 200)"}, 400)
            return

        me = self._me()
        meta = _load_meta()

        safe_names = []
        seen = set()
        for n in names:
            safe = os.path.basename(str(n).strip())
            if not safe or safe in seen:
                continue
            seen.add(safe)
            full = os.path.join(SHARED_DIR, safe)
            if not os.path.isfile(full):
                continue
            if not can_access(safe, me, meta):
                continue
            safe_names.append(safe)

        if not safe_names:
            self._json({"error": "no accessible files"}, 403)
            return

        fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="lanshare-")
        os.close(fd)

        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name in safe_names:
                    full = os.path.join(SHARED_DIR, name)
                    try:
                        zf.write(full, arcname=name)
                    except OSError:
                        continue

            size = os.path.getsize(tmp_path)
            ts = time.strftime("%Y%m%d-%H%M%S")
            if len(safe_names) == 1:
                base = os.path.splitext(safe_names[0])[0]
                filename = base + ".zip"
            else:
                filename = f"lanshare-{ts}.zip"

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" +
                             urllib.parse.quote(filename))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            if self.command == "HEAD":
                return

            try:
                with open(tmp_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

            push_log(f"📦 打包下载 {len(safe_names)} 个文件 "
                     f"({fmt_bytes(size)}) - {self._me_name()}")

        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # ---------------- 修改权限 ----------------

    def _handle_permission(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "bad length"}, 400)
            return
        if length <= 0 or length > 65536:
            self._json({"error": "bad length"}, 400)
            return

        try:
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        name = os.path.basename((data.get("name") or "").strip())
        if not name:
            self._json({"error": "missing name"}, 400)
            return

        to_clients = data.get("to_clients")
        if to_clients is None:
            old = (data.get("to") or "").strip()
            to_clients = [old] if old else []
        if not isinstance(to_clients, list):
            to_clients = []
        to_clients = [str(x).strip() for x in to_clients if str(x).strip()]

        full = os.path.join(SHARED_DIR, name)
        if not os.path.isfile(full):
            self._json({"error": "file not found"}, 404)
            return

        me = self._me()
        meta = _load_meta()
        info = meta.get(name, {})
        from_key = client_key(info.get("from_client", ""))

        if not from_key:
            if me != _LOCAL_KEY:
                self._json(
                    {"error": "该文件由服务端管理，只有服务端可以修改权限"},
                    403)
                return
        elif from_key != me:
            self._json({"error": "只有发送者可以修改权限"}, 403)
            return

        from_name = get_client_name(me) or me
        to_names = [get_client_name(t) or t for t in to_clients]

        set_file_meta(name, me, to_clients, from_name, to_names)

        if to_clients:
            names_str = "、".join(to_names)
            push_log(f"🔒 {from_name} 把 {name} 改为仅 {names_str} 可见")
        else:
            push_log(f"🔓 {from_name} 把 {name} 改为公开")

        self._json({"ok": True, "name": name,
                    "to_clients": to_clients,
                    "to_names": to_names})


# =====================================================================
#  IP 探测
# =====================================================================

def get_lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            main = s.getsockname()[0]
            if main and not main.startswith("127."):
                ips.append(main)
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if not (ip.startswith("10.") or
                    ip.startswith("192.168.") or
                    (ip.startswith("172.") and
                     16 <= int(ip.split(".")[1]) <= 31)):
                continue
            if ip not in ips:
                ips.append(ip)
    except (OSError, ValueError):
        pass
    return ips


# =====================================================================
#  GUI
# =====================================================================

class App:
    def __init__(self, root):
        self.root = root
        self.httpd = None
        self.port = PORT
        self.stop_event = threading.Event()
        self.last_ips = None
        self.last_log_id = 0
        self._last_client_key = None
        self._transfer_rows = {}
        self._transfer_empty = None
        self._transfer_order = None

        root.title("局域网互传")
        root.geometry("980x700")
        root.minsize(840, 580)

        self._build_ui()
        self._start_server()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._ip_watcher, daemon=True).start()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self.root, padding=(18, 14, 18, 6))
        top.pack(fill="x")

        ttk.Label(top, text="局域网互传",
                  font=("", 15, "bold")).pack(side="left")

        self.status_var = tk.StringVar(value="● 运行中")
        self.status_label = ttk.Label(top, textvariable=self.status_var,
                                       foreground="#16a34a",
                                       font=("", 10, "bold"))
        self.status_label.pack(side="left", padx=(14, 0))

        self.info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.info_var,
                  foreground="#6b7280",
                  font=("", 9)).pack(side="right")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x",
                                                            padx=18,
                                                            pady=(0, 8))

        main = ttk.Frame(self.root, padding=(18, 6, 18, 6))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0, minsize=270)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(main, text="扫码连接", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        self.qr_canvas = tk.Canvas(left, width=240, height=240,
                                    bg="#ffffff", highlightthickness=0, bd=0)
        self.qr_canvas.pack(anchor="n")

        self.qr_hint = ttk.Label(left, text="正在获取 IP…",
                                  foreground="#6b7280",
                                  wraplength=240, justify="center",
                                  font=("", 9))
        self.qr_hint.pack(pady=(10, 0))

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)

        clients_box = ttk.LabelFrame(right, text="在线设备", padding=10)
        clients_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.clients_frame = ttk.Frame(clients_box)
        self.clients_frame.pack(fill="x")
        self._clients_widgets = []

        urls_box = ttk.LabelFrame(right, text="访问地址（点击复制）", padding=12)
        urls_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.urls_frame = ttk.Frame(urls_box)
        self.urls_frame.pack(fill="x")

        btns = ttk.Frame(right)
        btns.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(btns, text="打开共享文件夹",
                   command=self._open_folder).pack(side="left")
        ttk.Button(btns, text="更改共享目录…",
                   command=self._choose_folder).pack(side="left", padx=(8, 0))

        transfers_box = ttk.LabelFrame(right, text="正在传输", padding=10)
        transfers_box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.transfers_frame = ttk.Frame(transfers_box)
        self.transfers_frame.pack(fill="x")
        self.transfers_frame.columnconfigure(0, weight=1)

        logs_box = ttk.LabelFrame(right, text="活动日志", padding=8)
        logs_box.grid(row=4, column=0, sticky="nsew")
        logs_box.columnconfigure(0, weight=1)
        logs_box.rowconfigure(0, weight=1)

        self.logs_text = tk.Text(
            logs_box, height=8, wrap="word",
            font=("Consolas", 9) if sys.platform == "win32" else ("Menlo", 10),
            bg="#fafafa", fg="#333", relief="flat", bd=0,
            padx=8, pady=6, state="disabled",
        )
        self.logs_text.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(logs_box, orient="vertical",
                            command=self.logs_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.logs_text.configure(yscrollcommand=sb.set)

        self.logs_text.tag_configure("time", foreground="#9ca3af")
        self.logs_text.tag_configure("ok", foreground="#16a34a")
        self.logs_text.tag_configure("warn", foreground="#d97706")

    def _start_server(self):
        os.makedirs(SHARED_DIR, exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(PARTIAL_DIR, exist_ok=True)
        os.makedirs(THUMB_DIR, exist_ok=True)
        _migrate_legacy_data()

        httpd, port = None, PORT
        for p in range(PORT, PORT + 20):
            try:
                httpd = ThreadingHTTPServer(("0.0.0.0", p), Handler)
                port = p
                break
            except OSError:
                continue
        if httpd is None:
            raise RuntimeError(f"端口 {PORT}-{PORT + 19} 都被占用")

        self.httpd = httpd
        self.port = port
        push_log(f"✓ 服务已启动，端口 {port}")

        if _HAS_PIL:
            extra = "（含 HEIC）" if _HAS_HEIF else ""
            push_log(f"🖼 缩略图已启用{extra}")
        else:
            push_log("⚠️ 未检测到 Pillow，图片缩略图将使用原图")

        # 探测 LibreOffice，尽早把结果写进日志
        try:
            _find_soffice()
        except Exception:
            pass

    def _stop_server(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    def _ip_watcher(self):
        if not hasattr(self, "_last_known_ips"):
            self._last_known_ips = None

        while not self.stop_event.is_set():
            try:
                cur = get_lan_ips()
            except Exception:
                cur = []
            cur_set = set(cur)

            if cur_set and self._last_known_ips and \
                    not (set(self._last_known_ips) & cur_set):
                n = _reset_clients_for_new_network()
                if n > 0:
                    push_log(f"🌐 网络已切换，清空 {n} 个旧设备的在线状态")
                else:
                    push_log("🌐 网络已切换")
            if cur_set:
                self._last_known_ips = list(cur_set)

            if cur != self.last_ips:
                self.last_ips = list(cur)
                if cur:
                    push_log(f"🔁 当前 IP: {', '.join(cur)}")
                else:
                    push_log("⚠️ 未检测到局域网 IP")

            if self.stop_event.wait(3):
                break

    def _poll(self):
        if self.stop_event.is_set():
            return
        self._update_header()
        self._refresh_info()
        self._refresh_clients()
        self._refresh_transfers()
        self._refresh_logs()
        self.root.after(500, self._poll)

    def _update_header(self):
        p = SHARED_DIR
        if len(p) > 38:
            p = "…" + p[-35:]
        self.info_var.set(f"端口 {self.port}  ·  {p}")

    def _refresh_clients(self):
        clients = get_active_clients()

        rows = []
        seen = {}
        for c in clients:
            is_self = c["key"] == _LOCAL_KEY
            label = c["name"] or c["ip"]
            if is_self:
                label = label + "（本机）"
            if label in seen:
                seen[label] += 1
                label = f"{label} ({seen[label]})"
            else:
                seen[label] = 1
            rows.append((c["ip"], label))

        key = tuple((ip, label) for ip, label in rows)
        if key == self._last_client_key:
            return
        self._last_client_key = key

        for w in self._clients_widgets:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._clients_widgets.clear()

        if not rows:
            lbl = ttk.Label(self.clients_frame, text="暂无设备连接",
                            foreground="#9ca3af", font=("", 9))
            lbl.pack(anchor="w")
            self._clients_widgets.append(lbl)
            return

        for ip, label in rows:
            row = ttk.Frame(self.clients_frame)
            row.pack(fill="x", pady=1)
            dot = ttk.Label(row, text="●", foreground="#16a34a", font=("", 9))
            dot.pack(side="left", padx=(0, 6))
            name_lbl = ttk.Label(row, text=label, font=("", 9, "bold"))
            name_lbl.pack(side="left")
            ip_lbl = ttk.Label(row, text=f"  {ip}", foreground="#9ca3af",
                                font=("Consolas", 8))
            ip_lbl.pack(side="left")
            self._clients_widgets.extend([row, dot, name_lbl, ip_lbl])

    def _refresh_info(self):
        ips = self.last_ips or []
        key = "|".join(ips)

        if getattr(self, "_qr_key", None) != key:
            self._qr_key = key
            self._draw_qr(ips)

        if getattr(self, "_urls_key", None) != key:
            self._urls_key = key
            for w in self.urls_frame.winfo_children():
                w.destroy()
            if not ips:
                ttk.Label(self.urls_frame, text="未检测到局域网 IP",
                          foreground="#d97706").pack(anchor="w")
            else:
                for ip in ips:
                    url = f"http://{ip}:{self.port}/"
                    btn = tk.Label(self.urls_frame, text=url,
                                   fg="#2563eb", cursor="hand2",
                                   font=("Consolas", 10), anchor="w")
                    btn.pack(fill="x", pady=2)
                    btn.bind("<Button-1>", lambda e, u=url: self._copy_url(u))

    def _refresh_transfers(self):
        _gc_transfers(5.0)
        with TRANSFERS_LOCK:
            items = [dict(t) for t in TRANSFERS.values()]

        def sort_key(x):
            if x.get("paused"):
                return 0
            if not x["done"]:
                return 1
            return 2

        items.sort(key=lambda x: (sort_key(x), x["t0"]))
        items = items[:6]

        current_ids = {t["id"] for t in items}
        for tid in list(self._transfer_rows.keys()):
            if tid not in current_ids:
                widgets = self._transfer_rows.pop(tid)
                try:
                    widgets["frame"].destroy()
                except tk.TclError:
                    pass

        for t in items:
            tid = t["id"]
            if tid in self._transfer_rows:
                self._update_transfer_row(tid, t)
            else:
                self._create_transfer_row(tid, t)

        order_now = [t["id"] for t in items]
        if order_now != self._transfer_order:
            self._transfer_order = order_now
            for tid in order_now:
                self._transfer_rows[tid]["frame"].pack_forget()
            for tid in order_now:
                self._transfer_rows[tid]["frame"].pack(fill="x", pady=3)

        if not items:
            if self._transfer_empty is None:
                self._transfer_empty = ttk.Label(
                    self.transfers_frame, text="空闲",
                    foreground="#9ca3af")
                self._transfer_empty.pack(anchor="w")
        else:
            if self._transfer_empty is not None:
                try:
                    self._transfer_empty.destroy()
                except tk.TclError:
                    pass
                self._transfer_empty = None

    def _create_transfer_row(self, tid, t):
        row = ttk.Frame(self.transfers_frame)
        row.columnconfigure(0, weight=0, minsize=180)
        row.columnconfigure(1, weight=1)
        row.columnconfigure(2, weight=0, minsize=190)

        arrow = "⬆" if t["dir"] == "up" else "⬇"
        name = t["name"]
        if len(name) > 26:
            name = name[:12] + "…" + name[-11:]

        name_lbl = ttk.Label(row, text=f"{arrow} {name}",
                             anchor="w", font=("", 9))
        name_lbl.grid(row=0, column=0, sticky="w", padx=(0, 8))

        pb = ttk.Progressbar(row, mode="determinate", maximum=100.0, value=0)
        pb.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        slbl = ttk.Label(row, text="", anchor="e",
                         foreground="#374151", font=("", 9))
        slbl.grid(row=0, column=2, sticky="e")

        self._transfer_rows[tid] = {
            "frame": row, "name": name_lbl,
            "bar": pb, "status": slbl,
        }

    def _update_transfer_row(self, tid, t):
        w = self._transfer_rows.get(tid)
        if not w:
            return
        pct = (t["received"] / max(t["size"], 1)) * 100
        try:
            w["bar"]["value"] = pct
        except tk.TclError:
            return

        arrow = "⬆" if t["dir"] == "up" else "⬇"
        name = t["name"]
        if len(name) > 26:
            name = name[:12] + "…" + name[-11:]
        new_name = f"{arrow} {name}"
        if w["name"].cget("text") != new_name:
            w["name"].configure(text=new_name)

        if t.get("paused"):
            status = f"⏸ 已暂停 · {fmt_bytes(t['received'])}"
            color = "#d97706"
        elif t["done"]:
            if t["ok"]:
                status = "✓ 完成"; color = "#16a34a"
            else:
                status = "✗ 失败"; color = "#b91c1c"
        else:
            sp = t.get("speed", 0.0)
            if sp > 1000:
                remain = (t["size"] - t["received"]) / sp
                status = f"{pct:.0f}%  {fmt_speed(sp)}  剩余 {int(remain)}s"
            else:
                status = f"{pct:.0f}%"
            color = "#374151"

        if w["status"].cget("text") != status:
            w["status"].configure(text=status, foreground=color)

    def _refresh_logs(self):
        with LOG_LOCK:
            items = [x for x in LOG_BUFFER if x["id"] > self.last_log_id]
            max_id = LOG_COUNTER

        if items:
            self.logs_text.configure(state="normal")
            for it in items:
                t = time.strftime("%H:%M:%S", time.localtime(it["t"]))
                self.logs_text.insert("end", f"[{t}] ", ("time",))
                msg = it["msg"]
                tag = ()
                if msg.startswith("✓"):
                    tag = ("ok",)
                elif msg.startswith("⚠️") or msg.startswith("[error]"):
                    tag = ("warn",)
                self.logs_text.insert("end", msg + "\n", tag)
            total = int(self.logs_text.index("end-1c").split(".")[0])
            if total > 800:
                self.logs_text.delete("1.0", f"{total - 600}.0")
            self.logs_text.see("end")
            self.logs_text.configure(state="disabled")
        if max_id > self.last_log_id:
            self.last_log_id = max_id

    def _draw_qr(self, ips):
        c = self.qr_canvas
        c.delete("all")
        if not ips:
            c.create_text(120, 110, text="⚠️", font=("", 32), fill="#d97706")
            c.create_text(120, 150, text="未检测到局域网 IP",
                          font=("", 10), fill="#6b7280")
            c.create_text(120, 172, text="请连接局域网",
                          font=("", 9), fill="#9ca3af")
            self.qr_hint.configure(text="等待网络连接…")
            return
        url = f"http://{ips[0]}:{self.port}/"
        try:
            matrix = qr_encode(url)
        except Exception as e:
            c.create_text(120, 120, text=f"二维码生成失败\n{e}",
                          font=("", 9), fill="#b91c1c")
            self.qr_hint.configure(text="")
            return
        n = len(matrix)
        quiet = 2
        total = n + 2 * quiet
        size = 240
        cell = size / total
        for y in range(n):
            for x in range(n):
                if matrix[y][x]:
                    x0 = (x + quiet) * cell
                    y0 = (y + quiet) * cell
                    c.create_rectangle(x0, y0,
                                        x0 + cell + 0.5, y0 + cell + 0.5,
                                        fill="#000000", outline="#000000")
        self.qr_hint.configure(text=url)

    def _copy_url(self, url):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self._flash_status(f"✓ 已复制 {url}")
        except tk.TclError:
            pass

    def _flash_status(self, text):
        self.status_var.set(text)
        self.status_label.configure(foreground="#2563eb")
        self.root.after(2000, lambda: (
            self.status_var.set("● 运行中"),
            self.status_label.configure(foreground="#16a34a"),
        ))

    def _open_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(SHARED_DIR)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", SHARED_DIR])
            else:
                subprocess.Popen(["xdg-open", SHARED_DIR])
        except Exception as e:
            push_log(f"[error] 打开文件夹失败: {e}")

    def _choose_folder(self):
        from tkinter import filedialog, messagebox
        with TRANSFERS_LOCK:
            active = [t for t in TRANSFERS.values()
                      if not t["done"] and not t.get("paused")]
        if active:
            ok = messagebox.askyesno(
                "确认切换",
                f"当前有 {len(active)} 个传输正在进行。\n"
                "切换共享目录会让这些传输中断，是否继续？")
            if not ok:
                return
        new_dir = filedialog.askdirectory(
            title="选择共享文件夹", initialdir=SHARED_DIR, mustexist=False)
        if not new_dir:
            return
        if os.path.abspath(new_dir) == os.path.abspath(SHARED_DIR):
            return
        ok, err = set_shared_dir(new_dir)
        if not ok:
            messagebox.showerror("切换失败", f"无法使用该目录：\n{err}")
            return
        push_log(f"📁 共享目录已切换到 {SHARED_DIR}")
        self._update_header()

    def on_close(self):
        self.stop_event.set()
        self._stop_server()
        self.root.destroy()


# =====================================================================
#  入口
# =====================================================================

def main():
    _has_web = (
        os.path.isdir(os.path.join(BASE_DIR, "web")) or
        os.path.isdir(os.path.join(BUNDLE_DIR, "web"))
    )
    if not _has_web:
        try:
            root = tk.Tk()
            root.withdraw()
            from tkinter import messagebox
            messagebox.showerror(
                "启动失败",
                f"未找到 web 目录。\n\n"
                f"脚本目录: {BASE_DIR}\n"
                f"资源目录: {BUNDLE_DIR}\n\n"
                "请确认程序同目录下有 web/ 文件夹，"
                "或打包时使用了 --add-data \"web;web\"。")
            root.destroy()
        except Exception:
            print("[error] 未找到 web 目录")
        return

    root = tk.Tk()
    try:
        app = App(root)
    except Exception as e:
        from tkinter import messagebox
        messagebox.showerror("启动失败", str(e))
        return

    def _on_sigint(signum, frame):
        try:
            root.after(0, app.on_close)
        except Exception:
            pass

    try:
        import signal
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, OSError):
        pass

    def _tick():
        try:
            root.after(200, _tick)
        except Exception:
            pass
    root.after(200, _tick)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        try:
            app.on_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
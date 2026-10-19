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
import tkinter.font as tkfont
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
CHUNK = 1024 * 1024        # 1 MB，read/write 回退路径的块大小

# sendfile 每段最多推送的字节数（分段是为了更新进度条）
_SENDFILE_CHUNK = 8 * 1024 * 1024

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
        "doc","docx","docm","dot","dotx","dotm","rtf","odt","ott","fodt",
        "xls","xlsx","xlsm","xlsb","xltx","xltm","ods","ots","fods",
        "ppt","pptx","pptm","pps","ppsx","pot","potx","potm","odp","otp","fodp",
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
# =====================================================================

_OOXML_EXTS = {"docx", "xlsx", "pptx"}

_OFFICE_EXTS = {
    "docx", "docm", "dotx", "dotm",
    "doc",  "dot",  "rtf",
    "odt",  "ott",  "fodt",

    "xlsx", "xlsm", "xltx", "xltm",
    "xls",  "xlsb",
    "ods",  "ots",  "fods",

    "pptx", "pptm", "potx", "potm",
    "ppt",  "pps",  "ppsx", "pot",
    "odp",  "otp",  "fodp",
}
_OOXML_MAX_FILE  = 200 * 1024 * 1024
_OOXML_MAX_PART  = 50  * 1024 * 1024
_XLSX_MAX_ROWS   = 500
_XLSX_MAX_COLS   = 50


def _xml_local_name(tag):
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        i = tag.find("}")
        return tag[i + 1:] if i >= 0 else tag
    return tag


def _safe_parse_xml(zf, name, max_size=_OOXML_MAX_PART):
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


def _xlsx_col_index(ref):
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
    return v


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

    ordered = []
    for rid, name in names_by_rid.items():
        if rid in rid_to_path:
            ordered.append((name, rid_to_path[rid]))

    if not ordered:
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
    try:
        size = os.path.getsize(full_path)
    except OSError:
        return None
    if size > _OOXML_MAX_FILE:
        return None
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

_SOFFICE_PATH = None
_SOFFICE_LOCK = threading.Lock()
_OFFICE_PDF_LOCK = threading.Lock()


def _find_soffice():
    global _SOFFICE_PATH
    with _SOFFICE_LOCK:
        if _SOFFICE_PATH is not None:
            return _SOFFICE_PATH

        candidates = []

        env = os.environ.get("SOFFICE_PATH")
        if env:
            candidates.append(env)

        for name in ("soffice", "libreoffice"):
            try:
                p = shutil.which(name)
            except Exception:
                p = None
            if p:
                candidates.append(p)

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
            push_log(f"[error] Office → PDF 转换异常: {name} - {e}")
            return None

        return cached


def _cleanup_office_pdf_cache(max_files=200):
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
#  回环地址判断
# =====================================================================

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def _is_loopback_host(host):
    if not host:
        return True
    h = host.strip()
    if h.startswith("["):
        end = h.find("]")
        if end > 0:
            h = h[1:end]
    elif ":" in h:
        h = h.rsplit(":", 1)[0]
    h = h.lower()
    return h in _LOOPBACK_HOSTS or h.startswith("127.")


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
#  设备名
# =====================================================================

_DEVICE_NAME_MAX = 24


def _clean_device_name(raw):
    if not raw:
        return ""
    s = "".join(ch for ch in str(raw) if ch.isprintable() or ch == " ")
    s = s.strip()
    if not s:
        return ""
    if len(s) > _DEVICE_NAME_MAX:
        s = s[:_DEVICE_NAME_MAX]
    return s


def _short_ip_suffix(ip):
    if not ip:
        return ""
    if ip in ("127.0.0.1", "::1", "localhost"):
        return ""
    if ":" in ip:
        return ip.rsplit(":", 1)[-1]
    return ip.rsplit(".", 1)[-1]


def _build_default_name(ua, ip):
    base = parse_ua(ua) or ""
    suffix = _short_ip_suffix(ip)
    if not suffix:
        return base
    if base:
        return f"{base} · …{suffix}"
    return f"设备 · …{suffix}"


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
#  文件元数据
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


def set_file_meta(name, from_key, to_keys, from_name, to_names,
                  cid=None, fresh=False):
    """
    写入文件元数据。

    fresh=True 表示磁盘上这是全新文件（老文件已被删除或改名），
    此时必须重置 added_at；否则沿用旧的 added_at，以保持"最新上传"排序稳定。
    """
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

        if not fresh and old.get("added_at"):
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
                    return old_tid, None
        else:
            for old_tid, old in list(TRANSFERS.items()):
                if (old["dir"] == "down"
                        and old["name"] == name
                        and old["size"] == size
                        and old.get("client_key") == client_key_val):
                    if old.get("done") and old.get("ok"):
                        old["completed_bytes"] = 0
                        old["chunks"] = {}
                        old["received"] = 0
                        old["speed"] = 0.0
                        old["done"] = False
                        old["ok"] = False
                        old["done_at"] = None

                    old["paused"] = False
                    old["error"] = None
                    old["client"] = client
                    old["t0"] = now
                    old["last_t"] = now

                    req_id = next(_TID)
                    old.setdefault("completed_bytes", 0)
                    chunks = old.setdefault("chunks", {})
                    chunks[req_id] = 0
                    old["received"] = (old["completed_bytes"]
                                       + sum(chunks.values()))
                    old["last_b"] = old["received"]

                    return old_tid, req_id

        tid = next(_TID)
        rec = {
            "id": tid, "dir": direction, "name": name, "size": size,
            "received": offset, "t0": now, "last_t": now, "last_b": offset,
            "speed": 0.0, "done": False, "ok": False,
            "paused": False, "error": None, "client": client,
            "client_key": client_key_val,
            "cid": cid,
        }
        if direction == "down":
            req_id = next(_TID)
            rec["completed_bytes"] = 0
            rec["chunks"] = {req_id: 0}
            rec["received"] = 0
        else:
            req_id = None
        TRANSFERS[tid] = rec
    return tid, req_id


def _update_transfer(tid, received, req_id=None):
    now = time.time()
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        if t.get("done") and t.get("ok"):
            return

        chunks = t.get("chunks")
        if req_id is not None and chunks is not None and req_id in chunks:
            chunks[req_id] = received
            received = t.get("completed_bytes", 0) + sum(chunks.values())

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


def _pause_transfer(tid, reason=None, req_id=None):
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        if t.get("done") and t.get("ok"):
            return

        chunks = t.get("chunks")
        if req_id is not None and chunks is not None and req_id in chunks:
            t["completed_bytes"] = (t.get("completed_bytes", 0)
                                    + chunks.pop(req_id))
            t["received"] = t["completed_bytes"] + sum(chunks.values())

        if not chunks:
            t["done"] = False
            t["ok"] = False
            t["paused"] = True
            t["paused_at"] = time.time()
            t["error"] = reason
            t["speed"] = 0.0


def _detach_chunk(tid, req_id):
    with TRANSFERS_LOCK:
        t = TRANSFERS.get(tid)
        if not t:
            return
        chunks = t.get("chunks")
        if chunks is None or req_id not in chunks:
            return
        t["completed_bytes"] = t.get("completed_bytes", 0) + chunks.pop(req_id)
        t["received"] = t["completed_bytes"] + sum(chunks.values())

        if t["received"] >= t["size"]:
            t["done"] = True
            t["ok"] = True
            t["paused"] = False
            t["error"] = None
            t["done_at"] = time.time()
            t["received"] = t["size"]
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
                continue
            if (t["dir"] == "down" and t.get("paused")
                    and now - t.get("paused_at", t["t0"]) > 30.0):
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
#  零拷贝文件推送
# =====================================================================

class _SendfileUnsupported(Exception):
    pass


def _stream_sendfile(sock, f, start, length, on_progress=None):
    sent = 0
    while sent < length:
        count = min(_SENDFILE_CHUNK, length - sent)
        try:
            n = os.sendfile(sock, f, start + sent, count)
        except InterruptedError:
            continue
        except OSError as e:
            if sent == 0:
                raise _SendfileUnsupported() from e
            raise
        if n == 0:
            break
        sent += n
        if on_progress is not None:
            on_progress(sent)
    return sent


def _stream_read_write(sock, f, start, length, on_progress=None):
    f.seek(start)
    sent = 0
    while sent < length:
        chunk = f.read(min(CHUNK, length - sent))
        if not chunk:
            break
        sock.sendall(chunk)
        sent += len(chunk)
        if on_progress is not None:
            on_progress(sent)
    return sent


def stream_file_to_socket(sock, f, start, length, on_progress=None):
    if hasattr(os, "sendfile"):
        try:
            return _stream_sendfile(sock, f, start, length, on_progress)
        except _SendfileUnsupported:
            pass
    return _stream_read_write(sock, f, start, length, on_progress)


# =====================================================================
#  HTTP
# =====================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "FileDrop/2.9"
    protocol_version = "HTTP/1.1"
    timeout = 60

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

    def _is_wechat(self):
        ua = (self.headers.get("User-Agent", "") if self.headers else "") or ""
        return "MicroMessenger" in ua

    def _qr_target_host(self, qs=None):
        if qs:
            explicit = (qs.get("host") or [""])[0].strip()
            if explicit:
                return explicit

        host = (self.headers.get("Host", "") if self.headers else "") or ""
        if not _is_loopback_host(host):
            return host

        try:
            port = self.server.server_port
        except Exception:
            port = PORT

        ips = get_lan_ips()
        return f"{ips[0]}:{port}" if ips else f"localhost:{port}"

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

            custom = ""
            if self.headers:
                raw = self.headers.get("X-Device-Name", "") or ""
                if raw:
                    try:
                        raw = urllib.parse.unquote(raw)
                    except Exception:
                        raw = ""
                    custom = _clean_device_name(raw)

            name = custom if custom else _build_default_name(ua, ip)
            touch_client(client_key(ip), ip, name)
        except Exception:
            pass

        try:
            super().handle_one_request()
        except (ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                TimeoutError):
            self.close_connection = True

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
        elif path == "/dl":
            self._serve_download_page(qs)

        elif path == "/api/qr.svg":
            host = self._qr_target_host(qs)
            url = f"http://{host}/"
            try:
                svg = qr_svg(qr_encode(url))
            except Exception:
                svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
                       'viewBox="0 0 100 100"><text x="50" y="55" '
                       'text-anchor="middle" font-size="12">生成失败</text></svg>')
            self._send(200, svg, "image/svg+xml; charset=utf-8")

        elif path == "/api/qr.json":
            host = self._qr_target_host(qs)
            url = f"http://{host}/"
            try:
                svg = qr_svg(qr_encode(url))
                self._json({"url": url, "svg": svg})
            except Exception as e:
                self._json({"url": url, "svg": "", "error": str(e)}, 500)

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

    def _serve_static(self, path):
        rel = path[len("/static/"):]

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

        if _find_soffice():
            pdf_path = _ensure_office_pdf(name, full)
            if pdf_path:
                _cleanup_office_pdf_cache()
                self._json({
                    "mode": "pdf",
                    "url": "/api/office-pdf?name=" + urllib.parse.quote(name),
                })
                return

        if ext in _OOXML_EXTS:
            try:
                html = _render_ooxml(full, ext)
            except Exception as e:
                push_log(f"[error] 解析 {ext} 失败 {name}: {e}")
                html = None

            if html is not None:
                self._json({"mode": "html", "html": html})
                return

        self._json({"error": "无法预览该文件（请下载后用 Office 打开）"}, 422)

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
        req_id = None
        if not inline and not self._is_wechat():
            tid, req_id = _new_transfer(
                "down", safe, size, start, self._me_name(),
                client_key_val=me)

        def _on_progress(sent_in_file):
            if tid is not None:
                _update_transfer(tid, sent_in_file, req_id)

        try:
            with open(full, "rb") as f:
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                stream_file_to_socket(
                    self.connection, f, start, length, _on_progress
                )

            if tid is not None:
                _detach_chunk(tid, req_id)
        except (BrokenPipeError, ConnectionResetError):
            if tid is not None:
                _pause_transfer(tid, "客户端断开", req_id)
        except Exception as e:
            if tid is not None:
                _pause_transfer(tid, str(e), req_id)

    def _serve_thumb(self, name):
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
            full = thumb
            ctype = "image/jpeg"
            cache_control = "public, max-age=86400"
            size = os.path.getsize(full)
            mtime = int(os.path.getmtime(full))
            etag = f'W/"{mtime}-{size}"'

        else:
            src_ctype = mimetypes.guess_type(safe)[0] or ""

            if _HAS_PIL and src_ctype.startswith("image/"):
                _generate_thumbnail_async(shared_path, safe)

                self.send_response(302)
                self.send_header("Location", _THUMB_PLACEHOLDER_PATH)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

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

    def _serve_qr_page(self, qs):
        host = self._qr_target_host(qs)
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

    def _serve_download_page(self, qs):
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

        try:
            st = os.stat(full)
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            self._send(500, "文件读取失败")
            return

        template = read_web_file("download.html")
        if template is None:
            self._send(500, "web/download.html 未找到")
            return

        head_block = None
        if _is_image_file(name):
            thumb_path = _thumb_path(name)
            if not os.path.isfile(thumb_path) and _HAS_PIL:
                with _THUMB_FAILED_LOCK:
                    failed = name in _THUMB_FAILED
                if not failed:
                    try:
                        _generate_thumbnail_sync(full, name)
                    except Exception:
                        pass

            if os.path.isfile(thumb_path):
                thumb_url = "/thumb/" + urllib.parse.quote(name)
                head_block = (
                    '<img class="thumb" id="thumbImg" src="'
                    + thumb_url + '" alt="">'
                )

        if head_block is None:
            head_block = (
                '<div class="icon">'
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/>'
                '<line x1="12" y1="15" x2="12" y2="3"/>'
                '</svg></div>'
            )

        html = (
            template
            .replace("{{FILENAME}}", _html.escape(name))
            .replace("{{FILENAME_ATTR}}", _html.escape(name, quote=True))
            .replace("{{SIZE}}", fmt_bytes(size))
            .replace("{{MTIME}}", time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(mtime)))
            .replace("{{HEAD_BLOCK}}", head_block)
        )
        self._send(200, html, "text/html; charset=utf-8")

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
        tid, _ = _new_transfer("up", name, size, offset, who, from_client, cid)

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
            # fresh=True：磁盘上这是全新文件，必须重置 added_at，
            # 否则同名文件被删除后重新上传仍会保留旧的排序时间。
            set_file_meta(final_name, from_client, to_clients,
                          from_name, to_names, cid, fresh=True)

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

        # 修改权限不应改变"上传时间"，fresh 保持默认 False
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
#  =============  现代化 GUI  =============
# =====================================================================

# ---------- 配色（与 web/style.css 保持一致） ----------
C_BG          = "#f5f7fb"
C_BG_SUB      = "#eef1f7"
C_CARD        = "#ffffff"
C_CARD_HOVER  = "#fafbff"
C_TEXT        = "#0f172a"
C_TEXT_2      = "#334155"
C_DIM         = "#64748b"
C_DIM_2       = "#94a3b8"
C_BORDER      = "#e6e9f0"
C_BORDER_2    = "#d8dde7"

C_ACCENT      = "#4f6bff"
C_ACCENT_2    = "#6f86ff"
C_ACCENT_HOV  = "#3d55e8"
C_ACCENT_SOFT = "#eef1ff"

C_SUCCESS     = "#10b981"
C_SUCCESS_SOFT= "#e6faf2"
C_WARN        = "#f59e0b"
C_WARN_SOFT   = "#fdf5e6"
C_DANGER      = "#ef4444"
C_DANGER_SOFT = "#fdecec"


def _pick_ui_font(root):
    """选择合适的中文字体，找不到就回退默认。"""
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    if sys.platform == "win32":
        cands = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei"]
    elif sys.platform == "darwin":
        cands = ["PingFang SC", "Hiragino Sans GB", "STHeiti"]
    else:
        cands = ["Noto Sans CJK SC", "Source Han Sans SC",
                 "WenQuanYi Micro Hei", "DejaVu Sans"]

    for f in cands:
        if f in families:
            return f
    return "TkDefaultFont"


def _pick_mono_font(root):
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    cands = ["JetBrains Mono", "SF Mono", "Menlo", "Consolas",
             "Cascadia Mono", "DejaVu Sans Mono", "Courier New"]
    for f in cands:
        if f in families:
            return f
    return "TkFixedFont"


def draw_round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上绘制平滑圆角矩形。r 会自动限制在最小边长一半内。"""
    if r <= 0:
        return canvas.create_rectangle(x1, y1, x2, y2, **kw)

    w = x2 - x1
    h = y2 - y1
    if w <= 0 or h <= 0:
        return None
    r = min(r, w / 2, h / 2)

    # 用多边形 + smooth 模拟圆角
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kw)


class Card(tk.Frame):
    """
    圆角卡片容器。
    - 背景用 Canvas 绘制圆角矩形
    - 内容放在 self.body 里（普通 Frame）
    - 高度由内容自适应；需要固定高度时由外部 pack/grid 控制
    """

    def __init__(self, parent, padding=(18, 16, 18, 16),
                 radius=14, bg=C_BG, card_bg=C_CARD,
                 border=C_BORDER, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self._bg = bg
        self._card_bg = card_bg
        self._radius = radius
        self._border = border
        self._pad = padding

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        pl, pt, pr, pb = padding
        # body 稍微向内缩 1px，让卡片边框可见
        self.body = tk.Frame(self, bg=card_bg)
        self.body.pack(fill="both", expand=True,
                       padx=(pl + 1, pr + 1), pady=(pt + 1, pb + 1))

        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, e):
        w, h = e.width, e.height
        if w < 6 or h < 6:
            return
        self._canvas.delete("__bg")
        draw_round_rect(self._canvas, 1, 1, w - 1, h - 1, self._radius,
                        fill=self._card_bg, outline=self._border, width=1,
                        tags="__bg")


class RoundButton(tk.Canvas):
    """圆角按钮，支持 hover / press。"""

    def __init__(self, parent, text="", command=None,
                 bg=C_BG, fill=C_ACCENT, fg="#ffffff",
                 hover_fill=C_ACCENT_HOV, hover_fg=None,
                 active_fill=None, active_fg=None,
                 border=None, radius=8, padx=16, pady=8,
                 font=None, min_width=0):
        self._text = text
        self._fill = fill
        self._fg = fg
        self._hover_fill = hover_fill
        self._hover_fg = hover_fg or fg
        self._active_fill = active_fill or hover_fill
        self._active_fg = active_fg or self._hover_fg
        self._command = command
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._font = font
        self._border = border
        self._bg = bg
        self._hovering = False
        self._pressing = False
        self._min_width = min_width

        # 计算初始尺寸
        if self._font is None:
            self._font = ("TkDefaultFont", 10)
        try:
            f = tkfont.Font(font=self._font)
            tw = f.measure(text) if text else 0
            th = f.metrics("linespace")
        except Exception:
            tw = len(text) * 8
            th = 16

        w = max(self._min_width, tw + padx * 2)
        h = th + pady * 2

        super().__init__(parent, width=w, height=h, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")

        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def set_text(self, text):
        self._text = text
        self._redraw()

    def _redraw(self, e=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("all")

        if self._pressing:
            fill = self._active_fill
            fg = self._active_fg
        elif self._hovering:
            fill = self._hover_fill
            fg = self._hover_fg
        else:
            fill = self._fill
            fg = self._fg

        r = min(self._radius, h // 2)
        if self._border:
            draw_round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, r,
                            fill=fill, outline=self._border, width=1)
        else:
            draw_round_rect(self, 0, 0, w, h, r, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text,
                         fill=fg, font=self._font)

    def _on_press(self, e):
        self._pressing = True
        self._redraw()

    def _on_release(self, e):
        self._pressing = False
        self._redraw()
        if self._command and 0 <= e.x <= self.winfo_width() \
                and 0 <= e.y <= self.winfo_height():
            self._command()

    def _on_enter(self, e):
        self._hovering = True
        self._redraw()

    def _on_leave(self, e):
        self._hovering = False
        self._pressing = False
        self._redraw()


class ModernProgressBar(tk.Canvas):
    """圆角、细线条的现代进度条。"""

    def __init__(self, parent, height=6, bg=C_CARD,
                 fill=C_ACCENT, track=C_BORDER, radius=3):
        super().__init__(parent, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._fill = fill
        self._track = track
        self._radius = radius
        self._value = 0.0
        self.bind("<Configure>", self._redraw)

    def set(self, value):
        value = max(0.0, min(100.0, value))
        if abs(value - self._value) < 0.5:
            return
        self._value = value
        self._redraw()

    def set_color(self, color):
        if color != self._fill:
            self._fill = color
            self._redraw()

    def _redraw(self, e=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("all")
        r = min(self._radius, h // 2)
        draw_round_rect(self, 0, 0, w, h, r, fill=self._track, outline="")
        fw = w * (self._value / 100.0)
        if fw > 0:
            # 极小时画成小圆头，视觉更干净
            if fw < 2 * r:
                self.create_oval(0, 0, 2 * r, h, fill=self._fill, outline="")
            else:
                draw_round_rect(self, 0, 0, fw, h, r,
                                fill=self._fill, outline="")


class _Badge(tk.Canvas):
    """小圆角数字徽章。"""

    def __init__(self, parent, bg=C_CARD, fill=C_ACCENT_SOFT,
                 fg=C_ACCENT, font=None, padx=8, radius=10):
        self._fill = fill
        self._fg = fg
        self._text = ""
        self._font = font or ("TkDefaultFont", 9, "bold")
        self._padx = padx
        self._radius = radius
        self._bg = bg
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         height=20, width=10)
        self.bind("<Configure>", self._redraw)

    def set(self, text):
        text = str(text)
        if text == self._text:
            return
        self._text = text
        # 根据文字更新请求宽度
        try:
            f = tkfont.Font(font=self._font)
            tw = f.measure(text) if text else 0
        except Exception:
            tw = len(text) * 7
        self.configure(width=max(20, tw + self._padx * 2 + 4))
        self._redraw()

    def _redraw(self, e=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("all")
        r = min(self._radius, h // 2)
        draw_round_rect(self, 0, 0, w, h, r, fill=self._fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text,
                         fill=self._fg, font=self._font)


class _DeviceRow(tk.Frame):
    """在线设备一行。"""

    def __init__(self, parent, name, ip, is_self=False, font=None):
        super().__init__(parent, bg=C_CARD)
        dot = tk.Label(self, text="●", bg=C_CARD,
                        fg=C_SUCCESS, font=(font, 9) if font else ("TkDefaultFont", 9))
        dot.pack(side="left", padx=(0, 8))

        text = name + ("（本机）" if is_self else "")
        name_lbl = tk.Label(self, text=text, bg=C_CARD, fg=C_TEXT_2,
                             font=(font or "TkDefaultFont", 10, "bold"),
                             anchor="w")
        name_lbl.pack(side="left")

        if ip:
            ip_lbl = tk.Label(self, text=ip, bg=C_CARD, fg=C_DIM_2,
                                font=(font or "TkDefaultFont", 9),
                                anchor="e")
            ip_lbl.pack(side="right")


class _TransferRow(tk.Frame):
    """正在传输的一行。"""

    def __init__(self, parent, font=None, mono_font=None):
        super().__init__(parent, bg=C_CARD)
        self._font = font or "TkDefaultFont"
        self._mono = mono_font or "TkFixedFont"

        # 顶行：箭头 + 文件名 + 关闭
        top = tk.Frame(self, bg=C_CARD)
        top.pack(fill="x")

        self.arrow = tk.Label(top, text="⬆", bg=C_CARD, fg=C_ACCENT,
                               font=(self._font, 11, "bold"))
        self.arrow.pack(side="left", padx=(0, 6))

        self.name_lbl = tk.Label(top, text="", bg=C_CARD, fg=C_TEXT,
                                  font=(self._font, 10), anchor="w",
                                  justify="left")
        self.name_lbl.pack(side="left", fill="x", expand=True)

        self.close_btn = tk.Label(top, text="✕", bg=C_CARD, fg=C_DIM_2,
                                   font=(self._font, 10), cursor="hand2",
                                   padx=4)
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Enter>",
                             lambda e: self.close_btn.configure(fg=C_DANGER))
        self.close_btn.bind("<Leave>",
                             lambda e: self.close_btn.configure(fg=C_DIM_2))

        # 进度条
        self.bar = ModernProgressBar(self, height=6, bg=C_CARD)
        self.bar.pack(fill="x", pady=(6, 4))

        # 底行：状态
        self.status_lbl = tk.Label(self, text="", bg=C_CARD, fg=C_DIM,
                                    font=(self._mono, 9), anchor="w")
        self.status_lbl.pack(fill="x")

    def set_close_handler(self, fn):
        self.close_btn.bind("<Button-1>", lambda e: fn())

    def update(self, t, fmt_bytes_fn, fmt_speed_fn):
        arrow = "⬆" if t["dir"] == "up" else "⬇"
        name = t["name"]
        if len(name) > 40:
            name = name[:18] + "…" + name[-19:]
        if self.name_lbl.cget("text") != name:
            self.name_lbl.configure(text=name)
        if self.arrow.cget("text") != arrow:
            self.arrow.configure(text=arrow)

        pct = (t["received"] / max(t["size"], 1)) * 100
        self.bar.set(pct)

        if t.get("paused"):
            status = f"⏸ 已暂停 · {fmt_bytes_fn(t['received'])} / {fmt_bytes_fn(t['size'])}"
            color = C_WARN
            bar_color = C_WARN
        elif t["done"]:
            if t["ok"]:
                status = f"✓ 完成 · {fmt_bytes_fn(t['size'])}"
                color = C_SUCCESS
                bar_color = C_SUCCESS
            else:
                status = f"✗ 失败 · {t.get('error') or ''}"
                color = C_DANGER
                bar_color = C_DANGER
        else:
            sp = t.get("speed", 0.0)
            if sp > 1000:
                remain = (t["size"] - t["received"]) / sp
                status = (f"{pct:.0f}%  ·  {fmt_speed_fn(sp)}  ·  "
                          f"剩余 {int(remain)}s  ·  "
                          f"{fmt_bytes_fn(t['received'])} / {fmt_bytes_fn(t['size'])}")
            else:
                status = (f"{pct:.0f}%  ·  "
                          f"{fmt_bytes_fn(t['received'])} / {fmt_bytes_fn(t['size'])}")
            color = C_DIM
            bar_color = C_ACCENT

        if self.status_lbl.cget("text") != status:
            self.status_lbl.configure(text=status, fg=color)
        self.bar.set_color(bar_color)


# =====================================================================
#  主 App（GUI）
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
        self._transfer_rows = {}   # tid -> _TransferRow
        self._transfer_empty = None
        self._transfer_order = None
        self._qr_key = None
        self._clients_widgets = []
        self._current_url = ""

        # 字体
        self.FONT_UI = _pick_ui_font(root)
        self.FONT_MONO = _pick_mono_font(root)

        root.title("局域网互传")
        root.geometry("1060x760")
        root.minsize(900, 620)
        root.configure(bg=C_BG)

        self._build_ui()
        self._start_server()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._ip_watcher, daemon=True).start()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    #  整体布局
    # ------------------------------------------------------------------

    def _build_ui(self):
        F_UI = self.FONT_UI

        # ---------- 顶部栏 ----------
        header = tk.Frame(self.root, bg=C_BG)
        header.pack(fill="x", padx=22, pady=(18, 6))

        # 左侧：Logo + 标题
        left_box = tk.Frame(header, bg=C_BG)
        left_box.pack(side="left", fill="y")

        logo = tk.Canvas(left_box, width=46, height=46, bg=C_BG,
                          highlightthickness=0, bd=0)
        logo.pack(side="left", padx=(0, 12))
        self._draw_logo(logo)

        title_box = tk.Frame(left_box, bg=C_BG)
        title_box.pack(side="left", fill="y", pady=(2, 0))

        tk.Label(title_box, text="局域网互传", bg=C_BG, fg=C_TEXT,
                 font=(F_UI, 17, "bold"), anchor="w").pack(anchor="w")

        self.status_lbl = tk.Label(
            title_box, text="● 运行中", bg=C_BG, fg=C_SUCCESS,
            font=(F_UI, 10), anchor="w")
        self.status_lbl.pack(anchor="w", pady=(2, 0))

        # 右侧：共享目录 + 按钮
        right_box = tk.Frame(header, bg=C_BG)
        right_box.pack(side="right", fill="y")

        path_box = tk.Frame(right_box, bg=C_BG)
        path_box.pack(side="left", padx=(0, 12), pady=(2, 0))
        tk.Label(path_box, text="共享目录", bg=C_BG, fg=C_DIM_2,
                 font=(F_UI, 9), anchor="e").pack(anchor="e")
        self.path_lbl = tk.Label(path_box, text="", bg=C_BG, fg=C_TEXT_2,
                                  font=(F_UI, 10), anchor="e")
        self.path_lbl.pack(anchor="e")

        self.btn_open = RoundButton(
            right_box, text="打开文件夹", command=self._open_folder,
            bg=C_BG, fill=C_CARD, fg=C_TEXT_2,
            hover_fill=C_ACCENT_SOFT, hover_fg=C_ACCENT,
            border=C_BORDER_2, radius=8, padx=14, pady=7,
            font=(F_UI, 10),
        )
        self.btn_open.pack(side="right", padx=(0, 8))

        self.btn_change = RoundButton(
            right_box, text="更改目录",
            command=self._choose_folder,
            bg=C_BG, fill=C_ACCENT, fg="#ffffff",
            hover_fill=C_ACCENT_HOV,
            radius=8, padx=14, pady=7,
            font=(F_UI, 10),
        )
        self.btn_change.pack(side="right")

        # ---------- 分隔线 ----------
        sep = tk.Frame(self.root, bg=C_BORDER, height=1)
        sep.pack(fill="x", padx=22, pady=(6, 0))

        # ---------- 主体 ----------
        body = tk.Frame(self.root, bg=C_BG)
        body.pack(fill="both", expand=True, padx=22, pady=18)

        body.columnconfigure(0, weight=0, minsize=300)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=C_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))

        right = tk.Frame(body, bg=C_BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        self._build_left_column(left)
        self._build_right_column(right)

    # ------------------------------------------------------------------
    #  Logo（闪电图标）
    # ------------------------------------------------------------------

    def _draw_logo(self, canvas):
        c = canvas
        draw_round_rect(c, 1, 1, 45, 45, 13,
                        fill=C_ACCENT, outline="")
        draw_round_rect(c, 2, 2, 44, 20, 12,
                        fill="#6f86ff", outline="")
        c.create_polygon(
            24, 8, 16, 24, 22, 24, 20, 38, 30, 22, 23, 22, 27, 8,
            fill="#ffffff", outline="")

    # ------------------------------------------------------------------
    #  左栏：二维码卡片
    # ------------------------------------------------------------------

    def _build_left_column(self, parent):
        F_UI = self.FONT_UI

        # ---- 二维码卡片 ----
        qr_card = Card(parent, padding=(18, 16, 18, 18),
                       bg=C_BG, card_bg=C_CARD, radius=14)
        qr_card.pack(fill="x")

        tk.Label(qr_card.body, text="扫码连接", bg=C_CARD, fg=C_TEXT,
                 font=(F_UI, 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(qr_card.body, text="用手机扫码即可访问",
                 bg=C_CARD, fg=C_DIM, font=(F_UI, 9),
                 anchor="w").pack(fill="x", pady=(2, 12))

        # QR Canvas —— 用白色卡片包裹
        qr_wrap = tk.Frame(qr_card.body, bg=C_CARD)
        qr_wrap.pack(fill="x")

        self.qr_canvas = tk.Canvas(
            qr_wrap, width=240, height=240, bg="#ffffff",
            highlightthickness=0, bd=0)
        self.qr_canvas.pack(anchor="center")

        # 合并后的地址 Label
        self.qr_url_label = tk.Label(
            qr_card.body, text="正在获取 IP…",
            bg=C_CARD, fg=C_DIM, font=(F_UI, 10),
            cursor="hand2", justify="center"
        )
        self.qr_url_label.pack(pady=(12, 0))
        self.qr_url_label.bind("<Button-1>", self._on_url_clicked)
        self.qr_url_label.bind("<Enter>", lambda e: self._on_url_hover(True))
        self.qr_url_label.bind("<Leave>", lambda e: self._on_url_hover(False))

    # ------------------------------------------------------------------
    #  右栏：在线设备 / 正在传输 / 活动日志
    # ------------------------------------------------------------------

    def _build_right_column(self, parent):
        F_UI = self.FONT_UI

        # ---- 在线设备 ----
        dev_card = Card(parent, padding=(18, 16, 18, 16),
                        bg=C_BG, card_bg=C_CARD, radius=14)
        dev_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        head1 = tk.Frame(dev_card.body, bg=C_CARD)
        head1.pack(fill="x")

        tk.Label(head1, text="在线设备", bg=C_CARD, fg=C_TEXT,
                 font=(F_UI, 13, "bold")).pack(side="left")

        self.dev_count = _Badge(head1, bg=C_CARD,
                                 font=(F_UI, 9, "bold"))
        self.dev_count.pack(side="right")

        self.devices_frame = tk.Frame(dev_card.body, bg=C_CARD)
        self.devices_frame.pack(fill="x", pady=(8, 0))

        self._empty_dev_lbl = tk.Label(
            self.devices_frame, text="暂无设备连接",
            bg=C_CARD, fg=C_DIM_2, font=(F_UI, 10), anchor="w")
        self._empty_dev_lbl.pack(anchor="w", pady=2)

        # ---- 正在传输 ----
        tr_card = Card(parent, padding=(18, 16, 18, 16),
                       bg=C_BG, card_bg=C_CARD, radius=14)
        tr_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        head2 = tk.Frame(tr_card.body, bg=C_CARD)
        head2.pack(fill="x")

        tk.Label(head2, text="正在传输", bg=C_CARD, fg=C_TEXT,
                 font=(F_UI, 13, "bold")).pack(side="left")

        self._clear_done_btn = tk.Label(
            head2, text="清除已完成", bg=C_CARD, fg=C_DIM_2,
            font=(F_UI, 9), cursor="hand2")
        self._clear_done_btn.pack(side="right")
        self._clear_done_btn.bind("<Button-1>",
                                    lambda e: self._clear_done_transfers())
        self._clear_done_btn.bind(
            "<Enter>", lambda e: self._clear_done_btn.configure(fg=C_ACCENT))
        self._clear_done_btn.bind(
            "<Leave>", lambda e: self._clear_done_btn.configure(fg=C_DIM_2))

        self.transfers_frame = tk.Frame(tr_card.body, bg=C_CARD)
        self.transfers_frame.pack(fill="x", pady=(8, 0))

        self._empty_transfer_lbl = tk.Label(
            self.transfers_frame, text="空闲",
            bg=C_CARD, fg=C_DIM_2, font=(F_UI, 10), anchor="w")
        self._empty_transfer_lbl.pack(anchor="w", pady=2)

        # ---- 活动日志 ----
        log_card = Card(parent, padding=(18, 16, 18, 16),
                        bg=C_BG, card_bg=C_CARD, radius=14)
        log_card.grid(row=2, column=0, sticky="nsew")

        head3 = tk.Frame(log_card.body, bg=C_CARD)
        head3.pack(fill="x")

        tk.Label(head3, text="活动日志", bg=C_CARD, fg=C_TEXT,
                 font=(F_UI, 13, "bold")).pack(side="left")

        self._clear_log_btn = tk.Label(
            head3, text="清空", bg=C_CARD, fg=C_DIM_2,
            font=(F_UI, 9), cursor="hand2")
        self._clear_log_btn.pack(side="right")
        self._clear_log_btn.bind("<Button-1>",
                                    lambda e: self._clear_logs())
        self._clear_log_btn.bind(
            "<Enter>", lambda e: self._clear_log_btn.configure(fg=C_ACCENT))
        self._clear_log_btn.bind(
            "<Leave>", lambda e: self._clear_log_btn.configure(fg=C_DIM_2))

        # Text + 滚动条
        log_wrap = tk.Frame(log_card.body, bg=C_CARD)
        log_wrap.pack(fill="both", expand=True, pady=(8, 0))
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(0, weight=1)

        self.logs_text = tk.Text(
            log_wrap,
            wrap="word",
            font=(self.FONT_MONO, 9),
            bg=C_CARD, fg=C_TEXT_2,
            relief="flat", bd=0,
            padx=2, pady=2,
            state="disabled",
            insertbackground=C_ACCENT,
            selectbackground=C_ACCENT_SOFT,
            selectforeground=C_TEXT,
            spacing1=2, spacing3=2,
        )
        self.logs_text.grid(row=0, column=0, sticky="nsew")

        sb_style = ttk.Style()
        try:
            sb_style.theme_use("clam")
        except Exception:
            pass
        try:
            sb_style.configure(
                "Modern.Vertical.TScrollbar",
                background=C_CARD, troughcolor=C_CARD,
                bordercolor=C_CARD, arrowcolor=C_DIM_2,
                lightcolor=C_CARD, darkcolor=C_CARD,
                gripcount=0, relief="flat", borderwidth=0,
            )
            self._sb = ttk.Scrollbar(
                log_wrap, orient="vertical",
                command=self.logs_text.yview,
                style="Modern.Vertical.TScrollbar",
            )
        except Exception:
            self._sb = ttk.Scrollbar(
                log_wrap, orient="vertical", command=self.logs_text.yview)
        self._sb.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.logs_text.configure(yscrollcommand=self._sb.set)

        self.logs_text.tag_configure(
            "time", foreground=C_DIM_2, font=(self.FONT_MONO, 9))
        self.logs_text.tag_configure(
            "ok", foreground=C_SUCCESS, font=(self.FONT_MONO, 9))
        self.logs_text.tag_configure(
            "warn", foreground=C_WARN, font=(self.FONT_MONO, 9))
        self.logs_text.tag_configure(
            "error", foreground=C_DANGER, font=(self.FONT_MONO, 9))

    # ------------------------------------------------------------------
    #  服务端启动 / 停止
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    #  后台 IP 变化监听
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    #  主刷新循环
    # ------------------------------------------------------------------

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
        if len(p) > 50:
            p = "…" + p[-47:]
        if self.path_lbl.cget("text") != p:
            self.path_lbl.configure(text=p)

    # ------------------------------------------------------------------
    #  二维码 / 地址列表
    # ------------------------------------------------------------------

    def _refresh_info(self):
        ips = self.last_ips or []
        key = "|".join(ips)

        if self._qr_key != key:
            self._qr_key = key
            self._draw_qr(ips)

    def _draw_qr(self, ips):
        c = self.qr_canvas
        c.delete("all")
        if not ips:
            c.create_text(120, 110, text="⚠️", font=(self.FONT_UI, 30),
                          fill=C_WARN)
            c.create_text(120, 150, text="未检测到局域网 IP",
                          font=(self.FONT_UI, 10), fill=C_DIM)
            c.create_text(120, 172, text="请连接局域网",
                          font=(self.FONT_UI, 9), fill=C_DIM_2)
            self.qr_url_label.configure(text="等待网络连接…", fg=C_DIM)
            self._current_url = ""
            return
        url = f"http://{ips[0]}:{self.port}/"
        self._current_url = url

        try:
            matrix = qr_encode(url)
        except Exception as e:
            c.create_text(120, 120, text=f"二维码生成失败\n{e}",
                          font=(self.FONT_UI, 9), fill=C_DANGER)
            self.qr_url_label.configure(text="生成失败", fg=C_DANGER)
            self._current_url = ""
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
        
        # 更新文字，取消 wraplength 避免换行
        self.qr_url_label.configure(
            text=url, fg=C_ACCENT, 
            font=(self.FONT_MONO, 10)
        )

    def _on_url_hover(self, hovering):
        if not self._current_url:
            return
        if hovering:
            self.qr_url_label.configure(fg=C_ACCENT_HOV)
        else:
            self.qr_url_label.configure(fg=C_ACCENT)

    def _on_url_clicked(self, e):
        if not self._current_url:
            return
        # 1. 复制
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._current_url)
            self._flash_status("✓ 已复制地址并打开")
        except tk.TclError:
            pass
        # 2. 打开浏览器
        try:
            webbrowser.open(self._current_url)
        except Exception:
            pass

    def _flash_status(self, text):
        self.status_lbl.configure(text=text, fg=C_ACCENT)
        self.root.after(2000, lambda: self.status_lbl.configure(
            text="● 运行中", fg=C_SUCCESS))

    # ------------------------------------------------------------------
    #  在线设备列表
    # ------------------------------------------------------------------

    def _refresh_clients(self):
        clients = get_active_clients()

        # 排序：本机在前
        clients.sort(key=lambda c: (c["key"] != _LOCAL_KEY, c["name"]))

        key = tuple((c["ip"], c["name"], c["key"] == _LOCAL_KEY)
                    for c in clients)
        if key == self._last_client_key:
            return
        self._last_client_key = key

        for w in self._clients_widgets:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._clients_widgets.clear()

        # 隐藏空状态
        if self._empty_dev_lbl.winfo_exists():
            self._empty_dev_lbl.pack_forget()

        for c in clients:
            is_self = c["key"] == _LOCAL_KEY
            row = _DeviceRow(self.devices_frame, c["name"], c["ip"],
                              is_self=is_self, font=self.FONT_UI)
            row.pack(fill="x", pady=2)
            self._clients_widgets.append(row)

        if not clients:
            if not self._empty_dev_lbl.winfo_exists():
                self._empty_dev_lbl = tk.Label(
                    self.devices_frame, text="暂无设备连接",
                    bg=C_CARD, fg=C_DIM_2,
                    font=(self.FONT_UI, 10), anchor="w")
            self._empty_dev_lbl.pack(anchor="w", pady=2)

        # 更新 badge
        self.dev_count.set(str(len(clients)))

    # ------------------------------------------------------------------
    #  传输列表
    # ------------------------------------------------------------------

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
        items = items[:8]

        current_ids = [t["id"] for t in items]
        current_id_set = set(current_ids)

        # 移除消失的行
        for tid in list(self._transfer_rows.keys()):
            if tid not in current_id_set:
                row = self._transfer_rows.pop(tid)
                try:
                    row.destroy()
                except tk.TclError:
                    pass

        # 隐藏/显示空状态
        if items:
            if self._empty_transfer_lbl.winfo_exists() \
                    and self._empty_transfer_lbl.winfo_ismapped():
                self._empty_transfer_lbl.pack_forget()
        else:
            if not self._empty_transfer_lbl.winfo_exists():
                self._empty_transfer_lbl = tk.Label(
                    self.transfers_frame, text="空闲",
                    bg=C_CARD, fg=C_DIM_2,
                    font=(self.FONT_UI, 10), anchor="w")
            self._empty_transfer_lbl.pack(anchor="w", pady=2)

        # 更新或创建行
        for t in items:
            tid = t["id"]
            row = self._transfer_rows.get(tid)
            if row is None:
                row = _TransferRow(self.transfers_frame,
                                    font=self.FONT_UI,
                                    mono_font=self.FONT_MONO)
                row.set_close_handler(
                    lambda _tid=tid: self._remove_transfer_row(_tid))
                self._transfer_rows[tid] = row
            row.update(t, fmt_bytes, fmt_speed)

        # 重新排列
        if current_ids != self._transfer_order:
            self._transfer_order = current_ids
            for tid in current_ids:
                self._transfer_rows[tid].pack_forget()
            for tid in current_ids:
                self._transfer_rows[tid].pack(fill="x", pady=5)

    def _remove_transfer_row(self, tid):
        with TRANSFERS_LOCK:
            TRANSFERS.pop(tid, None)

    def _clear_done_transfers(self):
        with TRANSFERS_LOCK:
            for tid, t in list(TRANSFERS.items()):
                if t["done"]:
                    del TRANSFERS[tid]

    # ------------------------------------------------------------------
    #  日志
    # ------------------------------------------------------------------

    def _refresh_logs(self):
        with LOG_LOCK:
            items = [x for x in LOG_BUFFER if x["id"] > self.last_log_id]
            max_id = LOG_COUNTER

        if items:
            self.logs_text.configure(state="normal")
            for it in items:
                t = time.strftime("%H:%M:%S", time.localtime(it["t"]))
                self.logs_text.insert("end", f"{t}  ", ("time",))
                msg = it["msg"]
                if msg.startswith("✓"):
                    tag = ("ok",)
                elif msg.startswith("[error]") or msg.startswith("✗"):
                    tag = ("error",)
                elif msg.startswith("⚠️"):
                    tag = ("warn",)
                else:
                    tag = ()
                self.logs_text.insert("end", msg + "\n", tag)
            total = int(self.logs_text.index("end-1c").split(".")[0])
            if total > 800:
                self.logs_text.delete("1.0", f"{total - 600}.0")
            self.logs_text.see("end")
            self.logs_text.configure(state="disabled")

        if max_id > self.last_log_id:
            self.last_log_id = max_id

    def _clear_logs(self):
        with LOG_LOCK:
            LOG_BUFFER.clear()
            self.last_log_id = LOG_COUNTER
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", "end")
        self.logs_text.configure(state="disabled")

    # ------------------------------------------------------------------
    #  操作
    # ------------------------------------------------------------------

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
        try:
            self.root.destroy()
        except Exception:
            pass


# =====================================================================
#  入口
# =====================================================================

def main():
    # Windows 高 DPI 支持：在创建 root 之前设置
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

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
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯 Python QR 码生成器（零依赖）
- 字节模式（UTF-8）
- 纠错等级 L
- 支持版本 1-10（最多约 271 字节）
- 输出：二维 0/1 矩阵 / SVG 字符串

对外接口：
    qr_encode(text) -> list[list[int]]
    qr_svg(matrix, module=8, quiet=4) -> str
"""

# =====================================================================
#  GF(256) 域
# =====================================================================

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]
del _x, _i


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_gen(n):
    """Reed-Solomon 生成多项式，最高次项在前。"""
    g = [1]
    for i in range(n):
        c = _GF_EXP[i]
        h = [0] * (len(g) + 1)
        h[0] = g[0]
        for k in range(1, len(g)):
            h[k] = g[k] ^ _gmul(g[k - 1], c)
        h[-1] = _gmul(g[-1], c)
        g = h
    return g


def _rs_encode(data, n):
    gen = _rs_gen(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(1, len(gen)):
                res[i + j] ^= _gmul(gen[j], coef)
    return res[len(data):]


# =====================================================================
#  版本参数表（ECC L）
# =====================================================================

_DATA_CW = {1: 19, 2: 34, 3: 55, 4: 80, 5: 108,
            6: 136, 7: 156, 8: 194, 9: 232, 10: 274}
_EC_CW   = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26,
            6: 18, 7: 20, 8: 24, 9: 30, 10: 36}
_NBLOCKS = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1,
            6: 2, 7: 2, 8: 2, 9: 2, 10: 2}

_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
    9: [6, 26, 46], 10: [6, 28, 50],
}


def _choose_version(data_len):
    for v in range(1, 11):
        cc = 8 if v <= 9 else 16
        if 4 + cc + 8 * data_len <= _DATA_CW[v] * 8:
            return v
    raise ValueError("内容过长，二维码放不下")


# =====================================================================
#  编码
# =====================================================================

def _make_codewords(text, version):
    data = text.encode("utf-8")
    total_bits = _DATA_CW[version] * 8
    bits = [0, 1, 0, 0]  # 字节模式
    cc = 8 if version <= 9 else 16
    n = len(data)
    for i in range(cc - 1, -1, -1):
        bits.append((n >> i) & 1)
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    bits.extend([0] * min(4, total_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pad = [0xEC, 0x11]
    i = 0
    while len(bits) < total_bits:
        b = pad[i & 1]
        for j in range(7, -1, -1):
            bits.append((b >> j) & 1)
        i += 1

    cw = []
    for i in range(0, len(bits), 8):
        b = 0
        for j in range(8):
            b = (b << 1) | bits[i + j]
        cw.append(b)

    nb = _NBLOCKS[version]
    ec_len = _EC_CW[version]
    blk = len(cw) // nb
    blocks = [cw[i * blk:(i + 1) * blk] for i in range(nb)]
    ecs = [_rs_encode(b, ec_len) for b in blocks]

    out = []
    for i in range(blk):
        for b in blocks:
            out.append(b[i])
    for i in range(ec_len):
        for e in ecs:
            out.append(e[i])
    return out


def _mask_bit(mask, x, y):
    if mask == 0: return (x + y) % 2 == 0
    if mask == 1: return y % 2 == 0
    if mask == 2: return x % 3 == 0
    if mask == 3: return (x + y) % 3 == 0
    if mask == 4: return (y // 2 + x // 3) % 2 == 0
    if mask == 5: return ((x * y) % 2 + (x * y) % 3) == 0
    if mask == 6: return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    return ((x + y) % 2 + (x * y) % 3) % 2 == 0


def _build_matrix(version, codewords, mask):
    size = 17 + 4 * version
    m = [[0] * size for _ in range(size)]
    f = [[False] * size for _ in range(size)]

    def setf(x, y, v):
        m[y][x] = v
        f[y][x] = True

    # 时序图案
    for i in range(8, size - 8):
        setf(i, 6, 1 if i % 2 == 0 else 0)
        setf(6, i, 1 if i % 2 == 0 else 0)

    # 定位图案（含分隔符）
    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    d = max(abs(dx), abs(dy))
                    # d=0/1 黑、d=2 白、d=3 黑、d=4 白（分隔符）
                    setf(x, y, 0 if d in (2, 4) else 1)
    finder(3, 3)
    finder(size - 4, 3)
    finder(3, size - 4)

    # 校正图案
    if version >= 2:
        coords = _ALIGN[version]
        for cy in coords:
            for cx in coords:
                if (cx < 9 and cy < 9) or \
                   (cx > size - 9 and cy < 9) or \
                   (cx < 9 and cy > size - 9):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        d = max(abs(dx), abs(dy))
                        setf(cx + dx, cy + dy, 0 if d == 1 else 1)

    # 预留格式信息区域 + 暗模块
    for i in range(9):
        if i != 6:
            setf(i, 8, 0)
            setf(8, i, 0)
    for i in range(8):
        setf(size - 1 - i, 8, 0)
        setf(8, size - 1 - i, 0)
    setf(8, size - 8, 1)

    # 数据位（蛇形填充，跳过第 6 列）
    bit_idx = 0
    total = len(codewords) * 8
    up = True
    col = size - 1
    while col > 0:
        if col == 6:
            col = 5
        ys = range(size - 1, -1, -1) if up else range(size)
        for y in ys:
            for x in (col, col - 1):
                if not f[y][x] and bit_idx < total:
                    b = (codewords[bit_idx // 8] >> (7 - bit_idx % 8)) & 1
                    if _mask_bit(mask, x, y):
                        b ^= 1
                    m[y][x] = b
                    bit_idx += 1
        up = not up
        col -= 2
    return m


def _draw_format(m, mask, ecc="L"):
    size = len(m)
    ec = {"L": 1, "M": 0, "Q": 3, "H": 2}[ecc]
    data = (ec << 3) | mask
    poly = data << 10
    for i in range(14, 9, -1):
        if poly & (1 << i):
            poly ^= 0x537 << (i - 10)
    bits = ((data << 10) | poly) ^ 0x5412

    def bit(i): return (bits >> i) & 1

    for i in range(6):
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(9, 15):
        m[8][14 - i] = bit(i)

    for i in range(8):
        m[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        m[size - 15 + i][8] = bit(i)


def _penalty(m):
    n = len(m)
    score = 0
    lines = [list(row) for row in m] + \
            [[m[y][x] for y in range(n)] for x in range(n)]
    for line in lines:
        run = 1
        for i in range(1, n):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run = 1
        if run >= 5:
            score += 3 + run - 5
    for y in range(n - 1):
        for x in range(n - 1):
            v = m[y][x]
            if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    pat = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    rpat = pat[::-1]
    for y in range(n):
        for x in range(n - 10):
            seg = m[y][x:x + 11]
            if seg == pat or seg == rpat:
                score += 40
    for x in range(n):
        for y in range(n - 10):
            seg = [m[y + i][x] for i in range(11)]
            if seg == pat or seg == rpat:
                score += 40
    dark = sum(sum(r) for r in m)
    pct = dark * 100 / (n * n)
    score += int(abs(pct - 50)) // 5 * 10
    return score


# =====================================================================
#  对外接口
# =====================================================================

def qr_encode(text):
    """把文本编码成二维码矩阵（list[list[int]]，1 = 黑）。"""
    data = text.encode("utf-8")
    version = _choose_version(len(data))
    cw = _make_codewords(text, version)
    best, best_score = None, 1 << 30
    for mask in range(8):
        m = _build_matrix(version, cw, mask)
        _draw_format(m, mask, "L")
        s = _penalty(m)
        if s < best_score:
            best_score, best = s, m
    return best


def qr_svg(matrix, module=8, quiet=4):
    """把矩阵渲染成 SVG 字符串。"""
    n = len(matrix)
    total = (n + 2 * quiet) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" shape-rendering="crispEdges">',
        f'<rect width="{total}" height="{total}" fill="#ffffff"/>',
    ]
    for y in range(n):
        x = 0
        while x < n:
            if matrix[y][x]:
                x2 = x
                while x2 < n and matrix[y][x2]:
                    x2 += 1
                parts.append(
                    f'<rect x="{(x + quiet) * module}" y="{(y + quiet) * module}" '
                    f'width="{(x2 - x) * module}" height="{module}"/>'
                )
                x = x2
            else:
                x += 1
    parts.append("</svg>")
    return "".join(parts)
"""零依赖标准 QR Code (ISO/IEC 18004, Version 1-6, ECC-M) SVG 生成器。

用于 Workshop 现场完全离线生成局域网扫码链接，不依赖外部 CDN 或第三方库。
"""

# (version, size, total_data_cw, num_blocks, data_cw_per_block, ec_cw_per_block, align_centers)
_VERSIONS_M = (
    (1, 21, 16, 1, 16, 10, ()),
    (2, 25, 28, 1, 28, 16, (6, 18)),
    (3, 29, 44, 1, 44, 26, (6, 22)),
    (4, 33, 64, 2, 32, 18, (6, 26)),
    (5, 37, 86, 2, 43, 24, (6, 30)),
    (6, 41, 108, 4, 27, 16, (6, 34)),
)


def _gf_mul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1D
        b >>= 1
    return result


def _rs_generator(degree):
    poly = [1]
    root = 1
    for _ in range(degree):
        next_poly = [0] * (len(poly) + 1)
        for index, coeff in enumerate(poly):
            next_poly[index] ^= coeff
            next_poly[index + 1] ^= _gf_mul(coeff, root)
        poly = next_poly
        root = _gf_mul(root, 2)
    return poly


def _rs_remainder(data, degree):
    gen = _rs_generator(degree)
    reg = [0] * degree
    for byte in data:
        factor = byte ^ reg.pop(0)
        reg.append(0)
        if factor:
            for index in range(degree):
                reg[index] ^= _gf_mul(gen[index + 1], factor)
    return reg


def _encode_data_codewords(payload_bytes):
    chosen = None
    for spec in _VERSIONS_M:
        # 4-bit mode + 8-bit length + 8*N data bits <= total_data_cw * 8
        capacity_bytes = spec[2] - 2
        if len(payload_bytes) <= capacity_bytes:
            chosen = spec
            break
    if chosen is None:
        raise ValueError(f"QR payload too long ({len(payload_bytes)} bytes, max 106 bytes)")

    version, size, total_data_cw, num_blocks, data_per_block, ec_per_block, align = chosen
    bits = []

    def push(value, width):
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(0b0100, 4)  # Byte mode
    push(len(payload_bytes), 8)
    for byte in payload_bytes:
        push(byte, 8)

    max_bits = total_data_cw * 8
    terminator = min(4, max_bits - len(bits))
    push(0, terminator)
    while len(bits) % 8 != 0:
        bits.append(0)

    codewords = []
    for offset in range(0, len(bits), 8):
        byte = 0
        for bit in bits[offset : offset + 8]:
            byte = (byte << 1) | bit
        codewords.append(byte)

    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(codewords) < total_data_cw:
        codewords.append(pad_bytes[pad_idx % 2])
        pad_idx += 1

    data_blocks = []
    ec_blocks = []
    pos = 0
    for _ in range(num_blocks):
        block = codewords[pos : pos + data_per_block]
        pos += data_per_block
        data_blocks.append(block)
        ec_blocks.append(_rs_remainder(block, ec_per_block))

    interleaved = []
    for idx in range(data_per_block):
        for block in data_blocks:
            interleaved.append(block[idx])
    for idx in range(ec_per_block):
        for block in ec_blocks:
            interleaved.append(block[idx])
    return chosen, interleaved


def _format_bits_m(mask):
    data = (0b00 << 3) | mask  # ECC Level M = 00
    rem = data << 10
    for bit in range(14, 9, -1):
        if (rem >> bit) & 1:
            rem ^= 0x537 << (bit - 10)
    return ((data << 10) | rem) ^ 0x5412


def _mask_predicate(mask, row, col):
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return ((row // 2) + (col // 3)) % 2 == 0
    if mask == 5:
        return ((row * col) % 2) + ((row * col) % 3) == 0
    if mask == 6:
        return (((row * col) % 2) + ((row * col) % 3)) % 2 == 0
    return (((row + col) % 2) + ((row * col) % 3)) % 2 == 0


def _penalty(matrix):
    size = len(matrix)
    score = 0
    # Rule 1: runs of 5+ same color
    for line in list(matrix) + [[matrix[r][c] for r in range(size)] for c in range(size)]:
        run = 1
        for idx in range(1, size):
            if line[idx] == line[idx - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
    # Rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            v = matrix[r][c]
            if v == matrix[r][c + 1] == matrix[r + 1][c] == matrix[r + 1][c + 1]:
                score += 3
    # Rule 4: balance
    dark = sum(sum(row) for row in matrix)
    ratio = abs(dark * 100 // (size * size) - 50) // 5
    score += ratio * 10
    return score


def make_matrix(text):
    """返回二维 0/1 矩阵（1 为黑色模块）。"""
    payload = text.encode("utf-8")
    spec, codewords = _encode_data_codewords(payload)
    _, size, _, _, _, _, align = spec

    modules = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_fn(row, col, val):
        if 0 <= row < size and 0 <= col < size:
            modules[row][col] = 1 if val else 0
            reserved[row][col] = True

    # Finder patterns + separators
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = top + r, left + c
                if 0 <= rr < size and 0 <= cc < size:
                    in_outer = 0 <= r <= 6 and 0 <= c <= 6
                    on_border = r in (0, 6) or c in (0, 6)
                    in_core = 2 <= r <= 4 and 2 <= c <= 4
                    set_fn(rr, cc, in_outer and (on_border or in_core))

    # Alignment patterns
    for r0 in align:
        for c0 in align:
            if reserved[r0][c0]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    val = max(abs(dr), abs(dc)) != 1
                    set_fn(r0 + dr, c0 + dc, val)

    # Timing patterns
    for i in range(8, size - 8):
        if not reserved[6][i]:
            set_fn(6, i, i % 2 == 0)
        if not reserved[i][6]:
            set_fn(i, 6, i % 2 == 0)

    # Dark module
    set_fn(size - 8, 8, 1)

    # Reserve format info slots
    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True

    # Place data bits
    data_bits = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            data_bits.append((cw >> shift) & 1)

    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[row][c]:
                    modules[row][c] = data_bits[bit_idx] if bit_idx < len(data_bits) else 0
                    bit_idx += 1
        upward = not upward
        col -= 2

    def apply_mask_and_format(mask):
        grid = [row[:] for row in modules]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _mask_predicate(mask, r, c):
                    grid[r][c] ^= 1
        fmt = _format_bits_m(mask)
        tl_coords = (
            (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
            (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
        )
        for idx, (r, c) in enumerate(tl_coords):
            grid[r][c] = (fmt >> (14 - idx)) & 1
        for idx in range(7):
            grid[size - 1 - idx][8] = (fmt >> (14 - idx)) & 1
        for idx in range(8):
            grid[8][size - 8 + idx] = (fmt >> (7 - idx)) & 1
        return grid

    best_grid = None
    best_score = None
    for mask in range(8):
        candidate = apply_mask_and_format(mask)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best_score = score
            best_grid = candidate
    return best_grid


def render_svg(text, quiet_zone=4, fg="#0b0f19", bg="#ffffff"):
    """将文本渲染为离线可用的清晰 SVG 二维码。"""
    matrix = make_matrix(text)
    size = len(matrix)
    total = size + 2 * quiet_zone
    paths = []
    for r, row in enumerate(matrix):
        for c, bit in enumerate(row):
            if bit:
                paths.append(f"M{c + quiet_zone},{r + quiet_zone}h1v1h-1z")
    d = "".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR Code">'
        f'<rect width="{total}" height="{total}" fill="{bg}"/>'
        f'<path d="{d}" fill="{fg}"/>'
        f"</svg>"
    )

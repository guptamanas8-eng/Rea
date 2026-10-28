"""Minimal QR code encoder (byte mode, error correction M, versions 1-6) that renders SVG.

Enough for the short join links the games show on the TV, so neither the TV
nor the phones need a QR library from the internet.
"""

# version: (error-correction codewords per block, number of blocks, total codewords)
_VERSIONS = {1: (10, 1, 26), 2: (16, 1, 44), 3: (26, 1, 70), 4: (18, 2, 100), 5: (24, 2, 134), 6: (16, 4, 172)}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
_ECL_M = 0  # format bits for level M


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data):
    for version, (ec, blocks, total) in sorted(_VERSIONS.items()):
        capacity = total - ec * blocks
        if 4 + 8 + 8 * len(data) <= capacity * 8:
            break
    else:
        raise ValueError("text too long for this QR encoder")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(8))]
    for byte in data:
        bits += [(byte >> i) & 1 for i in reversed(range(8))]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    size = capacity // blocks
    data_blocks = [words[i * size:(i + 1) * size] for i in range(blocks)]
    divisor = _rs_divisor(ec)
    ec_blocks = [_rs_remainder(b, divisor) for b in data_blocks]
    out = [b[i] for i in range(size) for b in data_blocks]
    out += [b[i] for i in range(ec) for b in ec_blocks]
    return version, out


class _Matrix:
    def __init__(self, version):
        self.size = version * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]
        self.version = version

    def set_fn(self, x, y, dark):
        self.mod[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)

    def draw_format(self, mask):
        data = _ECL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
        n = self.size
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, n - 15 + i, bit(i))
        self.set_fn(8, n - 8, True)

    def place(self, codewords):
        n, i, total = self.size, 0, len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.mod[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        tests = [
            lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        ]
        t = tests[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and t(x, y):
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        n, m, score = self.size, self.mod, 0
        for lines in (m, [list(c) for c in zip(*m)]):  # runs of 5+ in rows and columns
            for line in lines:
                run = 1
                for a, b in zip(line, line[1:]):
                    run = run + 1 if a == b else 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
        for y in range(n - 1):  # 2x2 blocks
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, m))
        score += abs(dark * 20 - n * n * 10) // (n * n) * 10
        return score


def matrix(text):
    """The QR code for `text` as rows of booleans (True = dark)."""
    version, words = _codewords(text.encode("utf-8"))
    best = None
    for mask in range(8):
        q = _Matrix(version)
        q.draw_function_patterns()
        q.place(words)
        q.apply_mask(mask)
        q.draw_format(mask)
        p = q.penalty()
        if best is None or p < best[0]:
            best = (p, q.mod)
    return best[1]


def svg(text, border=4):
    m = matrix(text)
    n = len(m) + border * 2
    path = "".join(f"M{x + border},{y + border}h1v1h-1z"
                   for y, row in enumerate(m) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')

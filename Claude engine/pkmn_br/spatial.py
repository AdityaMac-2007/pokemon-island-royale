"""Uniform-grid spatial hash. Radius queries cost O(cells touched + hits), not O(N)."""
import math


class SpatialHash:
    def __init__(self, cell: float):
        self.cell = cell
        self.cells = {}
        self.pos = {}

    def clear(self):
        self.cells = {}
        self.pos = {}

    def insert(self, i, x, y):
        k = (math.floor(x / self.cell), math.floor(y / self.cell))
        self.cells.setdefault(k, []).append(i)
        self.pos[i] = (x, y, k)

    def remove(self, i):
        x, y, k = self.pos.pop(i)
        self.cells[k].remove(i)

    def query(self, x, y, r):
        """Ids within Euclidean distance r of (x, y), sorted by id (deterministic)."""
        c = self.cell
        x0, x1 = math.floor((x - r) / c), math.floor((x + r) / c)
        y0, y1 = math.floor((y - r) / c), math.floor((y + r) / c)
        r2 = r * r
        out = []
        cells, pos = self.cells, self.pos
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for i in cells.get((cx, cy), ()):
                    px, py, _ = pos[i]
                    if (px - x) ** 2 + (py - y) ** 2 <= r2:
                        out.append(i)
        out.sort()
        return out

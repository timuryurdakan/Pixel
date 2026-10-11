// Pixel Office — A* on the tile grid (ARCHITECTURE.md §6.2).
// 4-neighbour, cost 1 per step, Manhattan heuristic, binary-heap open set,
// deterministic tie-break: lower f, then lower h, then insertion order.

class MinHeap {
  constructor() { this.a = []; }
  get size() { return this.a.length; }
  static less(p, q) {
    return p.f !== q.f ? p.f < q.f : p.h !== q.h ? p.h < q.h : p.seq < q.seq;
  }
  push(n) {
    const a = this.a;
    a.push(n);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (!MinHeap.less(a[i], a[p])) break;
      [a[i], a[p]] = [a[p], a[i]];
      i = p;
    }
  }
  pop() {
    const a = this.a;
    const top = a[0];
    const last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < a.length && MinHeap.less(a[l], a[m])) m = l;
        if (r < a.length && MinHeap.less(a[r], a[m])) m = r;
        if (m === i) break;
        [a[i], a[m]] = [a[m], a[i]];
        i = m;
      }
    }
    return top;
  }
}

// Neighbour order is fixed (up, right, down, left) so results are deterministic.
const DIRS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

/**
 * @param {number} width
 * @param {number} height
 * @param {(x:number,y:number)=>boolean} passable
 * @param {{x:number,y:number}} start
 * @param {{x:number,y:number}} goal
 * @returns {{x:number,y:number}[]|null} tiles from the first step to goal inclusive;
 *   [] if start equals goal; null if unreachable.
 */
export function findPath(width, height, passable, start, goal) {
  if (start.x === goal.x && start.y === goal.y) return [];
  const inside = (x, y) => x >= 0 && y >= 0 && x < width && y < height;
  if (!inside(goal.x, goal.y) || !passable(goal.x, goal.y)) return null;
  const idx = (x, y) => y * width + x;
  const hOf = (x, y) => Math.abs(x - goal.x) + Math.abs(y - goal.y);
  const g = new Float64Array(width * height).fill(Infinity);
  const came = new Int32Array(width * height).fill(-1);
  const closed = new Uint8Array(width * height);
  const open = new MinHeap();
  let seq = 0;
  const s = idx(start.x, start.y);
  g[s] = 0;
  open.push({ x: start.x, y: start.y, g: 0, h: hOf(start.x, start.y), f: hOf(start.x, start.y), seq: seq++ });
  while (open.size) {
    const cur = open.pop();
    const ci = idx(cur.x, cur.y);
    if (closed[ci]) continue;
    if (cur.g > g[ci]) continue;
    closed[ci] = 1;
    if (cur.x === goal.x && cur.y === goal.y) {
      const path = [];
      let i = ci;
      while (i !== s) {
        path.push({ x: i % width, y: Math.floor(i / width) });
        i = came[i];
      }
      return path.reverse();
    }
    for (const [dx, dy] of DIRS) {
      const nx = cur.x + dx, ny = cur.y + dy;
      if (!inside(nx, ny)) continue;
      const ni = idx(nx, ny);
      if (closed[ni] || !passable(nx, ny)) continue;
      const ng = cur.g + 1;
      if (ng < g[ni]) {
        g[ni] = ng;
        came[ni] = ci;
        const h = hOf(nx, ny);
        open.push({ x: nx, y: ny, g: ng, h, f: ng + h, seq: seq++ });
      }
    }
  }
  return null;
}

// Pixel Office — office map, walkability, spots + reservation (ARCHITECTURE.md §4, §6.1).

export const MAP_W = 22, MAP_H = 14, TILE = 16, SCALE = 3;

export const MAP_ROWS = [
  "##WW##WW##WW######WW##",
  "#KK.P...KK..P...kCk.Q#",
  "#...............ccc..#",
  "#.TT..TT..TT.........#",
  "#.h...h...h....======#",
  "#..............======#",
  "#.TT..TT..TT...======#",
  "#.h...h...h..........#",
  "#.............###.####",
  "#.TT..TT..TT..#......#",
  "#.h...h...h...#bb==bb#",
  "#.............#BB==BB#",
  "#P...........P#.....P#",
  "####D#################",
];

export const DOOR = { x: 4, y: 13 };
export const SPAWN = { x: 4, y: 12 };

const WALKABLE = new Set(['.', '=', 'D']);

/**
 * The office cat sleeps on the lower-right corner of the lounge rug (v2.md H3). The tile is no spot (regular or
 * overflow) and nobody needs it to reach anything, so it is taken out of the walkable grid: characters walk around
 * the cat instead of over it.
 */
export const CAT_TILE = Object.freeze({ x: 20, y: 6 });
const isDecorTile = (x, y) => x === CAT_TILE.x && y === CAT_TILE.y;
const SPOT_TILES = new Set(['h', 'c', 'b']);

/** Spot kind → facing when settled. */
const FACING = { desk: 'up', coffee: 'down', sleep: 'down' };

/** Overflow spots per kind (§4.3), used after the regular ones are taken. */
const OVERFLOW = {
  desk: [[12, 3], [12, 6], [12, 9], [13, 4], [13, 7], [13, 10]],
  coffee: [[15, 4], [17, 4], [19, 4], [16, 6], [18, 6], [20, 5]],
  sleep: [[17, 10], [18, 10], [17, 11], [18, 11], [17, 9], [18, 9]],
};

/** Map char → regular spot kind. */
const SPOT_KIND = { h: 'desk', c: 'coffee', b: 'sleep' };

export class Office {
  constructor(rows = MAP_ROWS) {
    if (rows.length !== MAP_H || !rows.every((r) => r.length === MAP_W)) {
      throw new Error(`office map must be ${MAP_W}x${MAP_H}`);
    }
    this.rows = rows;
    this.width = MAP_W;
    this.height = MAP_H;
    /** @type {{desk: object[], coffee: object[], sleep: object[]}} */
    this.spots = { desk: [], coffee: [], sleep: [] };
    this.furniture = [];
    /** agentId → Spot (incl. ad-hoc floor spots) */
    this._held = new Map();
    this._parse();
  }

  tileAt(x, y) {
    if (y < 0 || y >= this.height || x < 0 || x >= this.width) return '#';
    return this.rows[y][x];
  }

  isWalkable(x, y) {
    return WALKABLE.has(this.tileAt(x, y)) && !isDecorTile(x, y);
  }

  isSpotTile(x, y) {
    return SPOT_TILES.has(this.tileAt(x, y));
  }

  /** Walkable, or a spot tile that is the path's start or goal (no through-traffic). */
  isPassable(x, y, start, goal) {
    const ch = this.tileAt(x, y);
    if (isDecorTile(x, y)) return false;
    if (WALKABLE.has(ch)) return true;
    if (!SPOT_TILES.has(ch)) return false;
    return (start && start.x === x && start.y === y) || (goal && goal.x === x && goal.y === y);
  }

  _parse() {
    const R = this.rows;
    const at = (x, y) => this.tileAt(x, y);
    const blockSort = (bottomRow) => (bottomRow + 1) * TILE - 1;
    for (let y = 0; y < this.height; y++) {
      for (let x = 0; x < this.width; x++) {
        const ch = R[y][x];
        const kind = SPOT_KIND[ch];
        if (kind) {
          const list = this.spots[kind];
          list.push({ kind, index: list.length, x, y, facing: FACING[kind], overflow: false, occupant: null });
        }
      }
    }
    for (const kind of Object.keys(OVERFLOW)) {
      for (const [x, y] of OVERFLOW[kind]) {
        const list = this.spots[kind];
        list.push({ kind, index: list.length, x, y, facing: FACING[kind], overflow: true, occupant: null });
      }
    }
    // Furniture. Desk spots (chairs) link to the desk directly above them.
    const deskSpotAt = (x, y) => this.spots.desk.find((s) => !s.overflow && s.x === x && s.y === y) || null;
    for (let y = 0; y < this.height; y++) {
      for (let x = 0; x < this.width; x++) {
        const ch = R[y][x];
        if (ch === 'T' && at(x - 1, y) !== 'T') {
          const spot = deskSpotAt(x, y + 1);
          if (spot) { spot.furnitureX = x; spot.furnitureY = y; }
          this.furniture.push({ kind: 'desk', x, y, w: 32, h: 24, sortY: blockSort(y), spot: spot || undefined });
        } else if (ch === 'h') {
          this.furniture.push({ kind: 'chair', x, y, w: 16, h: 16, sortY: y * TILE + 1, spot: deskSpotAt(x, y) || undefined });
        } else if (ch === 'K') {
          this.furniture.push({ kind: 'bookshelf', x, y, w: 16, h: 24, sortY: blockSort(y) });
        } else if (ch === 'P') {
          this.furniture.push({ kind: 'plant', x, y, w: 16, h: 24, sortY: blockSort(y) });
        } else if (ch === 'k' && at(x + 1, y) === 'C') {
          this.furniture.push({ kind: 'coffee_counter', x, y, w: 48, h: 24, sortY: blockSort(y) });
        } else if (ch === 'Q') {
          this.furniture.push({ kind: 'water_cooler', x, y, w: 16, h: 24, sortY: blockSort(y) });
        } else if (ch === 'b') {
          const spot = this.spots.sleep.find((s) => s.x === x && s.y === y);
          this.furniture.push({ kind: 'bed', x, y, w: 16, h: 32, sortY: y * TILE + 1, spot });
        }
      }
    }
  }

  /**
   * Reserve a free spot of `kind` for agentId (releases its previous spot).
   * Prefers `preferIndex` if free, then regular spots, then overflow. null if none left.
   */
  reserveSpot(kind, agentId, preferIndex = -1) {
    const list = this.spots[kind];
    if (!list) return null;
    const cur = this._held.get(agentId);
    if (cur && cur.kind === kind && (preferIndex < 0 || cur.index === preferIndex)) return cur;
    this.releaseSpot(agentId);
    let spot = null;
    if (preferIndex >= 0 && list[preferIndex] && !list[preferIndex].occupant) spot = list[preferIndex];
    if (!spot) spot = list.find((s) => !s.occupant) || null; // regular ones come first in the list
    if (!spot) return null;
    spot.occupant = agentId;
    this._held.set(agentId, spot);
    return spot;
  }

  /**
   * Fallback when even overflow is exhausted: reserve an ad-hoc spot on a random free '.' tile
   * (not used by any other spot) so two characters still never share a tile.
   */
  reserveFloorSpot(kind, agentId, rng = Math.random) {
    this.releaseSpot(agentId);
    const taken = new Set();
    for (const list of Object.values(this.spots)) for (const s of list) taken.add(`${s.x},${s.y}`);
    for (const s of this._held.values()) taken.add(`${s.x},${s.y}`);
    let tile = null;
    for (let i = 0; i < 200 && !tile; i++) {
      const t = this.randomFloorTile(rng);
      if (!taken.has(`${t.x},${t.y}`) && !(t.x === SPAWN.x && t.y === SPAWN.y)) tile = t;
    }
    if (!tile) tile = this.randomFloorTile(rng);
    const spot = { kind, index: -1, x: tile.x, y: tile.y, facing: FACING[kind] || 'down', overflow: true, occupant: agentId };
    this._held.set(agentId, spot);
    return spot;
  }

  releaseSpot(agentId) {
    const s = this._held.get(agentId);
    if (!s) return;
    if (s.occupant === agentId) s.occupant = null;
    this._held.delete(agentId);
  }

  spotOf(agentId) {
    return this._held.get(agentId) || null;
  }

  randomFloorTile(rng = Math.random) {
    if (!this._floor) {
      this._floor = [];
      for (let y = 0; y < this.height; y++) for (let x = 0; x < this.width; x++) {
        if (this.rows[y][x] === '.') this._floor.push({ x, y });
      }
    }
    const t = this._floor[Math.floor(rng() * this._floor.length) % this._floor.length];
    return { x: t.x, y: t.y };
  }
}

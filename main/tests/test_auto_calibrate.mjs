import assert from 'node:assert/strict';

globalThis.self = { postMessage() {} };
const {
  analyzeGeometry, traceBoundaryLoops, smoothClosedRing, ringSelfIntersects, extractExteriorAndHoles,
} = await import('../static/utils/auto-calibrate-worker.mjs');
const { collectWorldGeometry, analyzeInWorker } = await import('../static/utils/auto-calibrate.mjs');

const failures = [];
let currentTest = '';

function formatActual(actual) {
  if (actual === undefined) return 'undefined';
  try {
    return JSON.stringify(actual, null, 2);
  } catch {
    return String(actual);
  }
}

function recordFailure(error) {
  if (!error.message.startsWith('[' + currentTest + ']')) {
    error.message = '[' + currentTest + '] ' + error.message;
  }
  failures.push(error);
  console.error('FAIL ' + error.message);
}

function check(condition, message, actual = undefined) {
  try {
    assert.ok(condition, message + (actual === undefined ? '' : '\nActual: ' + formatActual(actual)));
    return true;
  } catch (error) {
    recordFailure(error);
    return false;
  }
}

function checkEqual(actual, expected, message, details = actual) {
  try {
    assert.deepEqual(
      actual,
      expected,
      message + '\nExpected: ' + formatActual(expected) + '\nActual: ' + formatActual(details),
    );
    return true;
  } catch (error) {
    recordFailure(error);
    return false;
  }
}

function near(actual, expected, tolerance, message) {
  return check(
    Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance,
    message + ' (expected ' + expected + ' ± ' + tolerance + ')',
    actual,
  );
}

function nearArray(actual, expected, tolerance, message) {
  const valid = Array.isArray(actual)
    && Array.isArray(expected)
    && actual.length === expected.length
    && actual.every((value, index) => Number.isFinite(value)
      && Math.abs(value - expected[index]) <= tolerance);
  return check(valid, message + ' (每个值应在 ' + tolerance + ' 以内)', { actual, expected });
}

function round(value, digits = 2) {
  return Number(Number(value).toFixed(digits));
}

function quad(x0, z0, x1, z1, y0, y1 = y0) {
  return { x0, z0, x1, z1, y0, y1 };
}

function addUpwardQuad(positions, indices, q) {
  const base = positions.length / 3;
  positions.push(
    q.x0, q.y0, q.z0,
    q.x1, q.y0, q.z0,
    q.x1, q.y1, q.z1,
    q.x0, q.y1, q.z1,
  );
  indices.push(base, base + 2, base + 1, base, base + 3, base + 2);
}

function makeMesh(quads) {
  const positions = [];
  const indices = [];
  for (const q of quads) addUpwardQuad(positions, indices, q);
  return { positions: new Float32Array(positions), indices: new Uint32Array(indices) };
}

function makeFloorMesh(offsetX = 0, offsetZ = 0) {
  return makeMesh([0.12, 1.32, 2.52, 3.72].map((y) => (
    quad(offsetX, offsetZ, offsetX + 4, offsetZ + 4, y)
  )));
}


function addOrientedQuad(positions, indices, q, upward) {
  const base = positions.length / 3;
  positions.push(
    q.x0, q.y0, q.z0,
    q.x1, q.y0, q.z0,
    q.x1, q.y1, q.z1,
    q.x0, q.y1, q.z1,
  );
  if (upward) indices.push(base, base + 2, base + 1, base, base + 3, base + 2);
  else indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
}

function makePairedStoreyMesh({ roofMaterial = 1, storeyMaterial = 0 } = {}) {
  const positions = [];
  const indices = [];
  const materialIds = [];
  const add = (q, upward, material = storeyMaterial) => {
    const before = indices.length / 3;
    addOrientedQuad(positions, indices, q, upward);
    while (materialIds.length < before + 2) materialIds.push(material);
  };
  for (const y of [0.12, 1.32, 2.52, 3.72]) {
    if (y > 0.12) add(quad(-6, -6, 6, 6, y - 0.12), false);
    add(quad(-6, -6, 6, 6, y), true);
  }
  add(quad(-6, -6, 6, 6, 4.80), false);
  add(quad(-6, -6, 6, 6, 4.92), true, roofMaterial);
  add(quad(-6, -6, 6, 6, 4.921), false, roofMaterial);
  return {
    positions: new Float32Array(positions), indices: new Uint32Array(indices),
    materialIds: new Int32Array(materialIds), materialNames: ['普通楼面', '屋面材料'],
  };
}


function addTriangle(positions, indices, a, b, c) {
  const base = positions.length / 3;
  positions.push(...a, ...b, ...c);
  indices.push(base, base + 1, base + 2);
  return base;
}

function addDoubleSidedTriangle(positions, indices, a, b, c) {
  const base = addTriangle(positions, indices, a, b, c);
  indices.push(base, base + 2, base + 1);
}

function makeRoofMaterialMeshWithSlope() {
  const positions = [];
  const indices = [];
  const materialIds = [];
  const addFloor = (x0, z0, x1, z1, y, material) => {
    const before = indices.length / 3;
    addOrientedQuad(positions, indices, quad(x0, z0, x1, z1, y), true);
    while (materialIds.length < before + 2) materialIds.push(material);
  };
  for (const y of [0.12, 1.32, 2.52, 3.72]) {
    if (y > 0.12) addFloor(-8, -8, 8, 8, y - 0.12, 0);
    addFloor(-8, -8, 8, 8, y, 0);
  }
  // A large roof slab spans the full plan at 4.92 m. A diagonally sloped
  // underside then rises from 2.62 m to 5.02 m across the same footprint.
  addFloor(-8, -8, 8, 8, 4.92, 1);
  const roofTriangles = indices.length / 3;
  addTriangle(positions, indices, [-8, 2.62, -8], [8, 2.62, -8], [8, 5.02, 8]);
  addTriangle(positions, indices, [-8, 2.62, -8], [8, 5.02, 8], [-8, 5.02, 8]);
  while (materialIds.length < roofTriangles + 2) materialIds.push(1);
  return {
    positions: new Float32Array(positions), indices: new Uint32Array(indices),
    materialIds: new Int32Array(materialIds), materialNames: ['普通楼面', '屋面材料'],
  };
}

function rotateMeshY(mesh, angle) {
  const positions = new Float32Array(mesh.positions);
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  for (let offset = 0; offset < positions.length; offset += 3) {
    const x = positions[offset];
    const z = positions[offset + 2];
    positions[offset] = x * cosine + z * sine;
    positions[offset + 2] = -x * sine + z * cosine;
  }
  return { positions, indices: new Uint32Array(mesh.indices) };
}

function analyze(mesh, requestId) {
  return analyzeGeometry(
    mesh.positions, mesh.indices, {}, requestId,
    mesh.materialIds ? { triangleMaterialIds: mesh.materialIds, materialNames: mesh.materialNames } : null,
  );
}

function analyzeWith(mesh, options, requestId) {
  return analyzeGeometry(
    mesh.positions, mesh.indices, options, requestId,
    mesh.materialIds ? { triangleMaterialIds: mesh.materialIds, materialNames: mesh.materialNames } : null,
  );
}

function findFloor(result, elevation) {
  return result.floors.find((floor) => Math.abs(floor.elevation_m - elevation) <= 0.011);
}

function samePoint(a, b) {
  return Array.isArray(a) && Array.isArray(b)
    && Math.abs(a[0] - b[0]) <= 1e-6
    && Math.abs(a[1] - b[1]) <= 1e-6;
}

function isFinitePoint(point) {
  return Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1]);
}

function isClosedRing(ring) {
  return Array.isArray(ring)
    && ring.length >= 4
    && ring.every(isFinitePoint)
    && samePoint(ring[0], ring[ring.length - 1]);
}

function polygonArea(ring) {
  if (!Array.isArray(ring) || ring.length < 4) return 0;
  let twiceArea = 0;
  for (let index = 0; index < ring.length - 1; index += 1) {
    twiceArea += ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1];
  }
  return Math.abs(twiceArea) * 0.5;
}

function pointInRing(x, z, ring) {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const xi = ring[index][0];
    const zi = ring[index][1];
    const xj = ring[previous][0];
    const zj = ring[previous][1];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function segmentsIntersect(a, b, c, d) {
  const orientation = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const epsilon = 1e-9;
  const onSegment = (p, q, r) => Math.min(p[0], r[0]) - epsilon <= q[0]
    && q[0] <= Math.max(p[0], r[0]) + epsilon
    && Math.min(p[1], r[1]) - epsilon <= q[1]
    && q[1] <= Math.max(p[1], r[1]) + epsilon;
  const abc = orientation(a, b, c);
  const abd = orientation(a, b, d);
  const cda = orientation(c, d, a);
  const cdb = orientation(c, d, b);
  if (((abc > epsilon && abd < -epsilon) || (abc < -epsilon && abd > epsilon))
    && ((cda > epsilon && cdb < -epsilon) || (cda < -epsilon && cdb > epsilon))) return true;
  return (Math.abs(abc) <= epsilon && onSegment(a, c, b))
    || (Math.abs(abd) <= epsilon && onSegment(a, d, b))
    || (Math.abs(cda) <= epsilon && onSegment(c, a, d))
    || (Math.abs(cdb) <= epsilon && onSegment(c, b, d));
}

function ringHasSelfIntersection(ring) {
  if (!isClosedRing(ring)) return true;
  const points = ring.slice(0, -1);
  for (let left = 0; left < points.length; left += 1) {
    const leftNext = (left + 1) % points.length;
    for (let right = left + 1; right < points.length; right += 1) {
      const rightNext = (right + 1) % points.length;
      if (leftNext === right || rightNext === left) continue;
      if (segmentsIntersect(points[left], points[leftNext], points[right], points[rightNext])) return true;
    }
  }
  return false;
}

function isValidRing(ring) {
  return isClosedRing(ring) && polygonArea(ring) > 1e-6 && !ringHasSelfIntersection(ring);
}

function regionDetails(region) {
  return {
    area_m2: region.area_m2,
    min_width_m: region.min_width_m,
    min_width_split_components: region.min_width_split_components,
    hole_count: region.hole_count,
    near_width_limit: region.near_width_limit,
    width_cut_warning: region.width_cut_warning,
    physically_blocked: region.physically_blocked,
    warnings: region.warnings,
    outer_points: region.outer?.length,
    outer_area_m2: region.outer ? round(polygonArea(region.outer), 3) : null,
    hole_areas_m2: region.holes?.map((hole) => round(polygonArea(hole), 3)),
  };
}

function stableSummary(result) {
  return result.floors.map((floor) => ({
    elevation_m: round(floor.elevation_m, 4),
    area_m2: round(floor.area_m2, 2),
    regions: floor.regions.map((region) => ({
      area_m2: round(region.area_m2, 2),
      min_width_m: round(region.min_width_m, 2),
      split_components: region.min_width_split_components,
      hole_count: region.hole_count,
      warning_count: region.warnings.length,
      outer_closed: isClosedRing(region.outer),
    outer_self_intersection: ringHasSelfIntersection(region.outer),
    holes_self_intersection: region.holes?.map(ringHasSelfIntersection),
      holes_valid: region.holes.every(isValidRing),
    })).sort((a, b) => (
      b.area_m2 - a.area_m2
      || b.min_width_m - a.min_width_m
      || b.hole_count - a.hole_count
    )),
  }));
}

function shuffleTriangles(mesh, seed) {
  const triangles = [];
  for (let offset = 0; offset < mesh.indices.length; offset += 3) {
    const triangle = [
      mesh.indices[offset],
      mesh.indices[offset + 1],
      mesh.indices[offset + 2],
    ];
    const rotation = (seed + offset / 3) % 3;
    triangles.push([
      triangle[rotation],
      triangle[(rotation + 1) % 3],
      triangle[(rotation + 2) % 3],
    ]);
  }

  let state = seed >>> 0;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  for (let index = triangles.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [triangles[index], triangles[swapIndex]] = [triangles[swapIndex], triangles[index]];
  }

  return {
    positions: mesh.positions,
    indices: new Uint32Array(triangles.flat()),
  };
}

function runTest(name, body) {
  currentTest = name;
  const before = failures.length;
  console.log('\n▶ ' + name);
  try {
    body();
  } catch (error) {
    recordFailure(error);
  }
  if (failures.length === before) {
    console.log('PASS ' + name);
  } else {
    console.log('FAILED ' + name + ' (' + (failures.length - before) + ' assertion(s))');
  }
}

runTest('1. 四个等面积楼层与整体平移稳定性', () => {
  const first = analyze(makeFloorMesh(), 'four-floors');
  const expectedElevations = [0.12, 1.32, 2.52, 3.72];
  checkEqual(
    first.floors.map((floor) => round(floor.elevation_m, 4)),
    expectedElevations,
    '应只得到 0.12/1.32/2.52/3.72 四个楼层',
  );
  checkEqual(first.floors.map((floor) => floor.regions.length), [1, 1, 1, 1], '每层应各有一个完整区域');
  const areas = first.floors.map((floor) => floor.area_m2);
  check(areas.length === 4 && areas.every((area) => Math.abs(area - 16) <= 0.5),
    '四个 4m × 4m 楼层面积应分别约为 16 m²', areas);
  check(areas.length === 4 && areas.every((area) => Math.abs(area - areas[0]) <= 0.2),
    '四个等面积楼层的输出面积应彼此相等', areas);

  const shifted = analyze(makeFloorMesh(31.25, -12.75), 'four-floors-shifted');
  checkEqual(
    shifted.floors.map((floor) => round(floor.elevation_m, 4)),
    expectedElevations,
    '整体平移后楼层高度不应变化',
  );
  checkEqual(
    shifted.floors.map((floor) => floor.regions.length),
    first.floors.map((floor) => floor.regions.length),
    '整体平移后各楼层区域数量应保持不变',
  );
  nearArray(
    shifted.floors.map((floor) => floor.area_m2),
    first.floors.map((floor) => floor.area_m2),
    0.5,
    '整体平移后楼层面积应保持稳定',
  );
  nearArray(
    shifted.floors.flatMap((floor) => floor.regions.map((region) => region.min_width_m)),
    first.floors.flatMap((floor) => floor.regions.map((region) => region.min_width_m)),
    0.3,
    '整体平移后最小宽度应保持稳定',
  );
});

runTest('2. 中央孔洞的外环、hole_count 与 holes', () => {
  const ring = makeMesh([
    quad(0, 0, 10, 3, 0.12),
    quad(0, 7, 10, 10, 0.12),
    quad(0, 3, 3, 7, 0.12),
    quad(7, 3, 10, 7, 0.12),
  ]);
  const result = analyze(ring, 'central-hole');
  checkEqual(result.floors.map((floor) => floor.elevation_m), [0.12], '带中央孔洞的网格应只得到一个 0.12m 楼层');
  const floor = findFloor(result, 0.12);
  if (!floor) return;
  checkEqual(floor.regions.length, 1, '带中央孔洞的外环应形成一个区域', floor.regions.length);
  const region = floor.regions[0];
  if (!region) return;
  near(region.area_m2, 84, 1.5, '10m × 10m 外框减去 4m × 4m 孔洞后的面积应约为 84 m²');
  check(isValidRing(region.outer), '外环应闭合、各点有限且无自交', region.outer);
  near(polygonArea(region.outer), 100, 1.5, '外环面积应约为 100 m²');
  checkEqual(region.hole_count, 1, '中央孔洞应输出 hole_count = 1', regionDetails(region));
  checkEqual(region.holes.length, region.hole_count, 'holes 数组长度应等于 hole_count', regionDetails(region));
  const hole = region.holes[0];
  if (!hole) return;
  check(isValidRing(hole), '孔洞应闭合、有限且无自交', hole);
  near(polygonArea(hole), 16, 1.0, '4m × 4m 中央孔洞的环面积应约为 16 m²');
  check(pointInRing(5, 5, region.outer), '孔洞中心点应位于外环内部', { outer_area: polygonArea(region.outer) });
  check(hole.slice(0, -1).every((point) => isFinitePoint(point)), '孔洞环的所有顶点都应是有限的二维点', hole);
});

function maskFromRows(rows) {
  const height = rows.length;
  const width = rows[0].length;
  const mask = new Uint8Array(width * height);
  for (let row = 0; row < height; row += 1) {
    if (rows[row].length !== width) throw new Error('测试栅格行宽不一致');
    for (let col = 0; col < width; col += 1) mask[row * width + col] = rows[row][col] ? 1 : 0;
  }
  return { mask, width, height };
}

runTest('3. 凹形、对角接触和细长回折轮廓均不自交', () => {
  const masks = [
    maskFromRows([
      [1, 1, 1],
      [1, 0, 0],
      [1, 1, 1],
    ]),
    maskFromRows([
      [1, 1],
      [1, 0],
    ]),
    maskFromRows([
      [1, 1, 1, 1],
      [1, 0, 0, 1],
      [1, 1, 1, 1],
      [1, 0, 1, 1],
      [1, 1, 1, 0],
    ]),
  ];
  for (const [index, grid] of masks.entries()) {
    const loops = traceBoundaryLoops(grid.mask, grid.width, grid.height, 0, 0, 1);
    check(loops.length > 0, '栅格案例 ' + (index + 1) + ' 应生成边界环', loops);
    check(loops.every((loop) => loop.length >= 4 && !ringSelfIntersects(loop)),
      '栅格案例 ' + (index + 1) + ' 的每个边界环都不能自交', loops);
    check(loops.every((loop) => polygonArea(loop) > 0),
      '栅格案例 ' + (index + 1) + ' 的边界环面积必须有效', loops);
  }
  const corners = traceBoundaryLoops(masks[0].mask, masks[0].width, masks[0].height, 0, 0, 1)[0];
  check(corners.length <= 9, '连续同方向栅格边应合并为拐角，而不是每格一个顶点', corners);
});

runTest('3b. 栅格鞍点处相切边界必须拆成合法独立环', () => {
  const saddle = maskFromRows([
    [0, 1, 1],
    [1, 0, 1],
    [1, 1, 1],
  ]);
  const loops = traceBoundaryLoops(saddle.mask, saddle.width, saddle.height, 0, 0, 1);
  checkEqual(loops.length, 2, '对角鞍点应由外环和内部封闭小环组成', loops);
  check(loops.every((loop) => !ringSelfIntersects(loop)), '鞍点两侧边界不得合并成 8 字形环', loops);
  const outer = loops.find((loop) => Math.abs(polygonArea(loop)) > 7);
  const inner = loops.find((loop) => Math.abs(polygonArea(loop)) < 2);
  check(Boolean(outer) && Boolean(inner), '外环与小孔洞都应保留', loops);
  check(inner && pointInRing(1.5, 1.5, inner), '内部小环应覆盖鞍点所属空单元', inner);
});

runTest('3c. 相切孔洞与重复非相邻顶点不得形成自交', () => {
  const touching = [
    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    [[2, 2], [5, 2], [5, 5], [2, 5], [2, 2]],
    [[5, 5], [8, 5], [8, 8], [5, 8], [5, 5]],
  ];
  const contour = extractExteriorAndHoles(touching, 0.05, 0.02);
  check(Boolean(contour), '带相切孔洞的外环应能生成合法轮廓', contour);
  checkEqual(contour?.holes.length, 1, '共享顶点接触的第二个孔洞必须被过滤', contour?.holes);
  const repeated = [[0, 0], [4, 0], [4, 4], [2, 2], [0, 4], [2, 2], [0, 0]];
  check(ringSelfIntersects(repeated), '非相邻重复顶点必须判定为自交', repeated);
  check(extractExteriorAndHoles([repeated], 0.1, 0.03) === null, '重复顶点轮廓不得进入自动候选', repeated);
});

runTest('3d. 微小孔洞按阈值过滤，真实楼板缺口保留', () => {
  const mesh = makeMesh([
    // Four bands tile the 10 m × 10 m outline while leaving a real
    // 4 m × 4 m atrium at (4,4)–(8,8). The tiny 0.15 m × 0.15 m seam at
    // (1,1) is intentionally below minHoleArea and must disappear.
    quad(0, 0, 10, 1, 0.12),
    quad(0, 1, 1, 1.15, 0.12),
    quad(0, 1.15, 10, 4, 0.12),
    quad(0, 4, 4, 10, 0.12),
    quad(8, 4, 10, 10, 0.12),
    quad(4, 8, 8, 10, 0.12),
  ]);
  const result = analyze(mesh, 'hole-area-filter');
  const floor = findFloor(result, 0.12);
  const region = floor?.regions?.[0];
  check(Boolean(region), '含微小接缝和真实缺口的楼面应生成区域', floor);
  if (!region) return;
  checkEqual(region.hole_count, 1, '0.15m × 0.15m 接缝孔洞应被过滤，4m × 4m 真实缺口应保留', regionDetails(region));
  near(Math.abs(polygonArea(region.holes[0] || [])), 16, 0.8, '保留的真实缺口面积应约为 16 m²');
  check(Boolean(result.diagnostics?.thresholds?.min_hole_area_m2), '诊断信息应包含最小孔洞面积阈值', result.diagnostics?.thresholds);
});

runTest('4. 简化造成自交时回退到合法原始环，非法轮廓被拒绝', () => {
  const valid = [
    [0, 0], [4, 0], [4, 4], [2.01, 4], [2, 4.01],
    [0, 4], [0, 0],
  ];
  const fallback = smoothClosedRing(valid, 100);
  check(Boolean(fallback), '原始合法环应保留为简化失败的回退结果', fallback);
  check(Boolean(fallback) && !ringSelfIntersects(fallback), '回退结果仍必须无自交', fallback);
  check(Boolean(fallback) && polygonArea(fallback) > 15.9, '回退结果应保持原轮廓面积', fallback && polygonArea(fallback));

  const crossing = [
    [0, 0], [4, 4], [0, 4], [4, 0], [0, 0],
  ];
  check(ringSelfIntersects(crossing), '测试用蝴蝶结轮廓应被识别为自交', crossing);
  check(smoothClosedRing(crossing, 0.03) === null, '自交外轮廓不得被平滑函数放行', smoothClosedRing(crossing, 0.03));
  check(extractExteriorAndHoles([crossing], 0.1, 0.03) === null,
    '自交外轮廓不得生成可应用区域', extractExteriorAndHoles([crossing], 0.1, 0.03));
});

runTest('5. 孔洞必须位于外环内且彼此不接触', () => {
  const outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
  const inside = [[3, 3], [6, 3], [6, 6], [3, 6], [3, 3]];
  const touchingBoundary = [[0, 3], [2, 3], [2, 5], [0, 5], [0, 3]];
  const selfCrossing = [[4, 4], [7, 7], [4, 7], [7, 4], [4, 4]];
  const contour = extractExteriorAndHoles([outer, inside, touchingBoundary, selfCrossing], 0.1, 0.03);
  check(Boolean(contour), '带合法中央孔洞的外环应通过', contour);
  if (contour) {
    checkEqual(contour.holes.length, 1, '边界接触孔洞和自交孔洞都应被过滤', contour.holes);
    check(!ringSelfIntersects(contour.holes[0]), '输出孔洞必须无自交', contour.holes[0]);
    check(pointInRing(4.5, 4.5, contour.holes[0]), '保留的孔洞应是内部合法孔洞', contour.holes[0]);
  }
});

runTest('6. 0.2m 哑铃窄口按人员半径断开，0.6m/1.2m 控制保留', () => {
  const narrow = analyze(makeMesh([
    quad(0, 0, 5, 6, 0.12),
    quad(6, 0, 11, 6, 0.12),
    quad(5, 2.9, 6, 3.1, 0.12),
  ]), 'dumbbell-narrow');
  checkEqual(narrow.floors.map((floor) => floor.elevation_m), [0.12], '窄口哑铃应只有一个 0.12m 楼层');
function segmentIntersection(a, b, c, d) {
  const orient = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const epsilon = 1e-8;
  const o1 = orient(a, b, c), o2 = orient(a, b, d);
  const o3 = orient(c, d, a), o4 = orient(c, d, b);
  if (Math.abs(o1) < epsilon && Math.abs(o2) < epsilon && Math.abs(o3) < epsilon && Math.abs(o4) < epsilon) {
    return Math.max(Math.min(a[0], b[0]), Math.min(c[0], d[0])) <= Math.min(Math.max(a[0], b[0]), Math.max(c[0], d[0]))
      && Math.max(Math.min(a[1], b[1]), Math.min(c[1], d[1])) <= Math.min(Math.max(a[1], b[1]), Math.max(c[1], d[1]));
  }
  return (o1 * o2 <= epsilon && o3 * o4 <= epsilon);
}
function ringsShareBoundary(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length < 2 || b.length < 2) return false;
  for (let i = 0; i < a.length - 1; i += 1) for (let j = 0; j < b.length - 1; j += 1) {
    if (segmentIntersection(a[i], a[i + 1], b[j], b[j + 1])) return true;
  }
  return false;
}

  const narrowFloor = findFloor(narrow, 0.12);
  if (!narrowFloor) return;
  checkEqual(narrowFloor.regions.length, 2, '0.2m 窄口小于人员直径，应切成两个区域', narrowFloor.regions.map(regionDetails));
  near(narrowFloor.area_m2, 60.2, 1.5, '窄口哑铃两区域合计面积应约为 60 m²');
  check(narrowFloor.regions.every((region) => region.physically_blocked === true),
    '窄口两侧区域都应保留低置信警告', narrowFloor.regions.map(regionDetails));
  check(narrowFloor.regions.every((region) => region.warnings.some((warning) => warning.includes('窄口'))),
    '切分后的区域都应说明窄口原因', narrowFloor.regions.map(regionDetails));
  for (const region of narrowFloor.regions) {
    check(isClosedRing(region.outer), '窄口切分后每块外环应闭合且有限', regionDetails(region));
    check(region.hole_count === 0, '窄口两侧房间不应因切分产生伪孔洞', regionDetails(region));
    near(polygonArea(region.outer), region.area_m2, 1.0, '窄口区域外环面积应与区域面积接近');
  }
  const centers = narrowFloor.regions.map((region) => {
    const points = region.outer.slice(0, -1);
    return [points.reduce((sum, item) => sum + item[0], 0) / points.length,
      points.reduce((sum, item) => sum + item[1], 0) / points.length];
  });
  check(centers.length === 2 && centers[0][0] < 5.5 && centers[1][0] > 5.5,
    '两个区域中心应分别位于窄口左右两侧房间', centers);
  check(!ringsShareBoundary(narrowFloor.regions[0].outer, narrowFloor.regions[1].outer),
    '窄口两侧的外环不能共边或共点，否则服务端仍会判定连通',
    { first: narrowFloor.regions[0].outer, second: narrowFloor.regions[1].outer });
  check(!narrowFloor.regions.some((region, index) => pointInRing(centers[1-index][0], centers[1-index][1], region.outer)),
    '两个区域中心不能落在对方外环内部', centers);


  const narrowButPassable = analyze(makeMesh([
    quad(0, 0, 5, 6, 0.12),
    quad(6, 0, 11, 6, 0.12),
    quad(5, 2.7, 6, 3.3, 0.12),
  ]), 'dumbbell-0.6');
  const passableFloor = findFloor(narrowButPassable, 0.12);
  if (passableFloor) {
    checkEqual(passableFloor.regions.length, 1, '0.6m 连接大于人员直径，应保留为一个待复核区域',
      passableFloor.regions.map(regionDetails));
    const region = passableFloor.regions[0];
    if (region) {
      check(region.physically_blocked === false, '0.6m 连接不应判为物理不可通行', regionDetails(region));
      check(region.split_applied === false, '0.6m 连接不应被自动切开', regionDetails(region));
      check(region.near_width_limit === true, '0.6m 连接应提示低于推荐宽度', regionDetails(region));
      check(region.warnings.some((warning) => warning.includes('推荐通行宽度')), '0.6m 连接应说明需要人工核对', region.warnings);
    }
  }

  // Rotation must exercise both raster axes. A horizontal-only test can miss
  // clearance errors in the Z raster direction.
  const rotatedNarrow = analyze(makeMesh([
    quad(0, 0, 6, 5, 0.12),
    quad(0, 6, 6, 11, 0.12),
    quad(2.9, 5, 3.1, 6, 0.12),
  ]), 'dumbbell-narrow-rotated');
  const rotatedFloor = findFloor(rotatedNarrow, 0.12);
  if (rotatedFloor) {
    checkEqual(rotatedFloor.regions.length, 2, '旋转 90° 后 0.2m 窄口仍应切开', rotatedFloor.regions.map(regionDetails));
    check(rotatedFloor.regions.every((region) => region.physically_blocked === true && region.split_applied === true),
      '旋转后两块区域都应标记物理阻挡和已切分', rotatedFloor.regions.map(regionDetails));
    if (rotatedFloor.regions.length === 2) {
      check(!ringsShareBoundary(rotatedFloor.regions[0].outer, rotatedFloor.regions[1].outer),
        '旋转后的两块区域外环不能共边，避免服务端重新连接', rotatedFloor.regions.map((region) => region.outer));
    }
  }

  const wide = analyze(makeMesh([
    quad(0, 0, 5, 6, 0.12),
    quad(6, 0, 11, 6, 0.12),
    quad(5, 2.4, 6, 3.6, 0.12),
  ]), 'dumbbell-wide');
  checkEqual(wide.floors.map((floor) => floor.elevation_m), [0.12], '宽口控制应只有一个 0.12m 楼层');
  const wideFloor = findFloor(wide, 0.12);
  if (!wideFloor) return;
  checkEqual(wideFloor.regions.length, 1, '宽口控制应保留为一个区域', wideFloor.regions.map(regionDetails));
  const wideRegion = wideFloor.regions[0];
  if (!wideRegion) return;
  near(wideFloor.area_m2, 61.2, 1.0, '1.2m 连接带的哑铃面积应约为 61 m²');
  near(wideRegion.min_width_m, 1.2, 0.15, '1.2m 宽口的最小宽度应约为 1.2m');
  checkEqual(wideRegion.warnings.length, 0, '1.2m 宽口不应产生窄口 warning', regionDetails(wideRegion));
  check(wideRegion.width_cut_warning === false, '1.2m 宽口不应标记 width_cut_warning', regionDetails(wideRegion));
  check(wideRegion.near_width_limit === false, '1.2m 宽口不应标记 near_width_limit', regionDetails(wideRegion));
  check(wideRegion.physically_blocked === false, '1.2m 宽口应满足人员直径判定', regionDetails(wideRegion));
});

runTest('7. 桌面、0.30m/0.10m 低平台和斜屋顶过滤', () => {
  const filtered = analyze(makeMesh([
    quad(0, 0, 10, 10, 0.12),
    quad(1, 1, 3, 3, 0.82),
    quad(7, 1, 9, 3, 0.42),
    quad(7, 7, 9, 9, 0.22),
    quad(0, 0, 10, 10, 2.2, 6.6),
  ]), 'filter-furniture-platform-roof');
  checkEqual(filtered.floors.map((floor) => floor.elevation_m), [0.12],
    '桌面、低平台和大于 15° 的斜屋顶都不应成为楼层');
  const floor = findFloor(filtered, 0.12);
  if (!floor) return;
  check(filtered.floors.every((candidate) => Math.abs(candidate.elevation_m - 0.82) > 0.02),
    '0.82m 桌面不应成为独立楼层', filtered.floors.map((item) => item.elevation_m));
  check(filtered.floors.every((candidate) => Math.abs(candidate.elevation_m - 0.42) > 0.02),
    '0.30m 低平台不应成为独立楼层', filtered.floors.map((item) => item.elevation_m));
  check(filtered.floors.every((candidate) => Math.abs(candidate.elevation_m - 0.22) > 0.02),
    '0.10m 低平台不应成为独立楼层', filtered.floors.map((item) => item.elevation_m));
  check(filtered.stats.horizontalTriangleCount === 8,
    '斜屋顶三角形不应计入朝上水平面统计', filtered.stats);
});

runTest('8. 结构板配对的小楼层', () => {
  const result = analyze(makeMesh([
    quad(0, 0, 8, 8, 0.12),
    quad(2, 2, 5, 5, 1.2),
    quad(2, 2, 5, 5, 1.32),
  ]), 'small-floor-structure-pair');
  checkEqual(result.floors.map((floor) => round(floor.elevation_m, 4)), [0.12, 1.32],
    '结构板 1.20m 应被过滤，只保留其上的 1.32m 小楼层');
  const smallFloor = findFloor(result, 1.32);
  if (!smallFloor) return;
  check(!result.floors.some((floor) => Math.abs(floor.elevation_m - 1.2) <= 0.011),
    '结构板底面 1.20m 不应作为楼层输出', result.floors.map((floor) => floor.elevation_m));
  near(smallFloor.area_m2, 9, 0.5, '3m × 3m 小楼层面积应约为 9 m²');
  check(smallFloor.structureMatch === true, '1.32m 小楼层应标记 structureMatch = true', smallFloor);
  check(smallFloor.platformSuspected === false, '已配对结构板的小楼层不应标记为可疑平台', smallFloor);
});

runTest('9. 两个孤立区域', () => {
  const result = analyze(makeMesh([
    quad(0, 0, 4, 4, 0.12),
    quad(10, 0, 14, 4, 0.12),
  ]), 'two-isolated-regions');
  checkEqual(result.floors.map((floor) => floor.elevation_m), [0.12], '两个孤立房间应位于同一 0.12m 楼层');
  const floor = findFloor(result, 0.12);
  if (!floor) return;
  checkEqual(floor.regions.length, 2, '两个不相连的四边形应输出两个区域', floor.regions.map(regionDetails));
  near(floor.area_m2, 32, 1.0, '两个 4m × 4m 孤立房间总面积应约为 32 m²');
  check(floor.regions.every((region) => Math.abs(region.area_m2 - 16) <= 0.5),
    '每个孤立区域面积应约为 16 m²', floor.regions.map((region) => region.area_m2));
  check(floor.regions.every((region) => isClosedRing(region.outer) && region.hole_count === 0),
    '每个孤立区域都应有闭合外环且没有孔洞', floor.regions.map(regionDetails));
});

runTest('10. 朝下楼板底面参与结构配对但不输出为楼层', () => {
  const positions = [];
  const indices = [];
  const addOriented = (y, upward) => {
    const base = positions.length / 3;
    positions.push(0, y, 0, 4, y, 0, 4, y, 4, 0, y, 4);
    if (upward) indices.push(base, base + 2, base + 1, base, base + 3, base + 2);
    else indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  addOriented(0.12, true);
  addOriented(1.20, false);
  addOriented(1.32, true);
  addOriented(2.40, false);
  addOriented(2.52, true);
  addOriented(3.60, false);
  addOriented(3.72, true);
  const result = analyzeGeometry(
    new Float32Array(positions),
    new Uint32Array(indices),
    {},
    'downward-slab-undersides',
  );
  checkEqual(result.floors.map((floor) => round(floor.elevation_m, 4)), [0.12, 1.32, 2.52, 3.72],
    '朝下楼板底面应与朝上楼板顶面配对，且不能把顶层 3.72m 删除',
    result.floors.map((floor) => floor.elevation_m));
  check(result.floors.filter((floor) => floor.elevation_m > 0.5).every((floor) => floor.structureMatch === true),
    '1.32m、2.52m、3.72m 楼层都应匹配到朝下的楼板底面',
    result.floors);
});

runTest('11. 三角面顺序与顶点起点打乱稳定性', () => {
  const mesh = makeMesh([
    quad(0, 0, 4, 4, 0.12),
    quad(10, 0, 14, 4, 0.12),
    quad(0, 0, 4, 4, 1.32),
    quad(10, 0, 14, 4, 1.32),
  ]);
  const shuffled = shuffleTriangles(mesh, 0x5eed1234);
  check(
    mesh.indices.length !== shuffled.indices.length
      || mesh.indices.some((value, index) => value !== shuffled.indices[index]),
    '打乱后的索引顺序应与原始网格不同',
    { original: Array.from(mesh.indices), shuffled: Array.from(shuffled.indices) },
  );
  const baseline = analyze(mesh, 'shuffle-baseline');
  const reordered = analyze(shuffled, 'shuffle-reordered');
  const baselineSummary = stableSummary(baseline);
  const reorderedSummary = stableSummary(reordered);
  checkEqual(
    reorderedSummary,
    baselineSummary,
    '三角面顺序及每个三角形顶点起点打乱后，楼层与区域结果应保持稳定',
    { baseline: baselineSummary, reordered: reorderedSummary },
  );
  checkEqual(reordered.stats.horizontalTriangleCount, baseline.stats.horizontalTriangleCount,
    '三角面顺序打乱后朝上水平面数量应保持不变', reordered.stats);
});

runTest('12. 无区域楼层保留为手工绘制候选', () => {
  const result = analyzeGeometry(
    new Float32Array([0, 0.12, 0, 4, 0.12, 0, 4, 0.12, 4, 0, 0.12, 4]),
    new Uint32Array([0, 2, 1, 0, 3, 2]),
    { minFloorArea: 8, minRegionArea: 100, minWalkableWidth: 1.2, minPersonDiameter: 1.4 },
    'manual-walkable-floor',
  );
  const floor = findFloor(result, 0.12);
  check(Boolean(floor), '即使没有可走区，也应保留楼面候选供人工补画', result.floors);
  if (!floor) return;
  checkEqual(floor.regions.length, 0, '无区域候选的 regions 应为空', floor.regions);
  check(floor.requiresManualWalkable === true, '无区域候选应标记 requiresManualWalkable', floor);
  check(floor.confidence === 'low', '无区域候选应保持低置信度', floor.confidence);
  check(floor.warnings.some((message) => message.includes('手工绘制')), '无区域候选应提示先手工绘制可走区', floor.warnings);
});

runTest('13. 屋面排除项可审查并可恢复为楼层候选', () => {
  const mesh = makeMesh([
    quad(0, 0, 10, 10, 0.12),
    quad(0, 0, 10, 10, 1.12),
    quad(0, 0, 10, 10, 4.92),
  ]);
  const removed = analyzeWith(mesh, { removeRoof: 1 }, 'roof-removed');
  const excluded = removed.excludedFloors?.[0];
  check(Boolean(excluded), '屋面排除应作为 excludedFloors 返回', removed.excludedFloors);
  if (excluded) {
    near(excluded.elevation_m, 4.92, 0.03, '排除项高度应保留为 4.92m');
    near(excluded.area_m2, 100, 2, '排除项应报告水平面面积');
    check(Number.isFinite(excluded.above_coverage), '排除项应报告上方覆盖率', excluded);
    check(excluded.reason.includes('屋面'), '排除项应给出可读原因', excluded.reason);
    check(Array.isArray(excluded.regions), '排除项应拥有与楼层候选一致的 regions 字段', excluded);
    check(excluded.regions.length > 0, '排除项应保留可用于定位和恢复的真实可走轮廓', excluded.regions);
    for (const region of excluded.regions) {
      check(isClosedRing(region.outer), '排除项轮廓应闭合且所有顶点有限', regionDetails(region));
      near(polygonArea(region.outer), 100, 2, '排除项外环面积应约为 100 m²');
      check(region.holes.every(isValidRing), '排除项孔洞应闭合且无自交', regionDetails(region));
    }
    check(!removed.floors.some((floor) => Math.abs(floor.elevation_m - 4.92) <= 0.03),
      '排除轮廓不能同时混入普通楼层候选', removed.floors.map((floor) => floor.elevation_m));
  }
  const restored = analyzeWith(mesh, { removeRoof: 0 }, 'roof-restored');
  const roof = restored.floors.find((floor) => Math.abs(floor.elevation_m - 4.92) <= 0.03);
  check(Boolean(roof), '恢复后应得到 4.92m 楼层候选', restored.floors.map((floor) => floor.elevation_m));
  if (roof) check(roof.requiresManualWalkable === true || roof.regions.length > 0,
    '恢复后的屋面候选应可被接受并处理可走区', { regions: roof.regions.length, requiresManualWalkable: roof.requiresManualWalkable });
});

runTest('14. 五层建筑中的薄双面屋面自动移入审计区', () => {
  const mesh = makePairedStoreyMesh();
  const result = analyzeWith(mesh, {}, 'five-storey-thin-roof');
  checkEqual(result.floors.map((floor) => round(floor.elevation_m, 4)), [0.12, 1.32, 2.52, 3.72],
    '普通楼层应仅保留 0.12/1.32/2.52/3.72，薄双面屋面不能成为第五层',
    result.floors.map((floor) => floor.elevation_m));
  checkEqual(result.excludedFloors.map((item) => round(item.elevation_m, 3)), [4.92],
    '4.92m 薄双面屋面应进入排除审计区', result.excludedFloors);
  const roof = result.excludedFloors[0];
  if (roof) {
    check(roof.roofEvidence === 'top_only_material', '独立顶层材料应生成屋面审计证据', roof);
    check(roof.materialEvidence?.materialName === '屋面材料', '屋面审计应保留可读的材料证据', roof);
    check(roof.reason.includes('顶层'), '屋面原因应说明顶层独有材料', roof.reason);
    check(roof.faceSeparation_m <= 0.02, '屋面上下表皮间距应小于 2cm', roof);
    check(roof.regions.length > 0 && roof.regions.every((region) => isValidRing(region.outer)),
      '屋面审计项必须保留有效轮廓供人工复核或恢复', roof.regions);
  }

  const rotated = { ...rotateMeshY(mesh, Math.PI / 3), materialIds: mesh.materialIds, materialNames: mesh.materialNames };
  const rotatedResult = analyzeWith(rotated, {}, 'five-storey-thin-roof-rotated');
  checkEqual(rotatedResult.floors.map((floor) => round(floor.elevation_m, 4)), [0.12, 1.32, 2.52, 3.72],
    '旋转模型后普通楼层仍不能包含薄双面屋面', rotatedResult.floors.map((floor) => floor.elevation_m));
  checkEqual(rotatedResult.excludedFloors.map((item) => round(item.elevation_m, 3)), [4.92],
    '旋转后 4.92m 薄双面屋面仍应进入审计区', rotatedResult.excludedFloors);
});

runTest('15. removeRoof:0 保留 1–2cm 厚真实顶层', () => {
  const positions = [];
  const indices = [];
  for (const y of [0.12, 1.32, 2.52]) {
    if (y > 0.12) addOrientedQuad(positions, indices, quad(-6, -6, 6, 6, y - 0.02), false);
    addOrientedQuad(positions, indices, quad(-6, -6, 6, 6, y), true);
  }
  addOrientedQuad(positions, indices, quad(-6, -6, 6, 6, 3.71), false);
  addOrientedQuad(positions, indices, quad(-6, -6, 6, 6, 3.72), true);
  const mesh = { positions: new Float32Array(positions), indices: new Uint32Array(indices) };
  for (const options of [{}, { removeRoof: 0 }]) {
    const result = analyzeWith(mesh, options, 'thin-real-top');
    checkEqual(result.floors.map((floor) => round(floor.elevation_m, 2)), [0.12, 1.32, 2.52, 3.72],
      '薄双面贴合不能单独删除真实顶层，removeRoof:' + (options.removeRoof ?? '默认') + ' 必须保留 3.72m',
      result.floors.map((floor) => floor.elevation_m));
    checkEqual(result.excludedFloors.length, 0, '无顶层独有材料时不得自动排除', result.excludedFloors);
    const top = findFloor(result, 3.72);
    check(top?.warnings?.some((message) => message.includes('人工确认')), '无材料证据时顶层应产生人工核对提示', top?.warnings);
  }
});

runTest('16. 顶层材料与普通楼面相同不得自动排除', () => {
  const mesh = makePairedStoreyMesh({ roofMaterial: 0, storeyMaterial: 0 });
  const result = analyzeWith(mesh, {}, 'same-material-roof');
  checkEqual(result.floors.map((floor) => round(floor.elevation_m, 2)), [0.12, 1.32, 2.52, 3.72, 4.92],
    '若顶层材料与普通地面一致，应保留为普通候选并等待人工确认', result.floors.map((floor) => floor.elevation_m));
  checkEqual(result.excludedFloors.length, 0, '普通材料不得自动触发屋面排除', result.excludedFloors);
});

runTest('17. 显式 removeRoof:1 仍可排除顶层', () => {
  const mesh = makePairedStoreyMesh({ roofMaterial: 0, storeyMaterial: 0 });
  const result = analyzeWith(mesh, { removeRoof: 1 }, 'explicit-roof-removal');
  checkEqual(result.excludedFloors.map((item) => round(item.elevation_m, 2)), [4.92],
    '显式 removeRoof:1 仍应把最高层移入审计区', result.excludedFloors);
  check(result.excludedFloors[0]?.roofEvidence === 'explicit_request', '显式移除需标记来源', result.excludedFloors);
});

runTest('18. 顶层独有材料在旋转后保持稳定', () => {
  const mesh = makePairedStoreyMesh();
  const rotated = { ...rotateMeshY(mesh, Math.PI / 4), materialIds: mesh.materialIds, materialNames: mesh.materialNames };
  const result = analyzeWith(rotated, {}, 'rotated-exclusive-material');
  checkEqual(result.excludedFloors.map((item) => round(item.elevation_m, 2)), [4.92],
    '材质证据必须与几何坐标无关，旋转后仍能识别顶层独有材料', result.excludedFloors);
});

runTest('19. 材质数组按 geometry groups 归属到三角面', () => {
  const identity = () => ({ elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] });
  const plain = { name: '普通楼面', uuid: 'mat-plain', type: 'MeshStandardMaterial', color: { r: 0.2, g: 0.3, b: 0.4 } };
  const roof = { name: '屋面材料', uuid: 'mat-roof', type: 'MeshStandardMaterial', color: { r: 0.6, g: 0.2, b: 0.1 } };
  const noMaterialMesh = {
    isMesh: true, matrixWorld: identity(), geometry: { attributes: { position: { count: 6, getX: () => 0, getY: () => 0, getZ: () => 0 } } },
  };
  const geometry = {
    attributes: {
      position: {
        count: 8,
        getX: (index) => [0, 4, 4, 0, 10, 14, 14, 10][index],
        getY: () => 0.12,
        getZ: (index) => [0, 0, 4, 4, 0, 0, 4, 4][index],
      },
    },
    index: {
      count: 12,
      getX: (index) => [0, 2, 1, 0, 3, 2, 4, 6, 5, 4, 7, 6][index],
    },
    groups: [
      { start: 0, count: 6, materialIndex: 0 },
      { start: 6, count: 6, materialIndex: 1 },
    ],
  };
  const mesh = { isMesh: true, matrixWorld: identity(), geometry, material: [plain, roof, { name: '未使用材质', uuid: 'mat-unused', type: 'MeshStandardMaterial', color: { r: 0.8, g: 0.8, b: 0.8 } }] };
  const root = { updateMatrixWorld() {}, traverseVisible(visit) { visit(noMaterialMesh); visit(mesh); } };
  const result = collectWorldGeometry(root);
  checkEqual(result.materialNames, ['普通楼面', '屋面材料', '未使用材质'], '材质名称应按首次出现顺序稳定记录', result.materialNames);
  checkEqual(Array.from(result.materialIds), [-1, -1, 0, 0, 1, 1], 'geometry groups 应把材质 ID 写入对应三角面，无材质记录为 -1', Array.from(result.materialIds));
  checkEqual(result.triangleCount, 6, '收集结果应包含所有有效三角面', result.triangleCount);
});

function makeMaterialRoofMesh() {
  const positions = [];
  const indices = [];
  const materialIds = [];
  const add = (q, upward, material) => {
    const triangle = indices.length / 3;
    addOrientedQuad(positions, indices, q, upward);
    materialIds.push(material, material);
    return triangle;
  };
  for (const y of [0.12, 1.32, 2.52, 3.72]) {
    if (y > 0.12) add(quad(-6, -6, 6, 6, y - 0.12), false, 0);
    add(quad(-6, -6, 6, 6, y), true, 0);
  }
  add(quad(-6, -6, 6, 6, 4.80), false, 0);
  add(quad(-6, -6, 6, 6, 4.92), true, 1);
  add(quad(-6, -6, 6, 6, 4.921), false, 1);
  return { positions: new Float32Array(positions), indices: new Uint32Array(indices), materialIds: new Int32Array(materialIds) };
}

function installWorkerHarness({ autoFinish = false } = {}) {
  const originalWorker = globalThis.Worker;
  const state = { instances: [], posted: [] };
  class FakeWorker {
    constructor(url, options) {
      this.url = url;
      this.options = options;
      this.terminated = false;
      this.onmessage = null;
      this.onerror = null;
      state.instances.push(this);
    }
    postMessage(message, transfer) {
      if (this.terminated) throw new Error('postMessage called after terminate');
      state.posted.push({ worker: this, message, transfer });
      if (autoFinish && message.type === 'analyze') {
        queueMicrotask(() => this.onmessage?.({
          data: {
            type: 'result', requestId: message.requestId,
            result: analyzeGeometry(message.positions, message.indices, message.options, message.requestId, {
              triangleMaterialIds: message.materialIds, materialNames: message.materialNames,
            }),
          },
        }));
      }
    }
    terminate() { this.terminated = true; }
    emit(data) { this.onmessage?.({ data }); }
  }
  globalThis.Worker = FakeWorker;
  return {
    state,
    restore() { globalThis.Worker = originalWorker; },
  };
}

await (async () => {
  currentTest = '20. Worker 传递材质、数组与取消生命周期';
  const before = failures.length;
  console.log('\n▶ ' + currentTest);
  const harness = installWorkerHarness();
  try {
    const mesh = makeMaterialRoofMesh();
    const job = analyzeInWorker({
      positions: mesh.positions, indices: mesh.indices,
      materialIds: mesh.materialIds, materialNames: ['普通楼面', '屋面材料'],
      options: {},
    });
    const posted = harness.state.posted[0];
    check(Boolean(posted), '分析任务应向 Worker 发送 analyze 消息');
    checkEqual(posted.message.materialNames, ['普通楼面', '屋面材料'], '材质名称应随任务传入 Worker', posted.message.materialNames);
    check(posted.message.materialIds instanceof Int32Array, '材质 ID 应作为 Int32Array 传入', posted.message.materialIds);
    checkEqual(posted.transfer.length, 3, '位置、索引和材质缓冲区都应进入 Transferable', posted.transfer.length);
    check(posted.transfer.includes(mesh.materialIds.buffer), '材质 ID 的 buffer 必须进入 Transferable', posted.transfer);
    job.cancel();
    await assert.rejects(job.promise, (error) => error.name === 'AbortError');
    check(harness.state.instances[0].terminated, '取消后应立即终止 Worker', harness.state.instances[0]);
    const postedAfterCancel = harness.state.posted.length;
    harness.state.instances[0].emit({ type: 'result', requestId: posted.message.requestId, result: { floors: [] } });
    checkEqual(harness.state.posted.length, postedAfterCancel, '取消后的迟到消息不应再改变任务结果', harness.state.posted.length);
  } catch (error) {
    recordFailure(error);
  } finally {
    harness.restore();
  }
  if (failures.length === before) console.log('PASS ' + currentTest);
  else console.log('FAILED ' + currentTest + ' (' + (failures.length - before) + ' assertion(s))');
})();

await (async () => {
  currentTest = '21. Worker 完成后释放资源且只结算一次';
  const before = failures.length;
  console.log('\n▶ ' + currentTest);
  const harness = installWorkerHarness({ autoFinish: true });
  try {
    const mesh = makeMaterialRoofMesh();
    const job = analyzeInWorker({ positions: mesh.positions, indices: mesh.indices, materialIds: mesh.materialIds, materialNames: ['普通楼面', '屋面材料'], options: {} });
    const result = await job.promise;
    checkEqual(result.excludedFloors.map((item) => round(item.elevation_m, 2)), [4.92], 'Worker 结果应依据材质把 4.92m 顶层放入审计区', result.excludedFloors);
    check(harness.state.instances[0].terminated, '收到结果后应终止 Worker');
    job.cancel();
    check(harness.state.instances[0].terminated, '完成后再次取消不应破坏结算状态');
  } catch (error) {
    recordFailure(error);
  } finally {
    harness.restore();
  }
  if (failures.length === before) console.log('PASS ' + currentTest);
  else console.log('FAILED ' + currentTest + ' (' + (failures.length - before) + ' assertion(s))');
})();

runTest('22. 栅格分辨率变化保持楼层、连通和面积稳定', () => {
  const mesh = makeMesh([
    quad(0, 0, 10, 10, 0.12),
    quad(12, 0, 20, 8, 0.12),
    quad(0, 0, 10, 10, 1.32),
    quad(12, 0, 20, 8, 1.32),
    quad(0, 0, 10, 3, 2.52),
    quad(0, 7, 10, 10, 2.52),
    quad(0, 3, 3, 7, 2.52),
    quad(7, 3, 10, 7, 2.52),
  ]);
  const counts = [];
  for (const rasterCellsAcross of [420, 720, 1200]) {
    const result = analyzeWith(mesh, { rasterCellsAcross }, 'resolution-' + rasterCellsAcross);
    const summary = result.floors.map((floor) => ({
      elevation: round(floor.elevation_m, 4),
      regions: floor.regions.length,
      area: round(floor.area_m2, 2),
    }));
    counts.push(summary);
    checkEqual(summary.map((item) => item.elevation), [0.12, 1.32, 2.52], `分辨率 ${rasterCellsAcross} 的楼层应稳定`, summary);
    checkEqual(summary.map((item) => item.regions), [2, 2, 1], `分辨率 ${rasterCellsAcross} 的连通区域应稳定`, summary);
    check(summary.every((item) => item.area > 0 && item.area < 165), `分辨率 ${rasterCellsAcross} 的面积应有效`, summary);
  }
  for (let index = 1; index < counts.length; index += 1) {
    for (let floorIndex = 0; floorIndex < counts[index].length; floorIndex += 1) {
      const baseline = counts[0][floorIndex].area;
      const current = counts[index][floorIndex].area;
      check(Math.abs(current - baseline) <= Math.max(0.5, baseline * 0.03),
        `分辨率 ${[420, 720, 1200][index]} 的第 ${floorIndex + 1} 层面积变化应小于 3%`, { baseline, current });
    }
  }
  check(counts.every((summary) => summary[2].area > 80 && summary[2].area < 100), '有孔洞楼层面积应始终约为 84 m²', counts);
});
if (failures.length) {
  console.error('\n' + failures.length + ' assertion failure(s). See FAIL lines above for actual values.');
  process.exitCode = 1;
} else {
  console.log('\nauto-calibrate geometry tests passed');
}

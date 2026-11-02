import assert from 'node:assert/strict';

globalThis.self = { postMessage() {} };
const { analyzeGeometry } = await import('../static/utils/auto-calibrate-worker.mjs');

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

function analyze(mesh, requestId) {
  return analyzeGeometry(mesh.positions, mesh.indices, {}, requestId);
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

function isValidRing(ring) {
  return isClosedRing(ring) && polygonArea(ring) > 1e-6;
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
  check(isClosedRing(region.outer), '外环应是闭合且各点有限的环', region.outer);
  near(polygonArea(region.outer), 100, 1.5, '外环面积应约为 100 m²');
  checkEqual(region.hole_count, 1, '中央孔洞应输出 hole_count = 1', regionDetails(region));
  checkEqual(region.holes.length, region.hole_count, 'holes 数组长度应等于 hole_count', regionDetails(region));
  const hole = region.holes[0];
  if (!hole) return;
  check(isValidRing(hole), '孔洞应是以闭合环表示的有限多边形', hole);
  near(polygonArea(hole), 16, 1.0, '4m × 4m 中央孔洞的环面积应约为 16 m²');
  check(pointInRing(5, 5, region.outer), '孔洞中心点应位于外环内部', { outer_area: polygonArea(region.outer) });
  check(hole.slice(0, -1).every((point) => isFinitePoint(point)), '孔洞环的所有顶点都应是有限的二维点', hole);
});

runTest('3. 0.2m 哑铃窄口与 1.2m 宽口控制', () => {
  const narrow = analyze(makeMesh([
    quad(0, 0, 5, 6, 0.12),
    quad(6, 0, 11, 6, 0.12),
    quad(5, 2.9, 6, 3.1, 0.12),
  ]), 'dumbbell-narrow');
  checkEqual(narrow.floors.map((floor) => floor.elevation_m), [0.12], '窄口哑铃应只有一个 0.12m 楼层');
  const narrowFloor = findFloor(narrow, 0.12);
  if (!narrowFloor) return;
  checkEqual(narrowFloor.regions.length, 1, '窄口哑铃应完整保留为一个区域', narrowFloor.regions.map(regionDetails));
  const narrowRegion = narrowFloor.regions[0];
  if (!narrowRegion) return;
  near(narrowFloor.area_m2, 60.2, 1.0, '窄口哑铃的完整面积应约为 60 m²');
  near(narrowRegion.area_m2, 60.2, 1.0, '窄口区域应完整保留约 60 m²，而不是断开房间');
  near(narrowRegion.min_width_m, 0.2, 0.15, '窄口最小宽度应约为 0.2m');
  check(narrowRegion.physically_blocked === true, '0.2m 窄口应标记低于人员直径，不能作为安全自动通路', regionDetails(narrowRegion));
  check(narrowRegion.min_width_split_components >= 2,
    '窄口诊断应报告至少两个被窄口分开的分量', regionDetails(narrowRegion));
  check(narrowRegion.warnings.length > 0, '窄口区域必须包含 warning', regionDetails(narrowRegion));
  check(narrowRegion.width_cut_warning === true, '窄口区域应标记 width_cut_warning = true', regionDetails(narrowRegion));
  check(narrowRegion.near_width_limit === true, '0.2m 窄口应标记 near_width_limit = true', regionDetails(narrowRegion));
  check(narrowRegion.warnings.some((warning) => warning.includes('窄口')), 'warning 文本应明确指出窄口', narrowRegion.warnings);

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

runTest('4. 桌面、0.30m/0.10m 低平台和斜屋顶过滤', () => {
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

runTest('5. 结构板配对的小楼层', () => {
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

runTest('6. 两个孤立区域', () => {
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

runTest('8. 朝下楼板底面参与结构配对但不输出为楼层', () => {
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

runTest('7. 三角面顺序与顶点起点打乱稳定性', () => {
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

if (failures.length) {
  console.error('\n' + failures.length + ' assertion failure(s). See FAIL lines above for actual values.');
  process.exitCode = 1;
} else {
  console.log('\nauto-calibrate geometry tests passed');
}

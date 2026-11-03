export const AUTO_MATCH_TOLERANCE_M = 0.25;

const EPSILON = 1e-9;
const FLOOR_NAMES = ['一层', '二层', '三层', '四层', '五层', '六层', '七层', '八层'];

function clone(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function compareText(left, right) {
  const a = String(left ?? '');
  const b = String(right ?? '');
  return a < b ? -1 : a > b ? 1 : 0;
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function floorIdOf(floor) {
  return String(floor?.id ?? '').trim();
}

function candidateIdOf(candidate, index) {
  return String(candidate?.id ?? candidate?.candidateId ?? 'candidate-' + (index + 1));
}

function acceptedRegionsOf(candidate) {
  return (Array.isArray(candidate?.regions) ? candidate.regions : [])
    .filter((region) => Boolean(region?.accepted));
}

function candidateIsApplicable(candidate) {
  if (!candidate?.accepted) return false;
  const regions = Array.isArray(candidate.regions) ? candidate.regions : [];
  return regions.length === 0 || regions.some((region) => Boolean(region?.accepted));
}

function generatedFloorName(index, usedNames) {
  const base = FLOOR_NAMES[index] || ('第' + (index + 1) + '层');
  let name = base;
  let suffix = 2;
  while (usedNames.has(name)) name = base + ' ' + (suffix++);
  usedNames.add(name);
  return name;
}

function nextFloorId(floors, usedIds, startIndex) {
  let index = Math.max(1, Number(startIndex) || 1);
  let id = 'floor-' + index;
  while (usedIds.has(id) || floors.some((floor) => floorIdOf(floor) === id)) {
    index += 1;
    id = 'floor-' + index;
  }
  usedIds.add(id);
  return id;
}

function nextRegionId(floorId, usedIds) {
  let index = 1;
  let id = floorId + '-auto-region-' + index;
  while (usedIds.has(id)) {
    index += 1;
    id = floorId + '-auto-region-' + index;
  }
  usedIds.add(id);
  return id;
}

function planRegion(region) {
  return {
    outer: clone(Array.isArray(region?.outer) ? region.outer : []),
    holes: clone(Array.isArray(region?.holes) ? region.holes : []),
  };
}

function cross2d(origin, left, right) {
  return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0]);
}

function pointOnSegment2D(point, start, end) {
  const cross = cross2d(start, point, end);
  if (Math.abs(cross) > EPSILON) return false;
  return point[0] >= Math.min(start[0], end[0]) - EPSILON
    && point[0] <= Math.max(start[0], end[0]) + EPSILON
    && point[1] >= Math.min(start[1], end[1]) - EPSILON
    && point[1] <= Math.max(start[1], end[1]) + EPSILON;
}

function pointInRing2D(point, ring) {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const currentPoint = ring[index];
    const previousPoint = ring[previous];
    if (pointOnSegment2D(point, previousPoint, currentPoint)) return true;
    if ((currentPoint[1] > point[1]) !== (previousPoint[1] > point[1])) {
      const intersectionX = (previousPoint[0] - currentPoint[0])
        * (point[1] - currentPoint[1])
        / (previousPoint[1] - currentPoint[1])
        + currentPoint[0];
      if (point[0] < intersectionX) inside = !inside;
    }
  }
  return inside;
}

function orientation2D(a, b, c) {
  return cross2d(a, b, c);
}

function segmentsIntersect2D(a, b, c, d) {
  const abC = orientation2D(a, b, c);
  const abD = orientation2D(a, b, d);
  const cdA = orientation2D(c, d, a);
  const cdB = orientation2D(c, d, b);
  if (((abC > EPSILON && abD < -EPSILON) || (abC < -EPSILON && abD > EPSILON))
    && ((cdA > EPSILON && cdB < -EPSILON) || (cdA < -EPSILON && cdB > EPSILON))) {
    return true;
  }
  return (Math.abs(abC) <= EPSILON && pointOnSegment2D(c, a, b))
    || (Math.abs(abD) <= EPSILON && pointOnSegment2D(d, a, b))
    || (Math.abs(cdA) <= EPSILON && pointOnSegment2D(a, c, d))
    || (Math.abs(cdB) <= EPSILON && pointOnSegment2D(b, c, d));
}

function ringSelfIntersects(ring) {
  for (let left = 0; left < ring.length; left += 1) {
    const leftNext = (left + 1) % ring.length;
    for (let right = left + 1; right < ring.length; right += 1) {
      const rightNext = (right + 1) % ring.length;
      if (left === right || leftNext === right || rightNext === left) continue;
      if (segmentsIntersect2D(ring[left], ring[leftNext], ring[right], ring[rightNext])) return true;
    }
  }
  return false;
}

function ringsIntersect2D(left, right) {
  for (let leftIndex = 0; leftIndex < left.length; leftIndex += 1) {
    const leftNext = (leftIndex + 1) % left.length;
    for (let rightIndex = 0; rightIndex < right.length; rightIndex += 1) {
      const rightNext = (rightIndex + 1) % right.length;
      if (segmentsIntersect2D(
        left[leftIndex], left[leftNext],
        right[rightIndex], right[rightNext],
      )) return true;
    }
  }
  return false;
}

function ringStrictlyInside2D(inner, outer) {
  if (!inner.every((point) => pointInRing2D(point, outer))) return false;
  return !ringsIntersect2D(inner, outer);
}

function normalizeRing(raw, label) {
  if (!Array.isArray(raw)) throw new Error(label + '必须是点数组');
  const ring = raw.map((point, pointIndex) => {
    if (!Array.isArray(point) || point.length < 2) {
      throw new Error(label + '第 ' + (pointIndex + 1) + ' 个点格式无效');
    }
    const x = finiteNumber(point[0]);
    const z = finiteNumber(point[1]);
    if (x == null || z == null) throw new Error(label + '第 ' + (pointIndex + 1) + ' 个点包含非有限坐标');
    return [x, z];
  });
  while (ring.length > 3) {
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (Math.abs(first[0] - last[0]) <= EPSILON && Math.abs(first[1] - last[1]) <= EPSILON) ring.pop();
    else break;
  }
  if (ring.length < 3) throw new Error(label + '至少需要三个边界点');
  for (let index = 0; index < ring.length; index += 1) {
    const current = ring[index];
    const next = ring[(index + 1) % ring.length];
    if (Math.abs(current[0] - next[0]) <= EPSILON && Math.abs(current[1] - next[1]) <= EPSILON) {
      throw new Error(label + '包含相邻重复点');
    }
  }
  const area = Math.abs(ring.reduce((sum, point, index) => {
    const next = ring[(index + 1) % ring.length];
    return sum + point[0] * next[1] - next[0] * point[1];
  }, 0)) * 0.5;
  if (!Number.isFinite(area) || area <= 1e-8) throw new Error(label + '面积无效');
  if (ringSelfIntersects(ring)) throw new Error(label + '存在自交');
  return ring;
}

function normalizeAutoRegion(region, id, label) {
  const outer = normalizeRing(region?.outer, label + '外轮廓');
  const holesRaw = Array.isArray(region?.holes) ? region.holes : [];
  const holes = holesRaw.map((hole, index) => normalizeRing(hole, label + '孔洞 ' + (index + 1)));
  for (let index = 0; index < holes.length; index += 1) {
    const holeLabel = label + '孔洞 ' + (index + 1);
    if (!ringStrictlyInside2D(holes[index], outer)) {
      throw new Error(holeLabel + '必须完整位于外轮廓内部，且不能接触外边界');
    }
    for (let other = index + 1; other < holes.length; other += 1) {
      if (ringsIntersect2D(holes[index], holes[other])
        || pointInRing2D(holes[other][0], holes[index])
        || pointInRing2D(holes[index][0], holes[other])) {
        throw new Error(label + '孔洞 ' + (index + 1) + ' 与孔洞 ' + (other + 1) + ' 重叠');
      }
    }
  }
  return { id, outer, holes };
}

function validateDraftStructure(draft) {
  if (!draft || typeof draft !== 'object' || !Array.isArray(draft.floors)) {
    throw new Error('草稿缺少楼层数组');
  }
  const floorIds = new Set();
  const regionIds = new Set();
  for (const floor of draft.floors) {
    const floorId = floorIdOf(floor);
    if (!floorId) throw new Error('草稿包含缺失楼层 ID');
    if (floorIds.has(floorId)) throw new Error('草稿包含重复楼层 ID ' + floorId);
    floorIds.add(floorId);
    const elevation = finiteNumber(floor?.elevation_m);
    if (elevation == null || Math.abs(elevation) > 1000) throw new Error('楼层 ' + floorId + ' 的楼面高度无效');
    if (!Array.isArray(floor.walkable)) throw new Error('楼层 ' + floorId + ' 的 walkable 必须是数组');
    for (const region of floor.walkable) {
      const regionId = String(region?.id ?? '').trim();
      if (!regionId) throw new Error('楼层 ' + floorId + ' 包含缺失区域 ID');
      if (regionIds.has(regionId)) throw new Error('区域 ID ' + regionId + ' 在草稿中重复');
      regionIds.add(regionId);
      normalizeRing(region?.outer, '区域 ' + regionId + ' 外轮廓');
      for (const [index, hole] of (Array.isArray(region?.holes) ? region.holes : []).entries()) {
        normalizeRing(hole, '区域 ' + regionId + ' 孔洞 ' + (index + 1));
      }
    }
  }
  return { floorIds, regionIds };
}

function applyAnnotationInvalidation(draft, reason) {
  const hasAnnotations = Boolean(
    (draft.floors || []).some((floor) => (floor.walkable || []).length || (floor.spawn_zones || []).length)
    || (draft.stairs || []).length
    || (draft.doors || []).length
    || (draft.exits || []).length,
  );
  if (hasAnnotations) {
    draft.annotation_recheck = {
      ...(draft.annotation_recheck || {}),
      status: 'needs-recheck',
      reason,
    };
  }
  if (draft.navigation_review?.status === 'reviewed') {
    draft.navigation_review = {
      ...(draft.navigation_review || {}),
      status: 'needs-review',
      message: '楼面标高或自动标注已修改，需重新核对并运行导航预览。',
    };
  }
}

export function syncFloorDependentHeights(draft, floorId, oldElevation, newElevation, reason = '') {
  if (!draft || !floorId) return false;
  const id = String(floorId);
  const oldValue = finiteNumber(oldElevation);
  const newValue = finiteNumber(newElevation);
  if (oldValue == null || newValue == null || Math.abs(oldValue - newValue) <= EPSILON) return false;

  for (const door of Array.isArray(draft.doors) ? draft.doors : []) {
    if (String(door?.floor ?? '') === id && Array.isArray(door.position) && door.position.length >= 3) {
      door.position[1] = newValue;
    }
  }
  for (const exit of Array.isArray(draft.exits) ? draft.exits : []) {
    if (String(exit?.floor ?? '') === id && Array.isArray(exit.position) && exit.position.length >= 3) {
      exit.position[1] = newValue;
    }
  }

  for (const stair of Array.isArray(draft.stairs) ? draft.stairs : []) {
    const fromMatches = String(stair?.from_floor ?? '') === id;
    const toMatches = String(stair?.to_floor ?? '') === id;
    if (!fromMatches && !toMatches) continue;
    const path = Array.isArray(stair.path) ? stair.path : [];
    if (path.length >= 2) {
      const oldStart = finiteNumber(path[0]?.[1]);
      const oldEnd = finiteNumber(path[path.length - 1]?.[1]);
      const newStart = fromMatches ? newValue : oldStart;
      const newEnd = toMatches ? newValue : oldEnd;
      if (newStart == null || newEnd == null) continue;
      const oldSpan = oldEnd - oldStart;
      for (let index = 0; index < path.length; index += 1) {
        if (!Array.isArray(path[index])) path[index] = [0, 0, 0];
        if (index === 0) path[index][1] = newStart;
        else if (index === path.length - 1) path[index][1] = newEnd;
        else {
          const ratio = Math.abs(oldSpan) > EPSILON
            ? (Number(path[index][1]) - oldStart) / oldSpan
            : index / Math.max(1, path.length - 1);
          path[index][1] = newStart + (newEnd - newStart) * ratio;
        }
      }
      stair.bottom = clone(path[0]);
      stair.top = clone(path[path.length - 1]);
    } else if (path.length === 1) {
      if (!Array.isArray(path[0])) path[0] = [0, oldValue, 0];
      if (fromMatches) path[0][1] = newValue;
      if (toMatches) path[0][1] = newValue;
      stair.bottom = clone(path[0]);
      stair.top = clone(path[0]);
    } else {
      if (fromMatches && Array.isArray(stair.bottom) && stair.bottom.length >= 3) stair.bottom[1] = newValue;
      if (toMatches && Array.isArray(stair.top) && stair.top.length >= 3) stair.top[1] = newValue;
    }
  }

  const note = String(reason || '').trim()
    || ('楼面高度已从 ' + oldValue.toFixed(2) + ' m 改为 ' + newValue.toFixed(2) + ' m，关联门、出口与楼梯高度已同步，仍需人工复核。');
  applyAnnotationInvalidation(draft, note);
  return true;
}

export function listEmptyWalkableFloors(draft) {
  return (Array.isArray(draft?.floors) ? draft.floors : [])
    .filter((floor) => !Array.isArray(floor.walkable) || floor.walkable.length === 0)
    .map((floor) => floorIdOf(floor));
}

export function createAutoApplyPlan(draft, candidates) {
  if (!draft || !Array.isArray(draft.floors)) throw new Error('草稿缺少楼层数组');
  const sourceFingerprint = JSON.stringify(draft);
  const candidateList = Array.isArray(candidates) ? candidates : [];
  const floors = draft.floors;
  const usedFloorIds = new Set(floors.map(floorIdOf).filter(Boolean));
  const usedRegionIds = new Set(floors.flatMap((floor) => (floor.walkable || []).map((region) => String(region?.id ?? ''))).filter(Boolean));
  const usedNames = new Set(floors.map((floor) => String(floor.name ?? '').trim()).filter(Boolean));
  const candidateRecords = candidateList.map((candidate, index) => {
    const elevation = finiteNumber(candidate?.elevation_m);
    const id = candidateIdOf(candidate, index);
    const eligibleFloors = [];
    let nearest = null;
    for (const floor of floors) {
      const floorId = floorIdOf(floor);
      const floorElevation = finiteNumber(floor?.elevation_m);
      if (!floorId || floorElevation == null) continue;
      const difference = Math.abs(floorElevation - elevation);
      if (!Number.isFinite(difference)) continue;
      const item = { floor, floorId, floorElevation, difference };
      if (!nearest || difference < nearest.difference
        || (Math.abs(difference - nearest.difference) <= EPSILON && compareText(floorId, nearest.floorId) < 0)) {
        nearest = item;
      }
      if (elevation != null && difference <= AUTO_MATCH_TOLERANCE_M + EPSILON) eligibleFloors.push(item);
    }
    return { candidate, index, id, elevation, eligibleFloors, nearest };
  });

  const pairs = [];
  for (const record of candidateRecords) {
    for (const eligible of record.eligibleFloors) {
      pairs.push({
        candidateIndex: record.index,
        candidateId: record.id,
        candidateElevation: record.elevation,
        floor: eligible.floor,
        floorId: eligible.floorId,
        floorElevation: eligible.floorElevation,
        difference: eligible.difference,
      });
    }
  }
  pairs.sort((left, right) => {
    if (Math.abs(left.difference - right.difference) > EPSILON) return left.difference - right.difference;
    if (left.candidateIndex !== right.candidateIndex) return left.candidateIndex - right.candidateIndex;
    const floorOrder = compareText(left.floorId, right.floorId);
    if (floorOrder) return floorOrder;
    return compareText(left.candidateId, right.candidateId);
  });

  const matchedCandidates = new Set();
  const matchedFloors = new Set();
  const assignmentByCandidate = new Map();
  for (const pair of pairs) {
    if (matchedCandidates.has(pair.candidateIndex) || matchedFloors.has(pair.floorId)) continue;
    matchedCandidates.add(pair.candidateIndex);
    matchedFloors.add(pair.floorId);
    assignmentByCandidate.set(pair.candidateIndex, pair);
  }

  const competitorsByFloor = new Map();
  for (const pair of pairs) {
    if (!competitorsByFloor.has(pair.floorId)) competitorsByFloor.set(pair.floorId, []);
    competitorsByFloor.get(pair.floorId).push(pair.candidateIndex);
  }

  const matches = candidateRecords.map((record) => {
    const assignment = assignmentByCandidate.get(record.index) || null;
    const eligibleFloorIds = record.eligibleFloors
      .map((item) => item.floorId)
      .filter((floorId, index, list) => list.indexOf(floorId) === index);
    const attributedFloorId = assignment?.floorId || eligibleFloorIds[0] || null;
    const competingCandidateIndexes = attributedFloorId
      ? (competitorsByFloor.get(attributedFloorId) || []).filter((index) => index !== record.index)
      : [];
    const nearest = record.nearest;
    return {
      candidateIndex: record.index,
      candidateId: record.id,
      candidateElevation: record.elevation,
      floorId: assignment?.floorId ?? null,
      floorName: assignment ? String(assignment.floor.name ?? assignment.floorId) : null,
      floorElevation: assignment?.floorElevation ?? null,
      elevationDifference: assignment?.difference ?? null,
      nearestFloorId: nearest?.floorId ?? null,
      nearestFloorName: nearest ? String(nearest.floor.name ?? nearest.floorId) : null,
      nearestFloorElevation: nearest?.floorElevation ?? null,
      nearestElevationDifference: nearest?.difference ?? null,
      assignedByMinimumDifference: Boolean(assignment),
      contestedFloorIds: eligibleFloorIds,
      competitionStatus: assignment
        ? (competingCandidateIndexes.length ? 'won' : 'uncontested')
        : (eligibleFloorIds.length ? 'lost' : 'none'),
      contested: competingCandidateIndexes.length > 0,
      competingCandidateIndexes: [...competingCandidateIndexes],
      skippedReason: !assignment && eligibleFloorIds.length ? 'competition' : null,
      operationKind: 'none',
      skippedRegions: 0,
      noRegions: false,
    };
  });

  const operations = [];
  let skippedExisting = 0;
  let skippedNoRegion = 0;
  let skippedCompetition = 0;
  let created = 0;
  for (const record of candidateRecords) {
    const candidate = record.candidate;
    const match = matches[record.index];
    const regions = acceptedRegionsOf(candidate);
    const allRegions = Array.isArray(candidate?.regions) ? candidate.regions : [];
    const applicable = candidateIsApplicable(candidate);
    const assignment = assignmentByCandidate.get(record.index) || null;
    if (!applicable) {
      match.operationKind = 'none';
      continue;
    }
    if (record.elevation == null) throw new Error('楼层候选 ' + record.id + ' 缺少有效标高');
    if (!assignment && match.contestedFloorIds.length) {
      match.operationKind = 'skipped';
      match.skippedReason = 'competition';
      skippedCompetition += 1;
      continue;
    }

    let targetId;
    let targetName;
    let kind;
    let floorBeforeElevation = null;
    let skippedRegions = 0;
    if (assignment) {
      const floor = assignment.floor;
      targetId = floorIdOf(floor);
      targetName = String(floor.name ?? targetId);
      floorBeforeElevation = finiteNumber(floor.elevation_m);
      kind = 'update';
      if (regions.length && (floor.walkable || []).length && !candidate.replaceExisting) {
        skippedRegions = regions.length;
        skippedExisting += regions.length;
      } else if (regions.length && (floor.walkable || []).length && candidate.replaceExisting) {
        kind = 'replace';
      }
      if (allRegions.length === 0) {
        match.noRegions = true;
        skippedNoRegion += 1;
      }
    } else {
      targetId = nextFloorId(floors, usedFloorIds, floors.length + created + 1);
      targetName = generatedFloorName(floors.length + created, usedNames);
      kind = 'create';
      floorBeforeElevation = null;
      created += 1;
      if (allRegions.length === 0) {
        match.noRegions = true;
        skippedNoRegion += 1;
      }
    }

    const effectiveRegions = skippedRegions ? [] : regions;
    const regionIds = effectiveRegions.map(() => nextRegionId(targetId, usedRegionIds));
    const operation = {
      kind,
      candidateIndex: record.index,
      candidateId: record.id,
      targetId,
      targetName,
      sourceElevation: record.elevation,
      elevation: record.elevation,
      floorBeforeElevation,
      elevationChanged: floorBeforeElevation == null || Math.abs(floorBeforeElevation - record.elevation) > EPSILON,
      replaceExisting: kind === 'replace',
      preserveExisting: kind !== 'replace',
      regions: effectiveRegions.map(planRegion),
      regionIds,
      skippedRegions,
    };
    operations.push(operation);
    match.operationKind = kind;
    match.skippedRegions = skippedRegions;
  }

  return {
    sourceFingerprint,
    matches,
    operations,
    skippedExisting,
    skippedNoRegion,
    skippedCompetition,
    created,
  };
}

export function applyAutoApplyPlan(draft, plan) {
  if (!draft || typeof draft !== 'object') throw new Error('缺少浏览器草稿');
  if (!plan || typeof plan !== 'object' || !Array.isArray(plan.operations) || !Array.isArray(plan.matches)) {
    throw new Error('自动应用计划无效');
  }
  if (plan.sourceFingerprint !== JSON.stringify(draft)) {
    throw new Error('生成自动应用计划后草稿已变化，请重新分析匹配');
  }

  const staged = clone(draft);
  if (!Array.isArray(staged.floors)) throw new Error('草稿缺少楼层数组');
  staged.doors = Array.isArray(staged.doors) ? staged.doors : [];
  staged.exits = Array.isArray(staged.exits) ? staged.exits : [];
  staged.stairs = Array.isArray(staged.stairs) ? staged.stairs : [];
  let addedFloors = 0;
  let addedRegions = 0;
  let updatedFloors = 0;
  let replacedFloors = 0;

  for (const operation of plan.operations) {
    const elevation = finiteNumber(operation?.elevation);
    if (elevation == null || Math.abs(elevation) > 1000) throw new Error('候选 ' + (operation?.candidateId || '') + ' 的标高无效');
    let floor;
    if (operation.kind === 'create') {
      const targetId = String(operation.targetId ?? '').trim();
      if (!targetId) throw new Error('新楼层缺少 ID');
      if (staged.floors.some((item) => floorIdOf(item) === targetId)) throw new Error('新楼层 ID ' + targetId + ' 已存在');
      floor = { id: targetId, name: String(operation.targetName || targetId), elevation_m: elevation, walkable: [], spawn_zones: [] };
      staged.floors.push(floor);
      addedFloors += 1;
    } else if (operation.kind === 'update' || operation.kind === 'replace') {
      const targetId = String(operation.targetId ?? '').trim();
      floor = staged.floors.find((item) => floorIdOf(item) === targetId);
      if (!floor) throw new Error('未找到目标楼层 ' + targetId);
      if (operation.kind === 'replace') {
        floor.walkable = [];
        floor.spawn_zones = [];
        staged.doors = staged.doors.filter((door) => String(door?.floor ?? '') !== targetId);
        staged.exits = staged.exits.filter((exit) => String(exit?.floor ?? '') !== targetId);
        staged.stairs = staged.stairs.filter((stair) => String(stair?.from_floor ?? '') !== targetId && String(stair?.to_floor ?? '') !== targetId);
        replacedFloors += 1;
      }
      const oldElevation = finiteNumber(floor.elevation_m);
      if (Math.abs(oldElevation - elevation) > EPSILON) {
        floor.elevation_m = elevation;
        syncFloorDependentHeights(
          staged,
          targetId,
          oldElevation,
          elevation,
          (floor.name || targetId) + ' 的模型候选楼面标高为 ' + elevation.toFixed(2) + ' m，原标高为 ' + oldElevation.toFixed(2) + ' m；请对照模型复核现有标注。',
        );
        updatedFloors += 1;
      }
    } else {
      throw new Error('未知自动应用操作 ' + String(operation.kind));
    }

    floor.walkable = Array.isArray(floor.walkable) ? floor.walkable : [];
    floor.spawn_zones = Array.isArray(floor.spawn_zones) ? floor.spawn_zones : [];
    const regions = Array.isArray(operation.regions) ? operation.regions : [];
    const regionIds = Array.isArray(operation.regionIds) ? operation.regionIds : [];
    if (regions.length !== regionIds.length) throw new Error('楼层 ' + floor.id + ' 的自动区域 ID 数量不一致');
    for (let index = 0; index < regions.length; index += 1) {
      const regionId = String(regionIds[index] ?? '').trim();
      if (!regionId) throw new Error('楼层 ' + floor.id + ' 包含缺失的自动区域 ID');
      const normalized = normalizeAutoRegion(regions[index], regionId, '区域 ' + regionId);
      const duplicateGeometry = floor.walkable.some((existing) => JSON.stringify(existing?.outer) === JSON.stringify(normalized.outer));
      if (duplicateGeometry) continue;
      floor.walkable.push(normalized);
      addedRegions += 1;
    }
  }

  staged.floors.sort((left, right) => Number(left.elevation_m) - Number(right.elevation_m));
  validateDraftStructure(staged);
  const changed = JSON.stringify(staged) !== plan.sourceFingerprint;
  if (changed && plan.operations.some((operation) => operation.kind === 'replace')) {
    applyAnnotationInvalidation(staged, '自动候选替换了部分楼层标注，请逐层复核门、出口、起始区与楼梯连接。');
  }
  return {
    draft: staged,
    addedFloors,
    addedRegions,
    updatedFloors,
    replacedFloors,
    skippedExisting: Number(plan.skippedExisting) || 0,
    skippedNoRegion: Number(plan.skippedNoRegion) || 0,
    skippedCompetition: Number(plan.skippedCompetition) || 0,
    created: Number(plan.created) || addedFloors,
    emptyFloorIds: listEmptyWalkableFloors(staged),
    changed,
  };
}

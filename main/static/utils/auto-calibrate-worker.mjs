const DEFAULTS = {
  horizontalAngleDeg: 15,
  clusterTolerance: 0.06,
  heightBinSize: 0.02,
  minFloorArea: 8,
  minFloorAreaRatio: 0.1,
  minSurfaceArea: 0.5,
  minRegionArea: 2,
  // Ignore sub-cell material seams and raster noise. Keep this separate from
  // the region floor threshold so a real atrium or stairwell remains a hole.
  minHoleArea: 0.5,
  minWalkableWidth: 0.9,
  minStructureGap: 0.08,
  maxStructureGap: 0.18,
  minStructureOverlap: 0.7,
  aboveCoverageMin: 0.7,
  aboveCoverageMax: 2.2,
  minAboveCoverage: 0.5,
  minStoryClearance: 0.9,
  maxCoincidentFaceGap: 0.02,
  minCoincidentFaceArea: 10,
  maxCoincidentFaceAreaRatio: 1.35,
  rasterCellsAcross: 1200,
  minRasterCellSize: 0.05,
  minObstacleHeight: 0.12,
  minPersonDiameter: 0.3,
  obstacleLow: 0.1,
  obstacleHigh: 0.8,
  simplifyTolerance: 0.03,
};

const cancelled = new Set();

function optionNumber(options, key) {
  const value = Number(options[key]);
  return Number.isFinite(value) && value >= 0 ? value : DEFAULTS[key];
}

function emitProgress(requestId, stage, value, message) {
  if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
    self.postMessage({ type: 'progress', requestId, stage, progress: value, message });
  }
}

const isCancelled = (requestId) => cancelled.has(requestId);

function scanUpwardTriangles(positions, indices, options, requestId, semantics = null) {
  const triangleCount = Math.floor(indices.length / 3);
  const angle = optionNumber(options, 'horizontalAngleDeg') * Math.PI / 180;
  const minimumNormalY = Math.cos(angle);
  const binSize = Math.max(0.001, optionNumber(options, 'heightBinSize'));
  const bins = new Map();
  const triangleMaterialIds = semantics?.triangleMaterialIds || null;
  const materialNames = Array.isArray(semantics?.materialNames) ? semantics.materialNames : [];
  let upwardCount = 0;
  let downwardCount = 0;
  let structuralCount = 0;
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;

  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    if (isCancelled(requestId)) return null;
    if (triangle % 50000 === 0) {
      const progress = triangleCount ? triangle / triangleCount * 0.34 : 0.34;
      emitProgress(requestId, 'horizontal-surfaces', progress, `正在筛选水平结构面 ${triangle.toLocaleString()} / ${triangleCount.toLocaleString()}`);
    }

    const ia = Number(indices[triangle * 3]) * 3;
    const ib = Number(indices[triangle * 3 + 1]) * 3;
    const ic = Number(indices[triangle * 3 + 2]) * 3;
    if (!Number.isInteger(ia) || !Number.isInteger(ib) || !Number.isInteger(ic) || ia < 0 || ib < 0 || ic < 0
      || ia + 2 >= positions.length || ib + 2 >= positions.length || ic + 2 >= positions.length) continue;

    const ax = positions[ia], ay = positions[ia + 1], az = positions[ia + 2];
    const bx = positions[ib], by = positions[ib + 1], bz = positions[ib + 2];
    const cx = positions[ic], cy = positions[ic + 1], cz = positions[ic + 2];
    if (![ax, ay, az, bx, by, bz, cx, cy, cz].every(Number.isFinite)) continue;
    const abx = bx - ax, aby = by - ay, abz = bz - az;
    const acx = cx - ax, acy = cy - ay, acz = cz - az;
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    const y = (ay + by + cy) / 3;
    const normalLength = Math.hypot(nx, ny, nz);
    if (normalLength <= 1e-10) continue;
    const normalY = Math.abs(ny) / normalLength;
    if (normalY < minimumNormalY) continue;

    // Both slab tops and undersides are useful structural evidence. Only a
    // face whose original winding points up may become a floor candidate;
    // downward faces can still confirm a slab during top/bottom pairing.
    const projectedArea = Math.abs(ny) * 0.5;
    if (projectedArea <= 1e-10) continue;
    const isUpward = ny > 0;
    const materialId = triangleMaterialIds ? Number(triangleMaterialIds[triangle]) : -1;
    const materialKey = Number.isInteger(materialId) && materialId >= 0 ? String(materialId) : '';
    const key = Math.floor(y / binSize + 1e-7);
    let bin = bins.get(key);
    if (!bin) {
      bin = {
        key, count: 0, area: 0, weightedY: 0, triangleIds: [],
        upwardCount: 0, downwardCount: 0,
        upwardArea: 0, downwardArea: 0,
        upwardWeightedY: 0, downwardWeightedY: 0,
        upwardMinY: Infinity, upwardMaxY: -Infinity,
        downwardMinY: Infinity, downwardMaxY: -Infinity,
        upwardMaterialArea: Object.create(null),
      };
      bins.set(key, bin);
    }
    bin.count += 1;
    bin.area += projectedArea;
    bin.weightedY += y * projectedArea;
    bin.triangleIds.push(triangle);
    const triangleMinY = Math.min(ay, by, cy);
    const triangleMaxY = Math.max(ay, by, cy);
    if (isUpward) {
      bin.upwardCount += 1;
      bin.upwardArea += projectedArea;
      bin.upwardWeightedY += y * projectedArea;
      bin.upwardMinY = Math.min(bin.upwardMinY, triangleMinY);
      bin.upwardMaxY = Math.max(bin.upwardMaxY, triangleMaxY);
      if (materialKey) bin.upwardMaterialArea[materialKey] = (bin.upwardMaterialArea[materialKey] || 0) + projectedArea;
      upwardCount += 1;
    } else {
      bin.downwardCount += 1;
      bin.downwardArea += projectedArea;
      bin.downwardWeightedY += y * projectedArea;
      bin.downwardMinY = Math.min(bin.downwardMinY, triangleMinY);
      bin.downwardMaxY = Math.max(bin.downwardMaxY, triangleMaxY);
      downwardCount += 1;
    }
    structuralCount += 1;

    minX = Math.min(minX, ax, bx, cx);
    maxX = Math.max(maxX, ax, bx, cx);
    minZ = Math.min(minZ, az, bz, cz);
    maxZ = Math.max(maxZ, az, bz, cz);
  }

  return {
    triangleCount,
    upwardCount,
    downwardCount,
    structuralCount,
    bins,
    bounds: structuralCount ? { minX, maxX, minZ, maxZ } : null,
  };
}

function clusterHeightLayers(bins, options) {
  const tolerance = Math.max(0.001, optionNumber(options, 'clusterTolerance'));
  const sorted = [...bins.values()]
    .map((bin) => ({
      ...bin,
      y: bin.upwardArea > 0
        ? bin.upwardWeightedY / Math.max(bin.upwardArea, 1e-12)
        : bin.weightedY / Math.max(bin.area, 1e-12),
    }))
    .sort((a, b) => a.y - b.y);
  const layers = [];

  for (const bin of sorted) {
    const previous = layers[layers.length - 1];
    const canMerge = previous
      && bin.y - previous.lastY <= tolerance
      && bin.y - previous.firstY <= tolerance * 2;
    if (!canMerge) {
      layers.push({
        firstY: bin.y,
        lastY: bin.y,
        weightedY: bin.weightedY,
        area: bin.area,
        count: bin.count,
        upwardCount: bin.upwardCount || 0,
        downwardCount: bin.downwardCount || 0,
        upwardArea: bin.upwardArea || 0,
        downwardArea: bin.downwardArea || 0,
        upwardWeightedY: bin.upwardWeightedY || 0,
        downwardWeightedY: bin.downwardWeightedY || 0,
        upwardMinY: bin.upwardMinY,
        upwardMaxY: bin.upwardMaxY,
        downwardMinY: bin.downwardMinY,
        downwardMaxY: bin.downwardMaxY,
        upwardMaterialArea: { ...bin.upwardMaterialArea },
        triangleIds: bin.triangleIds.slice(),
      });
      continue;
    }
    previous.lastY = bin.y;
    previous.weightedY += bin.weightedY;
    previous.area += bin.area;
    previous.count += bin.count;
    previous.upwardCount = (previous.upwardCount || 0) + (bin.upwardCount || 0);
    previous.downwardCount = (previous.downwardCount || 0) + (bin.downwardCount || 0);
    previous.upwardArea = (previous.upwardArea || 0) + (bin.upwardArea || 0);
    previous.downwardArea = (previous.downwardArea || 0) + (bin.downwardArea || 0);
    previous.upwardWeightedY = (previous.upwardWeightedY || 0) + (bin.upwardWeightedY || 0);
    previous.downwardWeightedY = (previous.downwardWeightedY || 0) + (bin.downwardWeightedY || 0);
    previous.upwardMinY = Math.min(previous.upwardMinY ?? Infinity, bin.upwardMinY ?? Infinity);
    previous.upwardMaxY = Math.max(previous.upwardMaxY ?? -Infinity, bin.upwardMaxY ?? -Infinity);
    previous.downwardMinY = Math.min(previous.downwardMinY ?? Infinity, bin.downwardMinY ?? Infinity);
    previous.downwardMaxY = Math.max(previous.downwardMaxY ?? -Infinity, bin.downwardMaxY ?? -Infinity);
    const previousMaterials = previous.upwardMaterialArea || (previous.upwardMaterialArea = Object.create(null));
    for (const [materialId, area] of Object.entries(bin.upwardMaterialArea || {})) {
      previousMaterials[materialId] = (previousMaterials[materialId] || 0) + Number(area || 0);
    }
    previous.triangleIds = previous.triangleIds.concat(bin.triangleIds);
  }

  return layers.map((layer) => ({
    ...layer,
    y: layer.upwardArea > 0
      ? layer.upwardWeightedY / Math.max(layer.upwardArea, 1e-12)
      : layer.weightedY / Math.max(layer.area, 1e-12),
  }));
}

function createGrid(bounds, options) {
  const cellsAcross = Math.max(1, Math.floor(optionNumber(options, 'rasterCellsAcross')));
  const minimumCell = Math.max(0.001, optionNumber(options, 'minRasterCellSize'));
  const span = Math.max(bounds.maxX - bounds.minX, bounds.maxZ - bounds.minZ);
  const cellSize = Math.max(minimumCell, span / cellsAcross);
  // Snap all cell edges to a common world-space lattice. Neighbouring layers
  // then share exactly the same occupancy cells, so structural pairing does
  // not depend on the position of each layer's bounding box.
  const originX = Math.floor(bounds.minX / cellSize) * cellSize - cellSize;
  const originZ = Math.floor(bounds.minZ / cellSize) * cellSize - cellSize;
  const width = Math.max(2, Math.ceil((bounds.maxX - originX) / cellSize) + 1);
  const height = Math.max(2, Math.ceil((bounds.maxZ - originZ) / cellSize) + 1);
  return { cellSize, originX, originZ, width, height, total: width * height };
}

function pointInTriangle2D(x, z, ax, az, bx, bz, cx, cz) {
  const ab = (bx - ax) * (z - az) - (bz - az) * (x - ax);
  const bc = (cx - bx) * (z - bz) - (cz - bz) * (x - bx);
  const ca = (ax - cx) * (z - cz) - (az - cz) * (x - cx);
  const epsilon = 1e-9;
  const hasNegative = ab < -epsilon || bc < -epsilon || ca < -epsilon;
  const hasPositive = ab > epsilon || bc > epsilon || ca > epsilon;
  return !(hasNegative && hasPositive);
}

function rasterizeLayer(layer, positions, indices, grid) {
  const { cellSize, originX, originZ, width, height, total } = grid;
  const mask = new Uint8Array(total);
  for (const triangle of layer.triangleIds) {
    const ia = Number(indices[triangle * 3]) * 3;
    const ib = Number(indices[triangle * 3 + 1]) * 3;
    const ic = Number(indices[triangle * 3 + 2]) * 3;
    const ax = positions[ia], az = positions[ia + 2];
    const bx = positions[ib], bz = positions[ib + 2];
    const cx = positions[ic], cz = positions[ic + 2];
    const minTriangleX = Math.min(ax, bx, cx);
    const maxTriangleX = Math.max(ax, bx, cx);
    const minTriangleZ = Math.min(az, bz, cz);
    const maxTriangleZ = Math.max(az, bz, cz);
    const colStart = Math.max(0, Math.floor((minTriangleX - originX) / cellSize));
    const colEnd = Math.min(width - 1, Math.ceil((maxTriangleX - originX) / cellSize));
    const rowStart = Math.max(0, Math.floor((minTriangleZ - originZ) / cellSize));
    const rowEnd = Math.min(height - 1, Math.ceil((maxTriangleZ - originZ) / cellSize));
    for (let row = rowStart; row <= rowEnd; row += 1) {
      const z = originZ + (row + 0.5) * cellSize;
      const rowOffset = row * width;
      for (let col = colStart; col <= colEnd; col += 1) {
        const index = rowOffset + col;
        if (mask[index]) continue;
        const x = originX + (col + 0.5) * cellSize;
        if (pointInTriangle2D(x, z, ax, az, bx, bz, cx, cz)) mask[index] = 1;
      }
    }
  }
  let occupiedCells = 0;
  for (let index = 0; index < mask.length; index += 1) occupiedCells += mask[index];
  return { ...layer, mask, occupiedCells, rasterArea: occupiedCells * cellSize * cellSize };
}

function overlapRatio(upper, lower, grid) {
  let overlap = 0;
  let upperCells = 0;
  for (let index = 0; index < upper.mask.length; index += 1) {
    if (!upper.mask[index]) continue;
    upperCells += 1;
    if (lower.mask[index]) overlap += 1;
  }
  return upperCells ? overlap / upperCells : 0;
}

function aboveCoverage(layer, layers, options, grid) {
  const low = layer.y + optionNumber(options, 'aboveCoverageMin');
  const high = layer.y + optionNumber(options, 'aboveCoverageMax');
  const covered = new Uint8Array(grid.total);
  for (const candidate of layers) {
    if (candidate === layer || candidate.y < low || candidate.y > high) continue;
    for (let index = 0; index < covered.length; index += 1) {
      if (candidate.mask[index]) covered[index] = 1;
    }
  }
  let layerCells = 0;
  let coveredCells = 0;
  for (let index = 0; index < layer.mask.length; index += 1) {
    if (!layer.mask[index]) continue;
    layerCells += 1;
    if (covered[index]) coveredCells += 1;
  }
  return layerCells ? coveredCells / layerCells : 0;
}

function coincidentFaceEvidence(layer, options) {
  const upwardArea = Number(layer.upwardArea || 0);
  const downwardArea = Number(layer.downwardArea || 0);
  const minimumArea = optionNumber(options, 'minCoincidentFaceArea');
  if (upwardArea < minimumArea || downwardArea < minimumArea) return null;
  const areaRatio = Math.max(upwardArea, downwardArea) / Math.max(1e-9, Math.min(upwardArea, downwardArea));
  if (areaRatio > optionNumber(options, 'maxCoincidentFaceAreaRatio')) return null;
  const upwardY = Number(layer.upwardWeightedY || 0) / upwardArea;
  const downwardY = Number(layer.downwardWeightedY || 0) / downwardArea;
  const separation = Math.abs(upwardY - downwardY);
  if (!Number.isFinite(separation) || separation > optionNumber(options, 'maxCoincidentFaceGap')) return null;
  return {
    upwardArea,
    downwardArea,
    upwardY,
    downwardY,
    separation,
    areaRatio,
  };
}

function materialAreaBreakdown(layer, materialNames = []) {
  const materialArea = layer?.upwardMaterialArea || {};
  const total = Number(layer?.upwardArea || 0);
  return Object.entries(materialArea)
    .map(([id, area]) => {
      const numericId = Number(id);
      const value = Number(area) || 0;
      return {
        id: numericId,
        name: materialNames[numericId] || ('材质 ' + (numericId + 1)),
        area: value,
        share: total > 1e-9 ? value / total : 0,
      };
    })
    .filter((item) => item.area > 0)
    .sort((a, b) => b.area - a.area);
}

function roofMaterialEvidence(highest, lowerLayers, options, materialNames = []) {
  const materials = materialAreaBreakdown(highest, materialNames);
  if (!materials.length) return null;
  const highestArea = Number(highest?.upwardArea || 0);
  if (highestArea < optionNumber(options, 'minCoincidentFaceArea')) return null;
  const minimumShare = 0.35;
  const dominant = materials.filter((item) => item.share >= minimumShare && item.area >= optionNumber(options, 'minCoincidentFaceArea'));
  if (!dominant.length) return null;
  const lowerMaterialTotals = new Map();
  let lowerArea = 0;
  for (const layer of lowerLayers) {
    lowerArea += Number(layer?.upwardArea || 0);
    for (const [id, area] of Object.entries(layer?.upwardMaterialArea || {})) {
      lowerMaterialTotals.set(Number(id), (lowerMaterialTotals.get(Number(id)) || 0) + Number(area || 0));
    }
  }
  if (lowerArea <= 1e-9) return null;
  for (const item of dominant) {
    const lowerShare = (lowerMaterialTotals.get(item.id) || 0) / lowerArea;
    // A roof membrane / terrace finish is typically exclusive at the top:
    // the same material is either absent or only a minor trim below. Ordinary
    // floor finishes repeat on every storey and must never trigger exclusion,
    // even when the top slab is thin or partly double-sided.
    if (lowerShare > 0.12) continue;
    return {
      materialId: item.id,
      materialName: item.name,
      materialArea: Number(item.area.toFixed(2)),
      materialShare: Number(item.share.toFixed(3)),
      lowerShare: Number(lowerShare.toFixed(3)),
    };
  }
  return null;
}

function selectFloorLayers(layers, options, grid, semantics = null) {
  const substantial = layers
    .filter((layer) => layer.rasterArea >= optionNumber(options, 'minSurfaceArea'))
    .sort((a, b) => a.y - b.y);
  const candidates = substantial.filter((layer) => (layer.upwardCount || 0) > 0);
  const minimumGap = optionNumber(options, 'minStructureGap');
  const maximumGap = optionNumber(options, 'maxStructureGap');
  const minimumOverlap = optionNumber(options, 'minStructureOverlap');
  const minimumClearance = optionNumber(options, 'minStoryClearance');

  // Pair exposed slab faces one-to-one. A single wide floor must not be
  // reused as the underside of several furniture tops or low platforms.
  const pairs = [];
  for (let lowerIndex = 0; lowerIndex < substantial.length; lowerIndex += 1) {
    const lower = substantial[lowerIndex];
    for (let upperIndex = lowerIndex + 1; upperIndex < substantial.length; upperIndex += 1) {
      const upper = substantial[upperIndex];
      const gap = upper.y - lower.y;
      if (gap < minimumGap) continue;
      if (gap > maximumGap) break;
      const upperOverlap = overlapRatio(upper, lower, grid);
      const lowerOverlap = overlapRatio(lower, upper, grid);
      const overlap = Math.min(upperOverlap, lowerOverlap);
      if (overlap < minimumOverlap) continue;
      const areaRatio = Math.max(upper.rasterArea, lower.rasterArea) / Math.max(1e-9, Math.min(upper.rasterArea, lower.rasterArea));
      if (areaRatio > 2.2) continue;
      // The lower face may be a slab underside or the upper face may be the
      // top of a thin slab. Require at least one genuine upward surface.
      if (!(lower.upwardCount > 0) && !(upper.upwardCount > 0)) continue;
      pairs.push({ lower, upper, overlap, gap, areaRatio });
    }
  }
  pairs.sort((a, b) => b.overlap - a.overlap || a.gap - b.gap);

  const lowerToUpper = new Map();
  const upperToLower = new Map();
  const assignedFaces = new Set();
  for (const pair of pairs) {
    if (assignedFaces.has(pair.lower) || assignedFaces.has(pair.upper)) continue;
    lowerToUpper.set(pair.lower, pair.upper);
    upperToLower.set(pair.upper, pair.lower);
    assignedFaces.add(pair.lower);
    assignedFaces.add(pair.upper);
  }

  // Structural slab tops are the strongest floor evidence. Furniture and
  // decorative horizontal surfaces may be lower than the next real floor, so
  // selecting every 0.9m-separated surface can make the algorithm choose a
  // desk or a ceiling trim instead of the floor above it.
  // A structural top is either the upward face of a paired slab top or the
  // highest exposed floor in a model whose slab undersides were paired. In the
  // latter case the roof support above it may be absent from the mesh.
  const structuralTops = candidates
    .filter((layer) => upperToLower.has(layer) || lowerToUpper.has(layer))
    .sort((a, b) => a.y - b.y);
  const hasStructuralTops = structuralTops.length > 0;
  let ordered;
  if (hasStructuralTops) {
    // Keep the lowest unpaired surface below the first slab top as a possible
    // ground floor, but do not promote the many unpaired furniture surfaces
    // between floors. A paired underside is never a floor.
    const firstStructuralY = structuralTops[0].y;
    const groundLike = candidates
      .filter((layer) => !lowerToUpper.has(layer)
        && !upperToLower.has(layer)
        && layer.y < firstStructuralY)
      .sort((a, b) => a.y - b.y);
    ordered = (groundLike.length ? [groundLike[0]] : []).concat(structuralTops)
      .sort((a, b) => a.y - b.y);
  } else {
    // Small or synthetic models often contain only exposed floor planes. The
    // original clearance-based fallback remains appropriate when no slab
    // underside can be paired.
    ordered = candidates;
  }

  const coverage = new Map();
  for (const layer of ordered) {
    coverage.set(layer, aboveCoverage(layer, substantial, options, grid));
  }

  // Explicit caller requests are distinct from model-size inference. An
  // automatic rule requires mutually overlapping structural slab pairs below
  // the highest surface. Two unrelated platforms in a small test mesh must not
  // turn the only valid upper floor into a roof.
  const roofRemovalExplicit = optionNumber(options, 'roofRemovalExplicit') > 0;
  const removeRoofRequested = optionNumber(options, 'removeRoof') > 0;
  const explicitRoofRemoval = roofRemovalExplicit && removeRoofRequested;
  const explicitRoofRetention = roofRemovalExplicit && !removeRoofRequested;
  const materialNames = Array.isArray(semantics?.materialNames) ? semantics.materialNames : [];
  const inferableSlabTops = structuralTops.filter((layer) => upperToLower.has(layer));
  // Roof evidence is a pair of consecutive storey slabs. Their horizontal
  // coverage must overlap; scattered platforms in synthetic tests do not form
  // a building and must never trigger automatic removal.
  const inferableRoofPairs = [];
  for (let index = 1; index < inferableSlabTops.length; index += 1) {
    const lower = inferableSlabTops[index - 1];
    const upper = inferableSlabTops[index];
    if (upper.y - lower.y >= minimumClearance
      && overlapRatio(upper, lower, grid) >= minimumOverlap
      && overlapRatio(lower, upper, grid) >= minimumOverlap) {
      inferableRoofPairs.push({ lower, upper });
    }
  }
  const reviewOnlyLayers = [];
  let excludedFloors = [];
  let excludedLayers = [];
  const highestStructural = structuralTops.length
    ? structuralTops[structuralTops.length - 1]
    : ordered[ordered.length - 1];
  // A highest storey whose upward face has a matching slab underside is
  // structurally complete. Treating it as a roof would silently delete the
  // real top floor. Automatic inference therefore requires roof evidence and
  // an unpaired highest surface. Explicit removal remains available for the
  // administrator when model metadata or visual review proves it is a roof.
  const highestIsPairedTop = upperToLower.has(highestStructural);
  // A thin, coincident upward/downward skin is common in exported roof meshes.
  // It is only an audit clue: exported buildings also contain genuinely thin
  // top-storey slabs, so a 1-2 cm double face must never remove a real floor
  // by itself. Automatic exclusion additionally requires a dominant top-only
  // finish material and complete storeys below.
  const coincidentFace = highestStructural
    ? coincidentFaceEvidence(highestStructural, options)
    : null;
  const lowerStructural = structuralTops.filter((layer) => layer !== highestStructural && layer.y < Number(highestStructural?.y || Infinity));
  const topMaterialEvidence = highestStructural
    ? roofMaterialEvidence(highestStructural, lowerStructural, options, materialNames)
    : null;
  const canInferRoof = !explicitRoofRetention
    && inferableRoofPairs.length > 0
    && Boolean(coincidentFace)
    && Boolean(topMaterialEvidence)
    && (!highestIsPairedTop || Boolean(coincidentFace));
  // A coincident top face is enough to request review, even when the export
  // omitted slab undersides between storeys. Review never removes a floor.
  const roofReviewWithoutMaterial = !explicitRoofRemoval
    && Boolean(coincidentFace)
    && !topMaterialEvidence;
  const canRemoveHighest = explicitRoofRemoval ? ordered.length >= 2 : canInferRoof;
  if (roofReviewWithoutMaterial && highestStructural) {
    // The selected layer is a copy, so keep review metadata on an explicit
    // side channel and map it back when serializing floor warnings.
    reviewOnlyLayers.push({
      y: highestStructural.y,
      roofReview: '无独立屋面材料证据：默认保留为普通楼层，请人工确认它是顶层还是屋面。',
    });
  }
  if (canRemoveHighest && highestStructural) {
    const highestCoverage = Number(coverage.get(highestStructural) || 0);
    if (explicitRoofRemoval || highestCoverage < optionNumber(options, 'minAboveCoverage')) {
      ordered = ordered.filter((layer) => layer !== highestStructural);
      excludedLayers = [highestStructural];
      excludedFloors = [{
        id: 'auto-excluded-1',
        name: '屋面 / 露台疑似',
        kind: 'roof_or_terrace',
        status: 'excluded',
        elevation_m: Number(highestStructural.y.toFixed(4)),
        area_m2: Number(highestStructural.rasterArea.toFixed(2)),
        aboveCoverage: Number(highestCoverage.toFixed(3)),
        above_coverage: Number(highestCoverage.toFixed(3)),
        triangleCount: highestStructural.count,
        confidence: 'low',
        roofEvidence: explicitRoofRemoval
          ? 'explicit_request'
          : topMaterialEvidence ? 'top_only_material' : coincidentFace ? 'coincident_double_face' : 'insufficient_above_coverage',
        upwardArea_m2: coincidentFace ? Number(coincidentFace.upwardArea.toFixed(2)) : null,
        downwardArea_m2: coincidentFace ? Number(coincidentFace.downwardArea.toFixed(2)) : null,
        faceSeparation_m: coincidentFace ? Number(coincidentFace.separation.toFixed(4)) : null,
        materialEvidence: topMaterialEvidence,
        materialName: topMaterialEvidence?.materialName || null,
        materialShare: topMaterialEvidence?.materialShare ?? null,
        materialLowerShare: topMaterialEvidence?.lowerShare ?? null,
        reason: explicitRoofRemoval
          ? '管理员显式请求将该屋面 / 露台移入审计区；确认前不会直接写入草稿'
          : topMaterialEvidence
            ? '最高层具有仅在顶层出现的主要饰面材料，且上方未形成完整楼层'
            : '最高结构板顶面及以上方覆盖不足，可能是屋面或开放露台',
        regions: null,
        warnings: [
          '请人工确认该平面是屋面、露台或真实楼层；确认前不会写入草稿。',
          ...(topMaterialEvidence ? ['顶面独有材料：' + topMaterialEvidence.materialName + '（' + Math.round(topMaterialEvidence.materialShare * 100) + '% 顶面面积，下方仅 ' + Math.round(topMaterialEvidence.lowerShare * 100) + '%）。'] : []),
        ],
      }];
    }
  }
  const selected = [];
  const largestCandidateArea = Math.max(0, ...candidates.map((layer) => layer.rasterArea));
  for (const layer of ordered) {
    if (lowerToUpper.has(layer)) continue;
    const layerCoverage = coverage.get(layer) || 0;
    const previous = selected[selected.length - 1];
    const clearance = previous ? layer.y - previous.y : Infinity;
    if (Number.isFinite(clearance) && clearance < minimumClearance) continue;
    const structureMatch = upperToLower.has(layer);
    selected.push({
      ...layer,
      aboveCoverage: layerCoverage,
      structureMatch,
      clearance,
      platformSuspected: !structureMatch && selected.length > 0 && layer.rasterArea < largestCandidateArea * 0.7,
    });
  }
  return { selected, lowerToUpper, upperToLower, excludedFloors, excludedLayers, reviewOnlyLayers };
}
function labelComponents(mask, width, height) {
  const labels = new Int32Array(mask.length);
  labels.fill(-1);
  const queue = new Int32Array(mask.length);
  const components = [];
  const neighbors = [0, 0, 0, 0];
  for (let start = 0; start < mask.length; start += 1) {
    if (!mask[start] || labels[start] !== -1) continue;
    const label = components.length;
    const cells = [];
    let head = 0;
    let tail = 0;
    queue[tail] = start;
    tail += 1;
    labels[start] = label;
    while (head < tail) {
      const index = queue[head];
      head += 1;
      cells.push(index);
      const row = Math.floor(index / width);
      const col = index - row * width;
      let count = 0;
      if (row > 0) neighbors[count++] = index - width;
      if (row + 1 < height) neighbors[count++] = index + width;
      if (col > 0) neighbors[count++] = index - 1;
      if (col + 1 < width) neighbors[count++] = index + 1;
      for (let neighborIndex = 0; neighborIndex < count; neighborIndex += 1) {
        const next = neighbors[neighborIndex];
        if (!mask[next] || labels[next] !== -1) continue;
        labels[next] = label;
        queue[tail] = next;
        tail += 1;
      }
    }
    components.push({ label, cells });
  }
  return { labels, components };
}

function simplifyClosedRing(ring, tolerance) {
  if (ring.length <= 4) return ring;
  const points = ring.slice(0, -1);
  const simplified = [];
  for (let index = 0; index < points.length; index += 1) {
    const previous = points[(index + points.length - 1) % points.length];
    const current = points[index];
    const next = points[(index + 1) % points.length];
    const cross = (current[0] - previous[0]) * (next[1] - current[1]) - (current[1] - previous[1]) * (next[0] - current[0]);
    const sameDirection = Math.abs(cross) < 1e-10;
    const tooShort = Math.hypot(current[0] - previous[0], current[1] - previous[1]) < tolerance;
    if (!sameDirection && !tooShort) simplified.push(current);
  }
  if (simplified.length < 3) return ring;
  simplified.push([simplified[0][0], simplified[0][1]]);
  return simplified;
}
function squaredDistanceTransform(source, width, height) {
  const outerWidth = width + 2;
  const outerHeight = height + 2;
  const total = outerWidth * outerHeight;
  const field = new Float64Array(total);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      field[(row + 1) * outerWidth + col + 1] = source[row * width + col] ? 1e20 : 0;
    }
  }
  const line = new Float64Array(Math.max(outerWidth, outerHeight));
  const sites = new Int32Array(line.length);
  const intersections = new Float64Array(line.length + 1);
  const distances = new Float64Array(line.length);

  // Exact squared Euclidean distance transform (Felzenszwalb-Huttenlocher).
  // Infinity cannot be used as a source value: the parabola envelope keeps
  // only the nearest source, and a finite large sentinel makes that selection
  // deterministic. The old infinity path saturated every free cell to the
  // same maximum, so neck measurements always reported the room length.
  const transformLine = (values, length) => {
    let envelope = 0;
    sites[0] = 0;
    intersections[0] = -Infinity;
    intersections[1] = Infinity;
    for (let q = 1; q < length; q += 1) {
      let boundary = ((values[q] + q * q) - (values[sites[envelope]] + sites[envelope] * sites[envelope])) / (2 * q - 2 * sites[envelope]);
      while (boundary <= intersections[envelope]) {
        envelope -= 1;
        boundary = ((values[q] + q * q) - (values[sites[envelope]] + sites[envelope] * sites[envelope])) / (2 * q - 2 * sites[envelope]);
      }
      envelope += 1;
      sites[envelope] = q;
      intersections[envelope] = boundary;
      intersections[envelope + 1] = Infinity;
    }
    envelope = 0;
    for (let q = 0; q < length; q += 1) {
      while (intersections[envelope + 1] < q) envelope += 1;
      const delta = q - sites[envelope];
      distances[q] = delta * delta + values[sites[envelope]];
    }
    for (let q = 0; q < length; q += 1) values[q] = distances[q];
  };

  for (let row = 0; row < outerHeight; row += 1) {
    const offset = row * outerWidth;
    for (let col = 0; col < outerWidth; col += 1) line[col] = field[offset + col];
    transformLine(line, outerWidth);
    for (let col = 0; col < outerWidth; col += 1) field[offset + col] = line[col];
  }
  for (let col = 0; col < outerWidth; col += 1) {
    for (let row = 0; row < outerHeight; row += 1) line[row] = field[row * outerWidth + col];
    transformLine(line, outerHeight);
    for (let row = 0; row < outerHeight; row += 1) field[row * outerWidth + col] = line[row];
  }

  const result = new Float64Array(width * height);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      result[row * width + col] = Math.sqrt(field[(row + 1) * outerWidth + col + 1]);
    }
  }
  return result;
}

function pushHeap(heap, cell, capacity) {
  let index = heap.length;
  heap.push({ cell, capacity });
  while (index > 0) {
    const parent = (index - 1) >> 1;
    if (heap[parent].capacity >= heap[index].capacity) break;
    [heap[parent], heap[index]] = [heap[index], heap[parent]];
    index = parent;
  }
}

function popHeap(heap) {
  if (!heap.length) return null;
  const top = heap[0];
  const tail = heap.pop();
  if (!heap.length) return top;
  heap[0] = tail;
  let index = 0;
  while (true) {
    const left = index * 2 + 1;
    if (left >= heap.length) break;
    const right = left + 1;
    const child = right < heap.length && heap[right].capacity > heap[left].capacity ? right : left;
    if (heap[index].capacity >= heap[child].capacity) break;
    [heap[index], heap[child]] = [heap[child], heap[index]];
    index = child;
  }
  return top;
}

function widestPathWidth(mask, width, height, distanceMap, cellSize) {
  const cells = [];
  let maximumDistance = 0;
  for (let index = 0; index < mask.length; index += 1) {
    if (!mask[index]) continue;
    cells.push(index);
    if (distanceMap[index] > maximumDistance) maximumDistance = distanceMap[index];
  }
  if (!cells.length) return { width: 0, splitComponents: 0, narrowConnections: 0, isEstimate: true };
  if (maximumDistance <= 0) return { width: cellSize, splitComponents: 0, narrowConnections: 0, isEstimate: true };

  // Recast-inspired clearance diagnostics. Raising the distance-field
  // threshold erodes narrow corridors first. The two largest surviving lobes
  // are labelled, then a multi-source flood assigns every free cell to one
  // lobe. The watershed boundary between the two basins is the doorway or
  // bottleneck; the largest clearance on that boundary measures its width.
  const minimumPartCells = Math.max(4, Math.ceil(cells.length * 0.005));
  const ratios = [0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79, 0.76, 0.73, 0.70, 0.67, 0.64, 0.61, 0.58, 0.55, 0.52, 0.49, 0.46, 0.43, 0.40, 0.37, 0.34, 0.31, 0.28, 0.25];
  for (const ratio of ratios) {
    const threshold = Math.max(cellSize * 0.5, maximumDistance * ratio);
    const wide = new Uint8Array(mask.length);
    for (const index of cells) if (distanceMap[index] >= threshold) wide[index] = 1;
    const labelled = labelComponents(wide, width, height);
    const parts = labelled.components
      .filter((component) => component.cells.length >= minimumPartCells)
      .sort((a, b) => b.cells.length - a.cells.length);
    if (parts.length < 2) continue;

    const basin = new Int32Array(mask.length);
    basin.fill(-1);
    const queue = new Int32Array(cells.length);
    let head = 0;
    let tail = 0;
    for (let part = 0; part < 2; part += 1) {
      for (const index of parts[part].cells) {
        basin[index] = part;
        queue[tail++] = index;
      }
    }
    let interfaces = 0;
    let maximumInterfaceDistance = 0;
    while (head < tail) {
      const index = queue[head++];
      const owner = basin[index];
      const row = Math.floor(index / width);
      const col = index - row * width;
      const neighbors = [0, 0, 0, 0];
      let count = 0;
      if (row > 0) neighbors[count++] = index - width;
      if (row + 1 < height) neighbors[count++] = index + width;
      if (col > 0) neighbors[count++] = index - 1;
      if (col + 1 < width) neighbors[count++] = index + 1;
      for (let neighborIndex = 0; neighborIndex < count; neighborIndex += 1) {
        const next = neighbors[neighborIndex];
        if (!mask[next]) continue;
        if (basin[next] === -1) {
          basin[next] = owner;
          queue[tail++] = next;
        } else if (basin[next] !== owner) {
          interfaces += 1;
          maximumInterfaceDistance = Math.max(maximumInterfaceDistance, distanceMap[next], distanceMap[index]);
        }
      }
    }
    if (!interfaces || maximumInterfaceDistance <= 0) continue;
    return {
      width: Math.max(cellSize, maximumInterfaceDistance * 2 * cellSize),
      splitComponents: parts.length,
      narrowConnections: interfaces,
      isEstimate: true,
    };
  }

  // A compact room never splits. Its maximum inscribed clearance gives the
  // room's narrow dimension for this first-pass diagnostic.
  return {
    width: Math.max(cellSize, maximumDistance * 2 * cellSize),
    splitComponents: 1,
    narrowConnections: 0,
    isEstimate: true,
  };
}

const RING_EPSILON = 1e-9;

function sameRingPoint(left, right, epsilon = RING_EPSILON) {
  return Math.abs(left[0] - right[0]) <= epsilon && Math.abs(left[1] - right[1]) <= epsilon;
}

function ringCross(origin, left, right) {
  return (left[0] - origin[0]) * (right[1] - origin[1])
    - (left[1] - origin[1]) * (right[0] - origin[0]);
}

function ringPointOnSegment(point, start, end, epsilon = RING_EPSILON) {
  const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
  if (Math.abs(ringCross(start, end, point)) > epsilon * Math.max(1, length)) return false;
  return point[0] >= Math.min(start[0], end[0]) - epsilon
    && point[0] <= Math.max(start[0], end[0]) + epsilon
    && point[1] >= Math.min(start[1], end[1]) - epsilon
    && point[1] <= Math.max(start[1], end[1]) + epsilon;
}

function ringSegmentsIntersect(a, b, c, d) {
  const abc = ringCross(a, b, c);
  const abd = ringCross(a, b, d);
  const cda = ringCross(c, d, a);
  const cdb = ringCross(c, d, b);
  if (((abc > RING_EPSILON && abd < -RING_EPSILON) || (abc < -RING_EPSILON && abd > RING_EPSILON))
    && ((cda > RING_EPSILON && cdb < -RING_EPSILON) || (cda < -RING_EPSILON && cdb > RING_EPSILON))) {
    return true;
  }
  return (Math.abs(abc) <= RING_EPSILON && ringPointOnSegment(c, a, b))
    || (Math.abs(abd) <= RING_EPSILON && ringPointOnSegment(d, a, b))
    || (Math.abs(cda) <= RING_EPSILON && ringPointOnSegment(a, c, d))
    || (Math.abs(cdb) <= RING_EPSILON && ringPointOnSegment(b, c, d));
}

function openRingPoints(ring) {
  if (!Array.isArray(ring)) return [];
  const points = [];
  for (const point of ring) {
    if (!Array.isArray(point) || point.length < 2
      || !Number.isFinite(Number(point[0])) || !Number.isFinite(Number(point[1]))) return [];
    points.push([Number(point[0]), Number(point[1])]);
  }
  while (points.length > 1 && sameRingPoint(points[0], points[points.length - 1])) points.pop();
  return points;
}

function cleanClosedRing(raw) {
  const points = openRingPoints(raw);
  if (points.length < 3) return null;
  const deduplicated = [];
  for (const point of points) {
    if (!deduplicated.length || !sameRingPoint(deduplicated[deduplicated.length - 1], point)) {
      deduplicated.push(point);
    }
  }
  if (deduplicated.length > 1 && sameRingPoint(deduplicated[0], deduplicated[deduplicated.length - 1])) {
    deduplicated.pop();
  }
  if (deduplicated.length < 3) return null;

  let cleaned = deduplicated;
  let changed = true;
  while (changed && cleaned.length > 3) {
    changed = false;
    const next = [];
    for (let index = 0; index < cleaned.length; index += 1) {
      const previous = cleaned[(index + cleaned.length - 1) % cleaned.length];
      const current = cleaned[index];
      const following = cleaned[(index + 1) % cleaned.length];
      if (ringPointOnSegment(current, previous, following)) {
        changed = true;
      } else {
        next.push(current);
      }
    }
    if (next.length >= 3) cleaned = next;
  }
  if (cleaned.length < 3) return null;
  return cleaned.concat([[cleaned[0][0], cleaned[0][1]]]);
}

export function ringSelfIntersects(ring) {
  const points = openRingPoints(ring);
  if (points.length < 3) return true;
  for (let index = 0; index < points.length; index += 1) {
    if (sameRingPoint(points[index], points[(index + 1) % points.length])) return true;
  }
  for (let left = 0; left < points.length; left += 1) {
    const leftNext = (left + 1) % points.length;
    for (let right = left + 1; right < points.length; right += 1) {
      const rightNext = (right + 1) % points.length;
      if (left === right || leftNext === right || rightNext === left) continue;
      if (ringSegmentsIntersect(points[left], points[leftNext], points[right], points[rightNext])) return true;
    }
  }
  return false;
}

function isValidClosedRing(ring) {
  const cleaned = cleanClosedRing(ring);
  return Boolean(cleaned
    && cleaned.length >= 4
    && Math.abs(ringArea(cleaned)) > 1e-8
    && !ringSelfIntersects(cleaned));
}

export function smoothClosedRing(ring, tolerance) {
  const original = cleanClosedRing(ring);
  if (!original || !isValidClosedRing(original)) return null;
  if (original.length <= 4) return original;

  const points = original.slice(0, -1);
  let farthest = 1;
  let farthestDistance = -1;
  for (let index = 1; index < points.length; index += 1) {
    const distance = Math.hypot(points[index][0] - points[0][0], points[index][1] - points[0][1]);
    if (distance > farthestDistance) {
      farthest = index;
      farthestDistance = distance;
    }
  }
  const simplifyOpen = (list) => {
    if (list.length <= 2) return list.slice();
    const first = list[0];
    const last = list[list.length - 1];
    const dx = last[0] - first[0];
    const dz = last[1] - first[1];
    const length = Math.hypot(dx, dz);
    let split = -1;
    let maximum = -1;
    for (let index = 1; index < list.length - 1; index += 1) {
      const point = list[index];
      const distance = length > 1e-12
        ? Math.abs(dz * point[0] - dx * point[1] + last[0] * first[1] - last[1] * first[0]) / length
        : Math.hypot(point[0] - first[0], point[1] - first[1]);
      if (distance > maximum) {
        maximum = distance;
        split = index;
      }
    }
    if (split < 0 || maximum <= tolerance) return [first, last];
    return simplifyOpen(list.slice(0, split + 1)).slice(0, -1)
      .concat(simplifyOpen(list.slice(split)));
  };
  const firstHalf = points.slice(0, farthest + 1);
  const secondHalf = points.slice(farthest).concat([points[0]]);
  const simplified = cleanClosedRing(
    simplifyOpen(firstHalf).slice(0, -1).concat(simplifyOpen(secondHalf)),
  );
  if (simplified && isValidClosedRing(simplified)) return simplified;
  return original;
}

export function traceBoundaryLoops(mask, width, height, originX, originZ, cellSize) {
  const outgoing = new Map();
  const addEdge = (x0, z0, x1, z1, cell) => {
    const from = z0 * (width + 1) + x0;
    const to = z1 * (width + 1) + x1;
    const edge = { from, to, cell, x0, z0, x1, z1, dx: x1 - x0, dz: z1 - z0, angle: Math.atan2(z1 - z0, x1 - x0), successor: null };
    const list = outgoing.get(from);
    if (list) list.push(edge); else outgoing.set(from, [edge]);
    return edge;
  };
  const edges = [];
  const occupied = (col, row) => col >= 0 && col < width && row >= 0 && row < height && Boolean(mask[row * width + col]);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const cell = row * width + col;
      if (!mask[cell]) continue;
      const sides = new Array(4);
      if (!occupied(col, row - 1)) sides[0] = addEdge(col + 1, row, col, row, cell);
      if (!occupied(col - 1, row)) sides[1] = addEdge(col, row, col, row + 1, cell);
      if (!occupied(col, row + 1)) sides[2] = addEdge(col, row + 1, col + 1, row + 1, cell);
      if (!occupied(col + 1, row)) sides[3] = addEdge(col + 1, row + 1, col + 1, row, cell);
      for (const edge of sides) if (edge) edges.push(edge);
    }
  }

  const sameDirection = (left, right) => left.dx === right.dx && left.dz === right.dz;
  const traceFrom = (start, localVisited, loops) => {
    const local = new Set();
    const corners = [[originX + start.x0 * cellSize, originZ + start.z0 * cellSize]];
    let edge = start;
    let closed = false;
    let guard = 0;
    while (edge && guard <= edges.length + 1) {
      if (local.has(edge)) { closed = edge === start; break; }
      if (localVisited.has(edge)) return false;
      local.add(edge);
      const next = edge.successor;
      if (!next) break;
      if (!sameDirection(edge, next)) corners.push([originX + next.x0 * cellSize, originZ + next.z0 * cellSize]);
      edge = next;
      guard += 1;
    }
    if (!closed || corners.length < 3) return false;
    const loop = cleanClosedRing(corners);
    if (!loop || Math.abs(ringArea(loop)) <= 1e-8 || !isValidClosedRing(loop)) return false;
    for (const item of local) localVisited.add(item);
    loops.push(loop);
    return true;
  };

  // At degree-four lattice vertices, two different continuations both avoid
  // immediate self-intersection. Keep both possibilities until a cycle has
  // been verified, then commit the first complete, globally consistent set.
  // This is bounded because saddles are rare in a building raster.
  const maxSearchNodes = Math.max(256, edges.length * 4);
  let searchNodes = 0;
  const search = (cursor, visited) => {
    if (searchNodes++ > maxSearchNodes) return null;
    const loops = [];
    while (cursor < edges.length && visited.has(edges[cursor])) cursor += 1;
    if (cursor >= edges.length) return loops;
    const start = edges[cursor];
    const options = (outgoing.get(start.to) || []).filter((candidate) => candidate !== start);
    // At a saddle, try each diagonal continuation. On a straight/corner seam,
    // try the same-cell edge first and then the remaining option as a fallback.
    const sameCell = options.filter((candidate) => candidate.cell === start.cell);
    const preferred = [...sameCell, ...options.filter((candidate) => candidate.cell !== start.cell)];
    for (const next of preferred) {
      start.successor = next;
      const nextVisited = new Set(visited);
      const nextLoops = [];
      if (!traceFrom(start, nextVisited, nextLoops)) continue;
      const tail = search(cursor + 1, nextVisited);
      if (tail) return nextLoops.concat(tail);
    }
    return null;
  };
  const paired = search(0, new Set());
  if (paired) return paired;

  // Deterministic fallback: widest left turn at every junction. This always
  // produces closed cycles; any invalid cycle is rejected by region building.
  const twoPi = Math.PI * 2;
  for (const edge of edges) {
    const options = (outgoing.get(edge.to) || []).filter((candidate) => candidate !== edge);
    const sameCell = options.filter((candidate) => candidate.cell === edge.cell);
    const candidates = options.length >= 3 && sameCell.length ? sameCell : options;
    let best = null;
    let bestTurn = -Infinity;
    for (const candidate of candidates) {
      let turn = ((edge.angle - candidate.angle) % twoPi + twoPi) % twoPi;
      if (turn < 1e-9) turn = twoPi;
      if (turn > bestTurn) { bestTurn = turn; best = candidate; }
    }
    edge.successor = best;
  }
  const visited = new Set();
  const loops = [];
  for (const start of edges) {
    if (!visited.has(start)) traceFrom(start, visited, loops);
  }
  return loops;
}

function ringArea(ring) {
  let area = 0;
  for (let index = 0; index < ring.length - 1; index += 1) {
    area += ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1];
  }
  return area * 0.5;
}

function pointInRing(x, z, ring) {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const xi = ring[index][0], zi = ring[index][1];
    const xj = ring[previous][0], zj = ring[previous][1];
    if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

function insidePointForLoop(loop, cellSize) {
  for (let index = 0; index < loop.length - 1; index += 1) {
    const ax = loop[index][0], az = loop[index][1];
    const bx = loop[index + 1][0], bz = loop[index + 1][1];
    const dx = bx - ax, dz = bz - az;
    const length = Math.hypot(dx, dz);
    if (length <= 1e-12) continue;
    const epsilon = cellSize * 0.1;
    return [(ax + bx) * 0.5 + dz / length * epsilon, (az + bz) * 0.5 - dx / length * epsilon];
  }
  return loop[0];
}

function forceRingWinding(ring, counterClockwise) {
  const oriented = [...ring];
  const area = ringArea(oriented);
  if ((counterClockwise && area < 0) || (!counterClockwise && area > 0)) oriented.reverse();
  return oriented;
}

function ringBounds(ring) {
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (const point of ring) {
    if (point[0] < minX) minX = point[0];
    if (point[0] > maxX) maxX = point[0];
    if (point[1] < minZ) minZ = point[1];
    if (point[1] > maxZ) maxZ = point[1];
  }
  return { minX, minZ, maxX, maxZ };
}

function boundsOverlap(left, right, epsilon = 0) {
  return left.minX <= right.maxX + epsilon
    && left.maxX >= right.minX - epsilon
    && left.minZ <= right.maxZ + epsilon
    && left.maxZ >= right.minZ - epsilon;
}

function ringInteriorPoint(ring, cellSize) {
  const points = ring.slice(0, -1);
  if (!points.length) return ring[0];
  let best = null;
  let bestScore = -Infinity;
  // Sampling at vertex offsets is sufficient to find an interior probe without
  // testing every pair of distinct X and Z coordinates. Complex stairwell
  // outlines can have hundreds of vertices; the former Cartesian product was
  // quadratic even before the ring intersection checks.
  const sampleStep = Math.max(1, Math.floor(points.length / 48));
  for (let index = 0; index < points.length; index += sampleStep) {
    const point = points[index];
    const probes = [
      [point[0] + cellSize * 0.25, point[1] + cellSize * 0.25],
      [point[0] - cellSize * 0.25, point[1] + cellSize * 0.25],
      [point[0] - cellSize * 0.25, point[1] - cellSize * 0.25],
      [point[0] + cellSize * 0.25, point[1] - cellSize * 0.25],
    ];
    for (const probe of probes) {
      if (!pointInRing(probe[0], probe[1], ring)) continue;
      let clearance = Infinity;
      for (let edge = 0; edge < ring.length - 1; edge += 1) {
        const ax = ring[edge][0], az = ring[edge][1], bx = ring[edge + 1][0], bz = ring[edge + 1][1];
        const dx = bx - ax, dz = bz - az;
        const length = Math.hypot(dx, dz);
        if (length <= 1e-12) continue;
        clearance = Math.min(clearance, Math.abs(dz * probe[0] - dx * probe[1] + bx * az - ax * bz) / length);
      }
      if (clearance > bestScore) { bestScore = clearance; best = probe; }
      if (bestScore >= cellSize * 0.2) return best;
    }
  }
  if (best) return best;
  const fallback = insidePointForLoop(ring, cellSize);
  return pointInRing(fallback[0], fallback[1], ring) ? fallback : [0, 0];
}

function segmentBounds(a, b) {
  return {
    minX: Math.min(a[0], b[0]),
    minZ: Math.min(a[1], b[1]),
    maxX: Math.max(a[0], b[0]),
    maxZ: Math.max(a[1], b[1]),
  };
}

function ringEdgesIntersect(a, b) {
  const left = openRingPoints(a);
  const right = openRingPoints(b);
  if (left.length < 3 || right.length < 3) return true;
  const leftBounds = ringBounds(left);
  const rightBounds = ringBounds(right);
  if (!boundsOverlap(leftBounds, rightBounds)) return false;
  // Index the smaller ring into a uniform grid and test only the opposite
  // segments that share a cell. This preserves the strict touching-edge rule
  // while avoiding an O(n*m) scan for stairwells with hundreds of vertices.
  const span = Math.max(
    leftBounds.maxX - leftBounds.minX,
    leftBounds.maxZ - leftBounds.minZ,
    rightBounds.maxX - rightBounds.minX,
    rightBounds.maxZ - rightBounds.minZ,
    Math.max(Math.abs(leftBounds.minX), Math.abs(leftBounds.maxX),
      Math.abs(leftBounds.minZ), Math.abs(leftBounds.maxZ)) * 1e-6,
    1e-6,
  );
  const bucketSize = Math.max(span / Math.max(1, Math.ceil(Math.sqrt(left.length + right.length))), 1e-6);
  const index = new Map();
  const bucketKey = (x, z) => Math.floor(x / bucketSize) + ',' + Math.floor(z / bucketSize);
  for (let indexLeft = 0; indexLeft < left.length; indexLeft += 1) {
    const bounds = segmentBounds(left[indexLeft], left[(indexLeft + 1) % left.length]);
    for (let gx = Math.floor(bounds.minX / bucketSize); gx <= Math.floor(bounds.maxX / bucketSize); gx += 1) {
      for (let gz = Math.floor(bounds.minZ / bucketSize); gz <= Math.floor(bounds.maxZ / bucketSize); gz += 1) {
        const key = gx + ',' + gz;
        const list = index.get(key);
        if (list) list.push(indexLeft);
        else index.set(key, [indexLeft]);
      }
    }
  }
  const tested = new Set();
  for (let indexRight = 0; indexRight < right.length; indexRight += 1) {
    const bounds = segmentBounds(right[indexRight], right[(indexRight + 1) % right.length]);
    for (let gx = Math.floor(bounds.minX / bucketSize); gx <= Math.floor(bounds.maxX / bucketSize); gx += 1) {
      for (let gz = Math.floor(bounds.minZ / bucketSize); gz <= Math.floor(bounds.maxZ / bucketSize); gz += 1) {
        for (const indexLeft of index.get(gx + ',' + gz) || []) {
          const key = indexLeft * right.length + indexRight;
          if (tested.has(key)) continue;
          tested.add(key);
          if (ringSegmentsIntersect(
            left[indexLeft], left[(indexLeft + 1) % left.length],
            right[indexRight], right[(indexRight + 1) % right.length],
          )) return true;
        }
      }
    }
  }
  return false;
}

function validHoleInsideOuter(hole, outer, cellSize) {
  if (!isValidClosedRing(hole) || !isValidClosedRing(outer)) return false;
  const holeBounds = ringBounds(hole);
  const outerBounds = ringBounds(outer);
  if (!boundsOverlap(holeBounds, outerBounds)) return false;
  const probe = ringInteriorPoint(hole, cellSize);
  if (!pointInRing(probe[0], probe[1], outer)) return false;
  if (ringEdgesIntersect(hole, outer)) return false;
  return openRingPoints(hole).every((point) => pointInRing(point[0], point[1], outer));
}

function holesOverlap(left, right) {
  if (!boundsOverlap(ringBounds(left), ringBounds(right))) return false;
  if (ringEdgesIntersect(left, right)) return true;
  const leftPoints = openRingPoints(left);
  const rightPoints = openRingPoints(right);
  return leftPoints.some((point) => pointInRing(point[0], point[1], right))
    || rightPoints.some((point) => pointInRing(point[0], point[1], left));
}

function isValidRegionContour(contour, cellSize) {
  if (!contour || !isValidClosedRing(contour.outer) || !Array.isArray(contour.holes)) return false;
  const outerArea = Math.abs(ringArea(contour.outer));
  const outerBounds = ringBounds(contour.outer);
  let holeArea = 0;
  for (let index = 0; index < contour.holes.length; index += 1) {
    const hole = contour.holes[index];
    if (!validHoleInsideOuter(hole, contour.outer, cellSize)) return false;
    holeArea += Math.abs(ringArea(hole));
    const holeBounds = ringBounds(hole);
    for (let other = index + 1; other < contour.holes.length; other += 1) {
      if (holesOverlap(hole, contour.holes[other])) return false;
    }
  }
  return holeArea + 1e-8 < outerArea;
}

export function extractExteriorAndHoles(loops, cellSize, tolerance, debug = false) {
  if (!Array.isArray(loops) || !loops.length) return null;
  const ordered = loops.map((ring, index) => ({ ring, index, area: Math.abs(ringArea(ring)) }))
    .sort((left, right) => right.area - left.area);
  const rawOuter = ordered[0].ring;
  let rawOuterCollision = null;
  if (debug && ringSelfIntersects(rawOuter)) {
    const pts = openRingPoints(rawOuter);
    outer: for (let i = 0; i < pts.length; i += 1) {
      for (let j = i + 1; j < pts.length; j += 1) {
        const inx = (i + 1) % pts.length;
        const jnx = (j + 1) % pts.length;
        if (i === j || inx === j || jnx === i) continue;
        if (ringSegmentsIntersect(pts[i], pts[inx], pts[j], pts[jnx])) {
          rawOuterCollision = { i, j, a: pts[i], b: pts[inx], c: pts[j], d: pts[jnx] };
          break outer;
        }
      }
    }
  }
  const outer = smoothClosedRing(ordered[0].ring, tolerance);
  if (!outer || !isValidClosedRing(outer)) {
    if (debug) console.log('AUTO_CONTOUR_REJECT', 'outer', {
      loopCount: loops.length,
      rawPoints: ordered[0].ring.length,
      outerPoints: outer?.length || 0,
      rawSelf: ringSelfIntersects(ordered[0].ring),
      outerSelf: outer ? ringSelfIntersects(outer) : null,
      outerArea: outer ? ringArea(outer) : 0,
      collision: rawOuterCollision,
    });
    return null;
  }
  const orientedOuter = forceRingWinding(outer, true);
  const holes = [];
  for (let index = 1; index < ordered.length; index += 1) {
    const candidate = smoothClosedRing(ordered[index].ring, tolerance);
    if (!candidate) continue;
    const hole = forceRingWinding(candidate, false);
    if (!validHoleInsideOuter(hole, orientedOuter, cellSize)) continue;
    if (holes.some((existing) => holesOverlap(existing, hole))) continue;
    holes.push(hole);
  }
  const contour = { outer: orientedOuter, holes };
  const valid = isValidRegionContour(contour, cellSize);
  if (debug && !valid) console.log('AUTO_CONTOUR_REJECT', 'final', {
    loopCount: loops.length,
    outerPoints: orientedOuter.length,
    holes: holes.length,
    outerArea: Math.abs(ringArea(orientedOuter)),
    holeArea: holes.reduce((sum, hole) => sum + Math.abs(ringArea(hole)), 0),
  });
  return valid ? contour : null;
}

function erodeMask(source, width, height, radius) {
  if (radius <= 0) return source.slice();
  const horizontal = new Uint8Array(source.length);
  const eroded = new Uint8Array(source.length);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const index = row * width + col;
      if (!source[index]) continue;
      let clear = true;
      for (let offset = -radius; offset <= radius && clear; offset += 1) {
        const neighbor = col + offset;
        if (neighbor < 0 || neighbor >= width || !source[row * width + neighbor]) clear = false;
      }
      if (clear) horizontal[index] = 1;
    }
  }
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const index = row * width + col;
      if (!horizontal[index]) continue;
      let clear = true;
      for (let offset = -radius; offset <= radius && clear; offset += 1) {
        const neighbor = row + offset;
        if (neighbor < 0 || neighbor >= height || !horizontal[neighbor * width + col]) clear = false;
      }
      if (clear) eroded[index] = 1;
    }
  }
  return eroded;
}

function dilateMask(source, width, height, radius) {
  if (radius <= 0) return source.slice();
  const horizontal = new Uint8Array(source.length);
  const dilated = new Uint8Array(source.length);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      let found = false;
      for (let offset = -radius; offset <= radius && !found; offset += 1) {
        const neighbor = col + offset;
        if (neighbor >= 0 && neighbor < width && source[row * width + neighbor]) found = true;
      }
      if (found) horizontal[row * width + col] = 1;
    }
  }
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      if (!horizontal[row * width + col]) continue;
      let found = false;
      for (let offset = -radius; offset <= radius && !found; offset += 1) {
        const neighbor = row + offset;
        if (neighbor >= 0 && neighbor < height && horizontal[neighbor * width + col]) found = true;
      }
      if (found) dilated[row * width + col] = 1;
    }
  }
  return dilated;
}

function maskAreaCells(mask) {
  let count = 0;
  for (let index = 0; index < mask.length; index += 1) count += mask[index] ? 1 : 0;
  return count;
}

function partitionMaskAroundSeeds(mask, seeds, width, height) {
  const labels = new Int32Array(mask.length);
  labels.fill(-1);
  const queue = new Int32Array(mask.length);
  let head = 0;
  let tail = 0;
  for (let seedIndex = 0; seedIndex < seeds.length; seedIndex += 1) {
    for (const index of seeds[seedIndex].cells) {
      if (!mask[index] || labels[index] !== -1) continue;
      labels[index] = seedIndex;
      queue[tail++] = index;
    }
  }
  while (head < tail) {
    const index = queue[head++];
    const owner = labels[index];
    const row = Math.floor(index / width);
    const col = index - row * width;
    const neighbors = [0, 0, 0, 0];
    let count = 0;
    if (row > 0) neighbors[count++] = index - width;
    if (row + 1 < height) neighbors[count++] = index + width;
    if (col > 0) neighbors[count++] = index - 1;
    if (col + 1 < width) neighbors[count++] = index + 1;
    for (let neighborIndex = 0; neighborIndex < count; neighborIndex += 1) {
      const next = neighbors[neighborIndex];
      if (!mask[next] || labels[next] !== -1) continue;
      labels[next] = owner;
      queue[tail++] = next;
    }
  }

  // nav3d.py treats a shared edge or vertex as connected. Remove a one-cell
  // band wherever two owners meet (including diagonal contact), then keep the
  // largest 4-connected body of each side. A sub-person-width neck therefore
  // becomes a real navigation gap instead of an outline-only split.
  const separated = labels.slice();
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const index = row * width + col;
      if (labels[index] < 0) continue;
      for (let dz = -1; dz <= 1; dz += 1) {
        const nextRow = row + dz;
        if (nextRow < 0 || nextRow >= height) continue;
        for (let dx = -1; dx <= 1; dx += 1) {
          if (!dx && !dz) continue;
          const nextCol = col + dx;
          if (nextCol < 0 || nextCol >= width) continue;
          const next = nextRow * width + nextCol;
          if (labels[next] >= 0 && labels[next] !== labels[index]) separated[index] = -1;
        }
      }
    }
  }
  for (let owner = 0; owner < seeds.length; owner += 1) {
    const ownerMask = new Uint8Array(mask.length);
    for (let index = 0; index < separated.length; index += 1) {
      if (separated[index] === owner) ownerMask[index] = 1;
    }
    const parts = labelComponents(ownerMask, width, height).components
      .sort((a, b) => b.cells.length - a.cells.length);
    for (let partIndex = 1; partIndex < parts.length; partIndex += 1) {
      for (const index of parts[partIndex].cells) separated[index] = -1;
    }
  }
  return separated;
}

function createRegions(layer, options, grid, obstacles = null, debugLabel = '') {
  const { cellSize, originX, originZ, width, height } = grid;
  const freeMask = new Uint8Array(grid.total);
  for (let index = 0; index < layer.mask.length; index += 1) {
    if (layer.mask[index] && !obstacles?.[index]) freeMask[index] = 1;
  }
  const labelled = labelComponents(freeMask, width, height);
  const minimumCells = Math.max(1, Math.ceil(optionNumber(options, 'minRegionArea') / (cellSize * cellSize)));
  const minimumWidth = optionNumber(options, 'minWalkableWidth');
  const personDiameter = optionNumber(options, 'minPersonDiameter');
  const tolerance = optionNumber(options, 'simplifyTolerance');
  const radius = Math.max(1, Math.round(personDiameter * 0.5 / cellSize));
  const regions = [];
  const debug = Boolean(options.debug);

  for (const component of labelled.components) {
    if (component.cells.length < minimumCells) {
      if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'small', component.cells.length * cellSize * cellSize);
      continue;
    }
    const mask = new Uint8Array(grid.total);
    for (const index of component.cells) mask[index] = 1;
    const distanceMap = squaredDistanceTransform(mask, width, height);
    const widthResult = widestPathWidth(mask, width, height, distanceMap, cellSize);
    const minWidth = widthResult.width;

    // Recast's core rule is that the agent's full footprint must clear the
    // surface. Doors just above the physical person diameter stay as candidates
    // but make the component ineligible for an automatic duplicate. Corrosion is
    // only applied below the person diameter, where no body can pass.
    const physicallyBlocked = minWidth + 1e-9 < personDiameter;
    const eroded = erodeMask(mask, width, height, radius);
    const erodedLabels = labelComponents(eroded, width, height);
    const erodedParts = erodedLabels.components
      .filter((part) => part.cells.length * cellSize * cellSize >= Math.min(
        optionNumber(options, 'minRegionArea') * 0.5,
        0.75,
      ))
      .sort((a, b) => b.cells.length - a.cells.length);
    let cutWidthArea = 0;
    if (!erodedParts.length) cutWidthArea = component.cells.length;
    else if (physicallyBlocked && erodedParts.length > 1) {
      cutWidthArea = erodedParts.slice(1).reduce((sum, part) => sum + part.cells.length, 0);
    } else if (minWidth + 1e-9 < minimumWidth) {
      cutWidthArea = component.cells.length;
    }

    const splitAttempted = physicallyBlocked && erodedParts.length > 1;
    const partition = splitAttempted
      ? partitionMaskAroundSeeds(mask, erodedParts, width, height)
      : null;
    const splitMasks = partition ? erodedParts.map((part, owner) => {
      const owned = new Uint8Array(grid.total);
      for (let index = 0; index < partition.length; index += 1) {
        if (partition[index] === owner) owned[index] = 1;
      }
      return owned;
    }) : [];

    const buildCandidate = (candidate, splitApplied, candidateIndex) => {
      const clipped = new Uint8Array(grid.total);
      for (let index = 0; index < candidate.length; index += 1) {
        if (candidate[index] && freeMask[index]) clipped[index] = 1;
      }
      if (maskAreaCells(clipped) < minimumCells) {
        if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'clipped-small', candidateIndex);
        return null;
      }
      const ownerCells = [];
      for (let index = 0; index < clipped.length; index += 1) if (clipped[index]) ownerCells.push(index);
      const loops = traceBoundaryLoops(clipped, width, height, originX, originZ, cellSize);
      if (debug) {
        const areas = loops.map(loop => Number(ringArea(loop).toFixed(3))).sort((a, b) => Math.abs(b) - Math.abs(a)).slice(0, 8);
        const points = loops.map(loop => loop.length).sort((a, b) => b - a).slice(0, 8);
        console.log('AUTO_TRACE_DEBUG', debugLabel, JSON.stringify({ loops: loops.length, areas, points }));
      }
      if (!loops.length) {
        if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'no-loop', ownerCells.length * cellSize * cellSize);
        return null;
      }
      const contour = extractExteriorAndHoles(loops, cellSize, tolerance, debug);
      if (!contour) {
        if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'invalid-contour', loops.length);
        return null;
      }
      const area = ownerCells.length * cellSize * cellSize;
      const contourOuterArea = Math.abs(ringArea(contour.outer));
      const holeArea = contour.holes.reduce((sum, hole) => sum + Math.abs(ringArea(hole)), 0);
      const contourRatio = contourOuterArea > 0 ? Math.max(0, contourOuterArea - holeArea) / area : 0;
      if (contourRatio < 0.8 || contourRatio > 1.25) {
        if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'contour', contourRatio, area, loops.length, contour.outer.length);
        return null;
      }
      const holeAreaM2 = contour.holes.reduce((sum, hole) => sum + Math.abs(ringArea(hole)), 0);
      const minimumHoleArea = Number(options.minHoleArea);
      if (Number.isFinite(minimumHoleArea) && minimumHoleArea > 0 && contour.holes.length) {
        const keptHoles = contour.holes.filter((hole) => Math.abs(ringArea(hole)) + 1e-8 >= minimumHoleArea);
        if (debug && keptHoles.length !== contour.holes.length) {
          console.log('AUTO_HOLE_FILTER', debugLabel, contour.holes.length - keptHoles.length, minimumHoleArea);
        }
        contour.holes = keptHoles;
      }
      const cutWidthAreaM2 = minWidth + 1e-9 < minimumWidth ? cutWidthArea * cellSize * cellSize : 0;
      const warnings = [];
      if (splitApplied) {
        warnings.push('检测到约 ' + minWidth.toFixed(2) + ' m 的窄口，小于人员直径 ' + personDiameter.toFixed(2) + ' m；已按人体半径将两侧楼面分别提取，请逐块确认。');
      } else if (splitAttempted) {
        warnings.push('检测到约 ' + minWidth.toFixed(2) + ' m 的窄口，小于人员直径 ' + personDiameter.toFixed(2) + ' m；拆分未能稳定生成全部独立轮廓，已回退为完整区域，请人工确认是否不可通行。');
      } else if (minWidth + 1e-9 < minimumWidth) {
        warnings.push('检测到约 ' + minWidth.toFixed(2) + ' m 的窄口，小于推荐通行宽度 ' + minimumWidth.toFixed(2) + ' m，但大于人员直径 ' + personDiameter.toFixed(2) + ' m；请人工核对是否需要保留。');
      }
      return {
        outer: contour.outer,
        holes: contour.holes,
        area,
        minWidth,
        cutWidthAreaM2,
        splitComponents: splitApplied ? splitMasks.length : widthResult.splitComponents,
        minWidthIsEstimate: Boolean(widthResult.isEstimate),
        physicallyBlocked,
        splitApplied,
        warnings,
        confidence: physicallyBlocked ? 'low' : minWidth >= Math.max(1.2, minimumWidth * 1.5) ? 'high' : 'medium',
      };
    };

    // A split is committed atomically. If any side fails area or contour
    // validation, keep the complete parent mask instead of leaking a half-room.
    let builtCandidates = [];
    if (splitAttempted) {
      builtCandidates = splitMasks.map((candidate, candidateIndex) => (
        buildCandidate(candidate, true, candidateIndex)
      ));
      if (builtCandidates.length < 2 || builtCandidates.some((candidate) => !candidate)) {
        if (debug) console.log('AUTO_REGION_SPLIT_FALLBACK', debugLabel, builtCandidates.length);
        builtCandidates = [];
      }
    }
    if (!builtCandidates.length) {
      const fallback = buildCandidate(mask, false, 0);
      if (fallback) regions.push(fallback);
    } else {
      regions.push(...builtCandidates);
    }
  }

  return regions;
}
function serializeRegions(regions, options, idPrefix) {
  return regions.map((region, regionIndex) => ({
    id: idPrefix + '-region-' + (regionIndex + 1),
    outer: region.outer,
    holes: region.holes,
    area_m2: Number(region.area.toFixed(2)),
    min_width_m: Number(region.minWidth.toFixed(2)),
    min_width_is_estimate: Boolean(region.minWidthIsEstimate),
    min_width_split_components: Number(region.splitComponents || 0),
    split_applied: Boolean(region.splitApplied),
    width_cut_area_m2: Number(region.cutWidthAreaM2.toFixed(2)),
    hole_count: Array.isArray(region.holes) ? region.holes.length : 0,
    near_width_limit: Number(region.minWidth) + 1e-9 < optionNumber(options, 'minWalkableWidth'),
    physically_blocked: Boolean(region.physicallyBlocked),
    width_cut_warning: Array.isArray(region.warnings) && region.warnings.length > 0,
    warnings: region.warnings,
    confidence: region.confidence,
  }));
}

function generatedFloorLabel(index) {
  const names = ['一层', '二层', '三层', '四层', '五层', '六层', '七层', '八层'];
  return names[index] || ('第' + (index + 1) + '层');
}

export function analyzeGeometry(positions, indices, options = {}, requestId = 'local', semantics = null) {
  const mergedOptions = { ...DEFAULTS, ...options };
  const thresholds = {
    min_walkable_width_m: optionNumber(mergedOptions, 'minWalkableWidth'),
    min_hole_area_m2: optionNumber(mergedOptions, 'minHoleArea'),
    min_person_diameter_m: optionNumber(mergedOptions, 'minPersonDiameter'),
    min_above_coverage: optionNumber(mergedOptions, 'minAboveCoverage'),
    min_story_clearance_m: optionNumber(mergedOptions, 'minStoryClearance'),
    max_coincident_face_gap_m: optionNumber(mergedOptions, 'maxCoincidentFaceGap'),
    min_coincident_face_area_m2: optionNumber(mergedOptions, 'minCoincidentFaceArea'),
  };
  const started = performance.now();
  if (!positions || !indices || indices.length < 3) {
    return { floors: [], exclusions: [], excludedFloors: [], holes: [], stairs: [], doors: [], stats: { triangleCount: 0, horizontalTriangleCount: 0, analysisMs: 0 }, warnings: ['模型中没有可分析的三角网格。'], diagnostics: { thresholds } };
  }

  const scanned = scanUpwardTriangles(positions, indices, mergedOptions, requestId, semantics);
  if (!scanned) return null;
  const layers = clusterHeightLayers(scanned.bins, mergedOptions);
  emitProgress(requestId, 'floor-clustering', 0.36, '发现 ' + layers.length + ' 个朝上高度层');

  if (!layers.length || !scanned.bounds) {
    return {
      floors: [], exclusions: [], excludedFloors: [], holes: [], stairs: [], doors: [],
      stats: { triangleCount: scanned.triangleCount, horizontalTriangleCount: scanned.upwardCount, analysisMs: Math.round(performance.now() - started) },
      warnings: ['未找到朝上的水平面，请检查模型朝向或比例。'],
      diagnostics: { thresholds },
    };
  }

  const grid = createGrid(scanned.bounds, mergedOptions);
  const rasterized = layers.map((layer) => rasterizeLayer(layer, positions, indices, grid));
  // The school model contains a complete roof slab. Enable roof removal only
  // for a building-scale grid; small isolated rooms and unit tests keep their
  // highest exposed floor.
  const roofRemovalExplicit = Object.prototype.hasOwnProperty.call(options, 'removeRoof');
  mergedOptions.roofRemovalExplicit = roofRemovalExplicit ? 1 : 0;
  if (!roofRemovalExplicit && grid.total >= 25000) {
    mergedOptions.removeRoof = 1;
  }
  const structural = selectFloorLayers(rasterized, mergedOptions, grid, semantics);
  const selected = structural.selected;
  const excludedFloors = Array.isArray(structural.excludedFloors) ? structural.excludedFloors : [];
  const excludedLayers = Array.isArray(structural.excludedLayers) ? structural.excludedLayers : [];
  const largestArea = Math.max(0, ...selected.map((layer) => layer.rasterArea));
  const minimumFloorArea = Math.max(
    optionNumber(mergedOptions, 'minFloorArea'),
    largestArea * optionNumber(mergedOptions, 'minFloorAreaRatio'),
  );
  const floors = [];
  const warnings = [];
  const debug = Boolean(mergedOptions.debug);
  if (debug) {
    console.log('AUTO_LAYERS', layers.length, 'UPWARD', scanned.upwardCount);
    console.log('AUTO_SELECTED', selected.map((layer) => ({
      y: Number(layer.y.toFixed(4)),
      rasterArea: Number(layer.rasterArea.toFixed(2)),
      coverage: Number((layer.aboveCoverage || 0).toFixed(3)),
      triangles: layer.count,
    })));
  }

  // A lone high platform can pass the upward-surface filter. Apply the
  // existing area guard before the expensive obstacle projection so furniture
  // and decorative surfaces do not multiply the work.
  const eligible = selected.filter((layer) => layer.rasterArea >= optionNumber(mergedOptions, 'minFloorArea'));
  // Ordinary floors and excluded roof/terrace layers must share one ordered
  // list. Their obstacle masks and outline extraction then use the same layer
  // identity, and an excluded surface cannot leak into the floor candidates.
  const layerJobs = [
    ...eligible.map((layer) => ({ layer, excluded: false })),
    ...excludedLayers.map((layer) => ({ layer, excluded: true })),
  ].sort((a, b) => a.layer.y - b.layer.y);
  const projectionStarted = performance.now();
  const obstacleProjection = rasterizeObstacleFloors(
    positions, indices, layerJobs.map((job) => job.layer), grid, mergedOptions, requestId,
  );
  if (!obstacleProjection) return null;
  const obstacleMs = Math.round(performance.now() - projectionStarted);
  const excludedByLayer = new Map(excludedFloors.map((item) => [Number(item.elevation_m), item]));

  for (let floorIndex = 0; floorIndex < layerJobs.length; floorIndex += 1) {
    const { layer, excluded } = layerJobs[floorIndex];
    emitProgress(requestId, 'walkable-regions', Math.min(0.96, 0.63 + floors.length * 0.08), '正在提取 ' + Number(layer.y.toFixed(2)) + ' m 的可走区');
    const obstacles = obstacleProjection.masks[floorIndex];
    let obstacleCells = 0;
    for (let index = 0; index < obstacles.length; index += 1) obstacleCells += obstacles[index];
    const obstacleCoverage = layer.occupiedCells ? obstacleCells / layer.occupiedCells : 0;
    const regions = createRegions(layer, mergedOptions, grid, obstacles, Number(layer.y.toFixed(4)));
    if (debug) {
      console.log('AUTO_FLOOR_STAGE', Number(layer.y.toFixed(4)), {
        surfaceCells: layer.occupiedCells,
        obstacleCells,
        obstacleCoverage: Number(obstacleCoverage.toFixed(3)),
        obstacleProjectionMs: obstacleMs,
        regions: regions.length,
        regionArea: Number(regions.reduce((sum, region) => sum + region.area, 0).toFixed(2)),
      });
    }
    const area = regions.reduce((sum, region) => sum + region.area, 0);
    if (excluded) {
      const auditItem = excludedByLayer.get(Number(layer.y.toFixed(4)));
      if (auditItem) {
        auditItem.regions = serializeRegions(regions, mergedOptions, 'auto-excluded-1');
        auditItem.area_m2 = Number(area.toFixed(2));
        auditItem.warnings = [...(Array.isArray(auditItem.warnings) ? auditItem.warnings : []), ...regions
          .flatMap((region) => Array.isArray(region.warnings) ? region.warnings : [])]
          .filter((message, index, messages) => messages.indexOf(message) === index);
      }
      if (debug) {
        console.log('AUTO_EXCLUDED_STAGE', Number(layer.y.toFixed(4)), {
          surfaceCells: layer.occupiedCells,
          obstacleCells,
          obstacleCoverage: Number(obstacleCoverage.toFixed(3)),
          obstacleProjectionMs: obstacleMs,
          regions: regions.length,
          regionArea: Number(area.toFixed(2)),
        });
      }
      continue;
    }
    const floorWarnings = [];
    if (!regions.length) {
      floorWarnings.push('未提取到可走区。接受本层后请先手工绘制可走区，再保存校准。');
      warnings.push(Number(layer.y.toFixed(2)) + ' m 层未提取到可走区，已保留为低置信楼层候选。');
    } else if (area < minimumFloorArea) {
      floorWarnings.push('自动提取面积低于正式楼层门槛，请对照模型确认是否需要保留。');
    }
    if (layer.platformSuspected) floorWarnings.push('该水平面未匹配到可靠的下方结构板，可能是平台、家具顶面或屋面。');
    const reviewLayer = structural.reviewOnlyLayers?.find((candidate) => Math.abs(candidate.y - layer.y) < 1e-4);
    if (reviewLayer?.roofReview) floorWarnings.push(reviewLayer.roofReview);
    if (obstacleCoverage > 0.55) floorWarnings.push('障碍覆盖率达到 ' + (obstacleCoverage * 100).toFixed(0) + '%，请检查家具或薄面是否被误判为障碍。');
    floors.push({
      id: '',
      name: '',
      elevation_m: Number(layer.y.toFixed(4)),
      area_m2: Number(area.toFixed(2)),
      triangleCount: layer.count,
      obstacleCoverage: Number(obstacleCoverage.toFixed(3)),
      aboveCoverage: Number(Number(layer.aboveCoverage || 0).toFixed(3)),
      clearance_m: Number.isFinite(layer.clearance) ? Number(layer.clearance.toFixed(3)) : null,
      structureMatch: Boolean(layer.structureMatch),
      platformSuspected: Boolean(layer.platformSuspected),
      warnings: floorWarnings,
      diagnostics: {
        obstacle_coverage: Number(obstacleCoverage.toFixed(3)),
        above_coverage: Number(Number(layer.aboveCoverage || 0).toFixed(3)),
        platform_suspected: Boolean(layer.platformSuspected),
        structure_match: Boolean(layer.structureMatch),
      },
      confidence: regions.length ? 'medium' : 'low',
      requiresManualWalkable: !regions.length,
      regions,
    });
  }

  floors.sort((a, b) => a.elevation_m - b.elevation_m);
  const floorMaximum = Math.max(0, ...floors.map((floor) => floor.area_m2));
  for (let floorIndex = 0; floorIndex < floors.length; floorIndex += 1) {
    const floor = floors[floorIndex];
    floor.id = 'auto-floor-' + (floorIndex + 1);
    floor.name = generatedFloorLabel(floorIndex);
    floor.confidence = floor.requiresManualWalkable ? 'low' : floor.area_m2 >= floorMaximum * 0.8 ? 'high' : floor.area_m2 >= floorMaximum * 0.4 ? 'medium' : 'low';
    floor.regions = serializeRegions(
      floor.regions,
      mergedOptions,
      'auto-region-' + (floorIndex + 1),
    );
  }

  for (const excluded of excludedFloors) {
    warnings.push(excluded.elevation_m.toFixed(2) + ' m 的屋面/露台疑似已从普通楼层排除（上方结构覆盖 ' + Math.round(excluded.above_coverage * 100) + '%），可定位或显式恢复。');
  }

  if (!floors.length) warnings.push('未找到面积达到门槛的楼面。请检查模型朝向、比例或最小楼层面积设置。');
  emitProgress(requestId, 'complete', 1, '自动分析完成');
  return {
    floors,
    exclusions: excludedFloors,
    excludedFloors,
    holes: [],
    stairs: [],
    doors: [],
    stats: {
      triangleCount: scanned.triangleCount,
      horizontalTriangleCount: scanned.upwardCount,
      cachedTriangleCount: obstacleProjection.candidateTriangles,
      analysisMs: Math.round(performance.now() - started),
    },
    warnings,
    diagnostics: { thresholds },
  };
}

if (typeof self !== 'undefined') {
  self.onmessage = (event) => {
    const { type, requestId, positions, indices, materialIds, materialNames, options } = event.data || {};
    if (type === 'cancel') {
      cancelled.add(requestId);
      return;
    }
    if (type !== 'analyze') return;
    cancelled.delete(requestId);
    try {
      const result = analyzeGeometry(positions, indices, options, requestId, { triangleMaterialIds: materialIds, materialNames });
      if (result) self.postMessage({ type: 'result', requestId, result });
    } catch (error) {
      self.postMessage({ type: 'error', requestId, message: error?.message || '几何分析失败' });
    } finally {
      cancelled.delete(requestId);
    }
  };
}













function triangleIntersectsCell(ax, az, bx, bz, cx, cz, x0, z0, x1, z1) {
  const epsilon = 1e-9;
  if (Math.max(ax, bx, cx) < x0 - epsilon || Math.min(ax, bx, cx) > x1 + epsilon
    || Math.max(az, bz, cz) < z0 - epsilon || Math.min(az, bz, cz) > z1 + epsilon) return false;
  const inside = (x, z) => x >= x0 - epsilon && x <= x1 + epsilon && z >= z0 - epsilon && z <= z1 + epsilon;
  if (inside(ax, az) || inside(bx, bz) || inside(cx, cz)
    || pointInTriangle2D(x0, z0, ax, az, bx, bz, cx, cz)
    || pointInTriangle2D(x1, z0, ax, az, bx, bz, cx, cz)
    || pointInTriangle2D(x1, z1, ax, az, bx, bz, cx, cz)
    || pointInTriangle2D(x0, z1, ax, az, bx, bz, cx, cz)) return true;

  const segmentHitsBox = (px, pz, qx, qz) => {
    let start = 0;
    let end = 1;
    const dx = qx - px;
    const dz = qz - pz;
    const axes = [[px, dx, x0, x1], [pz, dz, z0, z1]];
    for (const [origin, delta, low, high] of axes) {
      if (Math.abs(delta) <= epsilon) {
        if (origin < low - epsilon || origin > high + epsilon) return false;
        continue;
      }
      let near = (low - origin) / delta;
      let far = (high - origin) / delta;
      if (near > far) [near, far] = [far, near];
      start = Math.max(start, near);
      end = Math.min(end, far);
      if (start > end + epsilon) return false;
    }
    return true;
  };
  return segmentHitsBox(ax, az, bx, bz)
    || segmentHitsBox(bx, bz, cx, cz)
    || segmentHitsBox(cx, cz, ax, az);
}

function triangleVerticalSpanInCell(ax, ay, az, bx, by, bz, cx, cy, cz, x0, z0, x1, z1) {
  const epsilon = 1e-9;
  let minY = Infinity;
  let maxY = -Infinity;
  const consider = (x, z, y) => {
    if (x < x0 - epsilon || x > x1 + epsilon || z < z0 - epsilon || z > z1 + epsilon) return;
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  };
  consider(ax, az, ay); consider(bx, bz, by); consider(cx, cz, cy);
  const edge = (px, py, pz, qx, qy, qz) => {
    for (const [boundLow, boundHigh, coordinate] of [[x0, x1, 'x'], [z0, z1, 'z']]) {
      const startValue = coordinate === 'x' ? px : pz;
      const delta = coordinate === 'x' ? qx - px : qz - pz;
      if (Math.abs(delta) <= epsilon) continue;
      for (const value of [boundLow, boundHigh]) {
        const t = (value - startValue) / delta;
        if (t >= -epsilon && t <= 1 + epsilon) consider(px + (qx - px) * t, pz + (qz - pz) * t, py + (qy - py) * t);
      }
    }
  };
  edge(ax, ay, az, bx, by, bz);
  edge(bx, by, bz, cx, cy, cz);
  edge(cx, cy, cz, ax, ay, az);
  const nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay);
  const ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az);
  const nz = (ax - bx) * (cy - by) - (az - bz) * (cx - bx);
  if (Math.abs(ny) > epsilon) {
    for (const x of [x0, (x0 + x1) * 0.5, x1]) {
      for (const z of [z0, (z0 + z1) * 0.5, z1]) {
        if (!pointInTriangle2D(x, z, ax, az, bx, bz, cx, cz)) continue;
        consider(x, z, ay - ((x - ax) * nx + (z - az) * nz) / ny);
      }
    }
  }
  return Number.isFinite(minY) && Number.isFinite(maxY) ? [minY, maxY] : null;
}

function rasterizeObstacleFloors(positions, indices, floors, grid, options, requestId) {
  // Project every triangle into each floor's vertical band in one pass. The
  // previous spatial index stored an 11-number JavaScript array for every
  // triangle in a 3M-triangle model, which used gigabytes of memory before
  // rasterization even started. Direct projection keeps only one Uint8Array
  // per selected floor and reuses the already-rasterized floor surface mask.
  const { cellSize, originX, originZ, width, height, total } = grid;
  const lowOffset = optionNumber(options, 'obstacleLow');
  const highOffset = Math.max(lowOffset + 0.01, optionNumber(options, 'obstacleHigh'));
  const masks = floors.map(() => new Uint8Array(total));
  const triangleCount = Math.floor(indices.length / 3);
  const floorsLength = floors.length;
  const bandLow = new Float64Array(floorsLength);
  const bandHigh = new Float64Array(floorsLength);
  for (let floorIndex = 0; floorIndex < floorsLength; floorIndex += 1) {
    bandLow[floorIndex] = floors[floorIndex].y + lowOffset;
    bandHigh[floorIndex] = floors[floorIndex].y + highOffset;
  }
  let candidateTriangles = 0;

  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    if (isCancelled(requestId)) return null;
    if (triangle % 100000 === 0) {
      const ratio = triangleCount ? triangle / triangleCount : 1;
      emitProgress(requestId, 'obstacle-projection', 0.55 + ratio * 0.07, '正在投影障碍物 ' + triangle.toLocaleString() + ' / ' + triangleCount.toLocaleString());
    }
    const ia = Number(indices[triangle * 3]) * 3;
    const ib = Number(indices[triangle * 3 + 1]) * 3;
    const ic = Number(indices[triangle * 3 + 2]) * 3;
    if (!Number.isInteger(ia) || !Number.isInteger(ib) || !Number.isInteger(ic)
      || ia < 0 || ib < 0 || ic < 0
      || ia + 2 >= positions.length || ib + 2 >= positions.length || ic + 2 >= positions.length) continue;
    const ax = positions[ia], ay = positions[ia + 1], az = positions[ia + 2];
    const bx = positions[ib], by = positions[ib + 1], bz = positions[ib + 2];
    const cx = positions[ic], cy = positions[ic + 1], cz = positions[ic + 2];
    if (![ax, ay, az, bx, by, bz, cx, cy, cz].every(Number.isFinite)) continue;
    const minY = Math.min(ay, by, cy);
    const maxY = Math.max(ay, by, cy);
    let matchesAnyFloor = false;
    for (let floorIndex = 0; floorIndex < floorsLength; floorIndex += 1) {
      if (maxY > bandLow[floorIndex] + 1e-7 && minY < bandHigh[floorIndex] - 1e-7) {
        matchesAnyFloor = true;
        break;
      }
    }
    if (!matchesAnyFloor) continue;
    candidateTriangles += 1;
    const colStart = Math.max(0, Math.floor((Math.min(ax, bx, cx) - originX) / cellSize));
    const colEnd = Math.min(width - 1, Math.floor((Math.max(ax, bx, cx) - originX) / cellSize));
    const rowStart = Math.max(0, Math.floor((Math.min(az, bz, cz) - originZ) / cellSize));
    const rowEnd = Math.min(height - 1, Math.floor((Math.max(az, bz, cz) - originZ) / cellSize));
    if (colEnd < colStart || rowEnd < rowStart) continue;
    for (let floorIndex = 0; floorIndex < floorsLength; floorIndex += 1) {
      if (maxY <= bandLow[floorIndex] + 1e-7 || minY >= bandHigh[floorIndex] - 1e-7) continue;
      const obstacleMask = masks[floorIndex];
      const surfaceMask = floors[floorIndex].mask;
      for (let row = rowStart; row <= rowEnd; row += 1) {
        const rowOffset = row * width;
        for (let col = colStart; col <= colEnd; col += 1) {
          const cell = rowOffset + col;
          const cellX0=originX+col*cellSize, cellZ0=originZ+row*cellSize;
          if (!surfaceMask[cell] || obstacleMask[cell]) continue;
          if (!triangleIntersectsCell(ax,az,bx,bz,cx,cz,cellX0,cellZ0,cellX0+cellSize,cellZ0+cellSize)) continue;
          // Exclude tabletops, chair backs and other low furniture: they float
          // above the lower walking band or stop before its upper half. Full
          // vertical partitions and columns remain structural obstacles.
          const floorY = floors[floorIndex].y;
          const span = triangleVerticalSpanInCell(
            ax, ay, az, bx, by, bz, cx, cy, cz,
            cellX0, cellZ0, cellX0 + cellSize, cellZ0 + cellSize,
          );
          // Recast's span model tests height overlap inside the current column.
          // A sloped roof must not block every projected cell merely because its
          // global triangle bounds crosses the walking band somewhere else.
          if (!span || span[0] > floorY + 0.25 || span[1] < floorY + 0.45) continue;
          obstacleMask[cell] = 1;
        }
      }
    }
  }
  return { masks, candidateTriangles };
}

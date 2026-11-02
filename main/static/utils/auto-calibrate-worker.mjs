const DEFAULTS = {
  horizontalAngleDeg: 15,
  clusterTolerance: 0.06,
  heightBinSize: 0.02,
  minFloorArea: 8,
  minFloorAreaRatio: 0.1,
  minSurfaceArea: 0.5,
  minRegionArea: 2,
  minWalkableWidth: 0.9,
  minStructureGap: 0.08,
  maxStructureGap: 0.18,
  minStructureOverlap: 0.7,
  aboveCoverageMin: 0.7,
  aboveCoverageMax: 2.2,
  minAboveCoverage: 0.5,
  minStoryClearance: 0.9,
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

function scanUpwardTriangles(positions, indices, options, requestId) {
  const triangleCount = Math.floor(indices.length / 3);
  const angle = optionNumber(options, 'horizontalAngleDeg') * Math.PI / 180;
  const minimumNormalY = Math.cos(angle);
  const binSize = Math.max(0.001, optionNumber(options, 'heightBinSize'));
  const bins = new Map();
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
    if (!Number.isInteger(ia) || !Number.isInteger(ib) || !Number.isInteger(ic) || ia < 0 || ib < 0 || ic < 0 || ic + 2 >= positions.length) continue;

    const ax = positions[ia], ay = positions[ia + 1], az = positions[ia + 2];
    const bx = positions[ib], by = positions[ib + 1], bz = positions[ib + 2];
    const cx = positions[ic], cy = positions[ic + 1], cz = positions[ic + 2];
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
    const key = Math.floor(y / binSize + 1e-7);
    let bin = bins.get(key);
    if (!bin) {
      bin = { key, count: 0, area: 0, weightedY: 0, triangleIds: [], upwardCount: 0, downwardCount: 0 };
      bins.set(key, bin);
    }
    bin.count += 1;
    bin.area += projectedArea;
    bin.weightedY += y * projectedArea;
    bin.triangleIds.push(triangle);
    if (isUpward) {
      bin.upwardCount += 1;
      upwardCount += 1;
    } else {
      bin.downwardCount += 1;
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
    .map((bin) => ({ ...bin, y: bin.weightedY / Math.max(bin.area, 1e-12) }))
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
    previous.triangleIds = previous.triangleIds.concat(bin.triangleIds);
  }

  return layers.map((layer) => ({
    ...layer,
    y: layer.weightedY / Math.max(layer.area, 1e-12),
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

function selectFloorLayers(layers, options, grid) {
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
  const structuralTops = candidates
    .filter((layer) => upperToLower.has(layer))
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

  // Roof removal is opt-in for building-sized inputs. Small synthetic models
  // must preserve an exposed top floor even when no surface is above it.
  let removedRoof = null;
  if (optionNumber(options, 'removeRoof') > 0 && structuralTops.length >= 2) {
    const highestStructural = structuralTops[structuralTops.length - 1];
    if (coverage.get(highestStructural) < optionNumber(options, 'minAboveCoverage')) {
      ordered = ordered.filter((layer) => layer !== highestStructural);
      removedRoof = {
        elevation_m: Number(highestStructural.y.toFixed(4)),
        above_coverage: Number(Number(coverage.get(highestStructural) || 0).toFixed(3)),
      };
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
  return { selected, lowerToUpper, upperToLower, removedRoof };
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

function smoothClosedRing(ring, tolerance) {
  if (ring.length <= 4) return ring;
  const points = ring.slice(0, -1);
  // Douglas-Peucker on a closed loop. The first point is duplicated at the
  // end, so split at the farthest point and simplify both halves. Keeping the
  // shared endpoints gives us a valid closed polygon without dropping corners.
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
  const simplified = simplifyOpen(firstHalf).slice(0, -1)
    .concat(simplifyOpen(secondHalf));
  if (simplified.length < 4) return ring;
  return simplified;
}

function traceBoundaryLoops(mask, width, height, originX, originZ, cellSize) {
  const outgoing = new Map();
  const addEdge = (x0, z0, x1, z1) => {
    const from = z0 * (width + 1) + x0;
    const to = z1 * (width + 1) + x1;
    const edge = { from, to, x0, z0, x1, z1, angle: Math.atan2(z1 - z0, x1 - x0), successor: null };
    const list = outgoing.get(from);
    if (list) list.push(edge);
    else outgoing.set(from, [edge]);
    return edge;
  };
  const edges = [];
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      if (!mask[row * width + col]) continue;
      if (row === 0 || !mask[(row - 1) * width + col]) edges.push(addEdge(col + 1, row, col, row));
      if (row + 1 >= height || !mask[(row + 1) * width + col]) edges.push(addEdge(col, row + 1, col + 1, row + 1));
      if (col === 0 || !mask[row * width + col - 1]) edges.push(addEdge(col, row, col, row + 1));
      if (col + 1 >= width || !mask[row * width + col + 1]) edges.push(addEdge(col + 1, row + 1, col + 1, row));
    }
  }

  const twoPi = Math.PI * 2;
  for (const edge of edges) {
    const options = outgoing.get(edge.to) || [];
    let best = null;
    let bestTurn = Infinity;
    for (const candidate of options) {
      let turn = ((edge.angle - candidate.angle) % twoPi + twoPi) % twoPi;
      if (turn < 1e-9) turn = twoPi;
      if (turn < bestTurn) {
        bestTurn = turn;
        best = candidate;
      }
    }
    edge.successor = best;
  }

  const visited = new Set();
  const loops = [];
  for (const start of edges) {
    if (visited.has(start)) continue;
    const loop = [];
    let edge = start;
    let guard = 0;
    while (edge && !visited.has(edge) && guard <= edges.length + 1) {
      visited.add(edge);
      loop.push([originX + edge.x0 * cellSize, originZ + edge.z0 * cellSize]);
      edge = edge.successor;
      guard += 1;
    }
    if (loop.length < 4) continue;
    const first = loop[0];
    const last = loop[loop.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      const startVertex = start.from;
      const startCol = startVertex % (width + 1);
      const startRow = Math.floor(startVertex / (width + 1));
      loop.push([originX + startCol * cellSize, originZ + startRow * cellSize]);
    }
    if (loop.length >= 4) loops.push(loop);
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

function ringInteriorPoint(ring, cellSize) {
  const points = ring.slice(0, -1);
  if (!points.length) return ring[0];
  let best = null;
  let bestScore = -Infinity;
  for (const x of new Set(points.map((point) => point[0]))) {
    for (const z of new Set(points.map((point) => point[1]))) {
      const probe = [x + cellSize * 0.25, z + cellSize * 0.25];
      if (!pointInRing(probe[0], probe[1], ring)) continue;
      let clearance = Infinity;
      for (let index = 0; index < ring.length - 1; index += 1) {
        const ax = ring[index][0], az = ring[index][1], bx = ring[index + 1][0], bz = ring[index + 1][1];
        const dx = bx - ax, dz = bz - az;
        const length = Math.hypot(dx, dz);
        if (length <= 1e-12) continue;
        clearance = Math.min(clearance, Math.abs(dz * probe[0] - dx * probe[1] + bx * az - ax * bz) / length);
      }
      if (clearance > bestScore) { bestScore = clearance; best = probe; }
    }
  }
  if (best) return best;
  const fallback = insidePointForLoop(ring, cellSize);
  return pointInRing(fallback[0], fallback[1], ring) ? fallback : [0, 0];
}

function ringEdgesIntersect(a, b) {
  const orientation = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const onSegment = (p, q, r) => Math.min(p[0], r[0]) - 1e-9 <= q[0] && q[0] <= Math.max(p[0], r[0]) + 1e-9
    && Math.min(p[1], r[1]) - 1e-9 <= q[1] && q[1] <= Math.max(p[1], r[1]) + 1e-9;
  for (let i = 0; i < a.length - 1; i += 1) {
    for (let j = 0; j < b.length - 1; j += 1) {
      const p1 = a[i], q1 = a[i + 1], p2 = b[j], q2 = b[j + 1];
      const o1 = orientation(p1, q1, p2), o2 = orientation(p1, q1, q2);
      const o3 = orientation(p2, q2, p1), o4 = orientation(p2, q2, q1);
      if (Math.abs(o1) <= 1e-9 && onSegment(p1, p2, q1)) return true;
      if (Math.abs(o2) <= 1e-9 && onSegment(p1, q2, q1)) return true;
      if (Math.abs(o3) <= 1e-9 && onSegment(p2, p1, q2)) return true;
      if (Math.abs(o4) <= 1e-9 && onSegment(p2, q1, q2)) return true;
      if ((o1 > 1e-9) !== (o2 > 1e-9) && (o3 > 1e-9) !== (o4 > 1e-9)) return true;
    }
  }
  return false;
}

function validHoleInsideOuter(hole, outer) {
  if (hole.length < 4) return false;
  const probe = ringInteriorPoint(hole, 0.01);
  if (!pointInRing(probe[0], probe[1], outer)) return false;
  if (ringEdgesIntersect(hole, outer)) return false;
  return hole.slice(0, -1).every((point) => pointInRing(point[0], point[1], outer));
}

function extractExteriorAndHoles(loops, cellSize, tolerance) {
  if (!loops.length) return null;
  let outerIndex = 0;
  for (let index = 1; index < loops.length; index += 1) {
    if (Math.abs(ringArea(loops[index])) > Math.abs(ringArea(loops[outerIndex]))) outerIndex = index;
  }
  const outer = forceRingWinding(smoothClosedRing(loops[outerIndex], tolerance), true);
  if (outer.length < 4) return null;
  const holes = [];
  for (let index = 0; index < loops.length; index += 1) {
    if (index === outerIndex) continue;
    const probe = ringInteriorPoint(loops[index], cellSize);
    if (!pointInRing(probe[0], probe[1], outer)) continue;
    const hole = forceRingWinding(smoothClosedRing(loops[index], tolerance), false);
    if (!validHoleInsideOuter(hole, outer)) continue;
    if (holes.some((existing) => ringEdgesIntersect(existing, hole)
      || hole.some((point) => pointInRing(point[0], point[1], existing))
      || existing.some((point) => pointInRing(point[0], point[1], hole)))) continue;
    holes.push(hole);
  }
  return { outer, holes };
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

function createRegions(layer, options, grid, obstacles = null, debugLabel = '') {
  const { cellSize, originX, originZ, width, height } = grid;
  const freeMask = new Uint8Array(grid.total);
  for (let index = 0; index < layer.mask.length; index += 1) {
    if (layer.mask[index] && !obstacles?.[index]) freeMask[index] = 1;
  }
  const labelled = labelComponents(freeMask, width, height);
  const minimumCells = Math.max(1, Math.ceil(optionNumber(options, 'minRegionArea') / (cellSize * cellSize)));
  const minimumWidth = optionNumber(options, 'minWalkableWidth');
  const tolerance = optionNumber(options, 'simplifyTolerance');
  const radius = Math.max(1, Math.round(minimumWidth * 0.5 / cellSize));
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

    // Width is a diagnostic, not a reason to discard an entire floor. The
    // existing doors can be narrower than the default person radius. Erosion is
    // used only to quantify which parts of a component are cut off by necks.
    const eroded = erodeMask(mask, width, height, radius);
    const erodedLabels = labelComponents(eroded, width, height);
    const largestEroded = erodedLabels.components.sort((a, b) => b.cells.length - a.cells.length)[0];
    let cutWidthArea = 0;
    if (!largestEroded) cutWidthArea = component.cells.length;
    else if (erodedLabels.components.length > 1) {
      cutWidthArea = erodedLabels.components.slice(1).reduce((sum, part) => sum + part.cells.length, 0);
    } else if (minWidth + 1e-9 < minimumWidth) {
      cutWidthArea = component.cells.length;
    }

    const loops = traceBoundaryLoops(mask, width, height, originX, originZ, cellSize);
    if (!loops.length) {
      if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'no-loop', component.cells.length * cellSize * cellSize);
      continue;
    }
    const contour = extractExteriorAndHoles(loops, cellSize, tolerance);
    if (!contour) {
      if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'no-outer', loops.length);
      continue;
    }
    const area = component.cells.length * cellSize * cellSize;
    const contourOuterArea = Math.abs(ringArea(contour.outer));
    const holeArea = contour.holes.reduce((sum, hole) => sum + Math.abs(ringArea(hole)), 0);
    const contourRatio = contourOuterArea > 0 ? Math.max(0, contourOuterArea - holeArea) / area : 0;
    if (contourRatio < 0.8 || contourRatio > 1.25) {
      if (debug) console.log('AUTO_REGION_REJECT', debugLabel, 'contour', contourRatio, area, loops.length, contour.outer.length);
      continue;
    }
    const cutWidthAreaM2 = minWidth + 1e-9 < minimumWidth ? cutWidthArea * cellSize * cellSize : 0;
    const warnings = [];
    const personDiameter = optionNumber(options, 'minPersonDiameter');
    const physicallyBlocked = minWidth + 1e-9 < personDiameter;
    if (physicallyBlocked) {
      warnings.push('检测到约 ' + minWidth.toFixed(2) + ' m 的窄口，小于人员直径 ' + personDiameter.toFixed(2) + ' m；该连通分量不可作为自动通行区域接受，请重新绘制或人工确认。');
    } else if (minWidth + 1e-9 < minimumWidth) {
      warnings.push('检测到约 ' + minWidth.toFixed(2) + ' m 的窄口，小于推荐通行宽度 ' + minimumWidth.toFixed(2) + ' m，但大于人员直径 ' + personDiameter.toFixed(2) + ' m；请人工核对是否需要保留。');
    }
    regions.push({
      outer: contour.outer,
      holes: contour.holes,
      area,
      minWidth,
      cutWidthAreaM2,
      splitComponents: widthResult.splitComponents,
      minWidthIsEstimate: Boolean(widthResult.isEstimate),
      physicallyBlocked,
      warnings,
      confidence: physicallyBlocked ? 'low' : minWidth >= Math.max(1.2, minimumWidth * 1.5) ? 'high' : 'medium',
    });
  }

  return regions;
}
function generatedFloorLabel(index) {
  const names = ['一层', '二层', '三层', '四层', '五层', '六层', '七层', '八层'];
  return names[index] || ('第' + (index + 1) + '层');
}

export function analyzeGeometry(positions, indices, options = {}, requestId = 'local') {
  const mergedOptions = { ...DEFAULTS, ...options };
  const thresholds = {
    min_walkable_width_m: optionNumber(mergedOptions, 'minWalkableWidth'),
    min_person_diameter_m: optionNumber(mergedOptions, 'minPersonDiameter'),
    min_above_coverage: optionNumber(mergedOptions, 'minAboveCoverage'),
    min_story_clearance_m: optionNumber(mergedOptions, 'minStoryClearance'),
  };
  const started = performance.now();
  if (!positions || !indices || indices.length < 3) {
    return { floors: [], holes: [], stairs: [], doors: [], stats: { triangleCount: 0, horizontalTriangleCount: 0, analysisMs: 0 }, warnings: ['模型中没有可分析的三角网格。'], diagnostics: { thresholds } };
  }

  const scanned = scanUpwardTriangles(positions, indices, mergedOptions, requestId);
  if (!scanned) return null;
  const layers = clusterHeightLayers(scanned.bins, mergedOptions);
  emitProgress(requestId, 'floor-clustering', 0.36, '发现 ' + layers.length + ' 个朝上高度层');

  if (!layers.length || !scanned.bounds) {
    return {
      floors: [], holes: [], stairs: [], doors: [],
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
  if (!Object.prototype.hasOwnProperty.call(options, 'removeRoof') && grid.total >= 25000) {
    mergedOptions.removeRoof = 1;
  }
  const structural = selectFloorLayers(rasterized, mergedOptions, grid);
  const selected = structural.selected;
  const removedRoof = structural.removedRoof;
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
  const projectionStarted = performance.now();
  const obstacleProjection = rasterizeObstacleFloors(positions, indices, eligible, grid, mergedOptions, requestId);
  if (!obstacleProjection) return null;
  const obstacleMs = Math.round(performance.now() - projectionStarted);

  for (let floorIndex = 0; floorIndex < eligible.length; floorIndex += 1) {
    const layer = eligible[floorIndex];
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
    if (!regions.length) {
      warnings.push(Number(layer.y.toFixed(2)) + ' m 层未提取到可走区，可能是楼面被障碍完全覆盖或网格不完整。');
      continue;
    }
    const area = regions.reduce((sum, region) => sum + region.area, 0);
    if (area < minimumFloorArea) continue;

    const floorWarnings = [];
    if (layer.platformSuspected) floorWarnings.push('该水平面未匹配到可靠的下方结构板，可能是平台、家具顶面或屋面。');
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
      confidence: 'medium',
      regions,
    });
  }

  floors.sort((a, b) => a.elevation_m - b.elevation_m);
  const floorMaximum = Math.max(0, ...floors.map((floor) => floor.area_m2));
  for (let floorIndex = 0; floorIndex < floors.length; floorIndex += 1) {
    const floor = floors[floorIndex];
    floor.id = 'auto-floor-' + (floorIndex + 1);
    floor.name = generatedFloorLabel(floorIndex);
    floor.confidence = floor.area_m2 >= floorMaximum * 0.8 ? 'high' : floor.area_m2 >= floorMaximum * 0.4 ? 'medium' : 'low';
    for (let regionIndex = 0; regionIndex < floor.regions.length; regionIndex += 1) {
      const region = floor.regions[regionIndex];
      floor.regions[regionIndex] = {
        id: 'auto-region-' + (floorIndex + 1) + '-' + (regionIndex + 1),
        outer: region.outer,
        holes: region.holes,
        area_m2: Number(region.area.toFixed(2)),
        min_width_m: Number(region.minWidth.toFixed(2)),
        min_width_is_estimate: Boolean(region.minWidthIsEstimate),
        min_width_split_components: Number(region.splitComponents || 0),
        width_cut_area_m2: Number(region.cutWidthAreaM2.toFixed(2)),
        hole_count: Array.isArray(region.holes) ? region.holes.length : 0,
        near_width_limit: Number(region.minWidth) + 1e-9 < optionNumber(mergedOptions, 'minWalkableWidth'),
        physically_blocked: Boolean(region.physicallyBlocked),
        width_cut_warning: Array.isArray(region.warnings) && region.warnings.length > 0,
        warnings: region.warnings,
        confidence: region.confidence,
      };
    }
  }

  if (removedRoof) {
    warnings.push('已将 ' + removedRoof.elevation_m.toFixed(2) + ' m 的水平面按屋面排除（上方结构覆盖 ' + Math.round(removedRoof.above_coverage * 100) + '%）。若该层实际为露台或开放式楼层，请在手动绘制中恢复。');
  }
  if (!floors.length) warnings.push('未找到面积达到门槛的楼面。请检查模型朝向、比例或最小楼层面积设置。');
  emitProgress(requestId, 'complete', 1, '自动分析完成');
  return {
    floors,
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
    const { type, requestId, positions, indices, options } = event.data || {};
    if (type === 'cancel') {
      cancelled.add(requestId);
      return;
    }
    if (type !== 'analyze') return;
    cancelled.delete(requestId);
    try {
      const result = analyzeGeometry(positions, indices, options, requestId);
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
          const triangleTop = Math.max(ay, by, cy);
          const triangleBottom = Math.min(ay, by, cy);
          if (triangleBottom > floorY + 0.25 || triangleTop < floorY + 0.45) continue;
          obstacleMask[cell] = 1;
        }
      }
    }
  }
  return { masks, candidateTriangles };
}

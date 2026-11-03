const WORKER_URL = '/static/utils/auto-calibrate-worker.mjs?v=20261103b';

function transformVertex(attribute, index, elements) {
  // Position accessors may be interleaved with normals or UVs and may use
  // normalized integer components (KHR_mesh_quantization). BufferAttribute
  // already handles both cases; indexing its raw array directly corrupts every
  // vertex after the first one for an interleaved accessor.
  const x = attribute.getX(index), y = attribute.getY(index), z = attribute.getZ(index);
  return [
    elements[0] * x + elements[4] * y + elements[8] * z + elements[12],
    elements[1] * x + elements[5] * y + elements[9] * z + elements[13],
    elements[2] * x + elements[6] * y + elements[10] * z + elements[14],
  ];
}

/**
 * Flatten the loaded Three.js scene into world-space triangles. The worker
 * should never need to know about Three.js objects or model hierarchy.
 */
function finiteNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function textureFingerprint(texture) {
  if (!texture) return null;
  const image = texture.image || {};
  const source = texture.source?.data || image;
  const name = String(texture.name || image.name || '');
  const sourceId = String(texture.userData?.sourceId || source?.src || source?.url || '');
  const width = finiteNumber(source?.width ?? image.width, 0);
  const height = finiteNumber(source?.height ?? image.height, 0);
  return [
    name,
    sourceId,
    width,
    height,
    finiteNumber(texture.offset?.x, 0).toFixed(6),
    finiteNumber(texture.offset?.y, 0).toFixed(6),
    finiteNumber(texture.repeat?.x, 1).toFixed(6),
    finiteNumber(texture.repeat?.y, 1).toFixed(6),
    finiteNumber(texture.rotation, 0).toFixed(6),
    finiteNumber(texture.center?.x, 0).toFixed(6),
    finiteNumber(texture.center?.y, 0).toFixed(6),
    String(texture.wrapS ?? ''),
    String(texture.wrapT ?? ''),
    String(texture.colorSpace || ''),
    String(texture.channel ?? ''),
  ].join('|');
}

/**
 * Build a stable material key from render-relevant properties. Three.js
 * assigns a new UUID when the same GLB material is loaded again, so UUIDs
 * cannot identify semantic materials across repeated analyses.
 */
export function materialSemanticFingerprint(material) {
  if (!material) return 'none';
  const color = material.color || {};
  const emissive = material.emissive || {};
  const maps = {};
  for (const key of [
    'map', 'alphaMap', 'aoMap', 'bumpMap', 'normalMap', 'roughnessMap',
    'metalnessMap', 'emissiveMap', 'displacementMap', 'lightMap',
  ]) {
    maps[key] = textureFingerprint(material[key]);
  }
  return JSON.stringify({
    name: String(material.name || ''),
    type: String(material.type || material.constructor?.name || ''),
    color: [color.r, color.g, color.b].map((value) => finiteNumber(value, 0).toFixed(6)),
    roughness: finiteNumber(material.roughness, 1).toFixed(6),
    metalness: finiteNumber(material.metalness, 0).toFixed(6),
    opacity: finiteNumber(material.opacity, 1).toFixed(6),
    transparent: Boolean(material.transparent),
    alphaTest: finiteNumber(material.alphaTest, 0).toFixed(6),
    side: String(material.side ?? ''),
    depthWrite: Boolean(material.depthWrite),
    depthTest: Boolean(material.depthTest),
    vertexColors: Boolean(material.vertexColors),
    emissive: [emissive.r, emissive.g, emissive.b].map((value) => finiteNumber(value, 0).toFixed(6)),
    emissiveIntensity: finiteNumber(material.emissiveIntensity, 1).toFixed(6),
    maps,
  });
}
export function collectWorldGeometry(root) {
  if (!root) throw new Error('建筑模型尚未加载完成');
  root.updateMatrixWorld(true);
  const meshes = [];
  let vertexCount = 0;
  let indexCount = 0;
  root.traverseVisible((object) => {
    const position = object.isMesh && object.geometry?.attributes?.position;
    if (!position || !position.count) return;
    const indices = object.geometry.index;
    const triangles = indices ? Math.floor(indices.count / 3) : Math.floor(position.count / 3);
    if (!triangles) return;
    meshes.push({ object, position, indices, triangles });
    vertexCount += position.count;
    indexCount += triangles * 3;
  });
  if (!meshes.length) throw new Error('模型中没有可分析的三角网格');

  const positions = new Float32Array(vertexCount * 3);
  const indices = new Uint32Array(indexCount);
  const triangleMaterialIds = new Int32Array(indexCount / 3);
  triangleMaterialIds.fill(-1);
  const materialNames = [];
  const materialIndex = new Map();
  const resolveMaterial = (material) => {
    if (!material) return -1;
    const key = materialSemanticFingerprint(material);
    if (!materialIndex.has(key)) {
      materialIndex.set(key, materialNames.length);
      materialNames.push(String(material.name || material.uuid || ('材质 ' + (materialNames.length + 1))));
    }
    return materialIndex.get(key);
  };
  let vertexOffset = 0;
  let indexOffset = 0;
  for (const { object, position, indices: sourceIndices, triangles } of meshes) {
    const matrix = object.matrixWorld.elements;
    const mirrored = matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
      - matrix[4] * (matrix[1] * matrix[10] - matrix[2] * matrix[9])
      + matrix[8] * (matrix[1] * matrix[6] - matrix[2] * matrix[5]) < 0;
    for (let vertex = 0; vertex < position.count; vertex += 1) {
      const value = transformVertex(position, vertex, matrix);
      const target = (vertexOffset + vertex) * 3;
      positions[target] = value[0];
      positions[target + 1] = value[1];
      positions[target + 2] = value[2];
    }
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    const materialIds = materials.map(resolveMaterial);
    const groups = Array.isArray(object.geometry.groups) ? object.geometry.groups : [];
    for (let triangle = 0; triangle < triangles; triangle += 1) {
      const a = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3) : triangle * 3);
      const b = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3 + 1) : triangle * 3 + 1);
      const c = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3 + 2) : triangle * 3 + 2);
      const target = indexOffset + triangle * 3;
      const start = triangle * 3;
      const group = groups.find((item) => start >= item.start && start < item.start + item.count);
      triangleMaterialIds[indexOffset / 3 + triangle] = group ? (materialIds[group.materialIndex] ?? -1) : (materialIds[0] ?? -1);
      indices[target] = a;
      indices[target + 1] = mirrored ? c : b;
      indices[target + 2] = mirrored ? b : c;
    }
    vertexOffset += position.count;
    indexOffset += triangles * 3;
  }
  return { positions, indices, materialIds: triangleMaterialIds, materialNames, triangleCount: indexCount / 3, vertexCount };
}

export function analyzeInWorker(payload, onProgress) {
  const worker = new Worker(WORKER_URL, { type: 'module' });
  const requestId = `auto-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  let settled = false;
  let resolveResult;
  let rejectResult;
  const promise = new Promise((resolve, reject) => {
    resolveResult = resolve;
    rejectResult = reject;
  });
  const finish = (callback, value) => {
    if (settled) return;
    settled = true;
    worker.terminate();
    callback(value);
  };
  worker.onmessage = (event) => {
    const message = event.data || {};
    if (message.requestId !== requestId) return;
    if (message.type === 'progress') onProgress?.(message);
    else if (message.type === 'result') finish(resolveResult, message.result);
    else if (message.type === 'error') finish(rejectResult, new Error(message.message || '自动分析失败'));
  };
  worker.onerror = (event) => finish(rejectResult, new Error(event.message || '自动分析 Worker 失败'));
  const transfer = [payload.positions.buffer, payload.indices.buffer];
  if (payload.materialIds?.buffer) transfer.push(payload.materialIds.buffer);
  worker.postMessage({
    type: 'analyze', requestId,
    positions: payload.positions, indices: payload.indices,
    materialIds: payload.materialIds || null,
    materialNames: Array.isArray(payload.materialNames) ? payload.materialNames : [],
    options: payload.options || {},
  }, transfer);
  return {
    promise,
    cancel() {
      if (settled) return;
      worker.postMessage({ type: 'cancel', requestId });
      finish(rejectResult, new DOMException('自动分析已取消', 'AbortError'));
    },
  };
}

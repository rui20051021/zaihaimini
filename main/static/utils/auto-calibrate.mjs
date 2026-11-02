const WORKER_URL = '/static/utils/auto-calibrate-worker.mjs?v=20261102d';

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
    for (let triangle = 0; triangle < triangles; triangle += 1) {
      const a = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3) : triangle * 3);
      const b = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3 + 1) : triangle * 3 + 1);
      const c = vertexOffset + (sourceIndices ? sourceIndices.getX(triangle * 3 + 2) : triangle * 3 + 2);
      const target = indexOffset + triangle * 3;
      indices[target] = a;
      indices[target + 1] = mirrored ? c : b;
      indices[target + 2] = mirrored ? b : c;
    }
    vertexOffset += position.count;
    indexOffset += triangles * 3;
  }
  return { positions, indices, triangleCount: indexCount / 3, vertexCount };
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
  worker.postMessage({ type: 'analyze', requestId, positions: payload.positions, indices: payload.indices, options: payload.options || {} }, transfer);
  return {
    promise,
    cancel() {
      if (settled) return;
      worker.postMessage({ type: 'cancel', requestId });
      finish(rejectResult, new DOMException('自动分析已取消', 'AbortError'));
    },
  };
}

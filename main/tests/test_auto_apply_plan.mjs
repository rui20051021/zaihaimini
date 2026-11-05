import assert from 'node:assert/strict';

import {
  AUTO_MATCH_TOLERANCE_M,
  AUTO_NAV_BOUNDARY_TOLERANCE_M,
  applyAutoApplyPlan,
  createAutoApplyPlan,
  inspectAutoApplyNavigation,
} from '../static/utils/auto-apply-plan.mjs';

const failures = [];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function ring(x0, z0, x1, z1) {
  return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]];
}

function walkable(id, offset = 0) {
  return { id, outer: ring(offset, 0, offset + 2, 1), holes: [] };
}

function makeDraft() {
  return {
    schema: 2,
    id: 'auto-apply-fixture',
    floors: [
      { id: 'floor-1', name: '一层', elevation_m: 0.12, walkable: [walkable('floor-1-room', 0)], spawn_zones: [{ id: 'spawn-1', outer: ring(0.2, 0.2, 0.8, 0.8) }] },
      { id: 'floor-2', name: '二层', elevation_m: 1.32, walkable: [walkable('floor-2-room', 3)], spawn_zones: [{ id: 'spawn-2', outer: ring(3.2, 0.2, 3.8, 0.8) }] },
      { id: 'floor-3', name: '三层', elevation_m: 2.52, walkable: [walkable('floor-3-room', 6)], spawn_zones: [{ id: 'spawn-3', outer: ring(6.2, 0.2, 6.8, 0.8) }] },
    ],
    doors: [
      { id: 'door-1', floor: 'floor-1', position: [1, 0.12, 1] },
      { id: 'door-2', floor: 'floor-2', position: [4, 1.32, 1] },
      { id: 'door-3', floor: 'floor-3', position: [7, 2.52, 1] },
    ],
    exits: [
      { id: 'exit-1', floor: 'floor-1', position: [0, 0.12, 0] },
      { id: 'exit-2', floor: 'floor-2', position: [3, 1.32, 0] },
    ],
    stairs: [
      {
        id: 'stair-1-2', from_floor: 'floor-1', to_floor: 'floor-2',
        bottom: [1, 0.12, 2], top: [1, 1.32, 2],
        path: [[1, 0.12, 2], [1, 0.72, 2], [1, 1.32, 2]],
      },
      {
        id: 'stair-2-3', from_floor: 'floor-2', to_floor: 'floor-3',
        bottom: [4, 1.32, 2], top: [4, 2.52, 2],
        path: [[4, 1.32, 2], [4, 1.92, 2], [4, 2.52, 2]],
      },
    ],
    navigation_review: { status: 'reviewed', message: 'fixture reviewed' },
  };
}


function makeNavigableDraft() {
  const draft = makeDraft();
  draft.doors = [];
  draft.exits = [{ id: 'exit-1', floor: 'floor-1', position: [0.5, 0.12, 0.5] }];
  draft.stairs = [
    {
      id: 'stair-1-2', from_floor: 'floor-1', to_floor: 'floor-2',
      bottom: [1, 0.12, 0.5], top: [3.5, 1.32, 0.5],
      path: [[1, 0.12, 0.5], [3.5, 1.32, 0.5]],
    },
    {
      id: 'stair-2-3', from_floor: 'floor-2', to_floor: 'floor-3',
      bottom: [3.5, 1.32, 0.5], top: [6.5, 2.52, 0.5],
      path: [[3.5, 1.32, 0.5], [6.5, 2.52, 0.5]],
    },
  ];
  return draft;
}

function candidate(id, elevation, { accepted = true, replaceExisting = false, regions = [] } = {}) {
  return {
    id,
    candidateId: id,
    elevation_m: elevation,
    accepted,
    replaceExisting,
    regions: regions.map((item, index) => ({
      id: item.id || `${id}-region-${index + 1}`,
      accepted: item.accepted !== false,
      outer: item.outer,
      holes: item.holes || [],
    })),
  };
}

function run(name, callback) {
  try {
    callback();
    console.log(`PASS ${name}`);
  } catch (error) {
    failures.push({ name, error });
    console.error(`FAIL ${name}`);
    console.error(error.stack || String(error));
  }
}

run('竞争候选一对一匹配，同一楼层只分配给最接近的候选', () => {
  const draft = makeDraft();
  const candidates = [
    candidate('candidate-far', 1.45, { accepted: false }),
    candidate('candidate-near', 1.28, { accepted: false }),
  ];
  const plan = createAutoApplyPlan(draft, candidates);
  const assignedToSecondFloor = plan.matches.filter((match) => match.floorId === 'floor-2');

  assert.equal(AUTO_MATCH_TOLERANCE_M, 0.25);
  assert.equal(assignedToSecondFloor.length, 1, '一个现有楼层只能被一个候选正式匹配');
  assert.equal(assignedToSecondFloor[0].candidateIndex, 1, '应把最接近 1.32m 的候选分配给二层');
  assert.equal(plan.matches[1].contested, true);
  assert.deepEqual(plan.matches[1].competingCandidateIndexes, [0]);
  assert.equal(plan.matches[0].floorId, null, '竞争失败的候选不能复用同一楼层');
  assert.deepEqual([...new Set(plan.operations.map((operation) => operation.targetId))], [], '未接受的竞争候选不应产生操作');
});

run('已接受候选竞争同一现有楼层时，落选候选不会创建新楼层', () => {
  const draft = makeDraft();
  draft.floors = [draft.floors[0]];
  draft.floors[0].elevation_m = 1.00;
  draft.doors = [];
  draft.exits = [];
  draft.stairs = [];
  const plan = createAutoApplyPlan(draft, [
    candidate('near', 0.90, { replaceExisting: true, regions: [{ outer: ring(10, 0, 12, 1) }] }),
    candidate('far', 1.10, { replaceExisting: true, regions: [{ outer: ring(13, 0, 15, 1) }] }),
  ]);

  assert.equal(plan.skippedCompetition, 1);
  assert.equal(plan.matches[0].competitionStatus, 'won');
  assert.equal(plan.matches[1].competitionStatus, 'lost');
  assert.deepEqual(plan.operations.map((operation) => [operation.kind, operation.targetId]), [['replace', 'floor-1']]);

  const result = applyAutoApplyPlan(draft, plan);
  assert.equal(result.draft.floors.length, 1, '竞争落选候选不能创建 floor-2');
  assert.equal(result.draft.floors[0].id, 'floor-1');
  assert.equal(result.addedFloors, 0);
  assert.equal(result.skippedCompetition, 1);
});

run('第四层 3.72m 正确创建楼层和首个自动可行区', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('fourth-floor', 3.72, {
      regions: [{ outer: ring(9, 0, 12, 4) }],
    }),
  ]);
  const operation = plan.operations[0];

  assert.equal(operation.kind, 'create');
  assert.equal(operation.targetId, 'floor-4');
  assert.equal(operation.targetName, '四层');

  const result = applyAutoApplyPlan(draft, plan);
  const fourth = result.draft.floors.find((floor) => floor.id === 'floor-4');
  assert.ok(fourth, '应创建第四层');
  assert.equal(fourth.elevation_m, 3.72);
  assert.equal(fourth.name, '四层');
  assert.equal(fourth.walkable.length, 1);
  assert.equal(fourth.walkable[0].id, 'floor-4-auto-region-1');
  assert.deepEqual(fourth.walkable[0].outer, ring(9, 0, 12, 4));
  assert.equal(result.addedFloors, 1);
  assert.equal(result.addedRegions, 1);
  assert.deepEqual(result.emptyFloorIds, []);
});

run('未勾选替换时保留现有可行区、起始区和自动区域', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-1-refresh', 0.12, {
      replaceExisting: false,
      regions: [{ outer: ring(10, 10, 13, 13) }],
    }),
  ]);

  assert.equal(plan.operations[0].kind, 'update');
  assert.equal(plan.operations[0].skippedRegions, 1);
  assert.equal(plan.skippedExisting, 1);

  const result = applyAutoApplyPlan(draft, plan);
  assert.equal(result.changed, false);
  assert.deepEqual(result.draft, before, '跳过替换时整个草稿应保持原样');
  assert.equal(result.draft.floors[0].walkable[0].id, 'floor-1-room');
  assert.equal(result.draft.floors[0].spawn_zones[0].id, 'spawn-1');
});

run('明确替换无出口和跨层楼梯的楼层时清理区域、起始区和门', () => {
  const draft = makeDraft();
  draft.exits = [];
  draft.stairs = [];
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-1-replace', 0.12, {
      replaceExisting: true,
      regions: [{ outer: ring(10, 10, 13, 13) }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);
  const first = result.draft.floors.find((floor) => floor.id === 'floor-1');
  const second = result.draft.floors.find((floor) => floor.id === 'floor-2');
  const third = result.draft.floors.find((floor) => floor.id === 'floor-3');

  assert.equal(plan.operations[0].kind, 'replace');
  assert.equal(result.replacedFloors, 1);
  assert.deepEqual(first.spawn_zones, [], '目标楼层起始区应清空');
  assert.deepEqual(first.walkable.map((region) => region.outer), [ring(10, 10, 13, 13)]);
  assert.deepEqual(second.walkable.map((region) => region.id), ['floor-2-room']);
  assert.deepEqual(third.walkable.map((region) => region.id), ['floor-3-room']);
  assert.deepEqual(result.draft.doors.map((door) => door.id), ['door-2', 'door-3']);
  assert.deepEqual(result.draft.exits, []);
  assert.deepEqual(result.draft.stairs, []);
  assert.equal(result.draft.annotation_recheck.status, 'needs-recheck');
  assert.equal(result.draft.navigation_review.status, 'needs-review');
});

run('明确替换含出口的楼层时原子拒绝并保留整份草稿', () => {
  const draft = makeDraft();
  draft.stairs = [];
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-1-exit-replace', 0.12, {
      replaceExisting: true,
      regions: [{ outer: ring(10, 10, 13, 13) }],
    }),
  ]);

  assert.throws(
    () => applyAutoApplyPlan(draft, plan),
    /自动替换已拒绝.*出口|出口.*现有草稿未修改/,
  );
  assert.deepEqual(draft, before, '替换被拒绝后不能删除出口、门、起始区或任何区域');
});

run('明确替换含跨层楼梯端点的楼层时原子拒绝并保留整份草稿', () => {
  const draft = makeDraft();
  draft.exits = [];
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-2-stair-replace', 1.32, {
      replaceExisting: true,
      regions: [{ outer: ring(10, 10, 13, 13) }],
    }),
  ]);

  assert.throws(
    () => applyAutoApplyPlan(draft, plan),
    /自动替换已拒绝.*跨层楼梯|跨层楼梯.*现有草稿未修改/,
  );
  assert.deepEqual(draft, before, '替换被拒绝后不能删除楼梯、门、起始区或任何区域');
});

run('已有楼层跳过替换与第四层新建可以在同一计划中混合应用', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-1-refresh', 0.12, {
      replaceExisting: false,
      regions: [{ outer: ring(10, 10, 13, 13) }],
    }),
    candidate('fourth-floor', 3.72, {
      regions: [{ outer: ring(9, 0, 12, 4) }],
    }),
  ]);

  assert.deepEqual(plan.operations.map((operation) => operation.kind), ['update', 'create']);
  const result = applyAutoApplyPlan(draft, plan);
  assert.equal(result.skippedExisting, 1);
  assert.equal(result.addedFloors, 1);
  assert.equal(result.draft.floors.length, 4);
  assert.deepEqual(result.draft.floors[0].walkable.map((region) => region.id), ['floor-1-room']);
  assert.deepEqual(result.draft.floors[3].walkable.map((region) => region.id), ['floor-4-auto-region-1']);
});

run('楼层标高更新同步门、出口、楼梯端点和中间路径高度', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('floor-2-height', 1.42, {
      replaceExisting: false,
      regions: [{ outer: ring(20, 20, 22, 22) }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);
  const firstFloorDoor = result.draft.doors.find((door) => door.id === 'door-1');
  const secondFloorDoor = result.draft.doors.find((door) => door.id === 'door-2');
  const secondFloorExit = result.draft.exits.find((exit) => exit.id === 'exit-2');
  const lowerStair = result.draft.stairs.find((stair) => stair.id === 'stair-1-2');
  const upperStair = result.draft.stairs.find((stair) => stair.id === 'stair-2-3');

  assert.equal(plan.operations[0].kind, 'update');
  assert.equal(plan.operations[0].skippedRegions, 1);
  assert.equal(result.updatedFloors, 1);
  assert.deepEqual(result.draft.floors[1].walkable.map((region) => region.id), ['floor-2-room']);
  assert.equal(firstFloorDoor.position[1], 0.12, '其他楼层的门高度不能改变');
  assert.equal(secondFloorDoor.position[1], 1.42);
  assert.equal(secondFloorExit.position[1], 1.42);
  assert.ok(lowerStair.path.every((point, index) => Math.abs(point[1] - [0.12, 0.77, 1.42][index]) < 1e-9), '下层楼梯路径高度应按比例同步');
  assert.deepEqual(lowerStair.top, [1, 1.42, 2]);
  assert.ok(upperStair.path.every((point, index) => Math.abs(point[1] - [1.42, 1.97, 2.52][index]) < 1e-9), '上层楼梯路径高度应按比例同步');
  assert.deepEqual(upperStair.bottom, [4, 1.42, 2]);
  assert.equal(result.draft.annotation_recheck.status, 'needs-recheck');
  assert.equal(result.draft.navigation_review.status, 'needs-review');
});

run('无效区域在应用阶段原子拒绝，原草稿完整保留', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('invalid-floor', 3.72, {
      regions: [{ outer: [[8, 0], [9, 0], [10, 0]], holes: [] }],
    }),
  ]);

  assert.throws(
    () => applyAutoApplyPlan(draft, plan),
    /面积无效/,
    '零面积外轮廓必须被拒绝',
  );
  assert.deepEqual(draft, before, '失败后不能向原草稿写入新楼层、区域或审计状态');
  assert.equal(JSON.stringify(draft), plan.sourceFingerprint, '失败后计划指纹仍应对应原草稿');
});

run('自交多边形被拒绝，且不会写入草稿', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('self-intersecting-floor', 3.72, {
      regions: [{
        outer: [[8, 0], [13, 3], [8, 4], [12, 1]],
        holes: [],
      }],
    }),
  ]);

  assert.throws(() => applyAutoApplyPlan(draft, plan), /存在自交/);
  assert.deepEqual(draft, before);
});

run('孔洞越出外轮廓时整体拒绝', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('outside-hole-floor', 3.72, {
      regions: [{
        outer: ring(8, 0, 12, 4),
        holes: [ring(11, 2, 13, 3)],
      }],
    }),
  ]);

  assert.throws(() => applyAutoApplyPlan(draft, plan), /完整位于外轮廓内部/);
  assert.deepEqual(draft, before);
});

run('孔洞接触外边界时整体拒绝', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('boundary-hole-floor', 3.72, {
      regions: [{
        outer: ring(8, 0, 12, 4),
        holes: [ring(8, 1, 10, 2)],
      }],
    }),
  ]);

  assert.throws(() => applyAutoApplyPlan(draft, plan), /不能接触外边界/);
  assert.deepEqual(draft, before);
});

run('重叠孔洞整体拒绝', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('overlapping-hole-floor', 3.72, {
      regions: [{
        outer: ring(8, 0, 12, 4),
        holes: [ring(8.5, 1, 10.5, 2), ring(9.5, 1.5, 11.5, 2.5)],
      }],
    }),
  ]);

  assert.throws(() => applyAutoApplyPlan(draft, plan), /重叠/);
  assert.deepEqual(draft, before);
});

run('合法内部孔洞原样写入自动区域', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('valid-hole-floor', 3.72, {
      regions: [{
        outer: ring(8, 0, 12, 4),
        holes: [ring(9, 1, 10, 2)],
      }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);
  const region = result.draft.floors.find((floor) => floor.id === 'floor-4').walkable[0];

  assert.deepEqual(region.outer, ring(8, 0, 12, 4));
  assert.deepEqual(region.holes, [ring(9, 1, 10, 2)]);
});


run('连通性预检将共享边界、门和楼梯组成可达出口的区域图', () => {
  const draft = makeNavigableDraft();
  draft.floors[0].walkable.push({ id: 'floor-1-side', outer: ring(4, 0, 6, 1), holes: [] });
  draft.doors.push({
    id: 'door-side', name: '侧门', floor: 'floor-1',
    position: [4, 0.12, 0.5], width_m: 0.9,
    from_region: 'floor-1-side', to_region: 'floor-1-room',
  });
  const result = inspectAutoApplyNavigation(draft);

  assert.equal(AUTO_NAV_BOUNDARY_TOLERANCE_M, 0.01);
  assert.equal(result.status, 'passed');
  assert.equal(result.ok, true);
  assert.equal(result.regionCount, 4);
  assert.deepEqual(result.unreachableRegionIds, []);
  assert.ok(result.reachableRegionIds.includes('floor-1-side'));
  assert.ok(result.connections.some((item) => item.kind === 'door' && item.label === '侧门'));
  assert.ok(result.connections.some((item) => item.kind === 'stair' && item.label === 'stair-1-2'));
  assert.equal(result.connectionCounts.stairs, 2);
});

run('门和楼梯缺少显式区域字段时按三维端点解析所属可走区', () => {
  const draft = makeDraft();
  draft.doors = [];
  draft.exits = [{ id: 'exit-1', floor: 'floor-1', position: [0, 0.12, 0] }];
  draft.floors[0].walkable = [walkable('floor-1-room', 0)];
  draft.floors[1].walkable = [walkable('floor-2-room', 3)];
  draft.floors[2].walkable = [walkable('floor-3-room', 6)];
  draft.stairs = [{
    id: 'stair-1-2', from_floor: 'floor-1', to_floor: 'floor-2',
    bottom: [1, 0.12, 1], top: [3.5, 1.32, 0.5],
    path: [[1, 0.12, 1], [3.5, 1.32, 0.5]],
  }, {
    id: 'stair-2-3', from_floor: 'floor-2', to_floor: 'floor-3',
    bottom: [3.5, 1.32, 0.5], top: [6.5, 2.52, 0.5],
    path: [[3.5, 1.32, 0.5], [6.5, 2.52, 0.5]],
  }];
  const result = inspectAutoApplyNavigation(draft);

  assert.equal(result.status, 'passed', result.warnings);
  assert.deepEqual(result.unreachableRegionIds, []);
  assert.ok(result.connections.some((item) => item.kind === 'stair' && item.from === 'floor-1-room'));
});

run('可走区仅相距边界容差以内时视为同一通路', () => {
  const draft = makeNavigableDraft();
  draft.floors[0].walkable = [
    walkable('floor-1-main', 0),
    { id: 'floor-1-near', outer: ring(2.005, 0, 4.005, 1), holes: [] },
  ];
  const result = inspectAutoApplyNavigation(draft, null, { boundaryToleranceM: 0.01 });

  assert.equal(result.status, 'passed');
  assert.ok(result.connections.some((item) => item.kind === 'shared-boundary'
    && new Set([item.from, item.to]).has('floor-1-near')));
});

run('仅共享一个顶点的可走区不会产生导航连接', () => {
  const draft = makeNavigableDraft();
  draft.floors[0].walkable = [
    walkable('floor-1-main', 0),
    { id: 'floor-1-corner', outer: ring(2, 1, 4, 2), holes: [] },
  ];
  const result = inspectAutoApplyNavigation(draft);

  assert.equal(result.status, 'warning');
  assert.ok(result.unreachableRegionIds.includes('floor-1-corner'));
  assert.equal(result.connections.some((item) => item.kind === 'shared-boundary'
    && new Set([item.from, item.to]).has('floor-1-corner')), false);
});

run('空楼层会返回 emptyFloorIds 并产生 warning', () => {
  const draft = makeNavigableDraft();
  draft.floors.push({ id: 'floor-empty', name: '空楼层', elevation_m: 4.92, walkable: [], spawn_zones: [] });
  const result = inspectAutoApplyNavigation(draft);

  assert.equal(result.status, 'warning');
  assert.equal(result.ok, false);
  assert.ok(result.emptyFloorIds.includes('floor-empty'));
  assert.ok(result.warnings.some((message) => message.includes('空楼层') && message.includes('没有任何可走区')));
});

run('自动候选应用后，孤立区域作为 warning 返回且不破坏原子应用', () => {
  const draft = makeNavigableDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('fourth-floor', 3.72, {
      regions: [{ id: 'isolated-fourth', outer: ring(20, 0, 23, 3) }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);

  const plannedId = plan.operations[0].regionIds[0];
  assert.equal(result.navigationPreflight.status, 'warning');
  assert.equal(result.navigationPreflight.ok, false);
  assert.deepEqual(result.navigationPreflight.autoUnreachableRegionIds, [plannedId]);
  assert.deepEqual(result.navigationPreflight.existingUnreachableRegionIds, []);
  assert.ok(result.navigationPreflight.warnings.some((message) => message.includes('自动候选区域') && message.includes('无法经共享边界、门或楼梯到达任何出口')));
  assert.equal(result.addedFloors, 1, '预检 warning 不能回滚已经验证的应用结果');
  assert.equal(result.draft.floors.find((floor) => floor.id === 'floor-4').walkable[0].id, plannedId);
  assert.equal(result.draft.navigation_review.status, 'needs-review');
});

run('连通性预检快照指纹绑定应用后的实际草稿', () => {
  const draft = makeNavigableDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('fourth-floor', 3.72, {
      regions: [{ id: 'fourth-room', outer: ring(20, 0, 23, 3) }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);

  assert.equal(result.navigationPreflight.draftFingerprint, JSON.stringify(result.draft));
  assert.equal(result.navigationPreflight.draftFingerprint, JSON.stringify(result.draft), '应用后再次序列化应保持稳定');
  const edited = clone(result.draft);
  edited.floors.find((floor) => floor.id === 'floor-4').name = '人工修改后的四层';
  assert.notEqual(result.navigationPreflight.draftFingerprint, JSON.stringify(edited));
  assert.equal(JSON.stringify(result.draft), result.navigationPreflight.draftFingerprint, '测试不得修改原始草稿');
});

run('没有出口时连通性预检返回错误，但原草稿保持不变', () => {
  const draft = makeDraft();
  draft.exits = [];
  const before = clone(draft);
  const result = inspectAutoApplyNavigation(draft);

  assert.equal(result.status, 'failed');
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes('没有可解析的出口')));
  assert.deepEqual(draft, before);
});

run('应用计划将新增第四层的孤立候选标记为自动区域来源', () => {
  const draft = makeNavigableDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('fourth-floor', 3.72, {
      regions: [{ id: 'floor-4-unreachable', outer: ring(20, 0, 23, 3) }],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);
  const plannedId = plan.operations[0].regionIds[0];
  const record = result.navigationPreflight.unreachableRegions.find((item) => item.id === plannedId);

  assert.ok(record);
  assert.equal(record.source, 'auto');
  assert.equal(record.floorId, 'floor-4');
  assert.equal(record.candidateId, 'fourth-floor');
});

run('同层自动区域交叉或部分重叠时原子拒绝应用', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('overlap-floor', 3.72, {
      regions: [
        { id: 'overlap-a', outer: ring(0, 0, 4, 4) },
        { id: 'overlap-b', outer: ring(3, 3, 7, 7) },
      ],
    }),
  ]);

  assert.throws(
    () => applyAutoApplyPlan(draft, plan),
    /存在正面积重叠；请调整边界、取消接受候选，或明确替换本层区域/,
  );
  assert.deepEqual(draft, before, '拒绝重叠后原草稿必须完全不变');
});

run('同层区域完全包含时拒绝应用', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('contained-floor', 3.72, {
      regions: [
        { id: 'contained-outer', outer: ring(0, 0, 10, 10) },
        { id: 'contained-inner', outer: ring(2, 2, 4, 4) },
      ],
    }),
  ]);

  assert.throws(() => applyAutoApplyPlan(draft, plan), /区域 floor-4-auto-region-1 与区域 floor-4-auto-region-2 存在正面积重叠/);
  assert.deepEqual(draft, before);
});

run('自动区域覆盖现有区域时拒绝应用且保留原草稿', () => {
  const draft = makeDraft();
  const before = clone(draft);
  const plan = createAutoApplyPlan(draft, [
    candidate('cover-existing', 3.72, { regions: [{ id: 'cover-existing-region', outer: ring(20, 0, 23, 3) }] }),
  ]);
  plan.operations = [{
    kind: 'update',
    candidateIndex: 0,
    candidateId: 'cover-existing',
    targetId: 'floor-1',
    targetName: '一层',
    sourceElevation: 0.12,
    elevation: 0.12,
    floorBeforeElevation: 0.12,
    elevationChanged: false,
    replaceExisting: false,
    preserveExisting: true,
    regions: [{ outer: ring(1, 0.5, 3, 1.5), holes: [] }],
    regionIds: ['cover-existing-auto-region'],
    skippedRegions: 0,
  }];

  assert.throws(
    () => applyAutoApplyPlan(draft, plan),
    /楼层 一层 \/ 区域 floor-1-room 与区域 cover-existing-auto-region 存在正面积重叠/,
  );
  assert.deepEqual(draft, before);
});

run('同层区域仅共享一条边时允许应用', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('shared-edge-floor', 3.72, {
      regions: [
        { id: 'shared-edge-a', outer: ring(0, 0, 2, 2) },
        { id: 'shared-edge-b', outer: ring(2, 0, 4, 2) },
      ],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);
  const floor = result.draft.floors.find((item) => item.id === 'floor-4');

  assert.equal(result.addedRegions, 2);
  assert.equal(floor.walkable.length, 2);
});

run('同层区域仅共享一个顶点时允许应用', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('shared-vertex-floor', 3.72, {
      regions: [
        { id: 'shared-vertex-a', outer: ring(0, 0, 2, 2) },
        { id: 'shared-vertex-b', outer: ring(2, 2, 4, 4) },
      ],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);

  assert.equal(result.addedRegions, 2);
});

run('区域位于另一区域的孔洞内时允许应用', () => {
  const draft = makeDraft();
  const plan = createAutoApplyPlan(draft, [
    candidate('hole-floor', 3.72, {
      regions: [
        { id: 'hole-outer', outer: ring(0, 0, 10, 10), holes: [ring(3, 3, 7, 7)] },
        { id: 'inside-hole', outer: ring(4, 4, 6, 6) },
      ],
    }),
  ]);
  const result = applyAutoApplyPlan(draft, plan);

  assert.equal(result.addedRegions, 2);
  assert.equal(result.draft.floors.find((item) => item.id === 'floor-4').walkable.length, 2);
});

run('重复楼梯不产生端点解析假警告', () => {
  const draft = makeNavigableDraft();
  draft.stairs.push({
    id: 'stair-duplicate', from_floor: 'floor-1', to_floor: 'floor-2',
    from_region: 'floor-1-room', to_region: 'floor-2-room',
    bottom: [1, 0.12, 0.5], top: [4, 1.32, 0.5],
    path: [[1, 0.12, 0.5], [2.5, 0.72, 0.5], [4, 1.32, 0.5]],
  });
  const preflight = inspectAutoApplyNavigation(draft);
  assert.equal(preflight.status, 'passed');
  assert.equal(preflight.connectionCounts.stairs, 2, '两条不同楼梯端点区域应产生两条有效连接');
  assert.equal(preflight.warnings.some((message) => message.includes('stair-duplicate')), false,
    '重复楼梯已有有效区域端点时不能报告端点解析失败');
});

run('完全缺少区域字段和位置的连接器会明确报警', () => {
  const draft = makeNavigableDraft();
  draft.doors.push({ id: 'door-unresolved', floor: 'floor-1' });
  draft.stairs.push({ id: 'stair-unresolved', from_floor: 'floor-1', to_floor: 'floor-2' });
  const preflight = inspectAutoApplyNavigation(draft);
  assert.equal(preflight.status, 'warning');
  assert.match(preflight.warnings.join(' '), /door-unresolved/, preflight.warnings);
  assert.match(preflight.warnings.join(' '), /stair-unresolved/, preflight.warnings);
});

if (failures.length) {
  console.error(`\n${failures.length} auto-apply test(s) failed.`);
  process.exitCode = 1;
} else {
  console.log('\nauto-apply-plan tests passed');
}

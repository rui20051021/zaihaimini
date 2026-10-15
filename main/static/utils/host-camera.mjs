// Camera/replay math shared by the host viewer and its regression tests.
// Discrete outcome flags come from the current sample, not a future sample.
export function sampleAtTime(samples, time) {
  if (!samples?.length) return null;
  if (time < samples[0].t_sim_s) return { ...samples[0] };
  let lo = 0, hi = samples.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (samples[mid].t_sim_s <= time) lo = mid + 1;
    else hi = mid;
  }
  const index = Math.max(0, lo - 1);
  const a = samples[index], b = samples[index + 1];
  if (!b) return { ...a };
  const fraction = Math.max(0, Math.min(1, (time - a.t_sim_s) / Math.max(.000001, b.t_sim_s - a.t_sim_s)));
  const result = { ...a };
  for (const field of ['x_m', 'y_m', 'z_m', 'vx_mps', 'vy_mps', 'vz_mps', 'smoke', 'smoke_dose', 'health', 'pressure', 'density']) {
    if (Number.isFinite(a[field]) && Number.isFinite(b[field])) {
      result[field] = a[field] + (b[field] - a[field]) * fraction;
    }
  }
  // CSV waypoints can omit heading/speed when the actor stops or exits.
  // Use the interpolated segment direction as the replay heading in that case.
  result.heading_x = b.x_m - a.x_m;
  result.heading_z = b.z_m - a.z_m;
  return result;
}

export function headingFor(actor, fallback = 0) {
  if (!actor) return fallback;
  const vx = actor.velocity?.x ?? 0, vz = actor.velocity?.z ?? 0;
  if (Math.hypot(vx, vz) > .08) return Math.atan2(vx, vz);
  if (Math.hypot(actor.heading_x ?? 0, actor.heading_z ?? 0) > .00001) {
    return Math.atan2(actor.heading_x, actor.heading_z);
  }
  return fallback;
}

export function dampAngle(current, target, deltaSeconds) {
  const difference = Math.atan2(Math.sin(target - current), Math.cos(target - current));
  return current + difference * (1 - Math.exp(-Math.max(0, deltaSeconds) * 10));
}

export function lookDirection(heading, yawOffset, pitch) {
  const yaw = heading + yawOffset, cosPitch = Math.cos(pitch);
  return { x: Math.sin(yaw) * cosPitch, y: Math.sin(pitch), z: Math.cos(yaw) * cosPitch };
}

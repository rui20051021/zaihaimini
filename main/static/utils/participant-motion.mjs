// Visual interpolation is read-only: never extrapolate through authoritative walls.
export class SnapshotMotion {
  constructor(delayMs = 80, {maxDelayMs=180,jitterMarginMs=25} = {}) {
    this.minDelayMs = delayMs; this.delayMs = delayMs; this.samples = [];
    this.maxDelayMs=Math.max(delayMs,maxDelayMs);this.jitterMarginMs=jitterMarginMs;
    this.intervals = []; this.playbackAt = -Infinity;
  }
  reset() { this.samples.length = 0; this.intervals.length = 0; this.delayMs = this.minDelayMs; this.playbackAt = -Infinity; }
  push(actor, now, reset = false) {
    if (reset) this.reset();
    if (!actor?.position) return;
    const p = actor.position;
    const sample = { at: now, x: p.x, y: p.y, z: p.z, floor: actor.floor, connector: actor.connector };
    if (![sample.x,sample.y,sample.z,now].every(Number.isFinite)) return;
    const last = this.samples.at(-1);
    if (last && (now <= last.at || now - last.at > 500 || Math.hypot(sample.x-last.x,sample.y-last.y,sample.z-last.z)>1.5 ||
        (sample.floor!==last.floor && !last.connector && !sample.connector))) this.reset();
    else if (last) {
      this.intervals.push(now - last.at); if (this.intervals.length > 12) this.intervals.shift();
      if (this.intervals.length >= 4) {
        const sorted = [...this.intervals].sort((a,b)=>a-b);
        const target = Math.max(this.minDelayMs, Math.min(this.maxDelayMs, sorted[Math.floor((sorted.length - 1)*.9)] + this.jitterMarginMs));
        this.delayMs += (target - this.delayMs) * .15;
      }
    }
    this.samples.push(sample);
    if (this.samples.length > 24) this.samples.shift();
  }
  sample(now, out) {
    const samples = this.samples;
    if (!samples.length) return false;
    // Adjust jitter buffering without ever running the playback clock backwards.
    const t = Math.max(this.playbackAt, now - this.delayMs); this.playbackAt = t;
    let a=samples[0],b=a;
    for(const next of samples){ b=next;if(next.at>=t)break;a=next; }
    const alpha = b.at>a.at ? Math.max(0,Math.min(1,(t-a.at)/(b.at-a.at))) : 0;
    out.set(a.x+(b.x-a.x)*alpha,a.y+(b.y-a.y)*alpha,a.z+(b.z-a.z)*alpha);
    return true;
  }
}

export function eyeHeight(floors, floorId, desired=1.55) {
  const sorted=[...(floors||[])].sort((a,b)=>a.elevation_m-b.elevation_m);
  const at=sorted.findIndex(f=>f.id===floorId);
  const gaps=sorted.slice(1).map((f,i)=>f.elevation_m-sorted[i].elevation_m).filter(x=>x>.1);
  const gap=sorted[at+1]&&at>=0?sorted[at+1].elevation_m-sorted[at].elevation_m:Math.min(...gaps);
  // Provisional models may still be underscaled. Keep the eye below the ceiling.
  return Math.max(.3,Math.min(desired,Number.isFinite(gap)?gap*.52:desired));
}


// The provisional school model has door openings about 0.78 units above the floor.
// Keep the rendered person below that lintel; this is visual scale only.
export function agentVisualScale(floors, floorId, nativeHeight) {
  const sorted=[...(floors||[])].sort((a,b)=>a.elevation_m-b.elevation_m);
  const at=sorted.findIndex(f=>f.id===floorId);
  const gaps=sorted.slice(1).map((f,i)=>f.elevation_m-sorted[i].elevation_m).filter(x=>x>.1);
  const gap=sorted[at+1]&&at>=0?sorted[at+1].elevation_m-sorted[at].elevation_m:Math.min(...gaps);
  const base=Math.max(.1,Number(nativeHeight)||1);
  const height=Number.isFinite(gap)?Math.min(base,gap*.58):base;
  return height/Math.max(.1,Number(nativeHeight)||1);
}

export function disposeModel(root) {
  if(!root)return;
  const geometries=new Set(),materials=new Set(),textures=new Set();
  root.traverse(o=>{
    if(o.geometry&&!o.geometry.userData?.shared)geometries.add(o.geometry);
    for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m){
      materials.add(m);
      for(const value of Object.values(m))if(value?.isTexture)textures.add(value);
    }
  });
  geometries.forEach(x=>x.dispose());materials.forEach(x=>x.dispose());
  textures.forEach(x=>{x.source?.data?.close?.();x.dispose();});
}

export class ModelRequest {
  constructor(){ this.key=null;this.pending=false;this.failed=false;this.generation=0; }
  begin(key){
    if(this.key===key)return null;
    this.key=key;this.pending=true;this.failed=false;
    return ++this.generation;
  }
  finish(generation,ok){
    if(generation!==this.generation)return false;
    this.pending=false;this.failed=!ok;return true;
  }
}

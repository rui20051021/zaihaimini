// The view may trade resolution for frame rate; simulation and input stay unchanged.
export class RenderBudget {
  constructor({maxPixelRatio = 1, minPixelRatio = .6, targetFps = 60} = {}) {
    this.maxPixelRatio = maxPixelRatio; this.minPixelRatio = Math.min(minPixelRatio, maxPixelRatio);
    this.pixelRatio = maxPixelRatio; this.targetFps = targetFps; this.fps = 0;
    this.frames = 0; this.windowAt = 0; this.lowWindows = 0; this.goodWindows = 0;
    this.lastQualityChange = 0; this.lastRateChange = 0;
    this.recoveryBackoff = 30000; this.probeUntil = 0;
  }
  get periodMs() { return 1000 / this.targetFps; }
  reset(now) { this.windowAt = now; this.frames = 0; this.lowWindows = this.goodWindows = 0; }
  record(now) {
    this.frames++;
    if (now - this.windowAt < 2000) return false;
    this.fps = this.frames * 1000 / (now - this.windowAt); this.frames = 0; this.windowAt = now;
    const old = this.pixelRatio;
    if (this.targetFps === 60 && this.fps < 45) {
      this.goodWindows = 0; this.lowWindows++;
      if (this.pixelRatio > this.minPixelRatio + .001) {
        this.pixelRatio = Math.max(this.minPixelRatio, Math.round((this.pixelRatio - .1) * 100) / 100);
        this.lastQualityChange = now;
      } else if (this.fps < 32 && this.lowWindows >= 2) {
        this.targetFps = 30; this.lastRateChange = now; this.lowWindows = 0;
        if(this.probeUntil)this.recoveryBackoff=Math.min(120000,this.recoveryBackoff*2);
        this.probeUntil=0;
      }
    } else {
      this.lowWindows = 0;
      this.goodWindows = this.fps >= this.targetFps * .94 ? this.goodWindows + 1 : 0;
      if (this.targetFps === 30 && this.goodWindows >= 3 && now - this.lastRateChange >= this.recoveryBackoff) {
        this.targetFps = 60; this.lastRateChange = now; this.goodWindows = 0;this.probeUntil=now+10000;
      } else if (this.targetFps === 60 && this.goodWindows >= 3 && now - this.lastQualityChange >= 10000) {
        this.pixelRatio = Math.min(this.maxPixelRatio, Math.round((this.pixelRatio + .05) * 100) / 100);
        this.lastQualityChange = now; this.goodWindows = 0;
        if(this.probeUntil&&now>=this.probeUntil){this.probeUntil=0;this.recoveryBackoff=30000;}
      }
    }
    return this.pixelRatio !== old;
  }
}

export function cacheFloorHeights(floors, eyeHeight) {
  return new Map((floors || []).map(f => [f.id, eyeHeight(floors, f.id)]));
}

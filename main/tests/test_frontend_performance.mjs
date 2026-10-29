import test from 'node:test';
import assert from 'node:assert/strict';
import {SnapshotMotion,disposeModel} from '../static/utils/participant-motion.mjs';
import {RenderBudget} from '../static/utils/render-performance.mjs';
import {ReconnectingSocket} from '../static/utils/reconnecting-socket.mjs';

function runtime(){
 const sockets=[],timers=new Map();let id=0;
 return {sockets,timers,Math:{random:()=>0},WebSocket:class {
  constructor(url){this.url=url;this.readyState=0;sockets.push(this);}
  close(code=1000){this.readyState=3;this.onclose?.({code});}
  open(){this.readyState=1;this.onopen?.();}
  message(data='{}'){this.onmessage?.({data});}
 },setTimeout(fn,delay){timers.set(++id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},tick(){const [id,t]=timers.entries().next().value;timers.delete(id);t.fn();}};
}
test('network loss reconnects without rejoining under a new identity; success resets backoff',()=>{
 const r=runtime(),seen=[];const c=new ReconnectingSocket({url:'/same-player',onmessage:e=>seen.push(e.data)},r);
 c.start();r.sockets[0].open();r.sockets[0].close(1006);
 assert.equal([...r.timers.values()][0].delay,500);r.tick();assert.equal(r.sockets[1].url,'/same-player');
 r.sockets[1].open();r.sockets[1].message('state');assert.deepEqual(seen,['state']);assert.equal(c.attempts,0);
 c.stop();assert.equal(r.timers.size,0);
});
test('intentional exit cancels a pending retry and ignores late socket events',()=>{
 const r=runtime();const c=new ReconnectingSocket({url:'/player'},r);c.start();const old=r.sockets[0];old.open();old.close(1006);
 c.stop();old.onclose({code:1006});c.retryNow();assert.equal(r.sockets.length,1);assert.equal(r.timers.size,0);
});
test('fatal consent/auth close does not retry and failed connections stop after the limit',()=>{
 const r=runtime();const c=new ReconnectingSocket({url:'/p',shouldRetry:e=>e.code!==4005,maxAttempts:2},r);
 c.start();r.sockets[0].open();r.sockets[0].close(4005);c.retryNow();assert.equal(r.sockets.length,1);assert.equal(r.timers.size,0);
 c.start();for(let i=1;i<=3;i++){r.sockets[i].open();r.sockets[i].close(1006);if(i<3)r.tick();}
 assert.equal(r.timers.size,0);assert.equal(r.sockets.length,4);
});
test('manual retry replaces a pending timer and stop closes a connecting socket',()=>{
 const r=runtime();const c=new ReconnectingSocket({url:'/p'},r);c.start();r.sockets[0].close(1006);c.retryNow();assert.equal(r.sockets.length,2);
 assert.equal(r.timers.size,1);c.stop();assert.equal(r.timers.size,0);assert.equal(c.socket,null);
});
test('a half-open connection without snapshots is closed and retried',()=>{
 const r=runtime();const c=new ReconnectingSocket({url:'/host'},r);c.start();r.sockets[0].open();
 assert.equal([...r.timers.values()][0].delay,10000);r.tick();assert.equal(c.socket,null);
 assert.equal([...r.timers.values()][0].delay,500);r.tick();assert.equal(r.sockets.length,2);c.stop();
});
test('own-position buffer stays bounded and never predicts through walls or reverses playback',()=>{
 const m=new SnapshotMotion(60,{maxDelayMs:120,jitterMarginMs:10}),out={set(x){this.x=x;}};
 let x=0,last=-1;
 for(let now=0;now<5000;now+=10){if(now%50===0||now%170===0)m.push({position:{x:x+=.02,y:0,z:0},floor:'f'},now);m.sample(now,out);assert.ok(out.x>=last);last=out.x;}
 assert.ok(m.delayMs>=60&&m.delayMs<=120);m.sample(10000,out);assert.equal(out.x,x);
 m.push({position:{x:20,y:0,z:0},floor:'f'},11000);m.sample(11000,out);assert.equal(out.x,20);
});
test('shared actor geometry survives removing one character',()=>{
 let sharedDisposals=0,privateDisposals=0;
 const shared={userData:{shared:true},dispose(){sharedDisposals++;}},privateGeometry={dispose(){privateDisposals++;}};
 disposeModel({traverse(fn){fn({geometry:shared});fn({geometry:privateGeometry});}});
 assert.equal(sharedDisposals,0);assert.equal(privateDisposals,1);
});
test('failed 60 FPS recovery increases the retry interval on sustained slow hardware',()=>{
 const b=new RenderBudget({maxPixelRatio:.6,minPixelRatio:.6});b.reset(0);
 for(let now=50;now<=6000;now+=50)b.record(now);assert.equal(b.targetFps,30);
 let now=6000;while(now<38000){now+=1000/30;b.record(now);}assert.equal(b.targetFps,60);
 for(let i=0;i<150;i++){now+=50;b.record(now);}assert.equal(b.targetFps,30);assert.equal(b.recoveryBackoff,60000);
});

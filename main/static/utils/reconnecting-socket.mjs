// A page keeps its decoded model while the transport reconnects. Explicit exits
// stop retries; protocol/consent failures are handled by the caller.
export class ReconnectingSocket {
  constructor({url,onopen=()=>{},onmessage=()=>{},onclose=()=>{},onretry=()=>{},onexhausted=()=>{},shouldRetry=()=>true,maxAttempts=8},runtime=globalThis) {
    Object.assign(this,{url,onopen,onmessage,onclose,onretry,onexhausted,shouldRetry,maxAttempts,runtime});
    this.socket=null;this.enabled=false;this.attempts=0;this.timer=null;this.watchdog=null;
  }
  clearTimers(){this.runtime.clearTimeout(this.timer);this.runtime.clearTimeout(this.watchdog);this.timer=this.watchdog=null;}
  start(){this.stop();this.enabled=true;this.attempts=0;this.connect();}
  stop(){this.enabled=false;this.clearTimers();const socket=this.socket;this.socket=null;if(socket)socket.close();}
  retryNow(){if(!this.enabled||this.socket)return;this.clearTimers();this.attempts=0;this.connect();}
  watch(socket,connecting=false){
    this.runtime.clearTimeout(this.watchdog);
    this.watchdog=this.runtime.setTimeout(()=>{if(this.socket!==socket)return;if(connecting)socket.close();else socket.close(4000,'同步超时');},connecting?12000:10000);
  }
  connect(){
    if(!this.enabled||this.socket)return;
    const socket=new this.runtime.WebSocket(typeof this.url==='function'?this.url():this.url);this.socket=socket;
    this.watch(socket,true);
    socket.onopen=()=>{if(this.socket!==socket)return;this.watch(socket);this.onopen(socket);};
    socket.onmessage=event=>{if(this.socket!==socket)return;this.watch(socket);this.attempts=0;this.onmessage(event,socket);};
    socket.onclose=event=>{
      if(this.socket!==socket)return;
      this.socket=null;this.clearTimers();this.onclose(event);
      if(!this.enabled)return;
      if(!this.shouldRetry(event)){this.enabled=false;return;}
      if(this.attempts>=this.maxAttempts){this.onexhausted();return;}
      const delay=Math.min(15000,500*2**this.attempts)+Math.floor((this.runtime.Math||Math).random()*250);
      this.attempts++;this.onretry(this.attempts,delay);
      this.timer=this.runtime.setTimeout(()=>{this.timer=null;this.connect();},delay);
    };
  }
}

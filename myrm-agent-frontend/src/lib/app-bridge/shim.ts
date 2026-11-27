/**
 * [INPUT]
 * - (none)
 *
 * [OUTPUT]
 * - injectAppBridge: prepends the page-side `window.myrm` client to an app page's HTML.
 *
 * [POS]
 * Page half of the channel. The sandboxed page has no network and no OS access, so `myrm.notify`, `myrm.fetch`,
 * `myrm.llm.complete`, `myrm.tray`, `myrm.shortcut` and `myrm.float` send JSON-RPC requests to `window.parent` and settle from the reply;
 * `myrm.on(name, handler)` receives what the host reports (`permission-revoked`, `shortcut`). Messages are accepted from
 * the parent window only. The shim is a convenience: the host authenticates the sender itself, so a page that skips it
 * gains nothing.
 */

const SHIM = `<script>
(function(){
  var pending={},seq=0,NO_BODY={204:1,205:1,304:1},handlers=Object.create(null);
  window.addEventListener('message',function(e){
    var m=e.data;
    if(e.source!==window.parent||!m||m.jsonrpc!=='2.0')return;
    if(m.method==='myrm/event'&&m.params&&typeof m.params.name==='string'){
      (handlers[m.params.name]||[]).slice().forEach(function(fn){try{fn(m.params.data)}catch(_){}});
      return;
    }
    if(!Object.prototype.hasOwnProperty.call(pending,m.id))return;
    var p=pending[m.id];delete pending[m.id];
    if(m.error){var err=new Error(m.error.message);err.code=(m.error.data&&m.error.data.reason)||'failed';p.reject(err)}
    else p.resolve(m.result);
  });
  function call(method,params){
    return new Promise(function(resolve,reject){
      var id=++seq;pending[id]={resolve:resolve,reject:reject};
      window.parent.postMessage({jsonrpc:'2.0',id:id,method:method,params:params},'*');
    });
  }
  function toHeaders(plain){
    var headers=new Headers();
    Object.keys(plain||{}).forEach(function(name){try{headers.append(name,plain[name])}catch(_){}});
    return headers;
  }
  function done(){}
  window.myrm=Object.freeze({
    on:function(name,fn){
      if(typeof fn!=='function')return done;
      (handlers[name]=handlers[name]||[]).push(fn);
      return function(){handlers[name]=(handlers[name]||[]).filter(function(h){return h!==fn})};
    },
    tray:Object.freeze({
      set:function(options){return call('tray.set',options||{}).then(done)},
      clear:function(){return call('tray.clear',{}).then(done)}
    }),
    shortcut:Object.freeze({
      register:function(accelerator){return call('shortcut.register',{accelerator:String(accelerator)}).then(done)},
      unregister:function(accelerator){return call('shortcut.unregister',{accelerator:String(accelerator)}).then(done)}
    }),
    float:function(on){return call('float.set',{on:!!on}).then(done)},
    llm:Object.freeze({
      complete:function(options){
        var o=typeof options==='string'?{prompt:options}:options||{};
        return call('ai.complete',{prompt:o.prompt,system:o.system,maxTokens:o.maxTokens}).then(function(r){return r.text});
      }
    }),
    notify:function(options){return call('notify',options||{}).then(function(){})},
    fetch:function(url,init){
      init=init||{};
      var headers={};
      if(init.headers)new Headers(init.headers).forEach(function(value,name){headers[name]=value});
      return call('fetch',{
        url:String(url),
        method:String(init.method||'GET').toUpperCase(),
        headers:headers,
        body:init.body==null?null:String(init.body)
      }).then(function(r){return new Response(NO_BODY[r.status]?null:r.body,{status:r.status,headers:toHeaders(r.headers)})});
    }
  });
})();
</script>`;

export function injectAppBridge(html: string): string {
  return SHIM + html;
}

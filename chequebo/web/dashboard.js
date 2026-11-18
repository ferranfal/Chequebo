'use strict';
(() => {
  const cards = new Map();
  let bridge, active = null, full = null, latest = null, pending = null, rendering = false;
  const emit = event => { if (bridge) bridge.send(JSON.stringify(event)); };
  const escapeText = text => String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const selectPeak = (key,peak) => emit({type:'peak',key,metric:peak.metric,elapsed:peak.elapsed});
  const icon = path => ({width:24,height:24,path});
  const icons = {
    xIn:icon('M2 5h3l3 5 3-5h3l-5 7 5 7h-3l-3-5-3 5H2l5-7zM19 8h2v3h3v2h-3v3h-2v-3h-3v-2h3z'),
    xOut:icon('M2 5h3l3 5 3-5h3l-5 7 5 7h-3l-3-5-3 5H2l5-7zM16 11h8v2h-8z'),
    yIn:icon('M2 5h3l3 6 3-6h3l-5 9v5H7v-5zM19 8h2v3h3v2h-3v3h-2v-3h-3v-2h3z'),
    yOut:icon('M2 5h3l3 6 3-6h3l-5 9v5H7v-5zM16 11h8v2h-8z'),
    fit:icon('M3 3h6v2H5v4H3zM15 3h6v6h-2V5h-4zM3 15h2v4h4v2H3zM19 15h2v6h-6v-2h4z'),
    reset:icon('M12 3a9 9 0 1 1-8 13l2-1a7 7 0 1 0 0-6h4v2H2V3h2v4a9 9 0 0 1 8-4z'),
  };
  function activate(key) {
    active = key;
    for (const [id,s] of cards) {
      s.card.classList.toggle('active', id === key);
      s.hint.textContent = id === key ? 'Zoom active · move outside or press Esc to scroll the panel' : 'Click the chart to enable wheel zoom · scroll normally to move down';
    }
  }
  document.addEventListener('pointerdown', event => activate(event.target.closest('.card')?.dataset.key || null), true);
  // Shapes are behind the traces. Hit-test a short click across the full-height
  // band without stealing Plotly's drag-to-zoom or pan gestures.
  let pointerStart=null;
  document.addEventListener('pointerdown', event=>{
    const plot=event.target.closest('.plot');
    pointerStart=event.button===0 && plot ? {key:plot.dataset.key,x:event.clientX,y:event.clientY} : null;
  },true);
  document.addEventListener('pointercancel',()=>{pointerStart=null;},true);
  document.addEventListener('pointerup',event=>{
    const start=pointerStart; pointerStart=null;
    if(!start || Math.hypot(event.clientX-start.x,event.clientY-start.y)>4)return;
    const s=cards.get(start.key), axes=s?.plot._fullLayout;
    if(!axes)return;
    const rect=s.plot.getBoundingClientRect(), x=event.clientX-rect.left, y=event.clientY-rect.top;
    if(x<axes.xaxis._offset || x>axes.xaxis._offset+axes.xaxis._length || y<axes.yaxis._offset || y>axes.yaxis._offset+axes.yaxis._length)return;
    const hits=(s.peaks||[]).filter(p=>p.elapsed>=axes.xaxis.range[0] && p.elapsed<=axes.xaxis.range[1] && Math.abs(axes.xaxis.d2p(p.elapsed)+axes.xaxis._offset-x)<=7);
    if(hits.length) {
      const peak=hits.reduce((best,p)=>Math.abs(axes.yaxis.d2p(p.value)+axes.yaxis._offset-y)<Math.abs(axes.yaxis.d2p(best.value)+axes.yaxis._offset-y)?p:best);
      // Run after Plotly's own point click, which may select a neighbouring x.
      setTimeout(()=>selectPeak(start.key,peak),0);
    }
  },true);
  document.addEventListener('wheel', event => {
    const card = event.target.closest('.card');
    if (!card || active !== card.dataset.key) {
      event.preventDefault(); event.stopImmediatePropagation();
      const dy = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
      emit({type:'scroll',dy});
    }
  }, {capture:true,passive:false});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { activate(null); if (full) emit({type:'restore'}); }
  });
  function clock(seconds) {
    const value = Math.max(0,Math.floor(seconds));
    return [Math.floor(value/3600),Math.floor(value/60)%60,value%60].map(v=>String(v).padStart(2,'0')).join(':');
  }
  function button(name, glyph, action) { return {name,icon:glyph,click:gd=>emit({type:'action',key:gd.dataset.key,action})}; }
  function config() {
    return {responsive:true,displaylogo:false,scrollZoom:true,displayModeBar:true,doubleClick:false,
      modeBarButtons:[['toImage','zoom2d','pan2d'],[
        button('Zoom X in',icons.xIn,'x_in'),button('Zoom X out',icons.xOut,'x_out'),
        button('Zoom Y in',icons.yIn,'y_in'),button('Zoom Y out',icons.yOut,'y_out'),
        button('Fit Y to visible data',icons.fit,'fit_y'),button('Reset axes',icons.reset,'reset'),
        button('Full screen / Restore',icons.fit,'fullscreen')]],
      toImageButtonOptions:{format:'png',filename:'chequebo-chart',scale:2}};
  }
  function makeCard(spec) {
    const card=document.createElement('section'); card.className='card'; card.dataset.key=spec.key;
    const heading=document.createElement('h2'); heading.className='heading';
    const value=document.createElement('p'); value.className='value';
    const hint=document.createElement('p'); hint.className='hint';
    const peaks=document.createElement('div'); peaks.className='peaks';
    const plot=document.createElement('div'); plot.className='plot'; plot.dataset.key=spec.key;
    card.append(heading,value,hint,peaks,plot); document.getElementById('charts').append(card);
    card.addEventListener('mouseleave',event=>{
      // Plotly places a drag cover over the page on mouse-down. That is not
      // the user leaving the chart: retain activation while still inside it.
      const rect=card.getBoundingClientRect();
      if(active===spec.key && (event.clientX<rect.left || event.clientX>=rect.right || event.clientY<rect.top || event.clientY>=rect.bottom)) activate(null);
    });
    const s={card,heading,value,hint,peakButtons:peaks,plot,busy:false,initialized:false,dragmode:'zoom'}; cards.set(spec.key,s);
    activate(active); return s;
  }
  function layout(spec, payload, state) {
    const axis = {showline:true,linecolor:'#909497',linewidth:1,ticks:'outside',tickcolor:'#909497',zeroline:false,automargin:true};
    const range=payload.range, ticks=Array.from({length:6},(_,i)=>range[0]+(range[1]-range[0])*i/5);
    const shapes=[],annotations=[];
    for(const peak of spec.peaks||[]) {
      // Pixel width keeps a sampled instant visible at any time zoom. This is
      // deliberately not an invented interval around the maximum.
      if(peak.elapsed<range[0] || peak.elapsed>range[1])continue;
      shapes.push({name:'peak:'+peak.metric,type:'rect',xref:'x',yref:'paper',xsizemode:'pixel',xanchor:peak.elapsed,
        x0:-6,x1:6,y0:0,y1:1,fillcolor:'rgba(255,105,105,.18)',line:{width:0},layer:'below'});
    }
    for(const [name,value,color] of [['Record',payload.markers[0],'#65df9b'],['Stop',payload.markers[1],'#ffc368']]) {
      if(value===null)continue;
      shapes.push({type:'line',xref:'x',yref:'paper',x0:value,x1:value,y0:0,y1:1,line:{color,width:2}});
      annotations.push({xref:'x',yref:'paper',x:value,y:name==='Record'?1:.87,text:name,showarrow:false,font:{color,size:11},xanchor:'left'});
    }
    if(spec.ramLimit!==null && spec.ramLimit!==undefined) {
      shapes.push({type:'line',xref:'paper',yref:'y',x0:0,x1:1,y0:spec.ramLimit,y1:spec.ramLimit,line:{color:'#e35656',width:2}});
      annotations.push({xref:'paper',yref:'y',x:1,y:spec.ramLimit,text:'Physical RAM: '+spec.ramLimit.toFixed(2)+' GiB',showarrow:false,xanchor:'right',yanchor:'bottom',font:{size:11,color:'#ff8181'},bgcolor:'rgba(28,37,49,.9)'});
    }
    if(payload.cursor!==null && !payload.cursorFollows) shapes.push({type:'line',xref:'x',yref:'paper',x0:payload.cursor,x1:payload.cursor,y0:0,y1:1,line:{color:'#a7b3b5',width:1,dash:'dot'}});
    return {autosize:true,paper_bgcolor:'#1c2531',plot_bgcolor:'#1c2531',margin:{l:66,r:18,t:42,b:52},
      font:{family:'Arial, sans-serif',size:12,color:'#c9d6e4'},hovermode:'x unified',dragmode:state.dragmode,
      uirevision:payload.epoch,showlegend:spec.series.length>1,legend:{orientation:'h',x:0,y:1.04,yanchor:'bottom'},
      hoverlabel:{bgcolor:'#283747',bordercolor:'#78d5d2',font:{color:'#f0f5fb'}},shapes,annotations,
      xaxis:{...axis,title:{text:'Elapsed time'},showgrid:false,range,autorange:false,tickvals:ticks,ticktext:ticks.map(v=>clock(v)+(range[1]-range[0]<10?'.'+Math.floor(Math.max(0,v%1)*10):''))},
      yaxis:{...axis,title:{text:spec.unit},range:spec.yRange,autorange:false,showgrid:true,gridcolor:'rgba(200,200,200,.25)',
        ...(spec.ticks?{tickvals:spec.ticks,ticktext:spec.ticks.map(v=>Number(v.toFixed(2)).toString())}:{tickmode:'auto'})}};
  }
  async function renderOne(spec,payload) {
    const s=cards.get(spec.key)||makeCard(spec);
    s.heading.textContent=spec.title; s.value.textContent=spec.value; s.value.title=spec.value;
    s.peaks=spec.peaks||[];
    const peakState=JSON.stringify(s.peaks.map(p=>[p.metric,p.elapsed,p.value,p.label,p.context.summary]));
    if(s.peakState!==peakState) {
      s.peakState=peakState; s.peakButtons.replaceChildren();
      for(const peak of s.peaks) {
        const button=document.createElement('button'); button.type='button'; button.dataset.metric=peak.metric;
        button.textContent=peak.label; button.title=peak.label+'\n'+peak.context.summary+'\nClick to inspect recorded context. Equal maxima use the first sample.';
        button.addEventListener('click',()=>selectPeak(spec.key,peak)); s.peakButtons.append(button);
      }
      if(!s.peaks.length)s.peakButtons.textContent='Peak unavailable · no measurements';
    }
    const colors=['#78d5d2','#ff9494'];
    const traces=spec.series.map((series,i)=>({type:'scatter',mode:'lines',name:series.name,x:spec.x,y:series.y,
      customdata:spec.x.map(clock),line:{color:colors[i],width:2},connectgaps:false,
      hovertemplate:'%{customdata}<br>%{y:.2f} '+spec.unit+'<br>Click to inspect processes<extra>%{fullData.name}</extra>'}));
    for(const peak of s.peaks)traces.push({type:'scatter',mode:'markers',name:peak.name+' peak',
      x:[peak.elapsed],y:[peak.value],showlegend:false,meta:{peak:peak.metric},
      marker:{color:'#ff8585',size:10,symbol:'diamond',line:{color:'#1c2531',width:1}},
      customdata:[escapeText(peak.label)+'<br>'+escapeText(peak.context.summary)+'<br>Click for processes and recorded phases'],
      hovertemplate:'%{customdata}<extra></extra>'});
    s.busy=true;
    try { await Plotly.react(s.plot,traces,layout(spec,payload,s),config()); }
    finally { s.busy=false; }
    if(!s.initialized) {
      s.initialized=true;
      s.plot.on('plotly_relayout', async event=>{
        if(s.busy)return;
        if(event.dragmode)s.dragmode=event.dragmode;
        const xChanged=Object.keys(event).some(k=>/^xaxis\.(range|autorange)/.test(k));
        const yChanged=Object.keys(event).some(k=>/^yaxis\.(range|autorange)/.test(k));
        if(!xChanged&&!yChanged)return;
        const x=s.plot._fullLayout.xaxis.range.slice(), y=s.plot._fullLayout.yaxis.range.slice();
        if(xChanged && latest?.live) {
          const width=Math.max(.2,x[1]-x[0]), end=latest.range[1];
          x[0]=Math.max(latest.earliest,end-width); x[1]=end;
          s.busy=true;
          try { await Plotly.relayout(s.plot,{'xaxis.range':x}); } finally { s.busy=false; }
        }
        emit({type:'range',key:spec.key,x:xChanged?x:null,y:yChanged?y:null});
      });
      s.plot.on('plotly_click',event=>{
        const point=event.points?.[0], x=point?.x;
        const peak=s.peaks.find(p=>p.metric===point?.data?.meta?.peak);
        if(peak)selectPeak(spec.key,peak);
        else if(Number.isFinite(x))emit({type:'select',key:spec.key,elapsed:x});
      });
      s.plot.on('plotly_doubleclick',()=>emit({type:'action',key:spec.key,action:'reset'}));
    }
  }
  async function drain() {
    if(rendering)return;
    rendering=true;
    try {
      while(pending) {
        const payload=pending; pending=null; latest=payload;
        for(const spec of payload.charts) await renderOne(spec,payload);
      }
    } catch(error) { const el=document.getElementById('error'); el.hidden=false; el.textContent='Chart error: '+String(error); }
    finally { rendering=false; }
  }
  window.Chequebo={
    render(payload){pending=payload;drain();},
    fullscreen(key){full=key;activate(null);document.body.classList.toggle('full',!!key);for(const [id,s]of cards)s.card.classList.toggle('full',id===key);requestAnimationFrame(()=>{for(const [id,s]of cards)if(!key||id===key)Plotly.Plots.resize(s.plot);});},
    // Read-only diagnostics for integration tests and support.
    state(){return {active,full,rendering,latest,plots:Object.fromEntries([...cards].map(([key,s])=>[key,{x:s.plot._fullLayout?.xaxis.range,y:s.plot._fullLayout?.yaxis.range,points:s.plot.data?.[0]?.x?.length}]))};}
  };
  new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;emit({type:'ready'});});
})();

(function(){
  'use strict';
  const cfg=window.ZECAO_CONFIG||{};
  const validUrl=u=>typeof u==='string'&&/^https:\/\//i.test(u);
  const validId=id=>typeof id==='string' && /^[A-Za-z0-9_-]{11}$/.test(id);
  document.querySelectorAll('[data-link]').forEach(el=>{
    let key=el.dataset.link;
    const url=key==='youtubeVideos' && validUrl(cfg.youtube)?cfg.youtube.replace(/\/$/,'')+'/videos':cfg[key];
    if(validUrl(url)){el.href=url;el.target='_blank';el.rel='noopener noreferrer';}
    else{el.removeAttribute('href');el.removeAttribute('target');el.setAttribute('aria-disabled','true');el.classList.add('disabled');}
  });
  // Discord ganha um link próprio quando for configurado, sem mudar o HTML manualmente.
  if(validUrl(cfg.discord)){
    const line=document.querySelector('#discord-line');
    const a=document.createElement('a');a.href=cfg.discord;a.target='_blank';a.rel='noopener noreferrer';
    a.className='discord-visit';a.textContent='Entrar na comunidade';
    const status=line.querySelector('#discord-status');status.replaceWith(a);
    const desc=line.querySelector('.discord-description');desc.textContent='O espaço da comunidade ZECAO.';
  }
  const button=document.getElementById('play-trailer');
  button?.addEventListener('click',event=>{
    if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0)return;
    if(!validId(cfg.trailerVideoId))return;
    event.preventDefault();
    const iframe=document.createElement('iframe');
    iframe.src='https://www.youtube-nocookie.com/embed/'+cfg.trailerVideoId+'?autoplay=1&rel=0';
    iframe.title='ZECAO — apresentação do canal';
    iframe.allowFullscreen=true;
    iframe.referrerPolicy='strict-origin-when-cross-origin';
    iframe.allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
    document.getElementById('trailer-frame').replaceChildren(iframe);
  });
  const grid=document.getElementById('videos-list');
  function node(tag,cls,txt){const el=document.createElement(tag);if(cls)el.className=cls;if(txt!==undefined)el.textContent=txt;return el;}
  function svgRef(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#'+name);svg.append(use);return svg;}
  function loadThumbnail(img,video){
    const jpg='https://i.ytimg.com/vi/'+video.id+'/';
    const webp='https://i.ytimg.com/vi_webp/'+video.id+'/';
    const local='assets/hero.webp';
    const supplied=validUrl(video.thumbnail)&&/^https:\/\/i\.ytimg\.com\//.test(video.thumbnail)?video.thumbnail:null;
    const candidates=[...new Set([
      webp+'maxresdefault.webp',jpg+'maxresdefault.jpg',
      webp+'sddefault.webp',jpg+'sddefault.jpg',
      supplied,jpg+'hqdefault.jpg',local
    ].filter(Boolean))];
    let next=0;
    function advance(){
      if(next>=candidates.length){img.onload=null;img.onerror=null;return;}
      img.src=candidates[next++];
    }
    img.onerror=advance;
    img.onload=()=>{
      // O YouTube pode responder com um placeholder 120 × 90, mesmo sem erro HTTP.
      if(candidates[next-1]!==local&&img.naturalWidth<=120){advance();return;}
      img.onload=null;img.onerror=null;
    };
    advance();
  }
  function showVideos(items){
    if(!Array.isArray(items))return;
    const seen=new Set();
    const limit=Number.isInteger(cfg.maxVisibleVideos)&&cfg.maxVisibleVideos>0?cfg.maxVisibleVideos:4;
    const filtered=items.filter(v=>{
      if(!v||!validId(v.id)||v.id===cfg.trailerVideoId||seen.has(v.id))return false;
      seen.add(v.id);return true;
    }).slice(0,limit);
    if(!filtered.length)return;
    grid.replaceChildren();
    for(const v of filtered){
      const link=node('a','video-tile');link.dataset.videoId=v.id;link.href='https://www.youtube.com/watch?v='+v.id;link.target='_blank';link.rel='noopener noreferrer';
      const title=String(v.title||'Vídeo no ZECAO');link.setAttribute('aria-label','Assistir no YouTube: '+title);
      const media=node('div','video-photo');const img=node('img');
      img.dataset.videoId=v.id;img.alt='Capa do vídeo '+title;img.loading='lazy';img.decoding='async';
      img.width=1280;img.height=720;
      loadThumbnail(img,v);
      const play=node('span','video-play');play.append(svgRef('icon-play'));media.append(img,play);
      const details=node('div','video-details');
      let date='Vídeo no YouTube';
      const parsed=new Date(v.published);
      if(!Number.isNaN(parsed.getTime()))date=parsed.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'America/Sao_Paulo'});
      details.append(node('span','video-date',date),node('h3','',title));
      link.append(media,details);grid.append(link);
    }
  }
  // Os cards gerados no build já contêm o feed atual, inclusive sem JavaScript.
  const initialImages=grid.querySelectorAll('img[data-video-id]');
  if(initialImages.length){
    initialImages.forEach(img=>{
      if(validId(img.dataset.videoId))loadThumbnail(img,{id:img.dataset.videoId,thumbnail:img.dataset.thumbnail||img.getAttribute('src')});
    });
  }else{
    showVideos(cfg.fallbackVideos||[]);
  }
  if(/^https?:$/.test(location.protocol)){
    const feed=cfg.videosFeedPath||'data/videos.json';
    fetch(feed+'?v='+Math.floor(Date.now()/1800000),{cache:'no-store'})
      .then(r=>{if(!r.ok)throw new Error('feed indisponível');return r.json();})
      .then(data=>{if(!data||!Array.isArray(data.videos)||!data.videos.length)return;showVideos(data.videos);})
      .catch(()=>{});
  }
  document.getElementById('year').textContent=String(new Date().getFullYear());
})();

(function(){
  var q=document.getElementById('q');
  if(q){q.addEventListener('input',function(){
    var v=this.value.trim().toLowerCase();
    document.querySelectorAll('.nav-link').forEach(function(a){
      a.classList.toggle('hidden', v && a.textContent.toLowerCase().indexOf(v)<0);
    });
  });}
  var sb=document.getElementById('sidebar'),mb=document.getElementById('menuBtn'),sc=document.getElementById('scrim');
  function close(){sb&&sb.classList.remove('open');sc&&sc.classList.remove('show');}
  if(mb){mb.addEventListener('click',function(){sb.classList.toggle('open');sc.classList.toggle('show');});}
  if(sc){sc.addEventListener('click',close);}
  var tlinks={};document.querySelectorAll('.toc a').forEach(function(a){tlinks[a.getAttribute('href').slice(1)]=a;});
  var heads=document.querySelectorAll('.doc h2[id],.doc h3[id]');
  if(heads.length){
    var obs=new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(e.isIntersecting){
          document.querySelectorAll('.toc a.active').forEach(function(x){x.classList.remove('active');});
          var a=tlinks[e.target.id];if(a){a.classList.add('active');a.scrollIntoView({block:'nearest'});}
        }
      });
    },{rootMargin:'-8% 0px -78% 0px'});
    heads.forEach(function(h){obs.observe(h);});
  }
  var bar=document.getElementById('progress'),top=document.getElementById('topBtn');
  function onScroll(){
    var h=document.documentElement,s=h.scrollTop||document.body.scrollTop,max=(h.scrollHeight-h.clientHeight)||1;
    if(bar)bar.style.width=(s/max*100)+'%';
    if(top)top.classList.toggle('show',s>320);
  }
  window.addEventListener('scroll',onScroll,{passive:true});onScroll();
  if(top){top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});}
})();

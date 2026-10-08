/* SAFAR APP — real read-only operations PWA. No carrier writes. */
(() => {
'use strict';
const app = document.getElementById('app');
const preview = new URLSearchParams(location.search).get('demo') === '1';
const icons = {
  home:'<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
  orders:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  box:'<path d="m12 2 9 5-9 5-9-5 9-5ZM3 7v10l9 5 9-5V7M12 12v10"/>',
  users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a2 2 0 0 1 1 1.8l-1 1.8a2 2 0 0 1-2.1.9l-1.5-.3a8 8 0 0 1-2 1l-.5 1.5H11l-.5-1.5a8 8 0 0 1-2-1l-1.5.3a2 2 0 0 1-2.1-.9l-1-1.8a2 2 0 0 1 1-1.8L6 14a8 8 0 0 1 0-4l-1.1-1a2 2 0 0 1-1-1.8l1-1.8a2 2 0 0 1 2.1-.9l1.5.3a8 8 0 0 1 2-1L11 2.3h2l.5 1.5a8 8 0 0 1 2 1l1.5-.3a2 2 0 0 1 2.1.9l1 1.8a2 2 0 0 1-1 1.8L18 10a8 8 0 0 1 0 4z"/>',
  search:'<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  trend:'<path d="m3 17 6-6 4 4 8-9M14 6h7v7"/>',
  arrow:'<path d="m9 18 6-6-6-6"/>',
  back:'<path d="m15 18-6-6 6-6"/>',
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  photo:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  shield:'<path d="m12 22 7-4V7l-7-4-7 4v11l7 4zM9 12l2 2 4-4"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  truck:'<path d="M1 5h14v12H1zM15 9h4l4 4v4h-8M5 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM19 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/>',
  logout:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  refresh:'<path d="M3 11a9 9 0 0 1 15-6l3 3M21 3v5h-5M21 13a9 9 0 0 1-15 6l-3-3M3 21v-5h5"/>',
  lock:'<rect x="5" y="10" width="14" height="12" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/>'
};
const icon = (name, cls='') => '<svg class="'+cls+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">'+(icons[name]||icons.box)+'</svg>';
const escape = val => String(val ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = n => n === null || n === undefined ? '—' : new Intl.NumberFormat('uk-UA',{maximumFractionDigits:0}).format(Number(n))+' ₴';
const stateNames={created:'ТТН створено',invalid:'Потрібна перевірка',failed:'Помилка',uncertain:'Перевірити у НП',processing:'В обробці',collecting:'Новий',deleted:'Видалено'};
const state = s => '<span class="state state-'+escape(s||'invalid')+'">'+escape(stateNames[s]||'Уточнити')+'</span>';
const dateStr = n => { const time = Number(n); if(!time) return '—'; try{return new Intl.DateTimeFormat('uk-UA',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(time<1e11?time*1000:time));}catch{return '—';}};
const demoData=[
 {id:'preview-01',state:'created',recipient:'Марія Коваленко',city:'Одеса',area:'Одеська',warehouse:'18',declared:3850,cod:0,ttn:'PREVIEW-0001',sender_profile:'default',phone:'Тестовий номер',created_at:1791400000,description:'Кросівки · 1 пара',photos:[{index:0}],weight:1.2},
 {id:'preview-02',state:'invalid',recipient:'Олена Демченко',city:'Київ',area:'Київська',warehouse:'8',declared:2190,cod:0,ttn:'',sender_profile:'default',created_at:1791395300,description:'Худі · чорний',photos:[{index:0},{index:1}]},
 {id:'preview-03',state:'processing',recipient:'Андрій Мельник',city:'Львів',area:'Львівська',warehouse:'41',declared:4990,cod:2500,ttn:'',sender_profile:'default',created_at:1791389000,description:'Черевики · 43 розмір',photos:[{index:0}]},
 {id:'preview-04',state:'created',recipient:'Тетяна Романюк',city:'Дніпро',area:'Дніпропетровська',warehouse:'11',declared:1200,cod:0,ttn:'PREVIEW-0004',sender_profile:'default',created_at:1791385000,description:'Сумка · шкіра',photos:[]},
 {id:'preview-05',state:'collecting',recipient:'Віктор Савченко',city:'Чернівці',area:'Чернівецька',warehouse:'5',declared:920,cod:0,ttn:'',sender_profile:'default',created_at:1791381000,description:'Футболка',photos:[{index:0}]},
 {id:'preview-06',state:'failed',recipient:'Ірина Бондар',city:'Вінниця',area:'Вінницька',warehouse:'3',declared:1690,cod:0,ttn:'',sender_profile:'default',created_at:1791377000,description:'Кеди · білі',photos:[{index:0}]}
];
const view={tab:'home',filter:'all',search:'',selected:null,orders:[],counts:{},authed:false,demo:preview,loading:true,error:'',now:Date.now(),toast:'',detailCache:{}};
const tabs=[['home','Головна','home'],['orders','Замовлення','orders'],['shipments','ТТН','truck'],['senders','Відправники','users'],['settings','Налаштування','settings']];
const topbar=()=>'<header class="topbar"><div class="mobile-header"><div class="mobile-brand">SAFAR</div><div class="mobile-caption">SHIPPING OPERATIONS OS</div></div><div class="top-date">SAFAR / OPERATIONS / '+new Intl.DateTimeFormat('uk-UA',{day:'2-digit',month:'long',year:'numeric'}).format(new Date())+'</div><div class="top-actions"><span class="status-chip"><span class="dot"></span>'+(view.demo?'DEMO MODE':'SYSTEM ONLINE')+'</span><button class="icon-button" type="button" title="Оновити" data-action="refresh">'+icon('refresh')+'</button><div class="profile-mark">S</div></div></header>';
const sidebar=()=>'<aside class="sidebar"><div class="brand"><div class="brand-name">SAFAR</div><div class="brand-caption">SHIPPING OPERATIONS OS</div><div class="brand-line"></div></div><div class="aside-label">CONTROL CENTER</div><nav aria-label="Основна навігація">'+tabs.map(t=>'<button class="nav-link '+(view.tab===t[0]?'active':'')+'" data-tab="'+t[0]+'">'+icon(t[2])+' '+t[1]+'</button>').join('')+'</nav><div class="sidebar-footer"><div class="connection"><span class="dot"></span>'+(view.demo?'МАКЕТ · БЕЗ ВІДПРАВОК':'ПІДКЛЮЧЕНО ДО SAFAR NP BOT')+'</div><div class="foot-sub">SAFAR APP · V0.1 · PRIVATE OS</div></div></aside>';
const bottom=()=>'<nav class="bottom-nav" aria-label="Мобільна навігація">'+tabs.map(t=>'<button class="bottom-item '+(view.tab===t[0]?'active':'')+'" data-tab="'+t[0]+'">'+icon(t[2])+'<span>'+t[1]+'</span></button>').join('')+'</nav>';
const title=(eyebrow,heading,desc,button='')=>'<div class="page-header"><div><div class="eyebrow">'+eyebrow+'</div><h1>'+heading+'</h1><div class="page-subtitle">'+desc+'</div></div>'+button+'</div>';
const counts=()=>{const c=view.counts;return {all:Object.values(c).reduce((x,y)=>x+(Number(y)||0),0),new:(Number(c.collecting)||0),attention:(Number(c.invalid)||0)+(Number(c.failed)||0)+(Number(c.uncertain)||0),created:Number(c.created)||0,processing:Number(c.processing)||0};};
const empty=(heading,desc)=>'<div class="empty">'+icon('box')+'<h3>'+heading+'</h3><p>'+desc+'</p></div>';
const imagePreview=(o,idx=0,cls='order-img')=>{
 const photo=o.photos&&o.photos[idx];
 if(photo&&!view.demo&&photo.url) return '<div class="'+cls+' has-photo"><img loading="lazy" alt="Фото товару" src="'+escape(photo.url)+'"></div>';
 return '<div class="'+cls+'">'+icon('box')+'</div>';
};
const oneOrder=o=>'<button class="order-row" data-order="'+escape(o.id)+'">'+imagePreview(o)+'<div class="order-main"><div class="order-name">'+escape(o.recipient)+'</div><div class="order-place">'+escape([o.city,o.warehouse?'Відділення №'+o.warehouse:''].filter(Boolean).join(' · ')||'Місто не визначено')+'</div><div class="order-extra">'+escape(dateStr(o.created_at))+'</div></div><div class="order-meta"><div class="order-money">'+money(o.declared)+'</div>'+state(o.state)+'</div><div class="order-arrow">›</div></button>';
const stat=(label,value,sub,ico,hot=false)=>'<div class="metric '+(hot?'hot':'')+'"><div class="metric-top"><span>'+label+'</span><span class="metric-icon">'+icon(ico)+'</span></div><div class="metric-num">'+value+'</div><div class="metric-foot">'+sub+'</div></div>';
const panelOrders=(data,limit=5)=>'<div class="orders-list">'+(data.length?data.slice(0,limit).map(oneOrder).join(''):empty('Поки немає замовлень','Перешли замовлення з фото своєму Telegram-боту SAFAR.'))+'</div>';
function home(){
 const c=counts(),num=Math.min(c.all,999);
 return '<div class="screen">'+title('SAFAR / COMMAND CENTER','Контроль відправок','Єдиний простір для замовлень, накладних і контролю доставки.')+
 '<div class="hero"><div class="hero-copy"><div class="eyebrow">SHIPPING. REFINED.</div><h2>Every order.<br>Under control.</h2><p>Від повідомлення з фото до створеної ТТН. Ваші операції — в одній системі.</p><button class="button-primary" data-tab="orders">'+icon('orders')+' Відкрити замовлення '+icon('arrow')+'</button></div><div class="hero-art">'+icon('box')+'</div></div>'+
 '<div class="metrics">'+stat('Всього замовлень',num,'У журналі відправлень','orders',true)+stat('Потребують уваги',c.attention,'Помилки та перевірки','bell')+stat('ТТН створено',c.created,'Документи оформлені','box')+stat('В обробці',c.processing,'Очікують завершення','clock')+'</div>'+
 '<div class="grid-panels"><div class="panel"><div class="panel-header"><h3>Останні замовлення</h3><button class="section-more" data-tab="orders">Усі замовлення →</button></div>'+panelOrders(view.orders,5)+'</div><div class="panel"><div class="panel-header"><h3>SAFAR SYSTEM</h3><span class="panel-small">LIVE OPERATIONS</span></div><div class="keyval"><span class="keyval-label">Джерело</span><span class="keyval-value">Telegram + PostgreSQL</span></div><div class="keyval"><span class="keyval-label">Оформлення ТТН</span><span class="keyval-value">Нова Пошта</span></div><div class="keyval"><span class="keyval-label">Статус доступу</span><span class="keyval-value">'+(view.demo?'Демонстрація':'Підтверджено Telegram')+'</span></div><div class="divider"></div><div class="eyebrow">ENGINEERED FOR CLARITY</div><p class="page-subtitle">Створення нових ТТН залишається в захищеному Telegram-боті до завершення перевірки операційних функцій застосунку.</p></div></div>'+
 '</div>';
}
function orders(){
 const filtered=view.orders.filter(o=>{
  if(view.filter!=='all'&& !(view.filter==='attention'?['failed','invalid','uncertain'].includes(o.state):o.state===view.filter))return false;
  const hay=(o.recipient+' '+o.city+' '+o.ttn+' '+o.warehouse).toLocaleLowerCase();
  return hay.includes(view.search.toLocaleLowerCase());
 });
 const c=counts();
 return '<div class="screen">'+title('WORKSPACE / ORDER MANAGEMENT','Замовлення','Кожен товар, кожна деталь, кожне відправлення — під контролем.')+
 '<div class="toolbar"><label class="search">'+icon('search')+'<input id="orderSearch" type="search" autocomplete="off" aria-label="Пошук замовлень" placeholder="Пошук за ім’ям, містом, ТТН..." value="'+escape(view.search)+'"></label><button class="button-quiet" data-action="refresh" title="Оновити">'+icon('refresh')+'</button></div>'+
 '<div class="chip-list">'+[['all','Усі · '+c.all],['collecting','Нові · '+c.new],['processing','В обробці'],['created','З ТТН · '+c.created],['attention','Увага · '+c.attention]].map(t=>'<button class="filter-chip '+(view.filter===t[0]?'active':'')+'" data-filter="'+t[0]+'">'+t[1]+'</button>').join('')+'</div>'+
 '<div class="panel"><div class="panel-header"><h3>Реєстр замовлень</h3><span class="panel-small">'+filtered.length+' записів</span></div>'+panelOrders(filtered,60)+'</div></div>';
}
const kv=(a,b)=>'<div class="keyval"><span class="keyval-label">'+a+'</span><span class="keyval-value">'+b+'</span></div>';
function detail(){
 const o=view.detailCache[view.selected] || view.orders.find(x=>x.id===view.selected);
 if(!o)return orders();
 const photo=(o.photos||[]).slice(0,8);
 return '<div class="screen"><button class="detail-back" data-tab="orders">'+icon('back')+' Назад до замовлень</button>'+
 title('ORDER / DETAIL','Картка замовлення',escape(o.recipient)+' · '+escape(o.city))+
 '<div class="details-grid"><div><div class="detail-block"><div class="detail-head"><h3>Товари та фото</h3>'+state(o.state)+'</div><div class="eyebrow">'+photo.length+' ФОТО З TELEGRAM</div><div class="gallery">'+(photo.length?photo.map((p,i)=>imagePreview(o,i,'gallery-item')).join(''):imagePreview(o,0,'gallery-item'))+'</div>'+
 '<div class="divider"></div>'+kv('Опис',escape(o.description||'Без опису'))+kv('Оголошена вартість',money(o.declared))+kv('Післяплата',money(o.cod))+'</div>'+
 '<div class="detail-block"><h3>Одержувач</h3>'+kv('ПІБ',escape(o.recipient))+kv('Телефон',escape(o.phone||'Перевіряється'))+kv('Місто',escape(o.city||'—'))+kv('Область',escape(o.area||'—'))+kv('Відділення НП',escape(o.warehouse||'—'))+kv('Вага',o.weight?escape(o.weight)+' кг':'—')+'</div></div>'+
 '<div><div class="detail-block"><h3>Накладна / ТТН</h3><div class="eyebrow">НОВА ПОШТА</div><p class="ttn-large">'+escape(o.ttn||'Ще не створено')+'</p>'+state(o.state)+'<div class="divider"></div>'+
 '<button class="button-quiet" data-action="copy" data-ttn="'+escape(o.ttn||'')+'" '+(!o.ttn||view.demo?'disabled':'')+'>'+icon('copy')+' Скопіювати ТТН</button>'+
 '<div class="notice">'+(view.demo?'Демо. Реальні накладні не створюються.':'Цей екран лише переглядає журнал. Для виправлення та повторного створення ТТН використовуй чинний Telegram-бот; автоматичні дублікати заборонені.')+'</div></div>'+
 '<div class="detail-block"><h3>Операційна інформація</h3>'+kv('Обліковий запис',escape(o.sender_profile||'default'))+kv('Створено',escape(dateStr(o.created_at)))+kv('Стан',escape(stateNames[o.state]||o.state))+(o.error?'<div class="notice">'+escape(o.error)+'</div>':'')+'</div></div></div></div>';
}
function shipments(){
 const records=view.orders.filter(o=>o.ttn);
 return '<div class="screen">'+title('LOGISTICS / SHIPMENTS','Відправлення','Накладні та операційний статус. Фактична доставка підтверджується Новою Поштою.')+
 '<div class="metrics">'+stat('Створено ТТН',counts().created,'Не дорівнює «доставлено»','truck',true)+stat('Відправлення у списку',records.length,'Останні збережені записи','box')+'</div>'+
 '<div class="panel"><div class="panel-header"><h3>Активні документи</h3><span class="panel-small">ТІЛЬКИ ПЕРЕГЛЯД</span></div>'+panelOrders(records,60)+'</div></div>';
}
function senders(){
 return '<div class="screen">'+title('IDENTITY / ACCOUNTS','Відправники','Кожна накладна назавжди прив’язана до свого початкового відправника.')+
 '<div class="sender-card"><div style="display:flex;align-items:center;gap:14px"><div class="sender-mark">S</div><div><div class="eyebrow">ACTIVE PROFILE</div><div class="sender-label">Основний кабінет НП</div><div class="sender-desc">default · поточні дозволені відправлення</div></div></div>'+state('created')+'</div>'+
 '<div class="sender-card"><div style="display:flex;align-items:center;gap:14px"><div class="sender-mark" style="background:#24252b;color:#a3a4ae">F</div><div><div class="eyebrow">CONNECTION PENDING</div><div class="sender-label">Кабінет ФОП</div><div class="sender-desc">Потрібна підтверджена API-авторизація</div></div></div>'+state('invalid')+'</div>'+
 '<div class="notice">Оформлення від імені іншого ФОП не вмикається лише за номером телефону. Налаштування відправників доступне через авторизовану команду /sender у Telegram.</div></div>';
}
function settings(){
 return '<div class="screen">'+title('SYSTEM / PREFERENCES','Налаштування','Безпека, підключення та інформація про систему.')+
 '<div class="detail-block"><h3>Сесія та підключення</h3>'+kv('SAFAR APP','v0.1 · Android-first PWA')+kv('Тип доступу',view.demo?'Відкритий демонстраційний макет':'Підписаний вхід Telegram')+kv('Джерело замовлень',view.demo?'Тільки тестові записи':'Особистий журнал PostgreSQL')+kv('Операції з ТТН','Заблоковані у додатку')+'</div>'+
 '<div class="detail-block"><h3>Керування</h3><button class="button-quiet" data-action="refresh">'+icon('refresh')+' Оновити дані</button> '+(view.authed?'<button class="button-quiet" data-action="logout">'+icon('logout')+' Вийти</button>':'')+'</div>'+
 '<div class="notice">Для встановлення на Android відкрий меню браузера → «Додати на головний екран». Особисті дані замовлень не кешуються у PWA.</div></div>';
}
function toast(msg){view.toast=msg;const item=document.createElement('div');item.style.cssText='position:fixed;bottom:100px;left:50%;transform:translateX(-50%);z-index:200;background:#321f28;border:1px solid #9b4453;border-radius:12px;padding:14px 19px;color:#fff;max-width:80vw;box-shadow:0 15px 50px #000';item.textContent=msg;document.body.appendChild(item);setTimeout(()=>item.remove(),3500)}
function render(){
 if(!view.authed&&!view.demo){app.className='safar-shell';app.innerHTML='<div class="locked"><div class="locked-card"><div class="locked-symbol">SAFAR</div><div class="eyebrow">PRIVATE SHIPPING OS</div><h2>Захищений простір</h2><p>Для перегляду ваших замовлень відкрий SAFAR через кнопку <b>/app</b> в особистому чаті з Telegram-ботом. Прямий доступ без перевіреного Telegram-входу закритий.</p><div class="locked-actions"><a href="/safar?demo=1" class="button-primary">Подивитися демо-інтерфейс →</a></div></div></div>';return;}
 app.className='safar-shell';
 const pages={home,orders,shipments,senders,settings};
 const content=view.selected?detail():(pages[view.tab]||home)();
 app.innerHTML=sidebar()+'<main class="workspace">'+topbar()+(view.demo?'<div class="demo-banner"><strong>DEMO</strong> Вигадані дані для попереднього перегляду. Реальні ТТН не створюються.</div>':'')+content+'<footer class="footer-note"><span>SAFAR · SHIPPING OPERATIONS OS</span><span>BUILT FOR CONTROL · V0.1</span></footer></main>'+bottom();
 const search=document.getElementById('orderSearch');
 if(search){search.selectionStart=search.selectionEnd=view.search.length;}
}
async function getJSON(url,options={}){
 const res=await fetch(url,{credentials:'same-origin',cache:'no-store',...options});
 if(!res.ok)throw new Error('Помилка завантаження ('+res.status+')');
 return await res.json();
}
async function fetchOrders(){
 if(view.demo){view.orders=demoData.map(o=>({...o}));view.counts=demoData.reduce((acc,o)=>{acc[o.state]=(acc[o.state]||0)+1;return acc},{});render();return;}
 try{const data=await getJSON('/api/safar/orders?limit=60');view.orders=data.orders||[];view.counts=data.counts||{};render();}
 catch(err){view.error=err.message;render();toast('Не вдалося завантажити замовлення. Повтори спробу.');}
}
async function fetchDetail(id){
 if(view.demo){view.detailCache[id]=view.orders.find(o=>o.id===id);render();return;}
 try{const data=await getJSON('/api/safar/orders/'+encodeURIComponent(id));view.detailCache[id]=data.order;render();}
 catch(err){render();toast('Не вдалося завантажити картку.');}
}
async function boot(){
 if(preview){view.loading=false;await fetchOrders();return;}
 const tg=window.Telegram && window.Telegram.WebApp;
 if(!tg||!tg.initData){view.loading=false;render();return;}
 try{
   tg.ready();tg.expand();
   await getJSON('/api/safar/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg.initData})});
   view.authed=true;view.loading=false;
   await fetchOrders();
 }catch(err){view.loading=false;render();}
}
document.addEventListener('click',async e=>{
 const control=e.target.closest('[data-tab],[data-filter],[data-order],[data-action]');
 if(!control)return;
 if(control.dataset.tab){view.tab=control.dataset.tab;view.selected=null;view.filter='all';window.scrollTo({top:0,behavior:'instant'});render();return}
 if(control.dataset.filter){view.filter=control.dataset.filter;render();return}
 if(control.dataset.order){view.selected=control.dataset.order;window.scrollTo({top:0,behavior:'instant'});render();await fetchDetail(view.selected);return}
 if(control.dataset.action==='refresh'){await fetchOrders();toast('Дані оновлено');return}
 if(control.dataset.action==='copy'&&control.dataset.ttn&&!view.demo){try{await navigator.clipboard.writeText(control.dataset.ttn);toast('ТТН скопійовано');}catch{toast('Не вдалося скопіювати');}return}
 if(control.dataset.action==='logout'){try{await getJSON('/api/safar/logout',{method:'POST'});}catch{}view.authed=false;render();}
});
document.addEventListener('input',e=>{
 if(e.target.id==='orderSearch'){const pos=e.target.selectionStart;view.search=e.target.value;render();const x=document.getElementById('orderSearch');if(x){x.focus();try{x.setSelectionRange(pos,pos)}catch{}}}
});
window.addEventListener('pageshow',e=>{if(e.persisted&&view.authed)fetchOrders()});
boot();
})();
'use strict';
(()=>{
 const key='wine-age-confirmed-v1',dialog=document.getElementById('age-dialog'),button=document.getElementById('age-confirm');
 let confirmed=false;
 try{confirmed=localStorage.getItem(key)==='yes';}catch{}
 dialog.addEventListener('cancel',event=>event.preventDefault());
 button.addEventListener('click',()=>{
  try{localStorage.setItem(key,'yes');}catch{}
  dialog.close();document.body.classList.toggle('modal-open',!!document.querySelector('dialog[open]'));
 });
 if(!confirmed){dialog.showModal();document.body.classList.add('modal-open');}
})();

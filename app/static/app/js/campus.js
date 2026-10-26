'use strict';
(() => {
  const title = document.querySelector('.campus-content h1');
  const crumb = document.querySelector('.campus-current-page');
  if (title && crumb) crumb.textContent = title.textContent.trim();
  document.querySelectorAll('.campus-mobile-nav a').forEach(link => {
    if (link.pathname === location.pathname) link.setAttribute('aria-current', 'page');
  });

  const sidebar = document.getElementById('campus-navigation');
  const scrim = document.querySelector('[data-campus-close]');
  const menuButtons = [...document.querySelectorAll('[data-campus-menu]')];
  let menuOpener;
  function closeMenu() {
    sidebar?.classList.remove('is-open');
    scrim?.classList.remove('is-open');
    if (scrim) scrim.hidden = true;
    menuButtons.forEach(button => button.setAttribute('aria-expanded', 'false'));
    document.body.style.overflow = '';
    menuOpener?.focus();
  }
  menuButtons.forEach(button => button.addEventListener('click', () => {
    if (sidebar.classList.contains('is-open')) return closeMenu();
    menuOpener = button;
    sidebar.classList.add('is-open');
    scrim.hidden = false;
    scrim.classList.add('is-open');
    menuButtons.forEach(item => item.setAttribute('aria-expanded', 'true'));
    document.body.style.overflow = 'hidden';
    sidebar.querySelector('a').focus();
  }));
  scrim?.addEventListener('click', closeMenu);
  document.addEventListener('keydown', event => {
    if (!sidebar?.classList.contains('is-open')) return;
    if (event.key === 'Escape') closeMenu();
    if (event.key === 'Tab') {
      const links = [...sidebar.querySelectorAll('a[href], button:not([disabled])')];
      const first = links[0], last = links[links.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  // Associate the existing field labels without changing IDs used by the AJAX forms.
  document.querySelectorAll('label:not([for])').forEach((label, i) => {
    if (label.querySelector('input,select,textarea')) return;
    const control = label.parentElement.querySelector('input:not([type=hidden]),select,textarea');
    if (!control) return;
    if (!control.id) control.id = 'campus-control-' + i;
    label.htmlFor = control.id;
  });

  document.querySelectorAll('.campus-content table').forEach(table => {
    if (!table.closest('.overflow-x-auto,.overflow-auto,.campus-table-wrap')) {
      const wrapper = document.createElement('div'); wrapper.className = 'campus-table-wrap';
      table.before(wrapper); wrapper.append(table);
    }
    if (['listar_estudiantes','listar_estudiantes_anio','listar_estudiantes_anio_var','listar_usuarios','listar_estudiantes_admin'].includes(document.body.dataset.page)) {
      table.dataset.campusCards = '';
      const labels = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
      table.querySelectorAll('tbody tr').forEach(row => [...row.cells].forEach((cell, i) => {
        if (!cell.hasAttribute('colspan') && labels[i]) cell.dataset.label = labels[i];
      }));
    }
  });

  function makeButton(text, className) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = className; button.textContent = text; return button;
  }
  document.querySelectorAll('form[data-campus-wizard]').forEach(form => {
    form.querySelectorAll('[required]').forEach(control => {
      const label = control.labels?.[0];
      if (label && !label.textContent.includes('*')) {
        const marker = document.createElement('span');
        marker.textContent = ' *'; marker.className = 'text-red-600'; label.append(marker);
      }
    });
    const panels = [...form.children].filter(el => el.matches('section,div') && el.querySelector('input:not([type=hidden]),select,textarea'));
    if (panels.length < 2) return;
    if (panels.length === 5) {
      const review = document.createElement('section'); review.className = 'campus-card campus-card-pad';
      const heading = document.createElement('h2'); heading.textContent = 'Revisar y finalizar';
      review.append(heading); panels.at(-1).after(review); panels.push(review);
    }
    const labels = ['Estudiante','Padres','Apoderado titular','Apoderado suplente','Salud y antecedentes','Revisar y finalizar'];
    const actions = new Set();
    form.querySelectorAll('button[type=submit]').forEach(button => {
      let parent = button;
      while (parent.parentElement !== form) parent = parent.parentElement;
      actions.add(parent); parent.classList.add('campus-wizard-actions');
    });
    const wrapper = document.createElement('div'); wrapper.className = 'campus-wizard-layout';
    const nav = document.createElement('nav'); nav.className = 'campus-steps'; nav.setAttribute('aria-label', 'Pasos de la matrícula');
    form.before(wrapper); wrapper.append(nav, form);
    const bar = document.createElement('div'); bar.className = 'campus-wizard-bar'; bar.setAttribute('aria-hidden', 'true');
    const progress = document.createElement('div'); bar.append(progress); form.prepend(bar);
    const status = document.createElement('p'); status.className = 'campus-wizard-status text-sm text-slate-600'; status.setAttribute('role','status');
    const controls = document.createElement('div'); controls.className = 'campus-wizard-controls';
    const previous = makeButton('← Anterior','campus-btn');
    const count = document.createElement('span');
    const next = makeButton('Continuar →','campus-btn primary');
    controls.append(previous, count, next); form.append(status, controls);
    const summary = document.createElement('div'); summary.className = 'campus-review'; panels.at(-1).append(summary);
    let current = 0;
    const buttons = panels.map((panel, i) => {
      panel.dataset.campusStep = String(i);
      const button = makeButton('', 'campus-step-button');
      const number = document.createElement('span'); number.className = 'campus-step-number'; number.textContent = i + 1;
      const label = document.createElement('span'); label.textContent = labels[i];
      button.append(number,label); button.setAttribute('aria-label', `Paso ${i+1}: ${labels[i]}`);
      button.addEventListener('click', () => show(i, true)); nav.append(button); return button;
    });
    const help = document.createElement('p'); help.className = 'campus-step-help';
    help.textContent = 'Completa un bloque a la vez. Puedes volver para revisar los antecedentes antes de guardarlos.'; nav.append(help);
    function review() {
      summary.replaceChildren();
      const heading = document.createElement('h3'); heading.textContent = 'Un último vistazo antes de guardar';
      const list = document.createElement('dl');
      for (const [label,name] of [['Estudiante','nombre_completo'],['RUT','rut'],['Curso','curso'],['Curso','curso_id'],['Apoderado titular','nombre_apoderado'],['Teléfono de contacto','telefono_apoderado'],['Fecha de matrícula','fecha_matricula']]) {
        const field = form.elements.namedItem(name); if (!field) continue;
        const group = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
        dt.textContent = label; dd.textContent = field.tagName === 'SELECT' ? field.selectedOptions[0]?.textContent || 'Sin seleccionar' : field.value || 'Sin completar';
        group.append(dt, dd); list.append(group);
      }
      summary.append(heading,list);
    }
    form.addEventListener('input', () => { if (current === panels.length - 1) review(); });
    form.addEventListener('change', () => { if (current === panels.length - 1) review(); });
    function show(index, focus=false) {
      current = index; status.textContent = '';
      panels.forEach((panel,i) => panel.hidden = i !== current);
      buttons.forEach((button,i) => { if(i === current) button.setAttribute('aria-current','step'); else button.removeAttribute('aria-current'); });
      actions.forEach(action => action.hidden = current !== panels.length - 1);
      previous.disabled = current === 0; next.hidden = current === panels.length - 1;
      count.textContent = `Paso ${current+1} de ${panels.length}`;
      progress.style.width = `${(current+1)/panels.length*100}%`;
      const oldCount = document.getElementById('bar-seccion'); if(oldCount) oldCount.textContent = count.textContent;
      if(current === panels.length-1) review();
      if(focus) { const title = panels[current].querySelector('h2') || panels[current]; title.tabIndex = -1; title.focus({preventScroll:true}); panels[current].scrollIntoView({block:'start',behavior:'auto'}); }
    }
    function invalid(panel) { return [...panel.querySelectorAll('input,select,textarea')].find(field => field.willValidate && !field.checkValidity()); }
    next.addEventListener('click', () => {
      const bad = invalid(panels[current]);
      if(bad) { bad.focus(); bad.reportValidity(); return; }
      if(panels[current].querySelector('.bad')) { status.textContent = 'Revisa los campos marcados antes de continuar.'; return; }
      show(current+1,true);
    });
    previous.addEventListener('click', () => show(current-1,true));
    form.noValidate = true;
    form.addEventListener('submit', event => {
      if (current !== panels.length - 1) {
        event.preventDefault(); event.stopImmediatePropagation(); show(panels.length - 1, true); return;
      }
      const panel = panels.find(panel => invalid(panel));
      if(panel) { event.preventDefault(); event.stopImmediatePropagation(); show(panels.indexOf(panel)); const bad = invalid(panel); bad.focus(); bad.reportValidity(); }
    }, true);
    form.addEventListener('reset', () => show(0));
    form.addEventListener('campus:loaded', () => show(0));
    document.addEventListener('click', event => {
      const anchor = event.target.closest('a[href^="#"]'); if(!anchor) return;
      const target = document.getElementById(anchor.hash.slice(1));
      const panel = target?.closest('[data-campus-step]');
      if(panel && form.contains(panel)) show(panels.indexOf(panel));
    }, true);
    show(0);
  });

  const settings = document.querySelector('[data-campus-settings]');
  if(settings) {
    const groups = [['General',['c06']],['Año y cursos',['c04','c05','c09','c10']],['Documentos y firmas',['c01','c02','c03']],['Accesos',['c07','c08']],['Importar / exportar',['c11','c12']]];
    const nav = document.createElement('nav'); nav.className = 'campus-settings-nav'; nav.setAttribute('aria-label','Secciones de configuración');
    settings.prepend(nav);
    const sections = [...settings.querySelectorAll('section[id^="c"]')];
    let selected = 0;
    function showGroup(index) {
      selected = index;
      sections.forEach(section => section.hidden = !groups[index][1].includes(section.id));
      [...nav.children].forEach((button,i) => button.setAttribute('aria-current',i===index?'page':'false'));
    }
    groups.forEach(([label],i) => {const button=makeButton(label,'');button.addEventListener('click',()=>showGroup(i));nav.append(button);});
    const requested = groups.findIndex(([,ids]) => ids.includes(location.hash.slice(1)));
    showGroup(requested >= 0 ? requested : selected);
    settings.querySelectorAll('a[href^="#c"]').forEach(anchor => anchor.addEventListener('click',() => {
      const index = groups.findIndex(([,ids])=>ids.includes(anchor.hash.slice(1)));if(index>=0)showGroup(index);
    }));
  }
  const detail = document.querySelector('[data-campus-detail]');
  if(detail) {
    const panels=[...detail.querySelectorAll('[data-campus-panel]')];
    const groups=[['Resumen',['estudiante']],['Familia y apoderados',['padres','apoderados']],['Salud y antecedentes',['salud','transporte']],['Documentos',['documentos']],['Identificación',['rut']]];
    const available=groups.filter(([,ids])=>panels.some(panel=>ids.includes(panel.dataset.campusPanel)));
    const nav=document.createElement('nav');nav.className='campus-detail-tabs';nav.setAttribute('aria-label','Secciones de la ficha');detail.before(nav);
    function showGroup(index) {
      panels.forEach(panel=>panel.hidden=!available[index][1].includes(panel.dataset.campusPanel));
      [...nav.children].forEach((button,i)=>button.setAttribute('aria-current',i===index?'page':'false'));
    }
    available.forEach(([label],i)=>{const button=makeButton(label,'');button.addEventListener('click',()=>showGroup(i));nav.append(button);});
    if(available.length)showGroup(0);
    document.querySelectorAll('a[href="#seccion-rut"]').forEach(anchor=>anchor.addEventListener('click',()=>showGroup(available.findIndex(([,ids])=>ids.includes('rut')))));
  }

  const directory = document.querySelector('[data-campus-directory]');
  if(directory) {
    const input=directory.querySelector('[data-directory-search]'), role=directory.querySelector('[data-directory-role]');
    const rows=[...directory.querySelectorAll('[data-directory-row]')];
    const normalize=text=>text.normalize('NFD').replace(/[\u0300-\u036f.\-]/g,'').toLowerCase();
    const filter=()=>{let count=0;rows.forEach(row=>{row.hidden=!(normalize(row.dataset.directorySearch).includes(normalize(input.value))&&(!role.value||role.value===row.dataset.directoryRole));if(!row.hidden)count++;});directory.querySelector('[data-directory-count]').textContent=count;directory.querySelector('[data-directory-empty]').hidden=count>0;};
    input.addEventListener('input',filter);role.addEventListener('change',filter);filter();
  }
  document.querySelectorAll('[data-campus-password]').forEach(button=>button.addEventListener('click',()=>{
    const input=document.getElementById(button.dataset.campusPassword);input.type=input.type==='password'?'text':'password';button.setAttribute('aria-label',input.type==='password'?'Mostrar contraseña':'Ocultar contraseña');
  }));
  document.querySelector('[data-campus-normalize-user]')?.addEventListener('submit',event=>{
    const rut=event.target.elements.namedItem('run');rut.value=rut.value.replace(/[^0-9kK]/g,'').toUpperCase();
  });
  document.body.classList.add('campus-enhanced');
})();

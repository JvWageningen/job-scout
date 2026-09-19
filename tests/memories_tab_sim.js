// Drive the real memories.js against a fake DOM; run by test_memories_tab_js.py.
// Every scenario prints PASS or FAIL, and the exit code is 1 on any FAIL.
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = process.argv[2];

class El {
    constructor(tag, id) {
        this.tag = tag; this.id = id || ''; this.children = []; this.listeners = {};
        this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false;
        this.hidden = false; this.textContent = ''; this.open = false; this.files = [];
        this.classList = {contains: () => this.active === true, add() {}, remove() {}};
    }
    append(...c) { this.children.push(...c); }
    replaceChildren(...c) { this.children = c; }
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
    dispatch(t, ev) {
        ev = ev || {}; ev.target = ev.target || this;
        ev.stopImmediatePropagation = () => { ev._stopped = true; };
        ev.preventDefault = () => { ev._prevented = true; };
        for (const f of this.listeners[t] || []) { if (ev._stopped) break; f(ev); }
        return ev;
    }
    setAttribute() {} focus() {} add(o) { this.children.push(o); }
}
const registry = {};
const byId = id => registry[id] || (registry[id] = new El('div', id));
let domReady = null;
const tabButton = new El('button');
const windowListeners = {};
const document = {
    getElementById: byId,
    createElement: tag => new El(tag),
    createTextNode: text => ({text}),
    querySelector: () => tabButton,
    addEventListener: (t, f) => { if (t === 'DOMContentLoaded') domReady = f; },
};
let handler = null, confirms = [], confirmAnswer = true;
const sandbox = {
    document, console, setTimeout, Promise,
    currentUser: 'Sam',
    confirm: msg => { confirms.push(msg); return confirmAnswer; },
    switchTab: () => {},
    FormData: class { append() {} },
    Option: function (t, v) { return {text: t, value: v}; },
    window: {addEventListener: (t, f) => { windowListeners[t] = f; }},
    fetchWithAuth: (url, options) => handler(url, options || {}),
};
// The page marks the switch disabled until the server has said what it is.
byId("memory-auto").disabled = true;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
domReady();

const reply = (data, ok = true, status = 200) => Promise.resolve({ok, status, json: async () => data});
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, 0)); };
const status = () => byId('memory-status').textContent;
const list = {memories: [], auto_capture: true, forgotten: 0};
const unsavedWarned = () => { const ev = {}; ev.preventDefault = () => { ev.prevented = true; }; windowListeners.beforeunload(ev); return Boolean(ev.prevented); };
const results = [];
const check = (name, ok, detail) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ': ' + detail}`);

(async () => {
    // MEM-UI-2: a failed first load, then Propose, then another tab click.
    let failFirst = true, pendingExtract = null;
    handler = (url, options) => {
        if (url.startsWith('/api/memories?')) {
            if (failFirst) { failFirst = false; return reply({detail: 'Your memories could not be read or saved.'}, false, 503); }
            return reply(list);
        }
        if (url.startsWith('/api/memories/extract')) {
            return new Promise(resolve => { pendingExtract = () => resolve({ok: true, status: 200, json: async () => ({drafts: [{text: 'Ik spreek Duits.', kind: 'skill', tags: [], hint: '', use_in: ['cv'], sensitive: false, origin: 'taken from a text', source: 'text_import', source_detail: ''}], truncated: false})}); });
        }
        if (url.startsWith('/api/memories/forgotten')) return reply({forgotten: []});
        return reply({});
    };
    tabButton.dispatch('click');
    await tick();
    check('switch stays disabled after a failed load', byId('memory-auto').disabled === true, byId('memory-auto').disabled);
    byId('memory-text').value = 'Ik spreek vloeiend Duits en ik ken Python.';
    check('typed text counts as unsaved', unsavedWarned(), 'no warning');
    byId('memory-propose').onclick();
    await tick();
    // MEM-UI-5: open the deleted list while the proposal run is going.
    const running = status();
    byId('memory-forgotten-box').open = true;
    byId('memory-forgotten-box').dispatch('toggle');
    await tick();
    check('status line kept during a run', status() === running, `${running} -> ${status()}`);
    // A tab click while the run is going does nothing.
    tabButton.dispatch('click');
    await tick();
    pendingExtract();
    await tick();
    check('proposals shown after the run', byId('memory-proposals').hidden === false, 'hidden');
    const text = byId('memory-text').value;
    tabButton.dispatch('click');
    await tick();
    check('tab click after failed load keeps proposals', byId('memory-proposals').hidden === false, 'hidden');
    check('tab click after failed load keeps the text', byId('memory-text').value === text, byId('memory-text').value);
    check('no confirm asked', confirms.length === 0, confirms.join(' | '));
    check('switch enabled after a successful load', byId('memory-auto').disabled === false, 'disabled');

    // MEM-UI-7: after proposals are discarded the proposed text is no longer unsaved.
    confirmAnswer = true;
    byId('memory-discard').onclick();
    check('proposed text no longer unsaved', !unsavedWarned(), 'warned');
    byId('memory-text').value = text + ' Ik ken ook SQL.';
    check('edited text unsaved again', unsavedWarned(), 'no warning');
    byId('memory-text').value = '';

    // MEM-UI-6: the empty summary follows the capture switch.
    list.auto_capture = false;
    byId('memory-refresh').onclick();
    await tick();
    check('summary with capture off', status().includes('only when you switch that on'), status());
    list.auto_capture = true;
    byId('memory-refresh').onclick();
    await tick();
    check('summary with capture on', status().includes('write notes for a letter or an interview'), status());

    results.forEach(line => console.log(line));
    process.exit(results.some(line => line.startsWith('FAIL')) ? 1 : 0);
})().catch(error => { console.error(error); process.exit(2); });

/* The Memories tab: facts about the applicant that job-scout keeps for later
   letters, interview sets and tailored CVs. Sibling of the letter writer: same
   token helper, same staleness guard, same disabled-fieldset pattern. A memory
   may have been written by a model reading the applicant's notes, and its
   origin can name a scraped vacancy, so everything below is written as text
   nodes and form values, never as markup. */
(() => {
    'use strict';
    const el = id => document.getElementById('memory-' + id);
    const KINDS = {
        project: 'Project', achievement: 'Result', skill: 'Skill',
        experience: 'Experience', education: 'Education or course',
        preference: 'Wish', constraint: 'Condition', personal: 'Personal',
        other: 'Other',
    };
    // A wish or a condition is not experience and never goes on a CV: the
    // extractor leaves CV out for them, and choosing one here unticks it.
    const OFF_CV = ['preference', 'constraint'];
    const USES = {cv: 'CV', letter: 'Letters', interview: 'Interviews'};
    const USED_IN = {cv: 'your CV', letter: 'letters', interview: 'interviews'};
    // Must match MAX_INPUT_CHARS in memory_extract.py.
    const MAX_TEXT = 20000;
    const NO_USER = 'Select a single user to see their memories.';
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];
    let epoch = 0, loadedUser = null, keepTab = false;
    // busy counts this user's runs in progress: the workspace opens again
    // only when the last one ends, and a quiet refresh waits for them.
    let busy = 0;
    // memories is the list as the server last sent it; proposals what the
    // model proposed for a text, each with its fields. editing is the memory
    // being edited with its fields, kept across a redraw so that searching
    // does not throw typing away.
    let memories = [], proposals = [], editing = null, addForm = null, formCount = 0;
    // The file the text box was filled from and the text it gave, so that the
    // proposals name the file only while the box still holds that text or part
    // of it, and whether the last text may hold more than was proposed.
    // proposedText is the text the model last read in full: a text in the box
    // that differs from it and from the file is work not yet saved.
    let fileName = '', fileText = '', moreInText = false, proposedText = '';
    const validUser = () => currentUser && currentUser !== 'all' ? currentUser : null;
    const status = (text, error = false) => {
        el('status').textContent = text;
        el('status').dataset.error = String(error);
    };
    const fresh = ctx => {
        if (ctx.epoch !== epoch || ctx.user !== validUser()) throw new Error('stale');
    };
    const FIELDS = {text: 'The memory', hint: 'The hint', tags: 'The tags', drafts: 'The proposals'};
    // FastAPI reports a refused field as a list; its first entry says enough.
    function problem(data) {
        if (typeof data.detail === 'string') return data.detail;
        const first = Array.isArray(data.detail) ? data.detail[0] : null;
        if (!first || !first.msg) return 'The request failed. Check your input and retry.';
        const names = (first.loc || []).filter(part => typeof part === 'string' && part !== 'body');
        return `${FIELDS[names[names.length - 1]] || 'A field'}: ${first.msg}.`;
    }
    async function api(path, ctx, options = {}) {
        const response = await fetchWithAuth('/api/memories' + path +
            (path.includes('?') ? '&' : '?') + 'user=' + encodeURIComponent(ctx.user), options);
        fresh(ctx);
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(problem(data));
        }
        return response;
    }
    const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    async function run(label, action) {
        const user = validUser();
        if (!user) { status(NO_USER); return; }
        const ctx = {user, epoch};
        busy++;
        el('workspace').disabled = true;
        status(label);
        try { await action(ctx); }
        catch (error) { if (error.message !== 'stale' && ctx.epoch === epoch) status(error.message, true); }
        finally {
            if (ctx.epoch === epoch) {
                busy--;
                if (!busy) el('workspace').disabled = false;
            }
        }
    }
    const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
    const capital = text => String(text).replace(/^./, c => c.toUpperCase());
    // Written out by hand: the same words in every browser and locale.
    function spokenDate(value) {
        const date = new Date(value);
        return !value || Number.isNaN(date.getTime()) ? ''
            : `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
    }
    // --- One set of fields for adding, editing and reviewing a proposal. ---
    function control(tag, id, value, max) {
        const node = document.createElement(tag);
        node.id = id;
        if (tag === 'input') node.type = 'text';
        node.maxLength = max;
        node.value = value;
        return node;
    }
    function caption(forId, text, note) {
        const label = document.createElement('label');
        label.htmlFor = forId;
        label.textContent = text;
        if (note) {
            const span = document.createElement('span');
            span.className = 'letter-hint';
            span.textContent = ' ' + note;
            label.append(span);
        }
        return label;
    }
    function tick(id, checked, text) {
        const label = document.createElement('label');
        label.className = 'memory-check';
        label.htmlFor = id;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.id = id;
        box.checked = checked;
        const span = document.createElement('span');
        span.textContent = text;
        label.append(box, span);
        return {label, box};
    }
    function paragraph(className, text) {
        const p = document.createElement('p');
        p.className = className;
        p.textContent = text;
        return p;
    }
    function useBoxes(id, chosen) {
        const group = document.createElement('fieldset');
        group.className = 'memory-uses';
        const legend = document.createElement('legend');
        legend.textContent = 'Where it may be used';
        group.append(legend);
        const boxes = Object.entries(USES).map(([value, name]) => {
            const {label, box} = tick(id('use-' + value), chosen.includes(value), name);
            box.value = value;
            group.append(label);
            return box;
        });
        return {group, boxes};
    }
    // values is a stored memory, a proposal, or {} for a new one.
    function memoryFields(values) {
        const n = ++formCount, id = name => `memory-form-${n}-${name}`;
        const text = control('textarea', id('text'), values.text || '', 600);
        text.rows = 3;
        const kind = document.createElement('select');
        kind.id = id('kind');
        Object.entries(KINDS).forEach(([value, name]) => kind.add(new Option(name, value)));
        kind.value = KINDS[values.kind] ? values.kind : 'other';
        const uses = useBoxes(id, values.use_in || Object.keys(USES));
        kind.addEventListener('change', () => {
            if (OFF_CV.includes(kind.value)) uses.boxes.find(box => box.value === 'cv').checked = false;
        });
        const tags = control('input', id('tags'), (values.tags || []).join(', '), 400);
        tags.placeholder = 'cro, retail, python';
        const hint = control('input', id('hint'), values.hint || '', 200);
        hint.placeholder = 'Use for CRO or experimentation roles';
        const secret = tick(id('private'), Boolean(values.sensitive), 'Private: keep it here, never send it to a model');
        const root = document.createElement('div');
        root.className = 'memory-fields';
        root.append(
            caption(text.id, 'What to remember'), text,
            caption(kind.id, 'Kind'), kind, uses.group,
            caption(tags.id, 'Tags', '(words a vacancy it fits would contain, separated by commas, at most 8)'), tags,
            caption(hint.id, 'When to use it', '(optional)'), hint, secret.label,
            paragraph('letter-hint', 'Private is for health, family, religion, politics, money and similar matters. A private memory is kept and shown here, and left out of every letter, interview and CV.'),
        );
        const read = () => ({
            text: text.value.trim(),
            kind: kind.value,
            tags: tags.value.split(',').map(tag => tag.trim()).filter(Boolean),
            hint: hint.value.trim(),
            use_in: uses.boxes.filter(box => box.checked).map(box => box.value),
            sensitive: secret.box.checked,
        });
        return {root, read, focus: () => text.focus()};
    }
    function newAddForm() {
        addForm = memoryFields({});
        el('new').replaceChildren(addForm.root);
    }
    // --- The list. ---
    function usedIn(uses) {
        const names = (uses || []).map(use => USED_IN[use] || use);
        if (!names.length) return 'Not used anywhere';
        const last = names.pop();
        return 'Used in ' + (names.length ? `${names.join(', ')} and ${last}` : last);
    }
    function button(text, label, onclick, className = 'btn btn-small') {
        const node = document.createElement('button');
        node.type = 'button';
        node.className = className;
        node.textContent = text;
        node.setAttribute('aria-label', label);
        node.onclick = onclick;
        return node;
    }
    function hintLine(text) {
        const p = document.createElement('p');
        p.className = 'memory-hint';
        const span = document.createElement('span');
        span.textContent = 'When to use it:';
        p.append(span, document.createTextNode(' ' + text));
        return p;
    }
    function tagList(tags) {
        const list = document.createElement('ul');
        list.className = 'memory-tags';
        list.setAttribute('aria-label', 'Tags');
        tags.forEach(tag => {
            const item = document.createElement('li');
            item.textContent = tag;
            list.append(item);
        });
        return list;
    }
    function card(memory) {
        const article = document.createElement('article');
        article.className = 'memory-card';
        article.dataset.private = String(Boolean(memory.sensitive));
        if (memory.sensitive) article.append(paragraph('memory-flag', 'Private: never sent to a model'));
        article.append(paragraph('memory-text', memory.text));
        if (memory.hint) article.append(hintLine(memory.hint));
        if (memory.tags && memory.tags.length) article.append(tagList(memory.tags));
        const made = spokenDate(memory.created_at);
        article.append(paragraph('memory-meta', [
            capital(memory.cited_as), KINDS[memory.kind] || capital(memory.kind), usedIn(memory.use_in),
            capital(memory.origin) + (made ? ` on ${made}` : ''),
        ].join(' \u00b7 ')));
        const actions = document.createElement('div');
        actions.className = 'letter-actions';
        actions.append(
            button('Edit', 'Edit ' + memory.cited_as, () => startEdit(memory)),
            button('Delete', 'Delete ' + memory.cited_as, () => remove(memory)),
        );
        article.append(actions);
        return article;
    }
    function editCard(memory) {
        const article = document.createElement('article');
        article.className = 'memory-card memory-editing';
        const actions = document.createElement('div');
        actions.className = 'letter-actions';
        actions.append(
            button('Save changes', 'Save changes to ' + memory.cited_as, () => saveEdit(memory), 'btn btn-primary'),
            button('Cancel', 'Stop editing ' + memory.cited_as, () => { editing = null; render(); }, 'btn btn-secondary'),
        );
        article.append(paragraph('memory-meta', `Editing ${memory.cited_as}`), editing.form.root, actions);
        return article;
    }
    // Case, accents and punctuation aside, like the command line's search.
    const fold = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ');
    function matches(memory, words) {
        const haystack = fold([memory.text, memory.hint, memory.kind, KINDS[memory.kind], ...(memory.tags || [])].join(' '));
        return words.every(word => haystack.includes(word));
    }
    function render() {
        const words = fold(el('search').value).split(' ').filter(Boolean);
        const shown = memories.filter(memory => matches(memory, words));
        el('list').replaceChildren(...shown.map(memory =>
            editing && editing.id === memory.id ? editCard(memory) : card(memory)));
        el('empty').hidden = memories.length > 0;
        const all = plural(memories.length, 'memory', 'memories');
        el('count').textContent = !memories.length ? ''
            : words.length ? `${shown.length} of ${all} match your search.` : `${all}, newest first.`;
    }
    function summary() {
        if (memories.length) return `${plural(memories.length, 'memory', 'memories')}. Letters, interview sets and tailored CVs use the ones whose text, hint or tags fit the vacancy.`;
        // apply() has just set the switch from the server, so it says whether
        // notes become memories at all.
        return el('auto').checked
            ? 'No memories yet. Add one by hand, turn a text into memories, or write notes for a letter or an interview.'
            : 'No memories yet. Add one by hand or turn a text into memories. Notes for a letter or an interview become memories only when you switch that on under From your notes.';
    }
    function apply(data) {
        memories = data.memories;
        if (editing && !memories.some(memory => memory.id === editing.id)) editing = null;
        el('auto').checked = Boolean(data.auto_capture);
        el('auto').disabled = false;
        el('forgotten-count').textContent = String(data.forgotten);
        el('forgotten-clear').disabled = !data.forgotten;
        render();
    }
    // Every successful read marks the user as loaded, whichever run made it:
    // after a failed first load, Refresh or saving a memory is enough.
    async function refresh(ctx) {
        const data = await (await api('', ctx)).json();
        fresh(ctx);
        apply(data);
        loadedUser = ctx.user;
    }
    function forgottenItem(item) {
        const li = document.createElement('li');
        const when = spokenDate(item.forgotten_at);
        const text = document.createElement('span');
        text.textContent = `${when ? when + ': ' : ''}${item.text}${item.sensitive ? ' (private)' : ''}`;
        li.append(text, ' ', button('Erase', 'Erase this deleted memory', () => eraseOne(item)));
        return li;
    }
    async function forgottenList(ctx) {
        const data = await (await api('/forgotten', ctx)).json();
        fresh(ctx);
        el('forgotten').replaceChildren(...data.forgotten.map(forgottenItem));
        el('forgotten-count').textContent = String(data.forgotten.length);
        el('forgotten-clear').disabled = !data.forgotten.length;
        return data.forgotten.length;
    }
    function eraseOne(item) {
        if (!confirm('Erase this deleted memory? Its wording is then gone from your database and no longer sent '
            + 'with automatic capture, and notes you type again may bring the fact back.')) return;
        run('Erasing the deleted memory...', async ctx => {
            await api('/forgotten/' + item.id, ctx, {method: 'DELETE'});
            fresh(ctx);
            const count = await forgottenList(ctx);
            status(`Erased. ${count ? plural(count, 'deleted memory is', 'deleted memories are') + ' still kept.' : 'No deleted memories are kept.'}`);
        });
    }
    // --- Changing one memory. ---
    function startEdit(memory) {
        if (editing && editing.id !== memory.id
            && !confirm('Stop editing the other memory? Your changes to it are not saved.')) return;
        editing = {id: memory.id, form: memoryFields(memory)};
        render();
        editing.form.focus();
    }
    function saveEdit(memory) {
        const values = editing.form.read();
        if (!values.text) { status('A memory cannot be empty. Write it, or delete the memory.', true); return; }
        run('Saving your changes...', async ctx => {
            const changed = await (await api('/' + memory.id, ctx, json('PUT', values))).json();
            fresh(ctx);
            editing = null;
            memories = memories.map(item => item.id === changed.id ? changed : item);
            render();
            status(`Your changes to ${changed.cited_as} are saved.`
                + (changed.sensitive ? ' It is private, so it is never sent to a model.' : ''));
        });
    }
    function remove(memory) {
        const sent = memory.sensitive
            ? 'It is private, so its wording is never sent to a model, and a fact written quite differently can come back, marked private.'
            : 'An automatic capture of notes on the same subject sends that wording to your configured model to say what to leave out.';
        if (!confirm('Delete this memory? Letters, interviews and CVs stop using it. Its wording stays in your database '
            + 'under Deleted memories, so that notes you type again do not bring it back in the same or similar words. '
            + sent + ' Erase it under Deleted memories to remove its wording.')) return;
        run('Deleting the memory...', async ctx => {
            await api('/' + memory.id, ctx, {method: 'DELETE'});
            fresh(ctx);
            await refresh(ctx);
            if (el('forgotten-box').open) await forgottenList(ctx);
            status('Memory deleted.');
        });
    }
    // --- Proposals for a text. ---
    function proposalCard(item, index) {
        const article = document.createElement('article');
        article.className = 'memory-card memory-proposal';
        article.dataset.private = String(Boolean(item.draft.sensitive));
        article.dataset.keep = 'true';
        const keep = tick(`memory-keep-${formCount}-${index}`, true, 'Keep this one');
        keep.box.addEventListener('change', () => { article.dataset.keep = String(keep.box.checked); });
        item.keep = keep.box;
        article.append(keep.label, item.form.root, paragraph('memory-meta', capital(item.draft.origin)));
        return article;
    }
    function showProposals(drafts, truncated) {
        proposals = drafts.map(draft => ({draft, form: memoryFields(draft), keep: null}));
        el('proposal-list').replaceChildren(...proposals.map(proposalCard));
        el('proposals').hidden = !proposals.length;
        el('truncated').hidden = !truncated;
        moreInText = truncated;
    }
    function keptDrafts() {
        const kept = proposals.filter(item => item.keep.checked);
        if (!kept.length) throw new Error('Tick at least one proposed memory to save, or discard them.');
        const drafts = kept.map(item => ({
            ...item.form.read(), source: item.draft.source,
            source_detail: item.draft.source_detail, job_id: item.draft.job_id,
        }));
        if (drafts.some(draft => !draft.text)) throw new Error('One of the ticked memories is empty. Write it, or untick it.');
        return drafts;
    }
    // --- Switching users and leaving the page. ---
    // A text typed or pasted in the box counts until the model has read it in
    // full; text loaded unchanged from a file is still on disk.
    function unsavedText() {
        const text = el('text').value.trim();
        return Boolean(text && text !== proposedText && text !== fileText.trim());
    }
    const unsaved = () => Boolean(proposals.length || editing || (addForm && addForm.read().text) || unsavedText());
    function reset() {
        epoch++;
        busy = 0; loadedUser = null; memories = []; proposals = []; editing = null;
        fileName = ''; fileText = ''; moreInText = false; proposedText = '';
        el('workspace').disabled = true;
        newAddForm();
        ['list', 'proposal-list', 'forgotten'].forEach(id => el(id).replaceChildren());
        el('proposals').hidden = true; el('truncated').hidden = true;
        ['text', 'file', 'search'].forEach(id => { el(id).value = ''; });
        el('count').textContent = ''; el('empty').hidden = false;
        // Not a guessed "off": the switch shows the server's value once read.
        el('auto').checked = false; el('auto').disabled = true;
        el('forgotten-count').textContent = '0';
        el('forgotten-box').open = false;
        status(NO_USER);
    }
    // Every user switch has already run reset(), so loading the same user
    // again after a failed load keeps what was typed or proposed meanwhile.
    async function load() {
        if (!validUser()) { status(NO_USER); return; }
        await run('Loading your memories...', async ctx => {
            await refresh(ctx);
            status(summary());
        });
    }
    const check = () => run('Checking for new memories...', async ctx => {
        await refresh(ctx);
        status(summary());
    });
    // --- What the buttons do. ---
    function addMemory() {
        const values = addForm.read();
        if (!values.text) { status('Write what to remember first.', true); addForm.focus(); return; }
        run('Saving the memory...', async ctx => {
            const added = await (await api('', ctx, json('POST', values))).json();
            fresh(ctx);
            newAddForm();
            await refresh(ctx);
            const similar = added.similar_id
                ? ` Memory ${added.similar_id} already says much the same; delete one of them if they repeat each other.` : '';
            status(`Saved as ${added.memory.cited_as}.${similar}`);
        });
    }
    function readFile() {
        const file = el('file').files[0];
        if (!file) return;
        if (el('text').value.trim() && !confirm('Replace the text in the box with the text of this file?')) {
            el('file').value = '';
            return;
        }
        run('Reading the file...', async ctx => {
            const form = new FormData();
            form.append('file', file);
            try {
                const data = await (await api('/read-file', ctx, {method: 'POST', body: form})).json();
                fresh(ctx);
                el('text').value = data.text;
                fileName = data.name;
                fileText = data.text;
                status(data.text.length > data.max_chars
                    ? `${data.name} holds ${data.text.length.toLocaleString('en')} characters, and at most ${data.max_chars.toLocaleString('en')} are read at a time. Shorten the text, or propose memories from one part and then the next.`
                    : `Read ${data.name}. Check the text, then press Propose memories.`);
            } finally {
                el('file').value = '';
            }
        });
    }
    // Where a text came from: the file it was read from while every line in
    // the box is still part of that file's text (kept, shortened, or one part
    // of it at a time); otherwise pasted, which the server names itself.
    function textOrigin(text) {
        if (!fileName) return '';
        const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
        return lines.every(line => fileText.includes(line)) ? `file ${fileName}` : '';
    }
    function propose() {
        const text = el('text').value.trim();
        if (!text) { status('Paste or type a text about yourself first, or read a file.', true); return; }
        if (text.length > MAX_TEXT) {
            status(`The text holds ${text.length.toLocaleString('en')} characters, and at most ${MAX_TEXT.toLocaleString('en')} are read at a time. Shorten it, or propose memories from one part and then the next.`, true);
            return;
        }
        if (proposals.length && !confirm('Replace the proposals you have not saved yet?')) return;
        run('Reading your text... This can take a few minutes.', async ctx => {
            const body = {text, source_detail: textOrigin(text)};
            const data = await (await api('/extract', ctx, json('POST', body))).json();
            fresh(ctx);
            // A cut text still has a part to propose, so it stays unsaved.
            proposedText = data.truncated ? '' : text;
            showProposals(data.drafts, data.truncated);
            status(data.drafts.length
                ? `${plural(data.drafts.length, 'memory', 'memories')} proposed. Tick the ones to keep, change what is not right, then save them.`
                : 'Nothing new to remember in that text. Facts you already have are left out.');
        });
    }
    function saveProposals() {
        let drafts;
        try { drafts = keptDrafts(); } catch (error) { status(error.message, true); return; }
        run('Saving the memories you kept...', async ctx => {
            const data = await (await api('/batch', ctx, json('POST', {drafts}))).json();
            fresh(ctx);
            const more = moreInText;
            showProposals([], false);
            await refresh(ctx);
            status(`Saved ${plural(data.memories.length, 'memory', 'memories')}.`
                + (more ? ' The text may hold more: press Propose memories again for the rest.' : ''));
        });
    }
    function switchCapture() {
        const enabled = el('auto').checked;
        run(enabled ? 'Switching automatic capture on...' : 'Switching automatic capture off...', async ctx => {
            try {
                const data = await (await api('/auto-capture', ctx, json('PUT', {enabled}))).json();
                fresh(ctx);
                el('auto').checked = data.enabled;
                status(data.enabled
                    ? 'Facts about you in your letter and interview notes will be kept as memories.'
                    : 'Your notes will no longer be turned into memories. The memories you have stay as they are.');
            } catch (error) {
                if (error.message !== 'stale') el('auto').checked = !enabled;
                throw error;
            }
        });
    }
    function eraseForgotten() {
        if (!confirm('Erase the list of deleted memories? Their wording is then erased from your database file, '
            + 'and notes you type again may bring those facts back.')) return;
        run('Erasing the list...', async ctx => {
            const data = await (await api('/forgotten', ctx, {method: 'DELETE'})).json();
            fresh(ctx);
            el('forgotten').replaceChildren();
            el('forgotten-count').textContent = '0';
            el('forgotten-clear').disabled = true;
            status(`Erased ${plural(data.erased, 'deleted memory', 'deleted memories')}.`);
        });
    }
    document.addEventListener('DOMContentLoaded', () => {
        newAddForm();
        // Opening the tab again picks up memories a capture added meanwhile.
        // A click while something runs, such as proposals for a text, does
        // nothing, so it never throws that run's result away.
        document.querySelector('[data-tab="memories"].tab-btn').addEventListener('click', () => {
            if (!validUser() || busy) return;
            if (loadedUser !== validUser()) load(); else check();
        });
        // Capture runs before app.js switches the user, so a declined switch
        // can still put the previous user back and stop the reload.
        document.getElementById('user-select').addEventListener('change', event => {
            keepTab = document.getElementById('memories-section').classList.contains('active');
            if (unsaved() && !confirm('Switch users and discard the memories and text you have not saved yet?')) {
                event.target.value = currentUser || '';
                event.stopImmediatePropagation();
            }
        }, true);
        document.getElementById('user-select').addEventListener('change', () => {
            reset();
            if (keepTab) { switchTab('memories'); load(); }
        });
        window.addEventListener('beforeunload', event => {
            if (!unsaved()) return;
            event.preventDefault();
            event.returnValue = '';
        });
        el('add').onclick = addMemory;
        el('file').addEventListener('change', readFile);
        el('propose').onclick = propose;
        el('save-proposals').onclick = saveProposals;
        el('discard').onclick = () => {
            if (!confirm('Discard the proposed memories? None of them is saved.')) return;
            showProposals([], false);
            status('Proposals discarded. Nothing was saved.');
        };
        el('search').addEventListener('input', render);
        el('refresh').onclick = check;
        el('auto').addEventListener('change', switchCapture);
        el('forgotten-box').addEventListener('toggle', () => {
            if (!el('forgotten-box').open || !validUser()) return;
            // Something is still running, such as proposals for a text: fill
            // the list without taking over the status line, which says what
            // that run is doing. The workspace is already disabled.
            if (busy) {
                const ctx = {user: validUser(), epoch};
                forgottenList(ctx).catch(error => {
                    if (error.message === 'stale') return;
                    const li = document.createElement('li');
                    li.textContent = 'The list could not be loaded. Close it and open it again.';
                    el('forgotten').replaceChildren(li);
                });
                return;
            }
            run('Loading your deleted memories...', async ctx => {
                const count = await forgottenList(ctx);
                status(count
                    ? `${plural(count, 'deleted memory is', 'deleted memories are')} kept by their wording, so notes do not bring them back in the same or similar words.`
                    : 'No deleted memories.');
            });
        });
        el('forgotten-clear').onclick = eraseForgotten;
    });
})();

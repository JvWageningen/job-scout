/* Private letter editor. All network calls use the dashboard's token helper. */
(() => {
    'use strict';
    const el = id => document.getElementById('letter-' + id);
    const fields = ['place_date', 'subject', 'salutation', 'closing', 'signature'];
    let epoch = 0, draft = null, dirty = false, styleDirty = false, loadedUser = null;
    let keepLetterTab = false;
    const validUser = () => currentUser && currentUser !== 'all' ? currentUser : null;
    const status = (text, error = false) => {
        el('status').textContent = text;
        el('status').dataset.error = String(error);
    };
    const fresh = ctx => {
        if (ctx.epoch !== epoch || ctx.user !== validUser()) throw new Error('stale');
    };
    async function api(path, ctx, options = {}) {
        const response = await fetchWithAuth('/api/letters' + path +
            (path.includes('?') ? '&' : '?') + 'user=' + encodeURIComponent(ctx.user), options);
        fresh(ctx);
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(typeof data.detail === 'string' ? data.detail : 'The request failed. Check your input and retry.');
        }
        return response;
    }
    const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    // The server says when the notes are being turned into memories; only then
    // does the page say so, so a switched-off capture is never claimed.
    const captureNote = response => response.headers.get('X-Memory-Capture') === 'started'
        ? ' Facts about you from your notes are being kept as memories. They appear in the Memories tab within a few minutes, where you can change or delete them.'
        : '';
    async function run(label, action) {
        const user = validUser();
        if (!user) { status('Select a single user to write a letter.'); return; }
        const ctx = {user, epoch};
        el('workspace').disabled = true;
        status(label);
        try { await action(ctx); }
        catch (error) { if (error.message !== 'stale' && ctx.epoch === epoch) status(error.message, true); }
        finally { if (ctx.epoch === epoch) el('workspace').disabled = false; }
    }
    function reset() {
        epoch++;
        loadedUser = null; draft = null; dirty = false; styleDirty = false;
        el('workspace').disabled = true;
        el('editor').hidden = true; el('empty').hidden = false;
        ['job', 'cv', 'examples', 'warnings'].forEach(id => el(id).replaceChildren());
        ['notes', 'recipient', 'guide', 'body', ...fields].forEach(id => { el(id).value = ''; });
        el('files').value = ''; el('language').value = 'auto';
        el('source').textContent = ''; el('count').textContent = ''; el('full-preview').textContent = '';
        status('Select a single user to write a letter.');
    }
    async function examples(ctx) {
        const rows = await (await api('/examples', ctx)).json(); fresh(ctx);
        el('examples').replaceChildren();
        rows.forEach(row => {
            const li = document.createElement('li');
            li.append(document.createTextNode(`${row.name} · ${row.language.toUpperCase()} · ${row.words} words`));
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'btn btn-small'; button.textContent = 'Remove';
            button.setAttribute('aria-label', 'Remove ' + row.name);
            button.onclick = () => {
                if (!confirm('Remove this style example?')) return;
                run('Removing example…', async ctx => {
                    await api('/examples/' + encodeURIComponent(row.name), ctx, {method: 'DELETE'});
                    await examples(ctx); status('Example removed. Your saved guide is unchanged.');
                });
            };
            li.append(button); el('examples').append(li);
        });
    }
    async function load() {
        reset();
        if (!validUser()) return;
        await run('Loading your vacancies and CVs…', async ctx => {
            const [data, style] = await Promise.all([
                api('/context', ctx).then(r => r.json()), api('/style', ctx).then(r => r.json()), examples(ctx),
            ]);
            fresh(ctx);
            el('job').add(new Option('Choose a vacancy', ''));
            data.jobs.forEach(j => el('job').add(new Option(
                `${j.title} — ${j.company}${j.fit_score == null ? '' : ` · ${j.fit_score}/100`}`, j.id)));
            fillCvs(data.profiles);
            el('guide').value = style.markdown;
            loadedUser = ctx.user;
            status(!data.sources.used.length ? data.sources.missing.join(' ') :
                !data.jobs.length ? 'No open vacancies yet. Run your search first.' :
                `Ready. Choose a vacancy and a language. ${sourcesLine(data.sources)}`);
        });
    }
    // CV Builder is one source among several, so a profile is a preference, not
    // a requirement. The example CV and empty profiles stay visible but cannot
    // be chosen, so it is clear why they are not used.
    function fillCvs(profiles) {
        el('cv').replaceChildren(new Option('Automatic — all your sources', ''));
        profiles.forEach(p => {
            const skip = p.example ? ' — example CV, not used' : p.empty ? ' — empty, not used' : '';
            const option = new Option(`CV Builder: ${p.slug} (${p.language})${skip}`, p.slug);
            option.disabled = Boolean(skip);
            el('cv').add(option);
        });
    }
    function sourcesLine(sources) {
        const used = `Using ${sources.used.join(', ')}.`;
        return sources.missing.length ? `${used} Not used: ${sources.missing.join('; ')}.` : used;
    }
    function editedDraft() {
        if (!draft) throw new Error('Generate or load a letter first.');
        const copy = {...draft, edited: draft.edited || dirty};
        fields.forEach(key => { copy[key] = el(key).value; });
        copy.paragraphs = el('body').value.trim().split(/\n\s*\n/).filter(Boolean);
        if (!copy.paragraphs.length) throw new Error('The letter body is empty.');
        return copy;
    }
    function count() {
        el('full-preview').textContent = [el('place_date').value, el('subject').value, el('salutation').value, el('body').value, el('closing').value, el('signature').value].filter(Boolean).join('\n\n');
        el('count').textContent = `${el('body').value.trim().split(/\s+/).filter(Boolean).length} words · ${dirty ? 'Unsaved edits' : 'Review before sending'}`;
    }
    function show(letter) {
        draft = letter; dirty = false;
        fields.forEach(key => { el(key).value = letter[key]; });
        el('body').value = letter.paragraphs.join('\n\n');
        el('editor').hidden = false; el('empty').hidden = true;
        el('source').textContent = `Vacancy #${letter.job_id} · ${letter.language.toUpperCase()} · Sources: ${(letter.sources_used || []).join(', ') || letter.cv_slug || '—'} · ${letter.examples_used.length} style examples`;
        el('warnings').replaceChildren();
        letter.warnings.forEach(w => { const li = document.createElement('li'); li.textContent = w.message; el('warnings').append(li); });
        count();
    }
    const canReplace = () => !draft || confirm('Replace the letter in the editor? Save it first if you want to keep it.');
    function download(blob, name) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = name; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
    window.openLetterWriter = async () => {
        switchTab('letters');
        if (loadedUser !== validUser()) await load();
    };
    window.openLetterForJob = async (id, user = currentUser) => {
        if (user !== currentUser) {
            const select = document.getElementById('user-select');
            select.value = user; select.dispatchEvent(new Event('change'));
        }
        await window.openLetterWriter();
        if (validUser() === user) el('job').value = String(id);
    };
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('[data-tab="letters"].tab-btn').addEventListener('click', () => {
            if (loadedUser !== validUser()) load();
        });
        document.getElementById('user-select').addEventListener('change', event => {
            keepLetterTab = document.getElementById('letters-section').classList.contains('active');
            if ((dirty || styleDirty) && !confirm('Switch users and discard unsaved letter or style edits?')) {
                event.target.value = currentUser || '';
                event.stopImmediatePropagation();
            }
        }, true);
        document.getElementById('user-select').addEventListener('change', () => {
            reset();
            if (keepLetterTab) { switchTab('letters'); load(); }
        });
        el('refresh').onclick = () => run('Refreshing vacancies and CVs…', async ctx => {
            const data = await (await api('/context', ctx)).json(); fresh(ctx);
            const selectedJob = el('job').value, selectedCv = el('cv').value;
            el('job').replaceChildren(new Option('Choose a vacancy', ''));
            data.jobs.forEach(j => el('job').add(new Option(
                `${j.title} — ${j.company}${j.fit_score == null ? '' : ` · ${j.fit_score}/100`}`, j.id)));
            fillCvs(data.profiles);
            el('job').value = selectedJob; el('cv').value = selectedCv;
            if (el('cv').selectedIndex < 0) el('cv').selectedIndex = 0;
            status('Vacancies and CVs refreshed. Your editor contents are unchanged.');
        });
        el('generate').onclick = () => {
            if (!canReplace()) return;
            run('Writing your letter… This can take a few minutes.', async ctx => {
                if (!el('job').value) throw new Error('Choose a vacancy first.');
                const body = {job_id: Number(el('job').value), language: el('language').value,
                    cv_slug: el('cv').value || null, recipient: el('recipient').value, notes: el('notes').value};
                const response = await api('/generate', ctx, json('POST', body));
                const letter = await response.json();
                fresh(ctx); show(letter); dirty = true; count();
                status('Your full cover letter is ready. Review the wording and facts, then save or download.' + captureNote(response));
            });
        };
        el('load').onclick = () => {
            if (!canReplace()) return;
            run('Loading saved draft…', async ctx => {
                if (!el('job').value || el('language').value === 'auto') throw new Error('Choose a vacancy and Dutch or English to load a saved draft.');
                const letter = await (await api(`/draft/${el('job').value}?language=${el('language').value}`, ctx)).json();
                fresh(ctx); show(letter); status('Saved draft loaded.');
            });
        };
        el('save').onclick = () => run('Saving draft…', async ctx => {
            const body = editedDraft();
            await api('/draft/' + body.job_id, ctx, json('PUT', body));
            draft = body; dirty = false; count(); status('Draft saved for this vacancy and language.');
        });
        el('pdf').onclick = () => run('Preparing PDF…', async ctx => {
            const body = editedDraft();
            const blob = await (await api('/pdf', ctx, json('POST', body))).blob();
            fresh(ctx); download(blob, `letter-${body.job_id}-${body.language}.pdf`); status('PDF downloaded with your current edits.');
        });
        el('text').onclick = () => {
            try {
                const d = editedDraft();
                const text = [d.place_date, d.subject, d.salutation, d.paragraphs.join('\n\n'), d.closing, d.signature].filter(Boolean).join('\n\n');
                download(new Blob([text], {type: 'text/plain;charset=utf-8'}), `letter-${d.job_id}-${d.language}.txt`);
            } catch (error) { status(error.message, true); }
        };
        el('upload').onclick = () => run('Importing examples…', async ctx => {
            const files = [...el('files').files];
            if (!files.length) throw new Error('Choose at least one previous letter.');
            let imported = 0;
            try {
                for (const file of files) {
                    const form = new FormData(); form.append('file', file);
                    await api('/examples', ctx, {method: 'POST', body: form}); imported++;
                }
            } finally {
                fresh(ctx); await examples(ctx); el('files').value = '';
            }
            status(`Added ${imported} examples. Learn your style or edit the guide below.`);
        });
        el('learn').onclick = () => {
            if (styleDirty && !confirm('Replace your unsaved style guide with a new proposal?')) return;
            run('Learning your writing style… This can take a few minutes.', async ctx => {
                const guide = await (await api('/style/derive', ctx, {method: 'POST'})).json();
                fresh(ctx); el('guide').value = guide.markdown; styleDirty = true;
                status('Style proposed. Review it and click Save style guide.');
            });
        };
        el('style-save').onclick = () => run('Saving style guide…', async ctx => {
            await api('/style', ctx, json('PUT', {markdown: el('guide').value}));
            styleDirty = false; status('Style guide saved. New drafts will use it.');
        });
        el('guide').addEventListener('input', () => { styleDirty = true; });
        [...fields, 'body'].forEach(id => el(id).addEventListener('input', () => { dirty = true; count(); }));
        window.addEventListener('beforeunload', event => {
            if (dirty || styleDirty) { event.preventDefault(); event.returnValue = ''; }
        });
    });
})();

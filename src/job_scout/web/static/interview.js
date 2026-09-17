/* Questions the candidate asks the employer. Sibling of the letter writer:
   same token helper, same staleness guard, same disabled-fieldset pattern. */
(() => {
    'use strict';
    const el = id => document.getElementById('interview-' + id);
    // Theme order is the order of the conversation, not the model's output order:
    // the role first, what is worth probing last, when trust has been built.
    const THEMES = {
        role: 'The role', team: 'The team', company: 'The company',
        growth: 'Growth and future', ways_of_working: 'Ways of working',
        concerns: 'Worth probing',
    };
    const NO_USER = 'Select a single user to prepare interview questions.';
    let epoch = 0, loadedUser = null, latest = null, keepInterviewTab = false;
    const validUser = () => currentUser && currentUser !== 'all' ? currentUser : null;
    const status = (text, error = false) => {
        el('status').textContent = text;
        el('status').dataset.error = String(error);
    };
    const fresh = ctx => {
        if (ctx.epoch !== epoch || ctx.user !== validUser()) throw new Error('stale');
    };
    async function api(path, ctx, options = {}) {
        const response = await fetchWithAuth('/api/interview' + path +
            (path.includes('?') ? '&' : '?') + 'user=' + encodeURIComponent(ctx.user), options);
        fresh(ctx);
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(typeof data.detail === 'string' ? data.detail : 'The request failed. Check your input and retry.');
        }
        return response;
    }
    const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    async function run(label, action) {
        const user = validUser();
        if (!user) { status(NO_USER); return; }
        const ctx = {user, epoch};
        el('workspace').disabled = true;
        status(label);
        try { await action(ctx); }
        catch (error) { if (error.message !== 'stale' && ctx.epoch === epoch) status(error.message, true); }
        finally { if (ctx.epoch === epoch) el('workspace').disabled = false; }
    }
    function reset() {
        epoch++;
        loadedUser = null; latest = null;
        el('workspace').disabled = true;
        el('results').hidden = true; el('empty').hidden = false;
        ['job', 'cv', 'themes'].forEach(id => el(id).replaceChildren());
        el('notes').value = ''; el('language').value = 'auto';
        el('source').textContent = ''; el('missing').textContent = '';
        status(NO_USER);
    }
    // The server already sorts on fit, but the dropdown is only useful if the best
    // match is on top, so the page does not depend on that ordering silently.
    const byScore = jobs => [...jobs].sort((a, b) =>
        (b.fit_score == null ? -1 : b.fit_score) - (a.fit_score == null ? -1 : a.fit_score));
    function choices(data) {
        el('job').replaceChildren(new Option('Choose a vacancy', ''));
        byScore(data.jobs).forEach(j => el('job').add(new Option(
            `${j.title} — ${j.company}${j.fit_score == null ? '' : ` · ${j.fit_score}/100`}`, j.id)));
        el('cv').replaceChildren(new Option('Automatic — match the question language', ''));
        data.profiles.forEach(p => el('cv').add(new Option(`${p.slug} (${p.language})`, p.slug)));
    }
    async function load() {
        reset();
        if (!validUser()) return;
        await run('Loading your vacancies and CVs…', async ctx => {
            const data = await (await api('/context', ctx)).json();
            fresh(ctx);
            choices(data);
            loadedUser = ctx.user;
            status(!data.profiles.length ? 'Save a current CV in CV Builder to get started.' :
                !data.jobs.length ? 'No open vacancies yet. Run your search first.' :
                'Ready. Choose the vacancy you are being interviewed for.');
        });
    }
    const label = theme => THEMES[theme] ||
        String(theme).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
    // Known themes in conversation order first, anything unexpected after it.
    function grouped(items) {
        const order = [...new Set([...Object.keys(THEMES), ...items.map(q => q.theme)])];
        return order
            .map(theme => [theme, items.filter(q => q.theme === theme)])
            .filter(([, list]) => list.length);
    }
    function line(className, caption, text) {
        const p = document.createElement('p');
        p.className = className;
        const span = document.createElement('span');
        span.textContent = caption;
        p.append(span, document.createTextNode(' ' + text));
        return p;
    }
    // Every value below comes from a scraped page or a model, so it is written as
    // text nodes only -- never as markup.
    function render(set) {
        latest = set;
        el('themes').replaceChildren();
        grouped(set.questions).forEach(([theme, list]) => {
            const block = document.createElement('div');
            block.className = 'interview-theme';
            const heading = document.createElement('h4');
            heading.textContent = label(theme);
            const ol = document.createElement('ol');
            ol.className = 'interview-questions';
            list.forEach(item => {
                const li = document.createElement('li');
                const question = document.createElement('p');
                question.className = 'interview-question';
                question.textContent = item.question;
                li.append(question);
                if (item.why) li.append(line('interview-why', 'Why this matters for you:', item.why));
                if (item.grounded_in) li.append(line('interview-grounded', 'Based on:', item.grounded_in));
                ol.append(li);
            });
            block.append(heading, ol);
            el('themes').append(block);
        });
        el('source').textContent = `Vacancy #${set.job_id} · ${set.company} · ${String(set.language).toUpperCase()} · ${set.questions.length} questions`;
        el('missing').textContent = set.missing_context && set.missing_context.length
            ? `Some grounding was missing: ${set.missing_context.join('; ')}. These questions were written without it, and nothing about it was assumed.`
            : '';
        el('results').hidden = false; el('empty').hidden = true;
    }
    function plainText(set) {
        return [`Questions to ask — ${set.company}`, ...grouped(set.questions).map(([theme, list]) =>
            [label(theme).toUpperCase(), ...list.map((item, index) => [
                `${index + 1}. ${item.question}`,
                item.why ? `   Why this matters for you: ${item.why}` : '',
                item.grounded_in ? `   Based on: ${item.grounded_in}` : '',
            ].filter(Boolean).join('\n'))].join('\n'))].join('\n\n') + '\n';
    }
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('[data-tab="interview"].tab-btn').addEventListener('click', () => {
            if (loadedUser !== validUser()) load();
        });
        document.getElementById('user-select').addEventListener('change', () => {
            keepInterviewTab = document.getElementById('interview-section').classList.contains('active');
        }, true);
        document.getElementById('user-select').addEventListener('change', () => {
            reset();
            if (keepInterviewTab) { switchTab('interview'); load(); }
        });
        el('refresh').onclick = () => run('Refreshing vacancies and CVs…', async ctx => {
            const data = await (await api('/context', ctx)).json(); fresh(ctx);
            const selectedJob = el('job').value, selectedCv = el('cv').value;
            choices(data);
            el('job').value = selectedJob; el('cv').value = selectedCv;
            if (el('cv').selectedIndex < 0) el('cv').selectedIndex = 0;
            status('Vacancies and CVs refreshed. Your questions are unchanged.');
        });
        el('generate').onclick = () => run('Writing your questions… This can take a minute or more.', async ctx => {
            if (!el('job').value) throw new Error('Choose a vacancy first.');
            // "Automatic" is the absence of a choice: the API detects the language
            // from the vacancy when it is null.
            const language = el('language').value;
            const body = {job_id: Number(el('job').value), language: language === 'auto' ? null : language,
                cv_slug: el('cv').value || null, notes: el('notes').value};
            const set = await (await api('/questions', ctx, json('POST', body))).json();
            fresh(ctx); render(set);
            status('Your questions are ready. Read them before you use them, and drop any that no longer fit.');
        });
        el('copy').onclick = async () => {
            if (!latest) { status('Generate questions first.', true); return; }
            const button = el('copy'), original = button.textContent;
            try {
                await navigator.clipboard.writeText(plainText(latest));
                button.textContent = 'Copied';
            } catch {
                // The clipboard needs a secure context; the questions are on screen either way.
                button.textContent = 'Copy failed';
            }
            setTimeout(() => { button.textContent = original; }, 1500);
        };
    });
})();

/* Both halves of the interview: the questions the candidate asks the employer,
   and the questions the employer is likely to ask back with a draft answer.
   Sibling of the letter writer: same token helper, same staleness guard, same
   disabled-fieldset pattern. One setup, one epoch, one status line for both. */
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
    // Kind order is the order an interview tends to take: why you are here
    // first, the awkward part in the middle, the practicalities at the end.
    const KINDS = {
        motivation: 'Motivation and fit', experience: 'Your experience',
        technical: 'Technical depth', behavioural: 'How you work with others',
        gap: 'Gaps they will probe', practical: 'Practical matters',
    };
    // How much real evidence stands behind an answer. GAP is the one the user
    // has to rehearse, so it is the one the page marks in amber.
    const FOOTINGS = {
        strong: 'Backed by your CV', partial: 'Partly covered',
        gap: 'Gap — rehearse this',
    };
    const NO_USER = 'Select a single user to prepare interview questions.';
    let epoch = 0, loadedUser = null, latest = null, keepInterviewTab = false;
    // The answer half keeps its set and the textareas holding it, because the
    // user's edits live in the DOM and Copy all must take them, not the draft.
    let answers = null, drafts = [];
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
    // Both halves are cleared together: they share one vacancy and one CV, so
    // leaving one behind would show the previous user's material. Which half is
    // on screen is a view choice and survives, like an open tab does.
    function reset() {
        epoch++;
        loadedUser = null; latest = null; answers = null; drafts = [];
        el('workspace').disabled = true;
        el('results').hidden = true; el('empty').hidden = false;
        el('answers-results').hidden = true; el('answers-empty').hidden = false;
        ['job', 'cv', 'themes', 'answers-list'].forEach(id => el(id).replaceChildren());
        el('notes').value = ''; el('language').value = 'auto';
        el('source').textContent = ''; el('missing').textContent = '';
        el('answers-source').textContent = ''; el('answers-missing').textContent = '';
        status(NO_USER);
    }
    // The two halves are one tab: the setup, the status line and the epoch are
    // shared, and only the result panel and the generate button swap.
    function showMode(mode) {
        const ask = mode !== 'answer';
        document.querySelectorAll('#interview-section .interview-mode').forEach(button => {
            const active = button.dataset.mode === (ask ? 'ask' : 'answer');
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', String(active));
            // Roving focus: a tablist is one stop on the Tab key, and the arrow
            // keys move inside it.
            button.tabIndex = active ? 0 : -1;
        });
        el('ask-panel').hidden = !ask; el('answer-panel').hidden = ask;
        el('generate').hidden = !ask; el('answers-generate').hidden = ask;
        el('badge').textContent = ask ? 'You ask the employer' : 'They ask you';
    }
    // The server already sorts on fit, but the dropdown is only useful if the best
    // match is on top, so the page does not depend on that ordering silently.
    const byScore = jobs => [...jobs].sort((a, b) =>
        (b.fit_score == null ? -1 : b.fit_score) - (a.fit_score == null ? -1 : a.fit_score));
    function choices(data) {
        el('job').replaceChildren(new Option('Choose a vacancy', ''));
        byScore(data.jobs).forEach(j => el('job').add(new Option(
            `${j.title} — ${j.company}${j.fit_score == null ? '' : ` · ${j.fit_score}/100`}`, j.id)));
        el('cv').replaceChildren(new Option('Automatic — match the chosen language', ''));
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
    const label = (value, names) => names[value] ||
        String(value).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
    // Known values in their intended order first, anything unexpected after it.
    // Both halves group the same way, on their own field and their own names.
    function grouped(items, key, names) {
        const order = [...new Set([...Object.keys(names), ...items.map(q => q[key])])];
        return order
            .map(value => [value, items.filter(q => q[key] === value)])
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
        grouped(set.questions, 'theme', THEMES).forEach(([theme, list]) => {
            const block = document.createElement('div');
            block.className = 'interview-theme';
            const heading = document.createElement('h4');
            heading.textContent = label(theme, THEMES);
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
        return [`Questions to ask — ${set.company}`, ...grouped(set.questions, 'theme', THEMES).map(([theme, list]) =>
            [label(theme, THEMES).toUpperCase(), ...list.map((item, index) => [
                `${index + 1}. ${item.question}`,
                item.why ? `   Why this matters for you: ${item.why}` : '',
                item.grounded_in ? `   Based on: ${item.grounded_in}` : '',
            ].filter(Boolean).join('\n'))].join('\n'))].join('\n\n') + '\n';
    }
    // --- The other half: what they ask, and what you would answer. ---
    function chip(text) {
        const span = document.createElement('span');
        span.className = 'interview-footing';
        span.textContent = text;
        return span;
    }
    // Same rule as the half above: everything here came from a scraped page or
    // a model, so it becomes text nodes and textarea values, never markup.
    function answerBlock(item) {
        const article = document.createElement('article');
        article.className = 'interview-answer';
        // The footing drives the marker in CSS, so an unknown value still lands
        // on the neutral styling rather than on the amber one.
        article.dataset.footing = item.footing;
        const question = document.createElement('p');
        question.className = 'interview-question';
        question.textContent = item.question;
        article.append(chip(label(item.footing, FOOTINGS)), question);
        if (item.why_asked) {
            article.append(line('interview-why', 'Why they may ask this:', item.why_asked));
        }
        const id = 'interview-draft-' + drafts.length;
        const caption = document.createElement('label');
        caption.className = 'interview-draft-label';
        caption.htmlFor = id;
        caption.textContent = 'Your answer — edit it until it sounds like you';
        const area = document.createElement('textarea');
        area.id = id;
        area.className = 'interview-draft';
        area.rows = 6;
        area.maxLength = 2500;
        area.value = item.draft_answer;
        drafts.push({item, area});
        article.append(caption, area);
        if (item.based_on && item.based_on.length) {
            article.append(line('interview-grounded', 'Based on:', item.based_on.join('; ')));
        }
        return article;
    }
    function renderAnswers(set) {
        answers = set; drafts = [];
        el('answers-list').replaceChildren();
        grouped(set.questions, 'kind', KINDS).forEach(([kind, list]) => {
            const block = document.createElement('div');
            block.className = 'interview-theme';
            const heading = document.createElement('h4');
            heading.textContent = label(kind, KINDS);
            block.append(heading, ...list.map(answerBlock));
            el('answers-list').append(block);
        });
        const gaps = set.questions.filter(q => q.footing === 'gap').length;
        el('answers-source').textContent = `Vacancy #${set.job_id} · ${set.company} · ${String(set.language).toUpperCase()} · ${set.questions.length} questions · ${gaps} marked as a gap`;
        el('answers-missing').textContent = set.missing_context && set.missing_context.length
            ? `Some grounding was missing: ${set.missing_context.join('; ')}. These answers were written without it, and nothing about it was assumed.`
            : '';
        el('answers-results').hidden = false; el('answers-empty').hidden = true;
    }
    // What is copied is what is on screen: the user's edits, not the draft.
    function plainTextAnswers(set) {
        const indent = text => String(text).split('\n').map(l => '   ' + l).join('\n');
        return [`Questions they may ask — ${set.company}`, ...grouped(set.questions, 'kind', KINDS).map(([kind, list]) =>
            [label(kind, KINDS).toUpperCase(), ...list.map((item, index) => {
                const draft = drafts.find(d => d.item === item);
                return [
                    `${index + 1}. ${item.question}`,
                    item.why_asked ? `   Why they may ask this: ${item.why_asked}` : '',
                    `   Footing: ${label(item.footing, FOOTINGS)}`,
                    '   Your answer:',
                    indent(draft ? draft.area.value : item.draft_answer),
                    item.based_on && item.based_on.length ? `   Based on: ${item.based_on.join('; ')}` : '',
                ].filter(Boolean).join('\n');
            })].join('\n'))].join('\n\n') + '\n';
    }
    async function copy(button, text) {
        const original = button.textContent;
        try {
            await navigator.clipboard.writeText(text);
            button.textContent = 'Copied';
        } catch {
            // The clipboard needs a secure context; the questions are on screen either way.
            button.textContent = 'Copy failed';
        }
        setTimeout(() => { button.textContent = original; }, 1500);
    }
    // One setup serves both halves, so one reader of it does too.
    function request() {
        if (!el('job').value) throw new Error('Choose a vacancy first.');
        // "Automatic" is the absence of a choice: the API detects the language
        // from the vacancy when it is null.
        const language = el('language').value;
        return {
            job_id: Number(el('job').value),
            language: language === 'auto' ? null : language,
            cv_slug: el('cv').value || null, notes: el('notes').value,
        };
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
            status('Vacancies and CVs refreshed. Everything already generated is unchanged.');
        });
        const halves = [...document.querySelectorAll('#interview-section .interview-mode')];
        halves.forEach((button, index) => {
            button.onclick = () => showMode(button.dataset.mode);
            button.onkeydown = event => {
                const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
                if (!step) return;
                event.preventDefault();
                const next = halves[(index + step + halves.length) % halves.length];
                showMode(next.dataset.mode);
                next.focus();
            };
        });
        el('generate').onclick = () => run('Writing your questions… This can take a minute or more.', async ctx => {
            const set = await (await api('/questions', ctx, json('POST', request()))).json();
            fresh(ctx); render(set);
            status('Your questions are ready. Read them before you use them, and drop any that no longer fit.');
        });
        el('answers-generate').onclick = () => {
            // Regenerating replaces every textarea, and the drafts only ever lived
            // on the page. Losing eight answers rewritten in your own words, after
            // a call that itself takes a minute, is not something to do silently.
            const edited = drafts.filter(d => d.area.value.trim() !== (d.item.draft_answer || '').trim());
            if (edited.length && !window.confirm(
                `You have rewritten ${edited.length} answer${edited.length === 1 ? '' : 's'}. `
                + 'Generating again replaces every draft and your edits are lost. Continue?')) {
                return;
            }
            run('Predicting their questions and drafting your answers… This can take a minute or more.', async ctx => {
                const set = await (await api('/answers', ctx, json('POST', request()))).json();
                fresh(ctx); renderAnswers(set);
                status('Your draft answers are ready. Rewrite each one in your own words, starting with the ones marked as a gap.');
            });
        };
        el('copy').onclick = () => latest
            ? copy(el('copy'), plainText(latest))
            : status('Generate questions first.', true);
        el('answers-copy').onclick = () => answers
            ? copy(el('answers-copy'), plainTextAnswers(answers))
            : status('Generate their questions first.', true);
    });
})();

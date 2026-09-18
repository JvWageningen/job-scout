/* Both halves of the interview: the questions the candidate asks the employer,
   and the questions the employer is likely to ask back with a draft answer.
   Sibling of the letter writer: same token helper, same staleness guard, same
   disabled-fieldset pattern. One setup, one epoch, one status line for both.
   Every generated set is saved per vacancy, half and language, so choosing a
   vacancy shows what was saved and nothing is generated again unasked. */
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
        gap: 'Gap: rehearse this',
    };
    const NO_USER = 'Select a single user to prepare interview questions.';
    // Must match THIN_REVIEW in interview_questions.py. A thin review is still
    // in the prompt, so it must not be described as absent like the others.
    const THIN_REVIEW = 'company review is based on little evidence';
    // The button says whether pressing it writes something new or replaces
    // what is already saved for this vacancy.
    const GENERATE = {
        ask: 'Generate questions to ask', answer: 'Generate questions & draft answers',
    };
    const AGAIN = {
        ask: 'Generate the questions again', answer: 'Generate questions & answers again',
    };
    const LANGUAGES = {nl: 'Dutch', en: 'English'};
    // How long typing may pause before an edited answer is saved.
    const SAVE_DELAY = 1500;
    const EMPTIED = 'An empty answer keeps its last saved text until you write a new one.';
    let epoch = 0, loadedUser = null, latest = null, keepInterviewTab = false;
    // The answer half keeps its set and the textareas holding it, because the
    // user's edits live in the DOM and every copy, save and download must take
    // them, not the draft. lastSaved is the set as the server has it; queued
    // is the newest version sent, which may still fail.
    let answers = null, drafts = [], lastSaved = '', queued = '';
    // Everything saved for the chosen vacancy: both halves, every language,
    // newest first. Switching half or language picks from it without a request.
    let saved = null;
    // The vacancy and language whose sets are on screen, so a switch that
    // cannot go ahead puts the dropdown back on what is shown.
    let shownJob = '', shownLanguage = 'auto';
    // Edits are saved one after another, and a vacancy is only read once they
    // have landed, so reading it back never returns the text before an edit.
    // busy counts the runs in progress: while one owns the status line, a
    // quiet save does not write over what it says.
    let saving = Promise.resolve(), saveTimer = null, busy = 0;
    // True once the server refused an edit because a newer set was generated
    // after this one was shown (in another tab, or on the command line). The
    // edits stay on screen, but are not sent again over the newer set.
    let stale = false;
    const STALE = 'Newer answers were generated for this vacancy after this page opened them, '
        + 'so your edits here were not saved over them. They are still on screen: download them, '
        + 'or choose the vacancy again to see the newer set.';
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
            const error = new Error(typeof data.detail === 'string' ? data.detail : 'The request failed. Check your input and retry.');
            error.status = response.status;
            throw error;
        }
        return response;
    }
    const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    async function run(label, action) {
        const user = validUser();
        if (!user) { status(NO_USER); return; }
        const ctx = {user, epoch};
        el('workspace').disabled = true;
        busy++;
        status(label);
        try { await action(ctx); }
        catch (error) { if (error.message !== 'stale' && ctx.epoch === epoch) status(error.message, true); }
        finally {
            busy--;
            if (ctx.epoch === epoch) el('workspace').disabled = false;
        }
    }
    function buttons() {
        el('generate').textContent = latest ? AGAIN.ask : GENERATE.ask;
        el('answers-generate').textContent = answers ? AGAIN.answer : GENERATE.answer;
    }
    function clearQuestions() {
        latest = null;
        el('results').hidden = true; el('empty').hidden = false;
        el('themes').replaceChildren();
        el('source').textContent = ''; el('missing').textContent = '';
        buttons();
    }
    function clearAnswers() {
        clearTimeout(saveTimer);
        answers = null; drafts = []; lastSaved = ''; queued = ''; stale = false;
        el('answers-results').hidden = true; el('answers-empty').hidden = false;
        el('answers-list').replaceChildren();
        el('answers-source').textContent = ''; el('answers-missing').textContent = '';
        buttons();
    }
    // Both halves are cleared together: they share one vacancy and one CV, so
    // leaving one behind would show the previous user's material. Which half is
    // on screen is a view choice and survives, like an open tab does.
    function reset() {
        epoch++;
        loadedUser = null; saved = null; shownJob = ''; shownLanguage = 'auto';
        clearQuestions(); clearAnswers();
        el('workspace').disabled = true;
        ['job', 'cv'].forEach(id => el(id).replaceChildren());
        el('notes').value = ''; el('language').value = 'auto';
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
        el('badge').textContent = ask ? 'You ask the employer' : 'The employer asks you';
    }
    // The server already sorts on fit, but the dropdown is only useful if the best
    // match is on top, so the page does not depend on that ordering silently.
    const byScore = jobs => [...jobs].sort((a, b) =>
        (b.fit_score == null ? -1 : b.fit_score) - (a.fit_score == null ? -1 : a.fit_score));
    const vacancy = j => new Option(
        `${j.title} · ${j.company}${j.fit_score == null ? '' : ` · ${j.fit_score}/100`}`, j.id);
    function choices(data) {
        el('job').replaceChildren(new Option('Choose a vacancy', ''));
        byScore(data.jobs).forEach(j => el('job').add(vacancy(j)));
        // A vacancy that left the shortlist, often because the posting came
        // down once the interviews started, keeps what was saved for it.
        const kept = data.saved_jobs || [];
        if (kept.length) {
            const group = document.createElement('optgroup');
            group.label = 'No longer on your shortlist, with saved preparation';
            kept.forEach(j => group.append(vacancy(j)));
            el('job').append(group);
        }
        fillCvs(data.profiles);
    }
    // Same as the letter tab: CV Builder is one source among several, so a
    // profile is a preference, not a requirement. The example CV and empty
    // profiles stay visible but cannot be chosen, so it is clear why they are
    // not used.
    function fillCvs(profiles) {
        el('cv').replaceChildren(new Option('Automatic: all your sources', ''));
        profiles.forEach(p => {
            const skip = p.example ? ' (example CV, not used)' : p.empty ? ' (empty, not used)' : '';
            const option = new Option(`CV Builder: ${p.slug} (${p.language})${skip}`, p.slug);
            option.disabled = Boolean(skip);
            el('cv').add(option);
        });
    }
    function sourcesLine(sources) {
        const used = `Using ${sources.used.join(', ')}.`;
        return sources.missing.length ? `${used} Not used: ${sources.missing.join('; ')}.` : used;
    }
    async function load() {
        reset();
        if (!validUser()) return;
        await run('Loading your vacancies and CVs...', async ctx => {
            const data = await (await api('/context', ctx)).json();
            fresh(ctx);
            choices(data);
            loadedUser = ctx.user;
            status(!data.sources.used.length ? data.sources.missing.join(' ') :
                !data.jobs.length && !(data.saved_jobs || []).length ? 'No open vacancies yet. Run your search first.' :
                `Ready. Choose the vacancy you are being interviewed for. ${sourcesLine(data.sources)}`);
        });
    }
    // Where the applicant's own facts came from, so a surprising answer can be
    // traced to the CV, the import or the profile that said it.
    const factsLine = set => set.sources_used && set.sources_used.length
        ? ` Your facts came from: ${set.sources_used.join(', ')}.` : '';
    // The day a set was generated, so an old set is recognisable as old.
    // Written out by hand: the same words in every browser and locale.
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];
    function spokenDate(value) {
        const date = new Date(value);
        return !value || Number.isNaN(date.getTime()) ? ''
            : `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
    }
    function generatedOn(set) {
        const day = spokenDate(set.generated_at);
        return day ? ` · generated on ${day}` : '';
    }
    // Stored research is reused for as long as it is kept, so how old the
    // company facts behind a set are is worth saying.
    function companyLine(set) {
        const research = spokenDate(set.company_research_date);
        const review = spokenDate(set.company_review_date);
        return (research ? ` Company research from ${research}.` : '')
            + (review ? ` Company review from ${review}.` : '');
    }
    // A missing source was not in the prompt at all; a thin review was, with a
    // warning. Saying "written without it" about the second would be false.
    function missingLine(missing, what) {
        const all = missing || [];
        const absent = all.filter(m => m !== THIN_REVIEW);
        return [
            absent.length ? `Some grounding was missing: ${absent.join('; ')}. These ${what} were written without it, and nothing about it was assumed.` : '',
            all.includes(THIN_REVIEW) ? `The company review rests on little web evidence, so it was used with caution.` : '',
        ].filter(Boolean).join(' ');
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
        el('source').textContent = `Vacancy #${set.job_id} · ${set.company} · ${String(set.language).toUpperCase()} · ${set.questions.length} questions${generatedOn(set)}.${factsLine(set)}${companyLine(set)}`;
        el('missing').textContent = missingLine(set.missing_context, 'questions');
        el('results').hidden = false; el('empty').hidden = true;
        buttons();
    }
    function plainText(set) {
        return [`Questions to ask ${set.company}`, ...grouped(set.questions, 'theme', THEMES).map(([theme, list]) =>
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
    // Typing pauses save the answers; leaving the box saves them at once.
    function watch(area) {
        area.addEventListener('input', () => {
            clearTimeout(saveTimer);
            saveTimer = setTimeout(saveEdits, SAVE_DELAY);
        });
        area.addEventListener('change', saveEdits);
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
        caption.textContent = 'Your answer: edit it until it sounds like you';
        const area = document.createElement('textarea');
        area.id = id;
        area.className = 'interview-draft';
        area.rows = 6;
        area.maxLength = 2500;
        area.value = item.draft_answer;
        drafts.push({item, area});
        watch(area);
        article.append(caption, area);
        if (item.based_on && item.based_on.length) {
            article.append(line('interview-grounded', 'Based on:', item.based_on.join('; ')));
        }
        return article;
    }
    function renderAnswers(set) {
        clearTimeout(saveTimer);
        answers = set; drafts = []; lastSaved = queued = JSON.stringify(set); stale = false;
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
        el('answers-source').textContent = `Vacancy #${set.job_id} · ${set.company} · ${String(set.language).toUpperCase()} · ${set.questions.length} questions · ${gaps} marked as a gap${generatedOn(set)}.${factsLine(set)}${companyLine(set)}`;
        el('answers-missing').textContent = missingLine(set.missing_context, 'answers');
        el('answers-results').hidden = false; el('answers-empty').hidden = true;
        buttons();
    }
    // The answer set as it is on screen: the user's edits, not the drafts. An
    // emptied box keeps the text last saved for it, so rewriting one answer
    // never holds back the edits to all the others.
    function onScreen() {
        let before = answers.questions;
        try { before = JSON.parse(lastSaved).questions || before; } catch { /* nothing saved yet */ }
        let emptied = 0;
        const questions = answers.questions.map((item, index) => {
            const draft = drafts.find(d => d.item === item);
            if (!draft) return item;
            if (String(draft.area.value).trim()) return {...item, draft_answer: draft.area.value};
            emptied++;
            return {...item, draft_answer: (before[index] || item).draft_answer};
        });
        return {set: {...answers, questions}, emptied};
    }
    // A download takes exactly what is on screen, and an empty box is not an answer.
    function editedAnswers() {
        const {set, emptied} = onScreen();
        if (emptied) {
            throw new Error('One of your answers is empty. Write something in it first; an empty answer cannot be downloaded.');
        }
        return set;
    }
    const unsaved = () => Boolean(answers && drafts.length) && JSON.stringify(onScreen().set) !== lastSaved;
    // What is copied is what is on screen: the user's edits, not the draft.
    function plainTextAnswers(set) {
        const indent = text => String(text).split('\n').map(l => '   ' + l).join('\n');
        return [`Questions ${set.company} may ask you`, ...grouped(set.questions, 'kind', KINDS).map(([kind, list]) =>
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
    // --- What is saved for the chosen vacancy. ---
    // Automatic shows the newest saved set; a chosen language shows that one.
    function pick(sets) {
        const language = el('language').value;
        return (language === 'auto' ? sets[0] : sets.find(s => s.language === language)) || null;
    }
    function showSaved() {
        const asked = saved ? pick(saved.questions) : null;
        const answered = saved ? pick(saved.answers) : null;
        if (asked) render(asked); else clearQuestions();
        if (answered) renderAnswers(answered); else clearAnswers();
    }
    function savedStatus() {
        if (latest || answers) {
            return 'Showing what is saved for this vacancy. Nothing is generated again unless you press the button.';
        }
        return saved && (saved.questions.length || saved.answers.length)
            ? 'Nothing is saved for this vacancy in this language. Choose Automatic to see the newest saved set, or generate one.'
            : 'Nothing generated for this vacancy yet. Choose the half you need and press its button.';
    }
    // A new or edited set takes the place of the saved one in its language.
    function remember(half, set) {
        if (!saved || saved.job_id !== set.job_id) saved = {job_id: set.job_id, questions: [], answers: []};
        saved[half] = [set, ...saved[half].filter(s => s.language !== set.language)]
            .sort((a, b) => new Date(b.generated_at) - new Date(a.generated_at));
    }
    // Quiet by design: it runs while the user types, so it neither disables
    // the page nor waits for anything but the save before it.
    function saveEdits() {
        clearTimeout(saveTimer);
        const user = validUser();
        if (!answers || !user || stale) return;
        const {set, emptied} = onScreen();
        const text = JSON.stringify(set);
        if (text === queued) {
            if (emptied && !busy) status(EMPTIED);
            return;
        }
        queued = text;
        const ctx = {user, epoch};
        saving = saving.then(() => putAnswers(ctx, set, text, emptied));
    }
    // An edit counts as saved only once the server has it. Until then the page
    // does not treat it as saved, and a failed save goes again on the next
    // change or blur instead of being skipped as done.
    async function putAnswers(ctx, set, text, emptied) {
        try {
            await api('/saved/answers/' + set.job_id, ctx, json('PUT', set));
        } catch (error) {
            if (queued === text) queued = '';
            if (error.message === 'stale' || ctx.epoch !== epoch) return;
            const shown = answers && answers.job_id === set.job_id && answers.language === set.language;
            if (error.status === 409 && shown) { stale = true; status(STALE, true); return; }
            status(error.message, true);
            return;
        }
        if (answers && answers.job_id === set.job_id && answers.language === set.language) lastSaved = text;
        if (saved && saved.job_id === set.job_id) remember('answers', set);
        if (!busy) status(emptied ? `Your edits are saved. ${EMPTIED}` : 'Your edited answers are saved with this vacancy.');
    }
    // Before the answers on screen make way for another set, their last edits
    // are sent and have to land. If they did not, the answers stay on screen.
    async function settle() {
        saveEdits();
        await saving;
        if (!unsaved()) return;
        // Edits that can never be saved must not hold the page forever: the
        // applicant decides whether to leave them, after downloading them.
        if (stale && window.confirm('Your edits to these answers were not saved, because newer answers '
            + 'were generated for this vacancy. Leave them? Download them first if you want to keep them.')) {
            return;
        }
        throw new Error(stale ? STALE
            : 'Your latest edits to the answers could not be saved, so they stay on screen. Try again, or download them first.');
    }
    async function loadSaved() {
        const job = el('job').value;
        await run('Opening what is saved for this vacancy...', async ctx => {
            try { await settle(); } catch (error) { el('job').value = shownJob; throw error; }
            fresh(ctx);
            // Nothing of the previous vacancy stays on screen while this one is
            // read: if the read fails, its sets must not pass for this one's.
            shownJob = job; saved = null; showSaved();
            if (!job) { status('Choose the vacancy you are being interviewed for.'); return; }
            const data = await (await api(`/saved/${job}`, ctx)).json();
            fresh(ctx);
            if (el('job').value !== job) return;
            saved = data; showSaved();
            status(savedStatus());
        });
    }
    async function showLanguage() {
        await run('Opening the saved set in this language...', async ctx => {
            try { await settle(); } catch (error) { el('language').value = shownLanguage; throw error; }
            fresh(ctx);
            shownLanguage = el('language').value;
            showSaved(); status(savedStatus());
        });
    }
    // --- Downloads: the server renders the file, the page hands it over. ---
    function download(blob, name) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = name; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
    // The server names the file; the fallback only matters if a proxy drops it.
    function fileName(response, fallback) {
        const match = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '');
        return match ? match[1] : fallback;
    }
    function exportSet(half, body, format) {
        const word = format === 'docx';
        return run(`Preparing the ${word ? 'Word' : 'text'} file...`, async ctx => {
            const response = await api(`/export/${half}?format=${format}`, ctx, json('POST', body()));
            const blob = await response.blob();
            fresh(ctx);
            download(blob, fileName(response, `interview-${half}.${format}`));
            status(word
                ? 'Downloaded as a Word file. Word, LibreOffice and Google Docs open it, and every line can be changed.'
                : 'Downloaded as a text file. Any editor opens it.');
        });
    }
    const shownQuestions = () => {
        if (!latest) throw new Error('Generate questions first.');
        return latest;
    };
    const shownAnswers = () => {
        if (!answers) throw new Error('Generate their questions first.');
        return editedAnswers();
    };
    // Whether the server kept what it just generated; if not, say so plainly.
    function ready(response, what, advice) {
        return response.headers.get('X-Interview-Saved') === 'false'
            ? `Your ${what} are ready, but they could not be saved. Download them to keep them. ${advice}`
            : `Your ${what} are ready and saved with this vacancy. ${advice}`;
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
    function request(shown) {
        const job = el('job').value;
        if (!job) throw new Error('Choose a vacancy first.');
        // "Automatic" is the absence of a choice: the API detects the language
        // from the vacancy when it is null. With a saved set on screen, though,
        // generating again means that set, so its language goes along. Else
        // "again" could replace a set in another language that is not shown.
        const language = el('language').value;
        const again = shown && shown.job_id === Number(job) ? shown.language : null;
        return {
            job_id: Number(job),
            language: language === 'auto' ? again : language,
            cv_slug: el('cv').value || null, notes: el('notes').value,
        };
    }
    const languageName = set => LANGUAGES[set.language] || String(set.language).toUpperCase();
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('[data-tab="interview"].tab-btn').addEventListener('click', () => {
            if (loadedUser !== validUser()) load();
        });
        // Capture runs before app.js switches the user, so a declined switch
        // can still put the previous user back and stop the reload.
        document.getElementById('user-select').addEventListener('change', event => {
            keepInterviewTab = document.getElementById('interview-section').classList.contains('active');
            if (unsaved() && !window.confirm('Switch users and discard the interview answers you edited? '
                + 'Their latest edits are not saved yet. Download them first if you want to keep them.')) {
                event.target.value = currentUser || '';
                event.stopImmediatePropagation();
            }
        }, true);
        document.getElementById('user-select').addEventListener('change', () => {
            reset();
            if (keepInterviewTab) { switchTab('interview'); load(); }
        });
        el('refresh').onclick = () => run('Refreshing vacancies and CVs...', async ctx => {
            const data = await (await api('/context', ctx)).json(); fresh(ctx);
            const selectedJob = el('job').value, selectedCv = el('cv').value;
            choices(data);
            el('job').value = selectedJob; el('cv').value = selectedCv;
            if (el('cv').selectedIndex < 0) el('cv').selectedIndex = 0;
            status('Vacancies and CVs refreshed. Everything already generated is unchanged.');
        });
        el('job').addEventListener('change', loadSaved);
        el('language').addEventListener('change', () => {
            if (!saved) { shownLanguage = el('language').value; return; }
            showLanguage();
        });
        // A reload inside the pause before an edit is saved would lose it.
        window.addEventListener('beforeunload', event => {
            if (!unsaved()) return;
            saveEdits();
            event.preventDefault();
            event.returnValue = '';
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
        el('generate').onclick = () => run('Writing your questions... If the company has not been researched yet, it is looked up on the web first, so this can take two to four minutes.', async ctx => {
            const response = await api('/questions', ctx, json('POST', request(latest)));
            const set = await response.json();
            fresh(ctx); remember('questions', set); render(set);
            status(ready(response, 'questions', 'Read them before you use them, and drop any that no longer fit.'));
        });
        el('answers-generate').onclick = () => {
            // Generating again replaces the saved set on screen, which request()
            // makes sure of, and that set holds the user's rewritten answers.
            // Losing those after a call that itself takes minutes is not
            // something to do silently.
            if (answers && !window.confirm(`Generating again replaces the saved ${languageName(answers)} answers, `
                + 'including the ones you rewrote. Download them first if you want to keep them. Continue?')) {
                return;
            }
            run('Predicting their questions and drafting your answers... If the company has not been researched yet, it is looked up on the web first, so this can take two to four minutes.', async ctx => {
                const body = request(answers);
                // An edit still on its way must land before the new set does,
                // or it would be saved over the set just generated.
                saveEdits(); await saving;
                const response = await api('/answers', ctx, json('POST', body));
                const set = await response.json();
                fresh(ctx); remember('answers', set); renderAnswers(set);
                status(ready(response, 'draft answers', 'Rewrite each one in your own words, starting with the ones marked as a gap.'));
            });
        };
        el('copy').onclick = () => latest
            ? copy(el('copy'), plainText(latest))
            : status('Generate questions first.', true);
        el('answers-copy').onclick = () => answers
            ? copy(el('answers-copy'), plainTextAnswers(answers))
            : status('Generate their questions first.', true);
        el('docx').onclick = () => exportSet('questions', shownQuestions, 'docx');
        el('txt').onclick = () => exportSet('questions', shownQuestions, 'txt');
        el('answers-docx').onclick = () => exportSet('answers', shownAnswers, 'docx');
        el('answers-txt').onclick = () => exportSet('answers', shownAnswers, 'txt');
    });
})();

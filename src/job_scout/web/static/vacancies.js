/* Search the whole private vacancy library; keep applicant choices independent. */
(() => {
    'use strict';
    const el = id => document.getElementById('vacancy-' + id);
    const stages = {
        to_review: ['To review', 'You have not decided yet.'],
        interested: ['Interested', 'You want to pursue this vacancy.'],
        applied: ['Applied', 'You have sent your application.'],
        interviewing: ['Interviewing', 'An interview or assessment is in progress.'],
        offer: ['Offer received', 'The employer has made an offer.'],
        closed: ['Closed', 'You are no longer pursuing this vacancy.'],
    };
    let activeUser = null, requestId = 0, offset = 0, total = 0, timer = null;
    const pageSize = 20, drafts = new Map();
    const user = () => currentUser && currentUser !== 'all' ? currentUser : null;
    const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const key = id => `${activeUser}:${id}`;
    const message = (text, error = false) => { el('message').textContent = text; el('message').dataset.error = String(error); };
    const link = (url, text) => {
        try { if (!['http:', 'https:'].includes(new URL(url).protocol)) return ''; }
        catch (_) { return ''; }
        return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(text)}</a>`;
    };
    function companyInfo(review) {
        if (!review) return '';
        const list = (values, title) => values && values.length ? `<h5>${title}</h5><ul>${values.map(v => `<li>${escape(v)}</li>`).join('')}</ul>` : '';
        return `<details class="vacancy-company"><summary>Company information</summary><div class="vacancy-company-body">
            ${review.work_score != null ? `<p><strong>Company work-quality estimate: ${escape(review.work_score)}/100</strong></p>` : ''}
            <p class="info-text">This estimates the employer as a workplace. Your vacancy match score is shown at the top of the card.</p>
            <p>${escape(review.summary)}</p><p class="info-text">Confidence: ${escape(review.confidence || 'low')}</p>
            ${list(review.pros, 'Potential strengths')}${list(review.cons, 'Things to check')}
            ${review.sources && review.sources.length ? `<p>${review.sources.map((url, i) => link(url, `Source ${i + 1}`)).filter(Boolean).join(' · ')}</p>` : ''}
        </div></details>`;
    }
    function card(job) {
        const stage = stages[job.application_stage] ? job.application_stage : 'to_review';
        const note = drafts.has(key(job.id)) ? drafts.get(key(job.id)) : (job.notes || '');
        const meta = [job.location, job.source, job.primary_track_name].filter(Boolean);
        if (job.salary_min || job.salary_max) {
            meta.push(`€${job.salary_min ? job.salary_min.toLocaleString() : '?'}–€${job.salary_max ? job.salary_max.toLocaleString() : '?'}${job.salary_period ? ' / ' + job.salary_period : ''}`);
        }
        const filtered = job.status === 'rejected' && stage === 'to_review';
        return `<article class="vacancy-card${job.pinned ? ' is-pinned' : ''}" data-id="${job.id}" data-user="${escape(activeUser)}">
            <div class="vacancy-card-header"><div class="vacancy-title"><h4>${escape(job.title)}</h4><p class="vacancy-employer">${escape(job.company)}</p><div class="vacancy-meta">${meta.map(m => `<span>${escape(m)}</span>`).join('')}</div></div>
                <div class="vacancy-match ${job.fit_score == null ? 'unscored' : getScoreClass(job.fit_score)}"><span>Vacancy match</span><strong>${job.fit_score == null ? 'Not scored' : `${escape(job.fit_score)}<small>/100</small>`}</strong></div></div>
            ${filtered ? '<p class="vacancy-filtered">Filtered out by your search rules — you can still pin it or mark it Interested.</p>' : ''}
            ${job.official_available === false ? '<p class="vacancy-filtered">The employer’s listing may no longer be available.</p>' : ''}
            ${job.fit_reasoning ? `<p class="vacancy-reason">${escape(job.fit_reasoning)}</p>` : ''}
            <div class="vacancy-actions"><button type="button" class="btn ${job.pinned ? 'btn-primary' : 'btn-secondary'}" data-action="pin" data-pinned="${job.pinned}" aria-pressed="${job.pinned}">${job.pinned ? '★ Pinned' : '☆ Pin vacancy'}</button>
                ${link(job.official_url || job.url, 'View vacancy')}${job.official_available === true ? '<span class="badge badge-open">Employer listing open</span>' : ''}
                <button type="button" class="btn btn-secondary" data-action="letter">Write cover letter</button></div>
            <div class="vacancy-progress"><label for="vacancy-stage-${job.id}">Your progress</label><select id="vacancy-stage-${job.id}" data-action="stage" data-stage="${stage}" aria-describedby="vacancy-stage-help-${job.id}">${Object.entries(stages).map(([value, label]) => `<option value="${value}"${stage === value ? ' selected' : ''}>${label[0]}</option>`).join('')}</select><p id="vacancy-stage-help-${job.id}" class="info-text">${stages[stage][1]} Changes save immediately.</p></div>
            <details class="vacancy-notes"><summary>Your notes${note ? ' · added' : ''}${drafts.has(key(job.id)) ? ' · unsaved' : ''}</summary><label for="vacancy-note-${job.id}" class="sr-only">Notes for ${escape(job.title)}</label><textarea id="vacancy-note-${job.id}" rows="3" maxlength="4000" data-action="notes" placeholder="Contacts, questions or next steps">${escape(note)}</textarea><button class="btn btn-small" type="button" data-action="save-notes">Save notes</button></details>
            <details class="vacancy-details"><summary>Vacancy details &amp; match explanation</summary>${job.negative_reasoning ? `<p><strong>Search assessment:</strong> ${escape(job.negative_reasoning)}</p>` : ''}${job.compensation_reasoning ? `<p><strong>Compensation:</strong> ${escape(job.compensation_reasoning)}</p>` : ''}${job.track_scores && job.track_scores.length ? `<p>${job.track_scores.map(t => `${escape(t.track_name || t.track_id)}: ${t.fit_score == null ? 'not scored' : escape(t.fit_score) + '/100'}`).join(' · ')}</p>` : ''}<p class="vacancy-description">${escape(job.description || 'No description saved. Open the vacancy for details.')}</p>${link(job.url, 'Original listing')}</details>
            ${companyInfo(job.company_review)}
        </article>`;
    }
    function resetFilters() {
        ['search','stage','min-score','source'].forEach(id => { el(id).value = ''; });
        el('scope').value = 'all'; el('sort').value = 'score_desc'; el('pinned').checked = false;
        offset = 0;
    }
    window.loadVacancies = async () => {
        const owner = user(), token = ++requestId;
        if (owner !== activeUser) {
            activeUser = owner; resetFilters();
            el('list').replaceChildren(); el('results').textContent = ''; el('page').textContent = '';
        }
        el('filters').disabled = !owner;
        el('prev').disabled = true; el('next').disabled = true;
        if (!owner) {
            el('list').innerHTML = '<p class="empty">Select one user to view and manage vacancies.</p>';
            el('results').textContent = ''; el('page').textContent = ''; message(''); return;
        }
        const params = new URLSearchParams({user:owner, limit:pageSize, offset, q:el('search').value, scope:el('scope').value, sort:el('sort').value, pinned_only:el('pinned').checked});
        ['stage','min-score','source'].forEach(id => { if (el(id).value !== '') params.set(id === 'min-score' ? 'min_score' : id, el(id).value); });
        message('Loading vacancies…');
        try {
            const response = await fetchWithAuth('/api/vacancies?' + params);
            const data = await response.json();
            if (token !== requestId || owner !== user()) return;
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load vacancies.');
            total = data.total;
            if (total && offset >= total) { offset = Math.floor((total - 1) / pageSize) * pageSize; return loadVacancies(); }
            el('list').innerHTML = data.items.length ? data.items.map(card).join('') : '<p class="empty">No vacancies match these filters. Try another search or clear the filters.</p>';
            const source = el('source').value;
            el('source').replaceChildren(new Option('All sources', ''));
            data.sources.forEach(s => el('source').add(new Option(s, s))); el('source').value = source;
            el('results').textContent = total ? `Showing ${offset + 1}–${offset + data.items.length} of ${total} vacancies · Pinned first` : '0 vacancies';
            el('page').textContent = total ? `Page ${Math.floor(offset / pageSize) + 1} of ${Math.ceil(total / pageSize)}` : '';
            el('prev').disabled = offset === 0; el('next').disabled = offset + pageSize >= total;
            message('');
        } catch (error) {
            if (token === requestId && owner === user()) message(error.message, true);
        }
    };
    async function patch(node, changes, confirmation) {
        const owner = user(), id = Number(node.dataset.id);
        if (!owner || node.dataset.user !== owner) return;
        node.querySelectorAll('button,select,textarea').forEach(c => { c.disabled = true; });
        try {
            const response = await fetchWithAuth(`/api/vacancies/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user:owner, ...changes})});
            const data = await response.json();
            if (owner !== user()) return;
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The change could not be saved.');
            if ('notes' in changes && drafts.get(`${owner}:${id}`) === changes.notes) drafts.delete(`${owner}:${id}`);
            await loadVacancies();
            if (owner === user()) message(confirmation);
        } catch (error) {
            if (owner === user()) {
                const select = node.querySelector('[data-action=stage]'); select.value = select.dataset.stage;
                message(error.message, true);
            }
        } finally { node.querySelectorAll('button,select,textarea').forEach(c => { c.disabled = false; }); }
    }
    document.addEventListener('DOMContentLoaded', () => {
        el('search').addEventListener('input', () => {
            ++requestId; clearTimeout(timer); offset = 0; timer = setTimeout(loadVacancies, 250);
        });
        ['scope','stage','min-score','source','sort','pinned'].forEach(id => el(id).addEventListener('change', () => { offset = 0; loadVacancies(); }));
        el('refresh').onclick = () => loadVacancies();
        el('clear').onclick = () => { resetFilters(); loadVacancies(); };
        el('prev').onclick = () => { offset = Math.max(0, offset - pageSize); loadVacancies(); };
        el('next').onclick = () => { offset += pageSize; loadVacancies(); };
        el('list').addEventListener('input', event => {
            if (event.target.dataset.action !== 'notes') return;
            const node = event.target.closest('[data-id]'); drafts.set(`${node.dataset.user}:${node.dataset.id}`, event.target.value);
        });
        el('list').addEventListener('change', event => {
            if (event.target.dataset.action !== 'stage') return;
            patch(event.target.closest('[data-id]'), {stage:event.target.value}, 'Progress saved.');
        });
        el('list').addEventListener('click', event => {
            const button = event.target.closest('button[data-action]'); if (!button) return;
            const node = button.closest('[data-id]'); if (node.dataset.user !== user()) return;
            if (button.dataset.action === 'pin') patch(node, {pinned:button.dataset.pinned !== 'true'}, button.dataset.pinned === 'true' ? 'Vacancy unpinned.' : 'Vacancy pinned.');
            if (button.dataset.action === 'save-notes') patch(node, {notes:node.querySelector('textarea').value}, 'Notes saved.');
            if (button.dataset.action === 'letter') openLetterForJob(Number(node.dataset.id));
        });
        window.addEventListener('beforeunload', event => { if (drafts.size) { event.preventDefault(); event.returnValue = ''; } });
    });
})();

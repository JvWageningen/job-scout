/**
 * job-scout Dashboard Frontend
 * Handles user interactions and API communication
 */

// API base URL
const API_BASE = '/api';

// Current user state (null for global, "all" for all users, or a specific user name)
let currentUser = null;

// Dashboard token stored in sessionStorage
let dashboardToken = sessionStorage.getItem('dashboardToken');

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    checkGlobalSetup();
    loadUsers();
    loadLLMSettings();
});

/**
 * Fetch wrapper that handles dashboard token authentication.
 * Prompts for token on 401 and retries with the provided token.
 *
 * @param {string} url - Request URL
 * @param {object} options - Fetch options (method, headers, body, etc.)
 * @returns {Promise<Response>} - Fetch response
 */
async function fetchWithAuth(url, options = {}) {
    // Add token to headers if available
    const headers = options.headers || {};
    if (dashboardToken && url.startsWith('/api/')) {
        headers.Authorization = `Bearer ${dashboardToken}`;
    }
    const modifiedOptions = { ...options, headers };

    let response = await fetch(url, modifiedOptions);

    // If we got 401 and it's an API request, prompt for token
    if (response.status === 401 && url.startsWith('/api/')) {
        const token = prompt('This dashboard requires authentication.\n\nEnter the dashboard token:');
        if (token) {
            dashboardToken = token;
            sessionStorage.setItem('dashboardToken', token);
            // Retry with the new token
            const retryHeaders = { ...headers, Authorization: `Bearer ${token}` };
            response = await fetch(url, { ...options, headers: retryHeaders });
        }
    }

    return response;
}

/**
 * Set up event listeners for UI interactions
 */
function setupEventListeners() {
    const userSelect = document.getElementById('user-select');
    const runBtn = document.getElementById('run-btn');
    const logSelect = document.getElementById('log-select');

    if (userSelect) {
        userSelect.addEventListener('change', (e) => {
            currentUser = e.target.value || null;
            if (currentUser === 'all') {
                // Show dashboard for all-users run
                showDashboard();
                // Don't load per-user data for all-users mode
            } else if (currentUser) {
                // Show dashboard for single user
                showDashboard();
                loadDashboard();
                loadAllUserData();
            } else {
                hideDashboard();
            }
        });
    }

    if (runBtn) {
        runBtn.addEventListener('click', () => {
            runPipeline();
        });
    }

    if (logSelect) {
        logSelect.addEventListener('change', (e) => {
            if (e.target.value) {
                loadLogFile(e.target.value);
            } else {
                document.getElementById('log-content').classList.add('hidden');
            }
        });
    }

    // Tab navigation
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
        });
    });

    // Form submissions
    const profileForm = document.getElementById('profile-form');
    if (profileForm) {
        profileForm.addEventListener('submit', (e) => {
            e.preventDefault();
            saveProfile();
        });
    }

    const sitesForm = document.getElementById('add-site-form');
    if (sitesForm) {
        sitesForm.addEventListener('submit', (e) => {
            e.preventDefault();
            addSite();
        });
    }

    const secretsForm = document.getElementById('secrets-form');
    if (secretsForm) {
        secretsForm.addEventListener('submit', (e) => {
            e.preventDefault();
            updateSecrets();
        });
    }

    const llmForm = document.getElementById('llm-form');
    if (llmForm) {
        llmForm.addEventListener('submit', (e) => {
            e.preventDefault();
            saveLLMSettings();
        });
    }

    const scheduleForm = document.getElementById('schedule-form');
    if (scheduleForm) {
        scheduleForm.addEventListener('submit', (e) => {
            e.preventDefault();
            installSchedule();
        });
    }
    const notificationsForm = document.getElementById('notifications-form');
    if (notificationsForm) {
        notificationsForm.addEventListener('submit', (e) => {
            e.preventDefault();
            saveNotifications();
        });
    }

    const notificationChannelSelect = document.getElementById('notification-channel');
    if (notificationChannelSelect) {
        notificationChannelSelect.addEventListener('change', (e) => {
            updateNotificationChannelUI(e.target.value);
        });
    }

    const testNotifBtn = document.getElementById('test-notif-btn');
    if (testNotifBtn) {
        testNotifBtn.addEventListener('click', testNotificationChannel);
    }

    

    const removeScheduleBtn = document.getElementById('remove-schedule-btn');
    if (removeScheduleBtn) {
        removeScheduleBtn.addEventListener('click', removeSchedule);
    }

    const refreshKeywordsBtn = document.getElementById('refresh-keywords-btn');
    if (refreshKeywordsBtn) {
        refreshKeywordsBtn.addEventListener('click', refreshKeywords);
    }

    const testConnBtn = document.getElementById('test-conn-btn');
    if (testConnBtn) {
        testConnBtn.addEventListener('click', testConnection);
    }

    const detectModelsBtn = document.getElementById('detect-models-btn');
    if (detectModelsBtn) {
        detectModelsBtn.addEventListener('click', detectLocalModels);
    }

    const createUserBtn = document.getElementById('create-user-btn');
    if (createUserBtn) {
        createUserBtn.addEventListener('click', () => {
            const name = prompt('Enter new user name:');
            if (name && name.trim()) {
                createUser(name.trim());
            }
        });
    }

    const globalSetupForm = document.getElementById('global-setup-form');
    if (globalSetupForm) {
        globalSetupForm.addEventListener('submit', (e) => {
            e.preventDefault();
            initializeGlobalSetup();
        });
    }

    // Filter controls for matched jobs
    const matchedMinScore = document.getElementById('matched-min-score');
    const matchedSource = document.getElementById('matched-source');
    const matchedSort = document.getElementById('matched-sort');

    [matchedMinScore, matchedSource, matchedSort].forEach((el) => {
        if (el) {
            el.addEventListener('change', loadMatchedJobs);
        }
    });

    // Filter controls for rejected jobs
    const rejectedMinScore = document.getElementById('rejected-min-score');
    const rejectedSource = document.getElementById('rejected-source');
    const rejectedSort = document.getElementById('rejected-sort');

    [rejectedMinScore, rejectedSource, rejectedSort].forEach((el) => {
        if (el) {
            el.addEventListener('change', loadRejectedJobs);
        }
    });

    // Profile enrichment (LinkedIn import + web person search)
    initEnrichmentUI();
    initCoachUI();
    initAutoScheduleUI();
    initNtfyUI();
    initFeedbackUI();

    // Coach suggestion chips are rendered dynamically, so delegate.
    document.addEventListener('click', handleCoachOptionClick);
}

/**
 * Load the list of users from the API
 */
async function loadUsers() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/users`);
        if (!response.ok) {
            console.error('Failed to load users:', response.status);
            return;
        }

        const users = await response.json();
        const userSelect = document.getElementById('user-select');

        // Clear existing options
        userSelect.innerHTML = '<option value="">-- Choose a user --</option>';

        // Add "all" option only if there are users
        if (users.length > 1) {
            const allOption = document.createElement('option');
            allOption.value = 'all';
            allOption.textContent = '-- All Users --';
            userSelect.appendChild(allOption);
        }

        // Add user options
        users.forEach((user) => {
            const option = document.createElement('option');
            option.value = user;
            option.textContent = user;
            userSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading users:', error);
    }
}

/**
 * Show the dashboard section
 */
function showDashboard() {
    switchTab('dashboard');
}

/**
 * Hide the dashboard section
 */
function hideDashboard() {
    document.getElementById('dashboard-section').classList.add('hidden');
    document.getElementById('dashboard-section').classList.remove('active');
}

/**
 * Load all dashboard data for the current user
 */
async function loadDashboard() {
    if (!currentUser) {
        return;
    }

    await Promise.all([
        loadMatchedJobs(),
        loadRejectedJobs(),
        loadLogs(),
        pollRunStatus(),
    ]);
}

/**
 * Load and display recently matched jobs with filtering and sorting
 */
async function loadMatchedJobs() {
    if (!currentUser) {
        return;
    }

    const container = document.getElementById('matched-jobs-container');
    container.innerHTML = '<p class="loading">Loading matched jobs...</p>';

    try {
        // Get filter values from UI
        const minScore = document.getElementById('matched-min-score').value;
        const source = document.getElementById('matched-source').value;
        const sort = document.getElementById('matched-sort').value;

        // Build query string
        const params = new URLSearchParams({
            user: currentUser,
            limit: '20',
        });
        if (minScore) {
            params.append('min_score', minScore);
        }
        if (source) {
            params.append('source', source);
        }
        if (sort) {
            params.append('sort', sort);
        }

        const response = await fetchWithAuth(`${API_BASE}/jobs/matched?${params.toString()}`);
        if (!response.ok) {
            container.innerHTML = '<p class="empty">Failed to load matched jobs</p>';
            return;
        }

        const jobs = await response.json();

        if (jobs.length === 0) {
            container.innerHTML = '<p class="empty">No matched jobs found</p>';
            return;
        }

        container.innerHTML = jobs.map((job) => renderJobCard(job, false)).join('');
        updateSourceDropdown(jobs, 'matched-source');
    } catch (error) {
        console.error('Error loading matched jobs:', error);
        container.innerHTML = '<p class="empty">Error loading matched jobs</p>';
    }
}

/**
 * Load and display recently rejected jobs with filtering and sorting
 */
async function loadRejectedJobs() {
    if (!currentUser) {
        return;
    }

    const container = document.getElementById('rejected-jobs-container');
    container.innerHTML = '<p class="loading">Loading rejected jobs...</p>';

    try {
        // Get filter values from UI
        const minScore = document.getElementById('rejected-min-score').value;
        const source = document.getElementById('rejected-source').value;
        const sort = document.getElementById('rejected-sort').value;

        // Build query string
        const params = new URLSearchParams({
            user: currentUser,
            limit: '20',
        });
        if (minScore) {
            params.append('min_score', minScore);
        }
        if (source) {
            params.append('source', source);
        }
        if (sort) {
            params.append('sort', sort);
        }

        const response = await fetchWithAuth(`${API_BASE}/jobs/rejected?${params.toString()}`);
        if (!response.ok) {
            container.innerHTML = '<p class="empty">Failed to load rejected jobs</p>';
            return;
        }

        const jobs = await response.json();

        if (jobs.length === 0) {
            container.innerHTML = '<p class="empty">No rejected jobs found</p>';
            return;
        }

        container.innerHTML = jobs.map((job) => renderJobCard(job, true)).join('');
        updateSourceDropdown(jobs, 'rejected-source');
    } catch (error) {
        console.error('Error loading rejected jobs:', error);
        container.innerHTML = '<p class="empty">Error loading rejected jobs</p>';
    }
}

/**
 * Render a single job card HTML string
 *
 * @param {Object} job - Job listing object
 * @param {boolean} rejected - Whether this is a rejected job
 * @returns {string} HTML for the job card
 */
function renderJobCard(job, rejected) {
    const cardClass = rejected ? 'job-card rejected' : 'job-card';
    const scoreClass = getScoreClass(job.fit_score);

    let meta = '';
    if (job.fit_score !== null && !rejected) {
        meta += `<span class="job-score ${scoreClass}">Score: ${job.fit_score}/100</span>`;
    }
    if (job.primary_track_name) {
        const others = (job.track_scores || [])
            .filter((s) => s.track_id !== job.primary_track_id && s.fit_score !== null)
            .map((s) => `${escapeHtml(s.track_name || s.track_id)} ${s.fit_score}`)
            .join(', ');
        meta +=
            `<span class="job-track" title="${others ? 'Also scored — ' + others : 'Matched direction'}">` +
            `${escapeHtml(job.primary_track_name)}</span>`;
    }
    if (job.location) {
        meta += `<span>Location: ${escapeHtml(job.location)}</span>`;
    }
    if (job.salary_min) {
        meta += `<span>Salary: €${job.salary_min.toLocaleString()} - €${job.salary_max?.toLocaleString() || '?'}</span>`;
    }

    const statusSection = !rejected ? `
        <div class="job-lifecycle-controls">
            <div class="status-control">
                <label for="status-${job.id}">Status:</label>
                <select id="status-${job.id}" class="status-select">
                    <option value="new" ${job.status === 'new' ? 'selected' : ''}>New</option>
                    <option value="viewed" ${job.status === 'viewed' ? 'selected' : ''}>Viewed</option>
                    <option value="approved" ${job.status === 'approved' ? 'selected' : ''}>Approved</option>
                    <option value="ready" ${job.status === 'ready' ? 'selected' : ''}>Ready</option>
                    <option value="submitted" ${job.status === 'submitted' ? 'selected' : ''}>Submitted</option>
                    <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>Interviewing</option>
                    <option value="offer" ${job.status === 'offer' ? 'selected' : ''}>Offer</option>
                    <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>Rejected</option>
                    <option value="expired" ${job.status === 'expired' ? 'selected' : ''}>Expired (filled)</option>
                </select>
            </div>
            <div class="notes-control">
                <label for="notes-${job.id}">Notes:</label>
                <textarea id="notes-${job.id}" class="notes-field" placeholder="Add notes..." rows="2">${job.notes ? escapeHtml(job.notes) : ''}</textarea>
            </div>
            <button class="btn btn-small" onclick="updateJobStatus(${job.id})">Save Status</button>
        </div>
    ` : '';

    return `
        <div class="${cardClass}">
            <h4>${escapeHtml(job.title)}</h4>
            <p><strong>${escapeHtml(job.company)}</strong></p>
            ${job.fit_reasoning ? `<p><em>${escapeHtml(job.fit_reasoning)}</em></p>` : ''}
            ${job.negative_reasoning ? `<p><em>Reason: ${escapeHtml(job.negative_reasoning)}</em></p>` : ''}
            ${job.compensation_reasoning ? `<p><em>Compensation: ${escapeHtml(job.compensation_reasoning)}</em></p>` : ''}
            ${renderCompanyReview(job.company_review)}
            <div class="job-meta">
                ${meta}
            </div>
            ${statusSection}
            <p class="job-links">
                <a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">View Job →</a>
                ${renderOfficialLink(job)}
            </p>
        </div>
    `;
}

/**
 * Render a link to the employer's own posting, with an availability badge.
 *
 * @param {Object} job - The job object (uses official_url / official_available).
 * @returns {string} HTML for the employer link, or empty string.
 */
function renderOfficialLink(job) {
    if (!job.official_url) {
        return '';
    }
    let badge = '';
    if (job.official_available === true) {
        badge = ' <span class="badge badge-open">open</span>';
    } else if (job.official_available === false) {
        badge = ' <span class="badge badge-filled">may be filled</span>';
    }
    return `<a href="${escapeHtml(job.official_url)}" target="_blank" rel="noopener noreferrer">🏢 Company site →</a>${badge}`;
}

/**
 * Render a company work-quality review block.
 *
 * @param {Object|null} review - The company_review object.
 * @returns {string} HTML for the review, or empty string.
 */
function renderCompanyReview(review) {
    if (!review || review.work_score === null || review.work_score === undefined) {
        return '';
    }
    const pros = (review.pros || []).slice(0, 3)
        .map((p) => `<li>${escapeHtml(p)}</li>`).join('');
    const cons = (review.cons || []).slice(0, 3)
        .map((c) => `<li>${escapeHtml(c)}</li>`).join('');
    return `
        <div class="company-review">
            <strong>Company: ${review.work_score}/100</strong>
            <span class="review-confidence">(${escapeHtml(review.confidence || 'low')} confidence)</span>
            ${review.summary ? `<p>${escapeHtml(review.summary)}</p>` : ''}
            ${pros ? `<ul class="review-pros">${pros}</ul>` : ''}
            ${cons ? `<ul class="review-cons">${cons}</ul>` : ''}
        </div>
    `;
}

/**
 * Update source dropdown with unique sources from loaded jobs
 *
 * @param {Array<Object>} jobs - Array of job objects
 * @param {string} dropdownId - ID of the source dropdown element
 */
function updateSourceDropdown(jobs, dropdownId) {
    const dropdown = document.getElementById(dropdownId);
    if (!dropdown) {
        return;
    }

    // Get unique sources from jobs
    const sources = new Set();
    jobs.forEach((job) => {
        if (job.source) {
            sources.add(job.source);
        }
    });

    // Get current selected value
    const currentValue = dropdown.value;

    // Clear existing options except the first one (All Sources)
    dropdown.innerHTML = '<option value="">All Sources</option>';

    // Add unique sources as options
    Array.from(sources)
        .sort()
        .forEach((source) => {
            const option = document.createElement('option');
            option.value = source;
            option.textContent = source;
            dropdown.appendChild(option);
        });

    // Restore the previously selected value if it still exists
    if (currentValue && Array.from(dropdown.options).some((opt) => opt.value === currentValue)) {
        dropdown.value = currentValue;
    }
}

/**
 * Get CSS class for score styling
 *
 * @param {number|null} score - Fit score
 * @returns {string} CSS class name
 */
function getScoreClass(score) {
    if (score === null) {
        return '';
    }
    if (score >= 70) {
        return 'score-high';
    }
    if (score >= 50) {
        return 'score-medium';
    }
    return 'score-low';
}

/**
 * Load and display list of log files
 */
async function loadLogs() {
    if (!currentUser) {
        return;
    }

    const logSelect = document.getElementById('log-select');
    logSelect.innerHTML = '<option value="">-- Choose a log file --</option>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/logs?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            console.error('Failed to load logs:', response.status);
            return;
        }

        const logs = await response.json();

        if (logs.length === 0) {
            return;
        }

        logs.forEach((log) => {
            const option = document.createElement('option');
            option.value = log.name;
            const mtime = new Date(log.mtime * 1000).toLocaleString();
            const sizeKB = (log.size / 1024).toFixed(1);
            option.textContent = `${log.name} (${mtime}, ${sizeKB} KB)`;
            logSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading logs:', error);
    }
}

/**
 * Load and display the content of a specific log file
 *
 * @param {string} filename - Log file name
 */
async function loadLogFile(filename) {
    if (!currentUser) {
        return;
    }

    const logContent = document.getElementById('log-content');
    const logText = document.getElementById('log-text');

    logText.textContent = 'Loading...';
    logContent.classList.remove('hidden');

    try {
        const response = await fetchWithAuth(
            `${API_BASE}/logs/${encodeURIComponent(filename)}?user=${encodeURIComponent(currentUser)}&lines=500`
        );
        if (!response.ok) {
            logText.textContent = 'Failed to load log file';
            return;
        }

        const data = await response.json();
        logText.textContent = data.content;

        // Scroll to bottom
        setTimeout(() => {
            const preElement = logContent.querySelector('pre');
            if (preElement) {
                preElement.scrollTop = preElement.scrollHeight;
            }
        }, 0);
    } catch (error) {
        console.error('Error loading log file:', error);
        logText.textContent = 'Error loading log file';
    }
}

/**
 * Poll the run status until completion
 */
let statusPollInterval = null;

/**
 * Format a duration in seconds as a short human-readable string.
 */
function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) {
        return '';
    }
    if (seconds < 60) {
        return `${Math.round(seconds)}s`;
    }
    const mins = Math.round(seconds / 60);
    if (mins < 60) {
        return `${mins} min`;
    }
    const hours = Math.floor(mins / 60);
    return `${hours}h ${mins % 60}m`;
}

/**
 * Render the live progress of a running pipeline.
 *
 * A full run can take well over an hour, so without stage, counts and a time
 * estimate a slow run is indistinguishable from a stuck one.
 */
function renderRunProgress(p) {
    if (!p) {
        return '';
    }

    const step = p.stage_index
        ? `Step ${p.stage_index}/${p.stage_count} — `
        : '';
    const counts = p.total ? ` (${p.current}/${p.total})` : '';
    const pct = p.percent === null || p.percent === undefined ? null : p.percent;

    const bar =
        pct === null
            ? '<div class="progress-bar indeterminate"><span></span></div>'
            : `<div class="progress-bar"><span style="width:${pct}%"></span></div>`;

    const eta = p.eta_seconds
        ? `about ${formatDuration(p.eta_seconds)} left in this step`
        : 'estimating…';
    const elapsed = p.elapsed_seconds
        ? ` · ${formatDuration(p.elapsed_seconds)} elapsed`
        : '';
    const detail = p.detail
        ? `<p class="progress-detail">${escapeHtml(p.detail)}</p>`
        : '';

    return `
        <div class="run-progress">
            <p class="progress-stage">${step}${escapeHtml(p.stage_label || '')}${counts}</p>
            ${bar}
            <p class="progress-eta">${eta}${elapsed}</p>
            ${detail}
        </div>`;
}

async function pollRunStatus() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/run/status?user=${currentUser}`);
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        const statusDiv = document.getElementById('run-status');
        if (!statusDiv) {
            return;
        }

        const statusText = `Status: <strong>${escapeHtml(data.status)}</strong>`;
        const messageText = escapeHtml(data.message || '');
        const errorText = data.error ? `<p class="error">Error: ${escapeHtml(data.error)}</p>` : '';
        const timeText = data.start_time ? `<p class="time">Started: ${new Date(data.start_time).toLocaleString()}</p>` : '';

        statusDiv.innerHTML =
            `<div class="status-info">${statusText}<p>${messageText}</p>` +
            `${renderRunProgress(data.progress)}${timeText}${errorText}</div>`;

        if (data.status === 'running' && !statusPollInterval) {
            // A run is already in progress (e.g. started from another tab/session) — start
            // tracking it so this tab's UI updates when it finishes.
            const runBtn = document.getElementById('run-btn');
            if (runBtn) {
                runBtn.disabled = true;
                runBtn.textContent = 'Running...';
            }
            statusPollInterval = setInterval(pollRunStatus, 2000);
            return;
        }

        // The backend keeps a user's last run status as 'done'/'error' indefinitely (it never
        // resets to 'idle'), so every dashboard load or user switch would otherwise see a
        // "terminal" status and re-trigger the reload below on a loop. Only react here if we
        // were actively polling this run ourselves (statusPollInterval set), so a stale status
        // from a past run doesn't cause an infinite loadDashboard -> pollRunStatus -> loadDashboard cycle.
        if ((data.status === 'done' || data.status === 'error') && statusPollInterval) {
            const runBtn = document.getElementById('run-btn');
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.textContent = 'Run Pipeline';
            }
            clearInterval(statusPollInterval);
            statusPollInterval = null;
            // Reload dashboard data
            setTimeout(() => {
                loadDashboard();
            }, 1000);
        }
    } catch (error) {
        console.error('Error polling run status:', error);
    }
}

/**
 * Run the pipeline (POST to /api/run)
 */
async function runPipeline() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const dryRun = document.getElementById('dry-run-check').checked;
    const full = document.getElementById('full-check').checked;
    const runBtn = document.getElementById('run-btn');

    runBtn.disabled = true;
    runBtn.textContent = 'Running...';

    try {
        const body = {
            dry_run: dryRun,
            full: full,
        };
        // Add either 'user' or 'all' to request
        if (currentUser === 'all') {
            body.all = true;
        } else {
            body.user = currentUser;
        }

        const response = await fetchWithAuth(`${API_BASE}/run`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Pipeline failed: ${error.detail || 'Unknown error'}`);
            runBtn.disabled = false;
            runBtn.textContent = 'Run Pipeline';
        } else {
            const result = await response.json();
            console.log('Pipeline started:', result);
            // Start polling status
            if (statusPollInterval) {
                clearInterval(statusPollInterval);
            }
            statusPollInterval = setInterval(pollRunStatus, 2000);
            // Poll immediately
            await pollRunStatus();
        }
    } catch (error) {
        console.error('Error running pipeline:', error);
        alert('Error running pipeline: ' + error.message);
        runBtn.disabled = false;
        runBtn.textContent = 'Run Pipeline';
    }
}

/**
 * Switch to a different tab
 *
 * @param {string} tab - Tab name to switch to
 */
function switchTab(tab) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach((el) => {
        el.classList.remove('active');
    });

    // Deactivate all tab buttons
    document.querySelectorAll('.tab-btn').forEach((el) => {
        el.classList.remove('active');
    });

    // Show selected tab
    const tabEl = document.querySelector(`.tab-content[data-tab="${tab}"]`);
    if (tabEl) {
        tabEl.classList.remove('hidden');
        tabEl.classList.add('active');
    }

    // Activate selected button
    const btnEl = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (btnEl) {
        btnEl.classList.add('active');
    }

    // Reload analytics when switching to that tab
    if (tab === 'analytics' && currentUser) {
        loadAnalytics();
    }
}

/**
 * Load all user-specific data (config, sites, schedule, etc.)
 */
async function loadAllUserData() {
    if (!currentUser) {
        return;
    }

    await Promise.all([
        loadProfileData(),
        loadCVProfile(),
        loadTracks(),
        loadNotificationData(),
        loadSitesData(),
        loadLLMSettings(),
        loadScheduleStatus(),
        loadKeywords(),
        loadAnalytics(),
        loadNtfySubscription(),
        loadFeedbackJobs(),
    ]);
}

/**
 * Load and populate the profile form
 */
/**
 * Load and display CV profile
 */
async function loadCVProfile() {
    if (!currentUser) {
        return;
    }

    const container = document.getElementById('cv-summary-container');
    const content = document.getElementById('cv-summary-content');
    const loading = document.getElementById('cv-loading');
    const error = document.getElementById('cv-error');
    const details = document.getElementById('cv-profile-details');

    // Reset state
    loading.style.display = 'none';
    error.style.display = 'none';
    details.style.display = 'none';

    try {
        const response = await fetchWithAuth(`${API_BASE}/profile/cv-summary?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            container.style.display = 'block';
            error.style.display = 'block';
            error.textContent = 'Failed to load CV profile';
            return;
        }

        const data = await response.json();
        
        if (data.error) {
            container.style.display = 'block';
            error.style.display = 'block';
            error.textContent = data.error;
            return;
        }

        if (!data.cv_profile) {
            return;
        }

        // Display the CV profile
        container.style.display = 'block';
        error.style.display = 'none';
        loading.style.display = 'none';
        details.style.display = 'block';

        const profile = data.cv_profile;

        // Years of experience
        const yearsEl = document.getElementById('cv-years');
        if (profile.years_experience !== null) {
            yearsEl.textContent = profile.years_experience + ' years';
        } else {
            yearsEl.textContent = 'Not specified';
        }

        // Skills
        const skillsList = document.getElementById('cv-skills');
        skillsList.innerHTML = '';
        if (profile.skills && profile.skills.length > 0) {
            profile.skills.forEach(skill => {
                const div = document.createElement('div');
                div.textContent = skill;
                skillsList.appendChild(div);
            });
        } else {
            skillsList.innerHTML = '<span>No skills extracted</span>';
        }

        // Education
        const eduList = document.getElementById('cv-education');
        eduList.innerHTML = '';
        if (profile.education && profile.education.length > 0) {
            profile.education.forEach(edu => {
                const div = document.createElement('div');
                div.textContent = edu;
                eduList.appendChild(div);
            });
        } else {
            eduList.innerHTML = '<span>No education information</span>';
        }

        // Past roles
        const rolesList = document.getElementById('cv-roles');
        rolesList.innerHTML = '';
        if (profile.past_roles && profile.past_roles.length > 0) {
            profile.past_roles.forEach(role => {
                const div = document.createElement('div');
                let text = `${role.title} at ${role.company}`;
                if (role.start_date) {
                    text += ` (${role.start_date} - ${role.end_date || 'present'})`;
                }
                div.textContent = text;
                rolesList.appendChild(div);
            });
        } else {
            rolesList.innerHTML = '<span>No past roles information</span>';
        }

    } catch (error) {
        console.error('Error loading CV profile:', error);
        container.style.display = 'block';
        error.style.display = 'block';
        error.textContent = 'Error loading CV profile: ' + error.message;
    }
}

// The coach's proposal, held until the user confirms it.
let _coachProposal = null;

/**
 * Wire up the job-coach panel.
 */
function initCoachUI() {
    const startBtn = document.getElementById('coach-start-btn');
    if (startBtn) {
        startBtn.addEventListener('click', startCoach);
    }
    const proposeBtn = document.getElementById('coach-propose-btn');
    if (proposeBtn) {
        proposeBtn.addEventListener('click', requestCoachProposal);
    }
    const cancelBtn = document.getElementById('coach-cancel-btn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            document.getElementById('coach-questions').classList.add('hidden');
            document.getElementById('coach-result').classList.add('hidden');
        });
    }
    const applyBtn = document.getElementById('coach-apply-btn');
    if (applyBtn) {
        applyBtn.addEventListener('click', applyCoachTracks);
    }
}

/**
 * Fetch the coach questions and render them as a short form.
 */
async function startCoach() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }
    const panel = document.getElementById('coach-questions');
    const list = document.getElementById('coach-question-list');
    list.innerHTML = '<p class="loading">Loading questions…</p>';
    panel.classList.remove('hidden');

    try {
        const response = await fetchWithAuth(
            `${API_BASE}/coach/questions?user=${encodeURIComponent(currentUser)}`
        );
        const data = await response.json();
        if (!response.ok) {
            list.innerHTML = `<p style="color:#d32f2f;">${escapeHtml(data.detail || 'Failed to load')}</p>`;
            return;
        }
        list.innerHTML = data.questions.map(renderCoachQuestion).join('');
    } catch (err) {
        list.innerHTML = `<p style="color:#d32f2f;">Error: ${escapeHtml(err.message)}</p>`;
    }
}

/**
 * Render one coach question, with its suggested answers as quick-fill chips.
 */
function renderCoachQuestion(q) {
    const options = (q.options || [])
        .map(
            (o) =>
                `<button type="button" class="btn btn-secondary coach-option" data-for="coach-a-${escapeHtml(q.id)}" data-value="${escapeHtml(o)}">${escapeHtml(o)}</button>`
        )
        .join(' ');
    return `
        <div class="form-group coach-question" data-qid="${escapeHtml(q.id)}">
            <label for="coach-a-${escapeHtml(q.id)}">${escapeHtml(q.question)}</label>
            ${q.hint ? `<p class="info-text">${escapeHtml(q.hint)}</p>` : ''}
            ${options ? `<div class="coach-options">${options}</div>` : ''}
            <textarea id="coach-a-${escapeHtml(q.id)}" rows="2" placeholder="Your answer — or leave blank if you're not sure"></textarea>
        </div>`;
}

/**
 * Append a clicked suggestion into that question's answer box.
 */
function handleCoachOptionClick(event) {
    const btn = event.target.closest('.coach-option');
    if (!btn) {
        return;
    }
    const box = document.getElementById(btn.dataset.for);
    if (!box) {
        return;
    }
    const value = btn.dataset.value;
    box.value = box.value.trim() ? `${box.value.trim()}, ${value}` : value;
}

/**
 * Send the answers to the coach and render its proposed directions.
 */
async function requestCoachProposal() {
    const answers = Array.from(document.querySelectorAll('.coach-question')).map(
        (el) => ({
            id: el.dataset.qid,
            answer: el.querySelector('textarea').value,
        })
    );

    const box = document.getElementById('coach-result');
    const loading = document.getElementById('coach-loading');
    const errorEl = document.getElementById('coach-error');
    const out = document.getElementById('coach-proposal');
    const applyBtn = document.getElementById('coach-apply-btn');

    box.classList.remove('hidden');
    loading.classList.remove('hidden');
    errorEl.classList.add('hidden');
    applyBtn.classList.add('hidden');
    out.innerHTML = '';
    _coachProposal = null;

    try {
        const response = await fetchWithAuth(`${API_BASE}/coach/propose`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser, answers }),
        });
        const data = await response.json();
        loading.classList.add('hidden');
        if (!response.ok) {
            errorEl.classList.remove('hidden');
            errorEl.textContent = data.detail || 'The coach could not respond';
            return;
        }
        _coachProposal = data;
        out.innerHTML = renderCoachProposal(data);
        if ((data.tracks || []).length) {
            applyBtn.classList.remove('hidden');
        }
    } catch (err) {
        loading.classList.add('hidden');
        errorEl.classList.remove('hidden');
        errorEl.textContent = 'Error: ' + err.message;
    }
}

/**
 * Render the coach's proposal as reviewable cards.
 */
function renderCoachProposal(data) {
    let html = data.summary ? `<p>${escapeHtml(data.summary)}</p>` : '';
    const tracks = data.tracks || [];
    if (!tracks.length) {
        return html + '<p>The coach could not suggest directions from those answers.</p>';
    }
    html += tracks.map(renderTrackCard).join('');
    if (data.negative_description) {
        html += `<p class="review-confidence">Will avoid: ${escapeHtml(data.negative_description)}</p>`;
    }
    if (data.follow_up) {
        html += `<p class="review-confidence">Worth thinking about: ${escapeHtml(data.follow_up)}</p>`;
    }
    return html;
}

/**
 * Render one career track as a card.
 */
function renderTrackCard(t) {
    const badge =
        t.mode === 'blend'
            ? '<span class="badge badge-blend">blend — folded into the others</span>'
            : '<span class="badge badge-open">standalone search</span>';
    const kw = [...(t.keywords_dutch || []), ...(t.keywords_english || [])]
        .slice(0, 6)
        .map((k) => escapeHtml(k))
        .join(', ');
    return `
        <div class="track-card">
            <p><strong>${escapeHtml(t.name)}</strong> ${badge}</p>
            <p>${escapeHtml(t.description || '')}</p>
            ${kw ? `<p class="review-confidence">Keywords: ${kw}</p>` : ''}
        </div>`;
}

/**
 * Save the proposed directions as the user's career tracks.
 */
async function applyCoachTracks() {
    if (!_coachProposal) {
        return;
    }
    try {
        const response = await fetchWithAuth(`${API_BASE}/coach/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user: currentUser,
                tracks: _coachProposal.tracks,
                negative_description: _coachProposal.negative_description,
            }),
        });
        const data = await response.json();
        if (!response.ok) {
            alert(`Error: ${data.detail || 'Failed to save'}`);
            return;
        }
        alert(`Saved ${data.saved} search directions.`);
        document.getElementById('coach-apply-btn').classList.add('hidden');
        document.getElementById('coach-questions').classList.add('hidden');
        _coachProposal = null;
        await loadTracks();
    } catch (err) {
        alert('Error saving directions: ' + err.message);
    }
}

/**
 * Load and display the user's configured search directions.
 */
async function loadTracks() {
    if (!currentUser) {
        return;
    }
    const container = document.getElementById('tracks-list');
    if (!container) {
        return;
    }
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/config?user=${encodeURIComponent(currentUser)}`
        );
        if (!response.ok) {
            return;
        }
        const config = await response.json();
        const tracks = config.career_tracks || [];
        if (!tracks.length) {
            container.innerHTML =
                '<p class="info-text">No separate directions configured — your profile description is used as a single search.</p>';
            return;
        }
        container.innerHTML = tracks.map(renderTrackCard).join('');
    } catch (err) {
        console.error('Error loading tracks:', err);
    }
}

/**
 * Wire up the LinkedIn import + person-search enrichment panel.
 */
function initEnrichmentUI() {
    const methodSelect = document.getElementById('linkedin-method');
    if (methodSelect) {
        methodSelect.addEventListener('change', () => {
            showLinkedInMethodGroup(methodSelect.value);
        });
    }

    const previewBtn = document.getElementById('linkedin-preview-btn');
    if (previewBtn) {
        previewBtn.addEventListener('click', previewLinkedInImport);
    }

    const searchBtn = document.getElementById('person-search-btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', previewPersonSearch);
    }

    const applyBtn = document.getElementById('enrichment-apply-btn');
    if (applyBtn) {
        applyBtn.addEventListener('click', applyEnrichment);
    }
}

/**
 * Show only the input group matching the selected LinkedIn import method.
 */
function showLinkedInMethodGroup(method) {
    const groups = {
        pdf: document.getElementById('linkedin-pdf-group'),
        paste: document.getElementById('linkedin-paste-group'),
        file: document.getElementById('linkedin-file-group'),
        url: document.getElementById('linkedin-url-group'),
    };
    Object.entries(groups).forEach(([key, el]) => {
        if (el) {
            el.classList.toggle('hidden', key !== method);
        }
    });
}

// Holds the last previewed request so "Apply" can re-send it with apply=true.
let _pendingEnrichment = null;

/**
 * Preview a LinkedIn import (nothing is persisted until "Apply" is clicked).
 */
async function previewLinkedInImport() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const method = document.getElementById('linkedin-method').value;
    if (method === 'pdf') {
        const pdfPath = document.getElementById('linkedin-pdf-path').value.trim();
        if (!pdfPath) {
            alert('Give the path to your LinkedIn profile PDF first');
            return;
        }
        await runEnrichmentPreview('/profile/import-linkedin-pdf', {
            user: currentUser,
            pdf_path: pdfPath,
            apply: false,
        });
        return;
    }

    const body = { user: currentUser, method, apply: false };
    if (method === 'paste') {
        body.text = document.getElementById('linkedin-paste-text').value;
    } else if (method === 'file') {
        body.file_path = document.getElementById('linkedin-file-path').value;
    } else if (method === 'url') {
        body.profile_url = document.getElementById('linkedin-url').value;
        body.allow_fetch = document.getElementById('linkedin-allow-fetch').checked;
    }

    await runEnrichmentPreview('/profile/import-linkedin', body);
}

/**
 * Preview a public web search for the current user's name.
 */
async function previewPersonSearch() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }
    const context = document.getElementById('person-search-context').value;
    const body = { user: currentUser, apply: false };
    if (context) {
        body.known_context = context;
    }
    await runEnrichmentPreview('/profile/search-person', body);
}

/**
 * POST an enrichment request and render the resulting diff.
 */
async function runEnrichmentPreview(path, body) {
    const container = document.getElementById('enrichment-result');
    const loading = document.getElementById('enrichment-loading');
    const errorEl = document.getElementById('enrichment-error');
    const diffEl = document.getElementById('enrichment-diff');
    const applyBtn = document.getElementById('enrichment-apply-btn');

    container.classList.remove('hidden');
    loading.classList.remove('hidden');
    errorEl.classList.add('hidden');
    diffEl.innerHTML = '';
    applyBtn.classList.add('hidden');
    _pendingEnrichment = null;

    try {
        const response = await fetchWithAuth(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await response.json();
        loading.classList.add('hidden');

        if (!response.ok) {
            errorEl.classList.remove('hidden');
            errorEl.textContent = data.detail || 'Request failed';
            return;
        }

        renderEnrichmentDiff(data, path, body);
    } catch (err) {
        loading.classList.add('hidden');
        errorEl.classList.remove('hidden');
        errorEl.textContent = 'Error: ' + err.message;
    }
}

/**
 * Render a proposed (or applied) diff, plus any person-search metadata.
 */
function renderEnrichmentDiff(data, path, requestBody) {
    const diffEl = document.getElementById('enrichment-diff');
    const applyBtn = document.getElementById('enrichment-apply-btn');
    const diff = data.diff || { added_skills: [], added_education: [], added_roles: [] };

    let html = '';
    if (data.result) {
        const r = data.result;
        html += `<p><strong>Confidence:</strong> ${escapeHtml(r.confidence)}</p>`;
        if (r.summary) {
            html += `<p>${escapeHtml(r.summary)}</p>`;
        }
        if (r.notes) {
            html += `<p class="review-confidence">Note: ${escapeHtml(r.notes)}</p>`;
        }
    }
    if (data.warning) {
        html += `<p>${escapeHtml(data.warning)}</p>`;
    }
    if (data.conflict) {
        const c = data.conflict;
        const was = c.cv_current_company
            ? `${escapeHtml(c.cv_current_title || 'a role')} at ${escapeHtml(c.cv_current_company)}`
            : 'nothing';
        html +=
            `<p class="review-confidence">Your CV still lists ${was} as your current job, ` +
            `but LinkedIn shows <strong>${escapeHtml(c.linkedin_current_company)}</strong>. ` +
            `Applying these changes will correct your work history.</p>`;
    }

    const updated = diff.updated_roles || [];
    const hasChanges =
        diff.added_skills.length ||
        diff.added_education.length ||
        diff.added_roles.length ||
        updated.length;

    if (!hasChanges) {
        html += '<p>No new additions found — everything is already in your CV.</p>';
        diffEl.innerHTML = html;
        return;
    }

    if (updated.length) {
        html += `<p><strong>${data.applied ? 'Corrected' : 'Will correct'} existing roles:</strong></p><ul>`;
        updated.forEach((r) => {
            const until = r.end_date ? `until ${escapeHtml(r.end_date)}` : 'now current';
            html += `<li>~ ${escapeHtml(r.title)} at ${escapeHtml(r.company)} — ${until}</li>`;
        });
        html += '</ul>';
    }

    html += `<p><strong>${data.applied ? 'Applied' : 'Proposed'} additions:</strong></p><ul>`;
    diff.added_skills.forEach((s) => {
        html += `<li>+ Skill: ${escapeHtml(s)}</li>`;
    });
    diff.added_education.forEach((e) => {
        html += `<li>+ Education: ${escapeHtml(e)}</li>`;
    });
    diff.added_roles.forEach((r) => {
        html += `<li>+ Role: ${escapeHtml(r.title)} at ${escapeHtml(r.company)}</li>`;
    });
    html += '</ul>';
    diffEl.innerHTML = html;

    if (data.applied) {
        applyBtn.classList.add('hidden');
        _pendingEnrichment = null;
    } else {
        _pendingEnrichment = { path, requestBody };
        applyBtn.classList.remove('hidden');
    }
}

/**
 * Re-send the last previewed request with apply=true to persist it.
 */
async function applyEnrichment() {
    if (!_pendingEnrichment) {
        return;
    }
    const { path, requestBody } = _pendingEnrichment;
    await runEnrichmentPreview(path, { ...requestBody, apply: true });
    await loadCVProfile();
}

async function loadProfileData() {
    if (!currentUser) {
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/config?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            console.error('Failed to load config');
            return;
        }

        const config = await response.json();
        document.getElementById('profile-desc').value = config.profile_description || '';
        document.getElementById('negative-desc').value = config.negative_description || '';
        document.getElementById('cv-path').value = config.cv_path || '';
        document.getElementById('cv-notes').value = config.cv_notes || '';
        document.getElementById('linkedin-profile-url-setting').value = config.linkedin_profile_url || '';
        document.getElementById('linkedin-allow-fetch-setting').checked = !!config.linkedin_import_allow_url_fetch;
        document.getElementById('linkedin-url').value = config.linkedin_profile_url || '';
        document.getElementById('salary-min').value = config.min_salary ?? '';
        document.getElementById('salary-max').value = config.max_salary ?? '';
        document.getElementById('max-distance-km').value = config.max_distance_km ?? '';
        document.getElementById('travel-car').value = config.max_travel_car ?? '';
        document.getElementById('travel-pt').value = config.max_travel_pt ?? '';
        document.getElementById('travel-bike').value = config.max_travel_bike ?? '';
        document.getElementById('vacation-days').value = config.min_vacation_days ?? '';
        document.getElementById('jobspy-keyword-limit').value = config.jobspy_keyword_limit ?? 5;
        document.getElementById('nvb-keyword-limit').value = config.nvb_keyword_limit ?? 3;

        // Load jobspy sites
        const jobspySites = config.jobspy_sites || ['indeed', 'linkedin'];
        document.querySelectorAll('input[name="jobspy-sites"]').forEach((checkbox) => {
            checkbox.checked = jobspySites.includes(checkbox.value);
        });
    } catch (error) {
        console.error('Error loading profile data:', error);
    }
}

/**
 * Save profile data
 */
async function saveProfile() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    // Collect checked jobspy sites
    const jobspySites = Array.from(document.querySelectorAll('input[name="jobspy-sites"]:checked')).map(
        (checkbox) => checkbox.value
    );

    const values = {
        profile_description: document.getElementById('profile-desc').value,
        negative_description: document.getElementById('negative-desc').value,
        cv_path: document.getElementById('cv-path').value,
        cv_notes: document.getElementById('cv-notes').value,
        linkedin_profile_url: document.getElementById('linkedin-profile-url-setting').value,
        linkedin_import_allow_url_fetch: document.getElementById('linkedin-allow-fetch-setting').checked,
        min_salary: document.getElementById('salary-min').value,
        max_salary: document.getElementById('salary-max').value,
        max_distance_km: document.getElementById('max-distance-km').value,
        max_travel_car: document.getElementById('travel-car').value,
        max_travel_pt: document.getElementById('travel-pt').value,
        max_travel_bike: document.getElementById('travel-bike').value,
        min_vacation_days: document.getElementById('vacation-days').value,
        jobspy_keyword_limit: document.getElementById('jobspy-keyword-limit').value,
        nvb_keyword_limit: document.getElementById('nvb-keyword-limit').value,
        jobspy_sites: jobspySites.length > 0 ? jobspySites : ['indeed', 'linkedin'],
    };
    for (const key of Object.keys(values)) {
        if (values[key] === '') {
            delete values[key];
        }
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser, values }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to save'}`);
            return;
        }

        const result = await response.json();
        if (result.errors) {
            alert(`Errors: ${JSON.stringify(result.errors)}`);
        } else {
            alert('Profile saved successfully');
        }
    } catch (error) {
        console.error('Error saving profile:', error);
        alert('Error saving profile');
    }
}

/**
 * Load and display custom sites
 */
async function loadSitesData() {
    if (!currentUser) {
        return;
    }

    const container = document.getElementById('sites-list');
    container.innerHTML = '<h3>Current Sites</h3>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/sites?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            container.innerHTML += '<p class="empty">No sites found</p>';
            return;
        }

        const sites = await response.json();
        if (sites.length === 0) {
            container.innerHTML += '<p class="empty">No custom sites added</p>';
            return;
        }

        const list = document.createElement('ul');
        sites.forEach((site) => {
            const li = document.createElement('li');
            li.style.marginBottom = '15px';
            const jsLabel = site.render_js ? ' <span style="background-color: #e8f4f8; padding: 2px 6px; border-radius: 3px; font-size: 0.85em;">[JS Rendered]</span>' : '';
            li.innerHTML = `
                <strong>${escapeHtml(site.name)}</strong>: ${escapeHtml(site.url)}${jsLabel}
                <button class="btn btn-danger" style="margin-left: 10px; padding: 5px 10px; font-size: 0.9em;"
                    onclick="removeSite('${escapeHtml(site.url)}')">Remove</button>
            `;
            list.appendChild(li);
        });
        container.appendChild(list);
    } catch (error) {
        console.error('Error loading sites:', error);
        container.innerHTML += '<p class="empty">Error loading sites</p>';
    }
}

/**
 * Add a new site
 */
async function addSite() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const url = document.getElementById('site-url').value.trim();
    const name = document.getElementById('site-name').value.trim();
    const renderJs = document.getElementById('site-render-js').checked;

    if (!url) {
        alert('URL is required');
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/sites`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser, url, name, render_js: renderJs }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to add site'}`);
            return;
        }

        document.getElementById('site-url').value = '';
        document.getElementById('site-name').value = '';
        document.getElementById('site-render-js').checked = false;
        alert('Site added successfully');
        await loadSitesData();
    } catch (error) {
        console.error('Error adding site:', error);
        alert('Error adding site');
    }
}

/**
 * Remove a site
 *
 * @param {string} identifier - Site URL or name to remove
 */
async function removeSite(identifier) {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    if (!confirm(`Remove site '${identifier}'?`)) {
        return;
    }

    try {
        const response = await fetchWithAuth(
            `${API_BASE}/sites?user=${encodeURIComponent(currentUser)}&identifier=${encodeURIComponent(identifier)}`,
            { method: 'DELETE' }
        );

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to remove site'}`);
            return;
        }

        alert('Site removed successfully');
        await loadSitesData();
    } catch (error) {
        console.error('Error removing site:', error);
        alert('Error removing site');
    }
}

/**
 * Load LLM settings
 */
async function loadLLMSettings() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/config`);
        if (!response.ok) {
            return;
        }

        const config = await response.json();
        document.getElementById('llm-provider').value = config.llm_provider || 'local';
        document.getElementById('eval-provider').value = config.evaluation_provider || '';
        document.getElementById('screen-provider').value = config.screening_provider || '';
        document.getElementById('quick-eval-provider').value = config.quick_eval_provider || '';
        document.getElementById('keywords-provider').value = config.keywords_provider || '';
        document.getElementById('local-base-url').value = config.local_base_url || 'http://localhost:11434/v1';

        // Populate local model dropdown with saved value
        const localModel = config.local_model || 'llama3.1';
        const localModelSelect = document.getElementById('local-model');
        localModelSelect.innerHTML = `<option value="${localModel}">${localModel}</option>`;
        localModelSelect.value = localModel;

        // Populate screening model dropdown with saved value and empty option
        const screeningModel = config.local_screening_model || '';
        const screeningModelSelect = document.getElementById('local-screen-model');
        screeningModelSelect.innerHTML = '<option value="">(use evaluation model)</option>';
        if (screeningModel) {
            const option = document.createElement('option');
            option.value = screeningModel;
            option.textContent = screeningModel;
            screeningModelSelect.appendChild(option);
            screeningModelSelect.value = screeningModel;
        }
    } catch (error) {
        console.error('Error loading LLM settings:', error);
    }
}

/**
 * Save LLM settings (global configuration)
 */
async function saveLLMSettings() {
    const values = {
        llm_provider: document.getElementById('llm-provider').value,
        evaluation_provider: document.getElementById('eval-provider').value || null,
        screening_provider: document.getElementById('screen-provider').value || null,
        quick_eval_provider: document.getElementById('quick-eval-provider').value || null,
        keywords_provider: document.getElementById('keywords-provider').value || null,
        local_base_url: document.getElementById('local-base-url').value,
        local_model: document.getElementById('local-model').value,
        local_screening_model: document.getElementById('local-screen-model').value || null,
    };

    try {
        const response = await fetchWithAuth(`${API_BASE}/global-init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(values),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to save'}`);
            return;
        }

        alert('LLM settings saved successfully');
    } catch (error) {
        console.error('Error saving LLM settings:', error);
        alert('Error saving LLM settings');
    }
}

/**
 * Detect available models from local LLM server.
 */
async function detectLocalModels() {
    const baseUrl = document.getElementById('local-base-url').value.trim();
    const messageDiv = document.getElementById('detect-models-message');

    if (!baseUrl) {
        messageDiv.style.color = 'red';
        messageDiv.textContent = 'Please enter a base URL first';
        return;
    }

    messageDiv.style.color = 'blue';
    messageDiv.textContent = 'Detecting models...';

    try {
        const response = await fetchWithAuth(`${API_BASE}/llm/detect-models`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url: baseUrl }),
        });

        const result = await response.json();

        if (result.ok && result.models && result.models.length > 0) {
            messageDiv.style.color = 'green';
            messageDiv.textContent = `Found ${result.models.length} model(s)`;

            // Store currently selected values before updating
            const localModelSelect = document.getElementById('local-model');
            const screeningModelSelect = document.getElementById('local-screen-model');
            const currentLocalModel = localModelSelect.value;
            const currentScreeningModel = screeningModelSelect.value;

            // Populate local model dropdown
            localModelSelect.innerHTML = '';
            const detectedSet = new Set(result.models);

            // Add detected models
            result.models.forEach((model) => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                localModelSelect.appendChild(option);
            });

            // If currently selected model is not in detected list, add it as extra option
            if (currentLocalModel && !detectedSet.has(currentLocalModel)) {
                const option = document.createElement('option');
                option.value = currentLocalModel;
                option.textContent = `${currentLocalModel} (not detected)`;
                localModelSelect.appendChild(option);
            }

            // Restore selection for local model
            localModelSelect.value = currentLocalModel;

            // Populate screening model dropdown
            screeningModelSelect.innerHTML = '<option value="">(use evaluation model)</option>';

            // Add detected models
            result.models.forEach((model) => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                screeningModelSelect.appendChild(option);
            });

            // If currently selected screening model is not in detected list and not empty, add it as extra option
            if (currentScreeningModel && !detectedSet.has(currentScreeningModel)) {
                const option = document.createElement('option');
                option.value = currentScreeningModel;
                option.textContent = `${currentScreeningModel} (not detected)`;
                screeningModelSelect.appendChild(option);
            }

            // Restore selection for screening model
            screeningModelSelect.value = currentScreeningModel;
        } else if (result.ok) {
            messageDiv.style.color = 'orange';
            messageDiv.textContent = 'No models found on server';
        } else {
            messageDiv.style.color = 'red';
            messageDiv.textContent = `Detection failed: ${result.message}`;
        }
    } catch (error) {
        console.error('Error detecting models:', error);
        messageDiv.style.color = 'red';
        messageDiv.textContent = 'Error detecting models';
    }
}

/**
 * Test LLM connection
 */
async function testConnection() {
    const provider = document.getElementById('test-provider').value;
    const model = document.getElementById('test-model').value;
    const baseUrl = document.getElementById('test-base-url').value;
    const apiKey = document.getElementById('test-api-key').value;
    const resultDiv = document.getElementById('test-result');

    if (!model && provider !== 'claude_cli') {
        resultDiv.innerHTML = '<p style="color: red;">Model is required</p>';
        return;
    }

    resultDiv.innerHTML = '<p style="color: blue;">Testing...</p>';

    try {
        const body = { provider, model };
        if (baseUrl) body.base_url = baseUrl;
        if (apiKey) body.api_key = apiKey;
        const response = await fetchWithAuth(`${API_BASE}/llm/test-connection`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const result = await response.json();
        if (result.ok) {
            resultDiv.innerHTML = '<p style="color: green;">Connection successful!</p>';
        } else {
            resultDiv.innerHTML = `<p style="color: red;">Connection failed: ${escapeHtml(result.message)}</p>`;
        }
    } catch (error) {
        console.error('Error testing connection:', error);
        resultDiv.innerHTML = '<p style="color: red;">Error testing connection</p>';
    }
}

/**
 * Update secrets
 */
async function updateSecrets() {
    const body = {};
    const fields = ['zai-key', 'local-key', 'ors-key', 'ns-key'];
    const fieldMap = {
        'zai-key': 'zai_api_key',
        'local-key': 'local_api_key',
        'ors-key': 'ors_api_key',
        'ns-key': 'ns_api_key',
    };

    fields.forEach((fieldId) => {
        const val = document.getElementById(fieldId).value.trim();
        if (val) {
            body[fieldMap[fieldId]] = val;
        }
    });

    if (Object.keys(body).length === 0) {
        alert('No secrets to update');
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/secrets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to update'}`);
            return;
        }

        alert('Secrets updated successfully');
        // Clear the form
        fields.forEach((fieldId) => {
            document.getElementById(fieldId).value = '';
        });
    } catch (error) {
        console.error('Error updating secrets:', error);
        alert('Error updating secrets');
    }
}

/**
 * Load and display keywords
 */
async function loadKeywords() {
    if (!currentUser) {
        return;
    }

    const container = document.getElementById('keywords-content');
    if (!container) {
        return;
    }

    container.innerHTML = '<p class="loading">Loading keywords...</p>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/keywords?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            container.innerHTML = '<p class="empty">Failed to load keywords</p>';
            return;
        }

        const data = await response.json();
        const html = `
            <div class="keywords-list">
                <div class="keyword-group">
                    <h4>Include (Dutch)</h4>
                    <div class="keywords">${(data.dutch || []).map(k => `<span class="keyword-tag">${escapeHtml(k)}</span>`).join('')}</div>
                </div>
                <div class="keyword-group">
                    <h4>Include (English)</h4>
                    <div class="keywords">${(data.english || []).map(k => `<span class="keyword-tag">${escapeHtml(k)}</span>`).join('')}</div>
                </div>
                <div class="keyword-group">
                    <h4>Title Include</h4>
                    <div class="keywords">${(data.title_include || []).map(k => `<span class="keyword-tag">${escapeHtml(k)}</span>`).join('')}</div>
                </div>
                <div class="keyword-group">
                    <h4>Title Exclude</h4>
                    <div class="keywords">${(data.title_exclude || []).map(k => `<span class="keyword-tag exclude">${escapeHtml(k)}</span>`).join('')}</div>
                </div>
            </div>
        `;
        container.innerHTML = html;
    } catch (error) {
        console.error('Error loading keywords:', error);
        container.innerHTML = '<p class="error">Error loading keywords</p>';
    }
}

/**
 * Refresh (regenerate) keywords
 */
async function refreshKeywords() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const btn = document.getElementById('refresh-keywords-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Refreshing...';

    try {
        const response = await fetchWithAuth(`${API_BASE}/keywords/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to refresh keywords'}`);
            return;
        }

        const result = await response.json();
        alert(result.message);
        await loadKeywords();
    } catch (error) {
        console.error('Error refreshing keywords:', error);
        alert('Error refreshing keywords: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

/**
 * Load and display schedule status
 */
/**
 * Load schedule data for the current user
 */
async function loadScheduleStatus() {
    if (!currentUser) {
        document.getElementById('schedule-status').innerHTML =
            '<p style="color: #999;">Please select a user to view their schedule</p>';
        return;
    }

    try {
        // Load schedule status
        const response = await fetchWithAuth(
            `${API_BASE}/schedule/status?user=${encodeURIComponent(currentUser)}`
        );
        if (response.ok) {
            const data = await response.json();
            const statusDiv = document.getElementById('schedule-status');
            statusDiv.innerHTML =
                `<p><strong>Current Status:</strong> ${escapeHtml(data.status)}</p>`;
        }

        // Load schedule config from user's config
        const configResponse = await fetchWithAuth(
            `${API_BASE}/config?user=${encodeURIComponent(currentUser)}`
        );
        if (configResponse.ok) {
            const config = await configResponse.json();
            document.getElementById('schedule-hour').value = config.schedule_hour || 8;
            document.getElementById('schedule-minute').value =
                config.schedule_minute || 0;
            document.getElementById('schedule-days').value = config.schedule_days || '1-5';
            document.getElementById('schedule-paused').checked =
                config.schedule_paused || false;
        }
    } catch (error) {
        console.error('Error loading schedule status:', error);
    }
}

/**
 * Install or update a schedule for the current user
 */
async function installSchedule() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const hour = parseInt(document.getElementById('schedule-hour').value);
    const minute = parseInt(document.getElementById('schedule-minute').value);
    const days = document.getElementById('schedule-days').value;
    const paused = document.getElementById('schedule-paused').checked;

    if (
        isNaN(hour) ||
        isNaN(minute) ||
        hour < 0 ||
        hour >= 24 ||
        minute < 0 ||
        minute >= 60
    ) {
        alert('Invalid hour or minute');
        return;
    }

    try {
        // First, save the schedule fields to config
        const configResponse = await fetchWithAuth(`${API_BASE}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user: currentUser,
                values: {
                    schedule_hour: hour,
                    schedule_minute: minute,
                    schedule_days: days,
                    schedule_paused: paused,
                },
            }),
        });

        if (!configResponse.ok) {
            const error = await configResponse.json();
            alert(`Error: ${error.detail || 'Failed to save schedule config'}`);
            return;
        }

        // Then install the cron job
        const response = await fetchWithAuth(`${API_BASE}/schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hour, minute, days, user: currentUser }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to install schedule'}`);
            return;
        }

        alert('Schedule saved and installed successfully');
        await loadScheduleStatus();
    } catch (error) {
        console.error('Error installing schedule:', error);
        alert('Error installing schedule');
    }
}

/**
 * Remove the schedule for the current user
 */
async function removeSchedule() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    if (!confirm('Remove the scheduled job?')) {
        return;
    }

    try {
        const response = await fetchWithAuth(
            `${API_BASE}/schedule?user=${encodeURIComponent(currentUser)}`,
            {
                method: 'DELETE',
            }
        );

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to remove schedule'}`);
            return;
        }

        alert('Schedule removed successfully');
        await loadScheduleStatus();
    } catch (error) {
        console.error('Error removing schedule:', error);
        alert('Error removing schedule');
    }
}

/**
 * Create a new user
 *
 * @param {string} name - New user name
 */
async function createUser(name) {
    try {
        const response = await fetchWithAuth(`${API_BASE}/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to create user'}`);
            return;
        }

        alert(`User '${name}' created successfully`);
        await loadUsers();
    } catch (error) {
        console.error('Error creating user:', error);
        alert('Error creating user');
    }
}

/**
 * Check if global setup is needed and show/hide the setup section
 */
async function checkGlobalSetup() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/config`);
        if (!response.ok) {
            return;
        }
        const config = await response.json();

        // global_initialized reflects the raw on-disk global config, not a
        // per-user field -- Config fields always carry defaults, so this is
        // the only reliable way to tell "never initialized" from "using
        // defaults".
        const setupSection = document.getElementById('global-setup-section');
        if (!config.global_initialized && setupSection) {
            setupSection.classList.remove('hidden');
            // Hide user section and tabs when setup is needed
            const userSection = document.getElementById('user-section');
            const tabs = document.querySelector('.tabs');
            if (userSection) userSection.classList.add('hidden');
            if (tabs) tabs.classList.add('hidden');
        } else if (setupSection) {
            setupSection.classList.add('hidden');
        }
    } catch (error) {
        console.error('Error checking global setup:', error);
    }
}

/**
 * Initialize global configuration
 */
async function initializeGlobalSetup() {
    const provider = document.getElementById('global-llm-provider').value;

    try {
        const response = await fetchWithAuth(`${API_BASE}/global-init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                llm_provider: provider,
            }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to initialize'}`);
            return;
        }

        alert('Global configuration initialized successfully');
        // Hide setup section and show user section
        const setupSection = document.getElementById('global-setup-section');
        const userSection = document.getElementById('user-section');
        const tabs = document.querySelector('.tabs');
        if (setupSection) setupSection.classList.add('hidden');
        if (userSection) userSection.classList.remove('hidden');
        if (tabs) tabs.classList.remove('hidden');
        // Refresh users list
        await loadUsers();
    } catch (error) {
        console.error('Error initializing global setup:', error);
        alert('Error initializing global setup: ' + error.message);
    }
}

/**
 * Escape HTML special characters to prevent XSS
 *
 * @param {string} text - Text to escape
 * @returns {string} Escaped text safe for HTML
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    };
    return String(text).replace(/[&<>"']/g, (char) => map[char]);
}

/**
 * Load and display run history analytics
 */
async function loadAnalytics() {
    if (!currentUser) {
        return;
    }

    try {
        const response = await fetchWithAuth(
            `${API_BASE}/runs/history?user=${encodeURIComponent(currentUser)}&limit=30`
        );
        if (!response.ok) {
            console.error('Failed to load analytics');
            return;
        }

        const history = await response.json();
        displayAnalyticsTable(history);
        displayAnalyticsChart(history);
    } catch (error) {
        console.error('Error loading analytics:', error);
    }
}

/**
 * Display analytics data in a table
 *
 * @param {Array} history - Array of run history entries
 */
function displayAnalyticsTable(history) {
    const container = document.getElementById('analytics-container');
    if (!history || history.length === 0) {
        container.innerHTML = '<p>No run history available.</p>';
        return;
    }

    let tableHtml = `
        <table class="analytics-table">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Scraped</th>
                    <th>Matched</th>
                    <th>Rejected</th>
                    <th>Notified</th>
                    <th>Errors</th>
                    <th>Duration</th>
                </tr>
            </thead>
            <tbody>
    `;

    history.forEach((entry) => {
        const date = new Date(entry.started_at);
        const dateStr = date.toLocaleString();
        const durationStr = `${entry.duration_seconds.toFixed(1)}s`;

        tableHtml += `
            <tr>
                <td>${escapeHtml(dateStr)}</td>
                <td>${entry.scraped}</td>
                <td>${entry.matched}</td>
                <td>${entry.rejected}</td>
                <td>${entry.notified}</td>
                <td>${entry.errors}</td>
                <td>${durationStr}</td>
            </tr>
        `;
    });

    tableHtml += `
            </tbody>
        </table>
    `;

    container.innerHTML = tableHtml;
}

/**
 * Display a lightweight trend chart using CSS bars
 *
 * @param {Array} history - Array of run history entries
 */
function displayAnalyticsChart(history) {
    if (!history || history.length < 2) {
        const chartDiv = document.getElementById('analytics-chart');
        if (chartDiv) {
            chartDiv.classList.add('hidden');
        }
        return;
    }

    const chartDiv = document.getElementById('analytics-chart');
    chartDiv.classList.remove('hidden');

    // Get last 7 days of data (or fewer if less history available)
    const recentRuns = history.slice(0, Math.min(7, history.length)).reverse();

    // Find max matched count for scaling
    const maxMatched = Math.max(...recentRuns.map((r) => r.matched), 1);

    let barsHtml = '<div class="chart-row-headers"><span></span><span>Matched</span></div>';

    recentRuns.forEach((entry) => {
        const date = new Date(entry.started_at);
        const dateStr = date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
        });
        const percentage = (entry.matched / maxMatched) * 100;

        barsHtml += `
            <div class="chart-row">
                <span class="chart-label">${escapeHtml(dateStr)}</span>
                <div class="chart-bar-container">
                    <div class="chart-bar" style="width: ${percentage}%;" title="${entry.matched} matched">
                        <span class="chart-value">${entry.matched}</span>
                    </div>
                </div>
            </div>
        `;
    });

    document.getElementById('chart-bars').innerHTML = barsHtml;
}

/**
 * Load notification settings for the current user
 */
async function loadNotificationData() {
    if (!currentUser) {
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/config?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            console.error('Failed to load config');
            return;
        }

        const config = await response.json();
        document.getElementById('notification-channel').value = config.notification_channel || 'ntfy';
        document.getElementById('notification-mode').value = config.notification_mode || 'per_job';
        document.getElementById('ntfy-topic').value = config.ntfy_topic || '';
        document.getElementById('ntfy-server').value = config.ntfy_server || '';
        document.getElementById('smtp-to').value = config.smtp_to || '';
        document.getElementById('smtp-host').value = config.smtp_host || '';
        document.getElementById('smtp-port').value = config.smtp_port || 587;
        document.getElementById('smtp-from').value = config.smtp_from || '';
        document.getElementById('slack-webhook-url').value = config.slack_webhook_url || '';
        document.getElementById('discord-webhook-url').value = config.discord_webhook_url || '';

        updateNotificationChannelUI(config.notification_channel || 'ntfy');
    } catch (error) {
        console.error('Error loading notification data:', error);
    }
}

/**
 * Update the notification channel UI to show/hide relevant settings
 */
function updateNotificationChannelUI(channel) {
    const channels = ['ntfy', 'email', 'slack', 'discord'];
    channels.forEach((ch) => {
        const el = document.getElementById(`${ch}-settings`);
        if (el) {
            el.style.display = ch === channel ? 'block' : 'none';
        }
    });
}

/**
 * Save notification settings for the current user
 */
async function saveNotifications() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const channel = document.getElementById('notification-channel').value;
    const mode = document.getElementById('notification-mode').value;
    const values = {
        notification_channel: channel,
        notification_mode: mode,
        ntfy_topic: document.getElementById('ntfy-topic').value || 'job-scout-alerts',
        slack_webhook_url: document.getElementById('slack-webhook-url').value,
        discord_webhook_url: document.getElementById('discord-webhook-url').value,
        smtp_to: document.getElementById('smtp-to').value,
    };

    // Remove empty strings
    for (const key of Object.keys(values)) {
        if (values[key] === '' && key !== 'notification_channel') {
            delete values[key];
        }
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser, values }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Failed to save settings: ${error.detail || 'Unknown error'}`);
            return;
        }

        alert('Notification settings saved successfully');
    } catch (error) {
        alert(`Error saving settings: ${error.message}`);
    }
}

/**
 * Test the current notification channel configuration
 */
async function testNotificationChannel() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

    const channel = document.getElementById('notification-channel').value;
    const body = {
        channel: channel,
    };

    if (channel === 'ntfy') {
        body.ntfy_topic = document.getElementById('ntfy-topic').value;
        body.ntfy_server = document.getElementById('ntfy-server').value;
    } else if (channel === 'email') {
        body.smtp_host = document.getElementById('smtp-host').value;
        body.smtp_port = parseInt(document.getElementById('smtp-port').value, 10);
        body.smtp_from = document.getElementById('smtp-from').value;
        body.smtp_to = document.getElementById('smtp-to').value;
    } else if (channel === 'slack') {
        body.slack_webhook_url = document.getElementById('slack-webhook-url').value;
    } else if (channel === 'discord') {
        body.discord_webhook_url = document.getElementById('discord-webhook-url').value;
    }

    const resultDiv = document.getElementById('test-notif-result');
    resultDiv.innerHTML = '<p class="loading">Testing...</p>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/notification/test-channel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const data = await response.json();
        if (data.ok) {
            resultDiv.innerHTML = `<p style="color: #4caf50;"><strong>✓</strong> ${escapeHtml(data.message)}</p>`;
        } else {
            resultDiv.innerHTML = `<p style="color: #d32f2f;"><strong>✗</strong> ${escapeHtml(data.message)}</p>`;
        }
    } catch (error) {
        resultDiv.innerHTML = `<p style="color: #d32f2f;"><strong>✗</strong> ${escapeHtml(error.message)}</p>`;
    }
}

/**
 * Load and display the approval queue
 */
async function loadApprovalQueue() {
    const container = document.getElementById('approval-queue-container');

    if (!currentUser) {
        container.innerHTML = '<p>Select a user to view their approval queue.</p>';
        return;
    }

    container.innerHTML = '<p class="loading">Loading approval queue...</p>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/approval/queue?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        displayApprovalQueue(data);
    } catch (error) {
        container.innerHTML = `<p style="color: #d32f2f;"><strong>Error:</strong> ${escapeHtml(error.message)}</p>`;
    }
}

/**
 * Display the approval queue in the UI
 */
function displayApprovalQueue(data) {
    const container = document.getElementById('approval-queue-container');

    if (!data.jobs || data.jobs.length === 0) {
        container.innerHTML = '<p>No jobs awaiting approval.</p>';
        return;
    }

    let html = `<p><strong>${data.count}</strong> job(s) awaiting approval</p><div class="jobs-container">`;

    for (const job of data.jobs) {
        const scoreClass = getScoreClass(job.fit_score);
        const fitScore = job.fit_score !== null ? `${job.fit_score}%` : 'N/A';

        html += `
            <div class="job-card approval-card">
                <div class="job-title">${escapeHtml(job.title)}</div>
                <div class="job-company">${escapeHtml(job.company)}</div>
                <div class="job-location">${escapeHtml(job.location || 'N/A')}</div>
                <div class="job-meta">
                    <span class="fit-score ${scoreClass}">Score: ${fitScore}</span>
                    <span class="job-source">${escapeHtml(job.source || 'Unknown')}</span>
                    <span class="job-status">${escapeHtml(job.status)}</span>
                </div>
                ${job.fit_reasoning ? `<div class="job-reasoning"><strong>Reasoning:</strong> ${escapeHtml(job.fit_reasoning)}</div>` : ''}
                <div class="job-url"><a href="${job.url}" target="_blank" rel="noopener noreferrer">View Job</a></div>
                <div class="approval-actions">
                    <input type="text" class="approval-notes" placeholder="Approval notes (optional)" data-job-id="${job.id}">
                    <button class="btn btn-primary" onclick="approveJob(${job.id})">Approve</button>
                </div>
            </div>
        `;
    }

    html += '</div>';
    container.innerHTML = html;
}

/**
 * Approve a job and transition it to APPROVED status
 */
async function approveJob(jobId) {
    const notesInput = document.querySelector(`input[data-job-id="${jobId}"]`);
    const notes = notesInput ? notesInput.value : null;

    try {
        const response = await fetchWithAuth(`${API_BASE}/approval/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                notes: notes,
                user: currentUser || 'web-user',
            }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Failed to approve job: ${error.detail || 'Unknown error'}`);
            return;
        }

        alert('Job approved successfully!');
        loadApprovalQueue();
    } catch (error) {
        alert(`Error approving job: ${error.message}`);
    }
}

/**
 * Update a job's lifecycle status
 *
 * @param {number} jobId - ID of the job to update
 */
async function updateJobStatus(jobId) {
    const statusSelect = document.getElementById(`status-${jobId}`);
    const notesField = document.getElementById(`notes-${jobId}`);

    if (!statusSelect) {
        alert('Could not find status control');
        return;
    }

    const status = statusSelect.value;
    const notes = notesField ? notesField.value : null;

    try {
        const response = await fetchWithAuth(`${API_BASE}/jobs/${jobId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                status: status,
                notes: notes,
                user: currentUser || 'web-user',
            }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(
                `Failed to update job status: ${error.detail || 'Unknown error'}`
            );
            return;
        }

        alert('Job status updated successfully!');
        // Reload the matched jobs to reflect the change
        loadMatchedJobs();
    } catch (error) {
        alert(`Error updating job status: ${error.message}`);
    }
}

// Set up event listeners for approval tab
document.addEventListener('DOMContentLoaded', function() {
    const refreshBtn = document.getElementById('refresh-approval-queue-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadApprovalQueue);
    }
});

// Override switchTab to load approval queue when switching to approvals tab
const originalSwitchTab = window.switchTab;
window.switchTab = function(tab) {
    originalSwitchTab(tab);
    if (tab === 'approvals') {
        loadApprovalQueue();
    }
};

// --- Automatic runs (container scheduler) -----------------------------------

const WEEKDAYS = [
    ['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'],
    ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday'],
];

function initAutoScheduleUI() {
    const form = document.getElementById('auto-schedule-form');
    if (!form) return;
    form.addEventListener('submit', (e) => { e.preventDefault(); saveAutoSchedule(); });
    document.getElementById('add-slot-btn')?.addEventListener('click', () => addSlotRow());
    document.getElementById('test-wake-btn')?.addEventListener('click', testWake);
    loadAutoSchedule();
}

// Slots are stored as one "day:HH:MM" string, but editing raw text is a poor
// interface, so the string is exploded into rows and rebuilt on save.
function renderSlotRows(spec) {
    const container = document.getElementById('slot-rows');
    container.innerHTML = '';
    (spec || '').split(',').map(s => s.trim()).filter(Boolean).forEach(entry => {
        const [day, hh, mm] = entry.split(':');
        addSlotRow(day, `${hh}:${mm}`);
    });
    if (!container.children.length) addSlotRow();
}

function addSlotRow(day = 'tue', time = '17:00') {
    const container = document.getElementById('slot-rows');
    const row = document.createElement('div');
    row.className = 'slot-row';
    const options = WEEKDAYS
        .map(([v, label]) => `<option value="${v}"${v === day ? ' selected' : ''}>${label}</option>`)
        .join('');
    row.innerHTML =
        `<select class="slot-day">${options}</select>` +
        `<input type="time" class="slot-time" value="${time}">` +
        `<button type="button" class="btn btn-secondary slot-remove">Remove</button>`;
    row.querySelector('.slot-remove').addEventListener('click', () => {
        row.remove();
        if (!container.children.length) addSlotRow();
    });
    container.appendChild(row);
}

function collectSlots() {
    return Array.from(document.querySelectorAll('#slot-rows .slot-row'))
        .map(row => {
            const day = row.querySelector('.slot-day').value;
            const time = row.querySelector('.slot-time').value || '00:00';
            return `${day}:${time}`;
        })
        .join(',');
}

async function loadAutoSchedule() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/auto-schedule`);
        if (!response.ok) return;
        const data = await response.json();
        document.getElementById('auto-schedule-enabled').checked = data.enabled;
        document.getElementById('auto-schedule-timezone').value = data.timezone || '';
        document.getElementById('auto-wake-mac').value = data.wake_mac || '';
        document.getElementById('auto-wake-broadcast').value = data.wake_broadcast || '';
        document.getElementById('auto-llm-health-url').value = data.llm_health_url || '';
        document.getElementById('auto-wake-timeout').value = data.wake_timeout_seconds ?? 300;
        renderSlotRows(data.slots);
        renderNextRuns(data);
    } catch (err) {
        console.error('Failed to load schedule', err);
    }
}

function renderNextRuns(data) {
    const box = document.getElementById('auto-schedule-result');
    if (!box) return;
    if (!data.valid) {
        box.innerHTML = `<p class="error-text">${escapeHtml(data.error || 'Invalid schedule')}</p>`;
        return;
    }
    if (!data.enabled) {
        box.innerHTML = '<p class="info-text">Automatic runs are turned off.</p>';
        return;
    }
    const items = (data.next_runs || [])
        .map(iso => `<li>${escapeHtml(formatRunTime(iso))}</li>`)
        .join('');
    box.innerHTML = items
        ? `<p class="info-text">Next runs:</p><ul>${items}</ul>`
        : '<p class="info-text">No upcoming runs.</p>';
}

function formatRunTime(iso) {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
        weekday: 'long', day: 'numeric', month: 'short',
        hour: '2-digit', minute: '2-digit',
    });
}

async function saveAutoSchedule() {
    const box = document.getElementById('auto-schedule-result');
    const payload = {
        enabled: document.getElementById('auto-schedule-enabled').checked,
        slots: collectSlots(),
        timezone: document.getElementById('auto-schedule-timezone').value.trim(),
        wake_mac: document.getElementById('auto-wake-mac').value.trim(),
        wake_broadcast: document.getElementById('auto-wake-broadcast').value.trim(),
        llm_health_url: document.getElementById('auto-llm-health-url').value.trim(),
        wake_timeout_seconds: document.getElementById('auto-wake-timeout').value,
    };
    box.innerHTML = '<p class="info-text">Saving...</p>';
    try {
        const response = await fetchWithAuth(`${API_BASE}/auto-schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
            box.innerHTML = `<p class="error-text">${escapeHtml(data.detail || 'Save failed')}</p>`;
            return;
        }
        renderNextRuns({ ...data, enabled: payload.enabled });
    } catch (err) {
        box.innerHTML = `<p class="error-text">${escapeHtml(err.message)}</p>`;
    }
}

async function testWake() {
    const box = document.getElementById('auto-schedule-result');
    box.innerHTML = '<p class="info-text">Sending magic packet and waiting for the model server...</p>';
    try {
        const response = await fetchWithAuth(`${API_BASE}/auto-schedule/test-wake`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) {
            box.innerHTML = `<p class="error-text">${escapeHtml(data.detail || 'Wake test failed')}</p>`;
            return;
        }
        box.innerHTML = `<p class="${data.reachable ? 'info-text' : 'error-text'}">${escapeHtml(data.message)}</p>`;
    } catch (err) {
        box.innerHTML = `<p class="error-text">${escapeHtml(err.message)}</p>`;
    }
}

// --- ntfy subscription (QR + tap-to-subscribe) ------------------------------

function initNtfyUI() {
    document.getElementById('ntfy-generate-btn')?.addEventListener('click', generateNtfyTopic);
    document.getElementById('ntfy-copy-btn')?.addEventListener('click', copyNtfyLink);
}

async function loadNtfySubscription() {
    const panel = document.getElementById('ntfy-subscribe');
    if (!panel) return;
    // Without a user there is no topic, and an <img> with no src renders as a
    // broken-image box. Keep the whole panel hidden instead.
    if (!currentUser) {
        panel.classList.add('hidden');
        return;
    }
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/ntfy/topic?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) return;
        renderNtfySubscription(await response.json());
    } catch (err) {
        console.error('Failed to load ntfy topic', err);
    }
}

function renderNtfySubscription(data) {
    const panel = document.getElementById('ntfy-subscribe');
    const link = document.getElementById('ntfy-open-link');
    const urlEl = document.getElementById('ntfy-url');
    const warn = document.getElementById('ntfy-warning');
    const img = document.getElementById('ntfy-qr');
    if (!panel) return;

    if (!data.topic) {
        panel.classList.add('hidden');
        return;
    }
    panel.classList.remove('hidden');

    // The app registers for its own scheme; fall back to https for anyone
    // opening this on a desktop without the app installed.
    link.href = data.app_url || data.subscribe_url;
    urlEl.textContent = data.subscribe_url;

    if (data.secure) {
        warn.classList.add('hidden');
        warn.innerHTML = '';
    } else {
        warn.classList.remove('hidden');
        warn.innerHTML =
            '<p class="warning-text">Anyone who guesses this topic name receives your ' +
            'job alerts. Generate a secure one below.</p>';
    }

    // Cache-bust so regenerating the topic never leaves the old code showing.
    img.src = `${API_BASE}/ntfy/qr?user=${encodeURIComponent(currentUser)}&t=${Date.now()}`;
}

async function generateNtfyTopic() {
    if (!currentUser) return;
    if (!confirm(
        'Generate a new topic?\n\nYou will need to re-subscribe on your phone; ' +
        'notifications sent to the old topic will no longer reach you.')) return;
    try {
        const response = await fetchWithAuth(`${API_BASE}/ntfy/topic/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser }),
        });
        if (!response.ok) return;
        const data = await response.json();
        const field = document.getElementById('ntfy-topic');
        if (field) field.value = data.topic;
        renderNtfySubscription(data);
    } catch (err) {
        console.error('Failed to generate topic', err);
    }
}

async function copyNtfyLink() {
    const url = document.getElementById('ntfy-url')?.textContent;
    if (!url) return;
    const btn = document.getElementById('ntfy-copy-btn');
    try {
        await navigator.clipboard.writeText(url);
        const original = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => { btn.textContent = original; }, 1500);
    } catch {
        // Clipboard needs a secure context; the URL is on screen either way.
        btn.textContent = 'Copy failed';
    }
}

// --- Document review (CV + motivational letter) -----------------------------

function initFeedbackUI() {
    document.getElementById('cv-feedback-btn')?.addEventListener('click', reviewCv);
    document.getElementById('letter-feedback-btn')?.addEventListener('click', reviewLetter);
}

async function loadFeedbackJobs() {
    const cvSelect = document.getElementById('cv-feedback-job');
    const letterSelect = document.getElementById('letter-feedback-job');
    if (!cvSelect || !currentUser) return;
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/feedback/jobs?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) return;
        const jobs = await response.json();
        const options = jobs
            .map(j => `<option value="${j.id}">${escapeHtml(j.title)} — ${escapeHtml(j.company)}</option>`)
            .join('');
        cvSelect.innerHTML =
            '<option value="">General review (no specific job)</option>' + options;
        letterSelect.innerHTML =
            '<option value="">-- Choose a vacancy --</option>' + options;
    } catch (err) {
        console.error('Failed to load jobs for review', err);
    }
}

async function reviewCv() {
    const jobId = document.getElementById('cv-feedback-job').value;
    const body = { user: currentUser };
    if (jobId) body.job_id = Number(jobId);
    await runReview('/feedback/cv', body, 'cv-feedback-result', 'cv-feedback-btn');
}

async function reviewLetter() {
    const jobId = document.getElementById('letter-feedback-job').value;
    const text = document.getElementById('letter-feedback-text').value.trim();
    const box = document.getElementById('letter-feedback-result');
    if (!jobId) {
        box.innerHTML = '<p class="error-text">Choose the vacancy this letter is for.</p>';
        return;
    }
    if (!text) {
        box.innerHTML = '<p class="error-text">Paste the letter first.</p>';
        return;
    }
    await runReview('/feedback/cover-letter',
        { user: currentUser, job_id: Number(jobId), text },
        'letter-feedback-result', 'letter-feedback-btn');
}

async function runReview(path, body, resultId, buttonId) {
    const box = document.getElementById(resultId);
    const btn = document.getElementById(buttonId);
    if (!currentUser) {
        box.innerHTML = '<p class="error-text">Select a user first.</p>';
        return;
    }
    btn.disabled = true;
    box.innerHTML = '<p class="info-text">Reading it through... this takes a moment.</p>';
    try {
        const response = await fetchWithAuth(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) {
            box.innerHTML = `<p class="error-text">${escapeHtml(data.detail || 'Review failed')}</p>`;
            return;
        }
        box.innerHTML = renderFeedback(data);
    } catch (err) {
        box.innerHTML = `<p class="error-text">${escapeHtml(err.message)}</p>`;
    } finally {
        btn.disabled = false;
    }
}

function renderFeedback(data) {
    if (!data.points?.length && !data.summary) {
        return '<p class="info-text">No feedback came back. Check the LLM settings.</p>';
    }
    const parts = [];

    const scoreClass = data.score == null ? 'score-none'
        : data.score >= 75 ? 'score-good'
        : data.score >= 50 ? 'score-mixed' : 'score-poor';
    parts.push('<div class="feedback-header">');
    if (data.score != null) {
        parts.push(`<span class="feedback-score ${scoreClass}">${data.score}</span>`);
    }
    parts.push('<div>');
    if (data.target) parts.push(`<p class="feedback-target">${escapeHtml(data.target)}</p>`);
    if (data.summary) parts.push(`<p class="feedback-summary">${escapeHtml(data.summary)}</p>`);
    parts.push('</div></div>');

    if (data.strengths?.length) {
        parts.push('<h4>What works</h4><ul class="feedback-strengths">');
        data.strengths.forEach(s => parts.push(`<li>${escapeHtml(s)}</li>`));
        parts.push('</ul>');
    }

    if (data.points?.length) {
        parts.push('<h4>What to change</h4><ul class="feedback-points">');
        data.points.forEach(p => {
            const sev = (p.severity || 'suggestion').toLowerCase();
            parts.push(`<li class="sev-${escapeHtml(sev)}">`);
            parts.push(`<span class="feedback-sev">${escapeHtml(sev)}</span>`);
            if (p.section) parts.push(`<span class="feedback-section">${escapeHtml(p.section)}</span>`);
            parts.push(`<p class="feedback-issue">${escapeHtml(p.issue)}</p>`);
            if (p.suggestion) parts.push(`<p class="feedback-suggestion">${escapeHtml(p.suggestion)}</p>`);
            if (p.example) parts.push(`<blockquote class="feedback-example">${escapeHtml(p.example)}</blockquote>`);
            parts.push('</li>');
        });
        parts.push('</ul>');
    }

    if (data.missing_keywords?.length) {
        parts.push('<h4>Terms the vacancy uses that your document does not</h4>');
        parts.push('<p class="feedback-keywords">');
        data.missing_keywords.forEach(k =>
            parts.push(`<span class="keyword-chip">${escapeHtml(k)}</span>`));
        parts.push('</p>');
    }

    return parts.join('');
}

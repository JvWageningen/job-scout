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
            <div class="job-meta">
                ${meta}
            </div>
            ${statusSection}
            <p><a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">View Job →</a></p>
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

        statusDiv.innerHTML = `<div class="status-info">${statusText}<p>${messageText}</p>${timeText}${errorText}</div>`;

        if (data.status === 'done' || data.status === 'error') {
            const runBtn = document.getElementById('run-btn');
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.textContent = 'Run Pipeline';
            }
            if (statusPollInterval) {
                clearInterval(statusPollInterval);
                statusPollInterval = null;
            }
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
        loadNotificationData(),
        loadSitesData(),
        loadLLMSettings(),
        loadScheduleStatus(),
        loadKeywords(),
        loadAnalytics(),
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
                div.textContent = role;
                rolesList.appendChild(div);
            });
        } else {
            rolesList.innerHTML = '<span>No past roles information</span>';
        }

        // Load CV profile
        await loadCVProfile();

    } catch (error) {
        console.error('Error loading CV profile:', error);
        container.style.display = 'block';
        error.style.display = 'block';
        error.textContent = 'Error loading CV profile: ' + error.message;
    }
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
            li.style.marginBottom = '10px';
            li.innerHTML = `
                <strong>${escapeHtml(site.name)}</strong>: ${escapeHtml(site.url)}
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

    if (!url) {
        alert('URL is required');
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/sites`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: currentUser, url, name }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to add site'}`);
            return;
        }

        document.getElementById('site-url').value = '';
        document.getElementById('site-name').value = '';
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
    if (!currentUser) {
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/config?user=${encodeURIComponent(currentUser)}`);
        if (!response.ok) {
            return;
        }

        const config = await response.json();
        document.getElementById('llm-provider').value = config.llm_provider || 'claude_cli';
        document.getElementById('eval-provider').value = config.evaluation_provider || '';
        document.getElementById('screen-provider').value = config.screening_provider || '';
        document.getElementById('quick-eval-provider').value = config.quick_eval_provider || '';
        document.getElementById('keywords-provider').value = config.keywords_provider || '';
        document.getElementById('local-base-url').value = config.local_base_url || 'http://localhost:11434/v1';
        document.getElementById('local-model').value = config.local_model || 'llama3.1';
        document.getElementById('local-screen-model').value = config.local_screening_model || '';
    } catch (error) {
        console.error('Error loading LLM settings:', error);
    }
}

/**
 * Save LLM settings
 */
async function saveLLMSettings() {
    if (!currentUser) {
        alert('Please select a user first');
        return;
    }

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
            alert('LLM settings saved successfully');
        }
    } catch (error) {
        console.error('Error saving LLM settings:', error);
        alert('Error saving LLM settings');
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
async function loadScheduleStatus() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/schedule/status`);
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        const statusDiv = document.getElementById('schedule-status');
        statusDiv.innerHTML = `<p><strong>Current Status:</strong> ${escapeHtml(data.status)}</p>`;
    } catch (error) {
        console.error('Error loading schedule status:', error);
    }
}

/**
 * Install a schedule
 */
async function installSchedule() {
    const hour = parseInt(document.getElementById('schedule-hour').value);
    const minute = parseInt(document.getElementById('schedule-minute').value);

    if (isNaN(hour) || isNaN(minute) || hour < 0 || hour >= 24 || minute < 0 || minute >= 60) {
        alert('Invalid hour or minute');
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hour, minute }),
        });

        if (!response.ok) {
            const error = await response.json();
            alert(`Error: ${error.detail || 'Failed to install schedule'}`);
            return;
        }

        alert('Schedule installed successfully');
        await loadScheduleStatus();
    } catch (error) {
        console.error('Error installing schedule:', error);
        alert('Error installing schedule');
    }
}

/**
 * Remove the schedule
 */
async function removeSchedule() {
    if (!confirm('Remove the scheduled job?')) {
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/schedule`, {
            method: 'DELETE',
        });

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

        // Check if profile_description is set (indicates initialized global config)
        const setupSection = document.getElementById('global-setup-section');
        if (!config.profile_description && setupSection) {
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
    container.innerHTML = '<p class="loading">Loading approval queue...</p>';

    try {
        const response = await fetchWithAuth(`${API_BASE}/approval/queue`);
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

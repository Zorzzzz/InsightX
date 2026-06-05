const CONFIG = {
    API_URL: '/api',
    MIN_SUMMARY_WORDS: 30
};

function showError(message, isSuccess = false) {
    const toast = document.getElementById('toast-notification');
    const toastTitle = document.querySelector('.toast-title');
    const toastMessage = document.getElementById('toast-message');

    if (!toast || !toastTitle || !toastMessage) {
        alert(message);
        return;
    }

    toastMessage.textContent = message;
    if (isSuccess) {
        toast.style.borderColor = '#10b981';
        toastTitle.style.color = '#10b981';
        toastTitle.textContent = 'Success';
    } else {
        toast.style.borderColor = 'rgba(239, 68, 68, 0.4)';
        toastTitle.style.color = '#f87171';
        toastTitle.textContent = 'Error';
    }

    toast.classList.add('show');
    setTimeout(() => closeToast(), 4000);
}

function closeToast() {
    const toast = document.getElementById('toast-notification');
    if (toast) {
        toast.classList.remove('show');
    }
}

function getErrorMessage(error, fallback = 'Something went wrong.') {
    return error instanceof Error && error.message ? error.message : fallback;
}

function decodeMaybeEncoded(value) {
    if (typeof value !== 'string') {
        return '';
    }

    try {
        return decodeURIComponent(value);
    } catch {
        return value;
    }
}

function parseJSONAttribute(value, fallback = '') {
    if (!value) {
        return fallback;
    }

    try {
        return JSON.parse(value);
    } catch {
        return fallback;
    }
}

function getWordCount(text) {
    return (text || '').trim().split(/\s+/).filter(Boolean).length;
}

function formatCompressionReduction(ratio) {
    const numeric = Number(ratio);
    if (!Number.isFinite(numeric)) {
        return 'Unavailable';
    }

    const normalized = Math.min(Math.max(numeric, 0), 1);
    return `${((1 - normalized) * 100).toFixed(1)}% reduction`;
}

function sanitizeDownloadName(filename) {
    const baseName = (filename || 'summary')
        .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
        .replace(/\.[^/.]+$/, '')
        .trim();

    return `${baseName || 'summary'}_summary.txt`;
}

function setButtonLoading(button, isLoading) {
    if (!button) {
        return;
    }

    const buttonText = button.querySelector('.btn-text');
    const loader = button.querySelector('.loader');

    if (buttonText) {
        buttonText.style.display = isLoading ? 'none' : 'inline';
    }
    if (loader) {
        loader.style.display = isLoading ? 'inline-block' : 'none';
    }
}

function renderSummaryResults(data) {
    const resultsContainer = document.getElementById('results-container');
    if (!resultsContainer) {
        return;
    }

    const domainEl = document.getElementById('res-domain');
    const confidenceEl = document.getElementById('res-confidence');
    const compressionEl = document.getElementById('res-compression');
    const summaryEl = document.getElementById('res-summary');
    const keywordsEl = document.getElementById('res-keywords');
    const explanationEl = document.getElementById('res-explanation');

    if (domainEl) {
        domainEl.innerHTML = `Domain: <b>${data.domain_type || 'General'}</b>`;
    }
    if (confidenceEl) {
        const confidence = Number(data.confidence_score || 0);
        confidenceEl.innerHTML = `Confidence: <b>${(confidence * 100).toFixed(1)}%</b>`;
    }
    if (compressionEl) {
        compressionEl.innerHTML = `Compression: <b>${formatCompressionReduction(data.compression_ratio)}</b>`;
    }
    if (summaryEl) {
        summaryEl.textContent = data.summary || '';
    }
    if (keywordsEl) {
        keywordsEl.textContent = Array.isArray(data.keywords) ? data.keywords.join(', ') : '';
    }
    if (explanationEl) {
        explanationEl.textContent = data.explanation || '';
    }

    resultsContainer.style.display = 'block';
}

function buildBackgroundBlobs() {
    const blobsContainer = document.createElement('div');
    blobsContainer.style.position = 'fixed';
    blobsContainer.style.top = '0';
    blobsContainer.style.left = '0';
    blobsContainer.style.width = '100%';
    blobsContainer.style.height = '100%';
    blobsContainer.style.zIndex = '-1';
    blobsContainer.style.pointerEvents = 'none';
    document.body.appendChild(blobsContainer);

    for (let index = 0; index < 3; index += 1) {
        const blob = document.createElement('div');
        blob.className = 'bg-blob';
        blob.style.top = `${Math.random() * 100}%`;
        blob.style.left = `${Math.random() * 100}%`;
        blob.style.animationDelay = `${index * 5}s`;
        blob.style.background = 'radial-gradient(circle, rgba(59, 130, 246, 0.08) 0%, transparent 70%)';
        blob.style.width = '400px';
        blob.style.height = '400px';
        blob.style.position = 'absolute';
        blob.style.filter = 'blur(60px)';
        blobsContainer.appendChild(blob);
    }
}

window.downloadSummary = function downloadSummary(filename, textValue) {
    const summary = decodeMaybeEncoded(textValue);
    const blob = new Blob([summary], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');

    anchor.href = url;
    anchor.download = sanitizeDownloadName(filename);
    anchor.click();

    URL.revokeObjectURL(url);
};

window.viewSummary = function viewSummary(textValue) {
    const summary = decodeMaybeEncoded(textValue);
    const popup = window.open('', '_blank', 'noopener,noreferrer');

    if (!popup) {
        showError('Popup blocked. Please allow popups to view the summary.');
        return;
    }

    popup.document.title = 'Summary View';

    const style = popup.document.createElement('style');
    style.textContent = `
        body {
            font-family: Arial, sans-serif;
            padding: 40px;
            line-height: 1.8;
            color: #111827;
            background: #ffffff;
        }
        h2 {
            margin-top: 0;
        }
        pre {
            white-space: pre-wrap;
            word-break: break-word;
        }
    `;

    const heading = popup.document.createElement('h2');
    heading.textContent = 'Summary';

    const divider = popup.document.createElement('hr');

    const content = popup.document.createElement('pre');
    content.textContent = summary;

    popup.document.head.appendChild(style);
    popup.document.body.appendChild(heading);
    popup.document.body.appendChild(divider);
    popup.document.body.appendChild(content);
};

function bindHistoryActions() {
    document.querySelectorAll('.history-view-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const summary = parseJSONAttribute(button.dataset.summary, '');
            window.viewSummary(summary);
        });
    });

    document.querySelectorAll('.history-download-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const filename = parseJSONAttribute(button.dataset.filename, 'summary');
            const summary = parseJSONAttribute(button.dataset.summary, '');
            window.downloadSummary(filename, summary);
        });
    });
}

function setupRegisterForm() {
    const registerForm = document.getElementById('register-form');
    if (!registerForm) {
        return;
    }

    registerForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const fullName = document.getElementById('reg-name')?.value.trim() || '';
        const username = document.getElementById('reg-username')?.value.trim() || '';
        const email = document.getElementById('reg-email')?.value.trim() || '';
        const password = document.getElementById('reg-password')?.value || '';
        const registerButton = document.getElementById('register-btn');
        const buttonText = registerButton?.querySelector('.btn-text');

        if (buttonText) {
            buttonText.textContent = 'Creating...';
        }

        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action: 'register',
                    full_name: fullName,
                    username,
                    email,
                    password
                })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Could not create account.');
            }

            showError('Account created successfully!', true);
            setTimeout(() => {
                window.location.href = data.redirect;
            }, 1000);
        } catch (error) {
            showError(getErrorMessage(error));
            if (buttonText) {
                buttonText.textContent = 'Register';
            }
        }
    });
}

function setupLoginForm() {
    const loginForm = document.getElementById('login-form');
    if (!loginForm) {
        return;
    }

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const email = document.getElementById('email-input')?.value.trim() || '';
        const password = document.getElementById('password-input')?.value || '';
        const loginButton = document.getElementById('login-btn');

        setButtonLoading(loginButton, true);

        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'login', email, password })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Could not log in.');
            }

            showError('Welcome back!', true);
            setTimeout(() => {
                window.location.href = data.redirect;
            }, 1000);
        } catch (error) {
            showError(getErrorMessage(error));
            setButtonLoading(loginButton, false);
        }
    });
}

function setupTextSummarization() {
    const summarizeButton = document.getElementById('summarize-btn');
    const textInput = document.getElementById('text-input');
    const resultsContainer = document.getElementById('results-container');

    if (!summarizeButton || !textInput) {
        return;
    }

    summarizeButton.addEventListener('click', async () => {
        const text = textInput.value.trim();
        if (getWordCount(text) < CONFIG.MIN_SUMMARY_WORDS) {
            showError(`Please enter at least ${CONFIG.MIN_SUMMARY_WORDS} words to summarize.`);
            return;
        }

        setButtonLoading(summarizeButton, true);
        if (resultsContainer) {
            resultsContainer.style.display = 'none';
        }

        try {
            const response = await fetch(`${CONFIG.API_URL}/summarize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Could not summarize text.');
            }

            renderSummaryResults(data);
        } catch (error) {
            showError(getErrorMessage(error));
        } finally {
            setButtonLoading(summarizeButton, false);
        }
    });
}

function setupFileSummarization() {
    const documentButtons = document.querySelectorAll('.file-buttons .file-btn');
    const resultsContainer = document.getElementById('results-container');

    if (documentButtons.length === 0) {
        return;
    }

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.style.display = 'none';
    document.body.appendChild(fileInput);

    documentButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const title = button.getAttribute('title');
            if (title === 'PDF') {
                fileInput.accept = '.pdf';
            } else if (title === 'Word') {
                fileInput.accept = '.docx';
            } else if (title === 'Excel') {
                fileInput.accept = '.csv,.xlsx';
            } else if (title === 'Text') {
                fileInput.accept = '.txt';
            } else {
                fileInput.accept = '';
            }

            fileInput.click();
        });
    });

    fileInput.addEventListener('change', async (event) => {
        const file = event.target.files?.[0];
        if (!file) {
            return;
        }

        const summarizeButton = document.getElementById('summarize-btn');
        const formData = new FormData();
        formData.append('file', file);

        setButtonLoading(summarizeButton, true);
        if (resultsContainer) {
            resultsContainer.style.display = 'none';
        }

        try {
            const response = await fetch(`${CONFIG.API_URL}/summarize`, {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Could not summarize file.');
            }

            renderSummaryResults(data);
        } catch (error) {
            showError(getErrorMessage(error));
        } finally {
            setButtonLoading(summarizeButton, false);
            fileInput.value = '';
        }
    });
}

function setupProfileActions() {
    const profileCard = document.querySelector('.profile-container');
    if (!profileCard) {
        return;
    }

    const buttons = profileCard.querySelectorAll('.btn-group button');
    const changePasswordButton = buttons[0];
    const updateNameButton = buttons[1];
    const deleteAccountButton = buttons[2];

    if (changePasswordButton) {
        changePasswordButton.addEventListener('click', async () => {
            const newPassword = prompt('Enter your new password (minimum 6 characters):');
            if (!newPassword) {
                return;
            }
            if (newPassword.length < 6) {
                showError('Password must be at least 6 characters.');
                return;
            }

            try {
                const response = await fetch('/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_password: newPassword })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Could not update password.');
                }

                showError('Password updated successfully!', true);
            } catch (error) {
                showError(getErrorMessage(error));
            }
        });
    }

    if (updateNameButton) {
        updateNameButton.addEventListener('click', async () => {
            const newName = prompt('Enter your new full name:');
            if (!newName || !newName.trim()) {
                return;
            }

            try {
                const response = await fetch('/api/update-profile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName.trim() })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Could not update your profile.');
                }

                showError('Name updated successfully!', true);
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            } catch (error) {
                showError(getErrorMessage(error));
            }
        });
    }

    if (deleteAccountButton) {
        deleteAccountButton.addEventListener('click', async () => {
            const confirmed = confirm(
                'Are you sure you want to delete your account? This will erase all of your summaries.'
            );
            if (!confirmed) {
                return;
            }

            try {
                const response = await fetch('/api/delete-account', { method: 'POST' });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Could not delete your account.');
                }

                alert('Account deleted. Redirecting to login...');
                window.location.href = data.redirect || '/login';
            } catch (error) {
                showError(getErrorMessage(error));
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    buildBackgroundBlobs();
    bindHistoryActions();
    setupRegisterForm();
    setupLoginForm();
    setupTextSummarization();
    setupFileSummarization();
    setupProfileActions();
});


function doLogout(){
    window.location.href='/logout';
}

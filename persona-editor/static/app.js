/**
 * Persona Editor — Browser client
 *
 * Features:
 * - List all personas in a grid
 * - Create, edit, clone, delete personas
 * - Upload/preview avatar images
 * - Upload/play/remove reference audio
 * - Multipart form submission for create/update
 */

// ==========================================================================
// State
// ==========================================================================

let editingName = null; // null = new, string = editing
let avatarRemoveRequested = false;
let audioRemoveRequested = false;
let currentAudioEl = null;
let currentAvatarUrl = null;

// DOM elements
const personaListEl = document.getElementById('persona-list');
const emptyStateEl = document.getElementById('empty-state');
const editorOverlay = document.getElementById('editor-overlay');
const deleteOverlay = document.getElementById('delete-overlay');
const editorTitle = document.getElementById('editor-title');
const formErrorEl = document.getElementById('form-error');
const personaForm = document.getElementById('persona-form');

// Form fields
const pfName = document.getElementById('pf-name');
const pfDescription = document.getElementById('pf-description');
const pfSystemPrompt = document.getElementById('pf-system-prompt');
const pfRouterHints = document.getElementById('pf-router-hints');
const pfAllowToolCalls = document.getElementById('pf-allow-tool-calls');
const pfMemorySize = document.getElementById('pf-memory-size');
const pfAvatarColor = document.getElementById('pf-avatar-color');
const pfAvatarImage = document.getElementById('pf-avatar-image');
const pfReferenceAudio = document.getElementById('pf-reference-audio');
const pfReferenceAudioTranscript = document.getElementById('pf-reference-audio-transcript');
const pfReferenceAudioLanguage = document.getElementById('pf-reference-audio-language');

// Preview elements
const avatarPreview = document.getElementById('avatar-preview');
const audioStatus = document.getElementById('audio-status');
const colorPreview = document.getElementById('color-preview');

// Buttons
const btnNew = document.getElementById('btn-new');
const btnClose = document.getElementById('btn-close');
const btnCancel = document.getElementById('btn-cancel');
const btnSave = document.getElementById('btn-save');
const btnRemoveAvatar = document.getElementById('btn-remove-avatar');
const btnRemoveAudio = document.getElementById('btn-remove-audio');
const btnPlayAudio = document.getElementById('btn-play-audio');
const btnConfirmDelete = document.getElementById('btn-confirm-delete');
const btnCancelDelete = document.getElementById('btn-cancel-delete');
const btnCloseDelete = document.getElementById('btn-close-delete');
const deleteMessage = document.getElementById('delete-message');

// ==========================================================================
// Init
// ==========================================================================

loadPersonas();

// ==========================================================================
// Persona list rendering
// ==========================================================================

async function loadPersonas() {
    try {
        const resp = await fetch('/api/personas');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const personas = await resp.json();
        renderPersonaList(personas);
    } catch (err) {
        console.error('Failed to load personas:', err);
        personaListEl.innerHTML = '<p class="empty-state">Failed to load personas. Is the server running?</p>';
    }
}

function renderPersonaList(personas) {
    personaListEl.innerHTML = '';

    // Sort alphabetically (case-insensitive)
    const sorted = [...personas].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

    if (sorted.length === 0) {
        emptyStateEl.classList.remove('hidden');
        personaListEl.classList.add('hidden');
        return;
    }

    emptyStateEl.classList.add('hidden');
    personaListEl.classList.remove('hidden');

    for (const p of sorted) {
        const card = document.createElement('div');
        card.className = 'persona-card';

        // Avatar
        const avatar = document.createElement('div');
        avatar.className = 'persona-avatar';
        avatar.style.backgroundColor = p.avatar_color;

        if (p.avatar_image) {
            const img = document.createElement('img');
            img.src = `/api/personas/${encodeURIComponent(p.name)}/avatar`;
            img.alt = p.name;
            img.onerror = () => {
                avatar.innerHTML = '';
                avatar.textContent = p.name.charAt(0).toUpperCase();
            };
            avatar.appendChild(img);
        } else {
            avatar.textContent = p.name.charAt(0).toUpperCase();
        }

        // Info
        const info = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'persona-name';
        name.textContent = p.name;

        const desc = document.createElement('div');
        desc.className = 'persona-desc';
        desc.textContent = p.description || '';

        info.appendChild(name);
        info.appendChild(desc);

        card.appendChild(avatar);
        card.appendChild(info);

        // Click to edit
        card.addEventListener('click', () => openEditor(p.name));

        // Action buttons overlay
        const actions = document.createElement('div');
        actions.className = 'persona-actions';
        actions.innerHTML = `
            <button class="btn-action btn-clone" title="Clone">📋</button>
            <button class="btn-action btn-delete" title="Delete">🗑️</button>
        `;

        actions.querySelector('.btn-clone').addEventListener('click', (e) => {
            e.stopPropagation();
            clonePersona(p.name);
        });

        actions.querySelector('.btn-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            confirmDeletePersona(p.name);
        });

        card.appendChild(actions);
        personaListEl.appendChild(card);
    }
}

// ==========================================================================
// Editor modal
// ==========================================================================

btnNew.addEventListener('click', () => openEditor(null));
btnClose.addEventListener('click', closeEditor);
btnCancel.addEventListener('click', closeEditor);
personaForm.addEventListener('submit', submitForm);

function openEditor(name) {
    editingName = name;
    avatarRemoveRequested = false;
    audioRemoveRequested = false;
    stopAudio();

    formErrorEl.classList.add('hidden');
    formErrorEl.textContent = '';

    // Reset file inputs
    pfAvatarImage.value = '';
    pfReferenceAudio.value = '';

    if (name) {
        editorTitle.textContent = `Edit Persona: ${name}`;
        loadPersonaDetail(name);
    } else {
        editorTitle.textContent = 'New Persona';
        resetForm();
    }

    editorOverlay.classList.remove('hidden');
}

function closeEditor() {
    stopAudio();
    revokeAvatarUrl();
    editorOverlay.classList.add('hidden');
    editingName = null;
}

async function loadPersonaDetail(name) {
    try {
        const resp = await fetch(`/api/personas/${encodeURIComponent(name)}/detail`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const p = await resp.json();

        pfName.value = p.name;
        pfDescription.value = p.description || '';
        pfSystemPrompt.value = p.system_prompt || '';
        pfRouterHints.value = p.router_hints || '';
        pfAllowToolCalls.checked = p.allow_tool_calls || false;
        pfMemorySize.value = p.memory_size ?? 8192;
        pfAvatarColor.value = p.avatar_color || '#FF0000';
        pfReferenceAudioTranscript.value = p.reference_audio_transcript || '';
        pfReferenceAudioLanguage.value = p.reference_audio_language || 'en';

        updateColorPreview();
        updateAvatarPreview(p.avatar_image);
        updateAudioControls(p.reference_audio);
    } catch (err) {
        showFormError('Failed to load persona details.');
    }
}

function resetForm() {
    pfName.value = '';
    pfDescription.value = '';
    pfSystemPrompt.value = '';
    pfRouterHints.value = '';
    pfAllowToolCalls.checked = false;
    pfMemorySize.value = 8192;
    pfAvatarColor.value = '#FF0000';
    pfReferenceAudioTranscript.value = '';
    pfReferenceAudioLanguage.value = 'en';
    updateColorPreview();
    updateAvatarPreview(false);
    updateAudioControls(false);
}

// ==========================================================================
// File previews
// ==========================================================================

pfAvatarColor.addEventListener('input', updateColorPreview);
pfAvatarImage.addEventListener('change', onAvatarFileSelected);
btnRemoveAvatar.addEventListener('click', resetAvatarField);
pfReferenceAudio.addEventListener('change', onAudioFileSelected);
btnRemoveAudio.addEventListener('click', resetAudioField);
btnPlayAudio.addEventListener('click', playAudio);

function updateColorPreview() {
    colorPreview.style.backgroundColor = pfAvatarColor.value;
}

function updateAvatarPreview(hasAvatar) {
    revokeAvatarUrl();
    if (hasAvatar && !avatarRemoveRequested) {
        const img = document.createElement('img');
        img.src = editingName ? `/api/personas/${encodeURIComponent(editingName)}/avatar` : '';
        img.alt = 'Current avatar';
        img.onerror = () => {
            avatarPreview.innerHTML = '<span class="avatar-initial">?</span>';
        };
        avatarPreview.innerHTML = '';
        avatarPreview.appendChild(img);
        btnRemoveAvatar.classList.remove('hidden');
    } else if (pfAvatarImage.files.length > 0) {
        // Preview selected file
        const file = pfAvatarImage.files[0];
        currentAvatarUrl = URL.createObjectURL(file);
        const img = document.createElement('img');
        img.src = currentAvatarUrl;
        img.alt = 'Avatar preview';
        avatarPreview.innerHTML = '';
        avatarPreview.appendChild(img);
        btnRemoveAvatar.classList.remove('hidden');
    } else {
        avatarPreview.innerHTML = '<span class="avatar-initial">?</span>';
        btnRemoveAvatar.classList.add('hidden');
    }
}

function revokeAvatarUrl() {
    if (currentAvatarUrl) {
        URL.revokeObjectURL(currentAvatarUrl);
        currentAvatarUrl = null;
    }
}

function onAvatarFileSelected() {
    avatarRemoveRequested = false;
    updateAvatarPreview(false);
}

function resetAvatarField() {
    pfAvatarImage.value = '';
    avatarRemoveRequested = true;
    updateAvatarPreview(false);
}

function updateAudioControls(hasAudio) {
    if (pfReferenceAudio.files.length > 0) {
        audioStatus.textContent = `New file: ${pfReferenceAudio.files[0].name}`;
    } else if (hasAudio && !audioRemoveRequested) {
        audioStatus.textContent = 'Current file on server';
    } else {
        audioStatus.textContent = 'None';
    }

    const hasSomething = pfReferenceAudio.files.length > 0 || (hasAudio && !audioRemoveRequested);
    btnPlayAudio.classList.toggle('hidden', !hasSomething);
    btnRemoveAudio.classList.toggle('hidden', !hasSomething);
}

function onAudioFileSelected() {
    audioRemoveRequested = false;
    updateAudioControls(false);
}

function resetAudioField() {
    pfReferenceAudio.value = '';
    audioRemoveRequested = true;
    stopAudio();
    updateAudioControls(false);
}

async function playAudio() {
    stopAudio();

    const file = pfReferenceAudio.files[0];
    if (file) {
        const url = URL.createObjectURL(file);
        currentAudioEl = new Audio(url);
        currentAudioEl.onended = () => stopAudio();
        currentAudioEl.onerror = () => {
            audioStatus.textContent = 'Playback failed';
            stopAudio();
        };
        await currentAudioEl.play().catch(() => {
            audioStatus.textContent = 'Playback failed';
            stopAudio();
        });
        return;
    }

    if (!editingName) return;

    try {
        const resp = await fetch(`/api/personas/${encodeURIComponent(editingName)}/reference-audio`);
        if (!resp.ok) {
            audioStatus.textContent = 'Playback failed';
            return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        currentAudioEl = new Audio(url);
        currentAudioEl.onended = () => stopAudio();
        currentAudioEl.onerror = () => {
            audioStatus.textContent = 'Playback failed';
            stopAudio();
        };
        await currentAudioEl.play().catch(() => {
            audioStatus.textContent = 'Playback failed';
            stopAudio();
        });
    } catch (err) {
        console.error('Failed to fetch audio:', err);
        audioStatus.textContent = 'Playback failed';
    }
}

function stopAudio() {
    if (currentAudioEl) {
        currentAudioEl.pause();
        currentAudioEl = null;
    }
    // Revoke object URLs created on the fly
    if (currentAudioEl) {
        // safety net
    }
}

// ==========================================================================
// Form submission
// ==========================================================================

async function submitForm(e) {
    e.preventDefault();
    formErrorEl.classList.add('hidden');

    const name = pfName.value.trim();
    const description = pfDescription.value.trim();
    const systemPrompt = pfSystemPrompt.value.trim();
    const routerHints = pfRouterHints.value.trim();
    const avatarColor = pfAvatarColor.value;
    const referenceAudioLanguage = pfReferenceAudioLanguage.value.trim();
    const allowToolCalls = pfAllowToolCalls.checked;
    const memorySize = parseInt(pfMemorySize.value, 10);
    const referenceAudioTranscript = pfReferenceAudioTranscript.value.trim();

    // Validation
    if (!name) return showFormError('Name is required.');
    if (name.toLowerCase() === 'user') return showFormError("'user' is a reserved name.");
    if (!systemPrompt) return showFormError('System prompt is required.');
    if (!routerHints) return showFormError('Router hints are required.');
    if (referenceAudioLanguage.length !== 2) return showFormError('Language must be a 2-letter code.');
    if (isNaN(memorySize) || memorySize < 0 || memorySize > 16384) {
        return showFormError('Memory size must be 0–16384.');
    }

    // Build multipart form
    const form = new FormData();
    form.append('name', name);
    form.append('description', description);
    form.append('system_prompt', systemPrompt);
    form.append('router_hints', routerHints);
    form.append('avatar_color', avatarColor);
    form.append('reference_audio_language', referenceAudioLanguage);
    form.append('allow_tool_calls', String(allowToolCalls));
    form.append('memory_size', String(memorySize));
    form.append('reference_audio_transcript', referenceAudioTranscript);

    const avatarFile = pfAvatarImage.files[0];
    if (avatarFile) {
        form.append('avatar_image', avatarFile);
    } else if (avatarRemoveRequested) {
        form.append('remove_avatar_image', 'true');
    }

    const audioFile = pfReferenceAudio.files[0];
    if (audioFile) {
        form.append('reference_audio', audioFile);
    } else if (audioRemoveRequested) {
        form.append('remove_reference_audio', 'true');
    }

    try {
        let resp;
        if (editingName) {
            resp = await fetch(`/api/personas/${encodeURIComponent(editingName)}`, {
                method: 'PUT',
                body: form,
            });
        } else {
            resp = await fetch('/api/personas', {
                method: 'POST',
                body: form,
            });
        }

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showFormError(err.detail || `HTTP ${resp.status}`);
            return;
        }

        closeEditor();
        await loadPersonas();
    } catch (err) {
        showFormError('Request failed. Is the server running?');
    }
}

function showFormError(msg) {
    formErrorEl.textContent = msg;
    formErrorEl.classList.remove('hidden');
}

// ==========================================================================
// Clone / Delete
// ==========================================================================

async function clonePersona(name) {
    try {
        const resp = await fetch(`/api/personas/${encodeURIComponent(name)}/clone`, {
            method: 'POST',
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            console.error('Clone failed:', err.detail);
            return;
        }
        await loadPersonas();
    } catch (err) {
        console.error('Clone error:', err);
    }
}

let deletingName = null;

// Global area for transient status messages on the persona list
const listStatusEl = document.getElementById('list-status');

function showListStatus(msg, isError) {
    if (!listStatusEl) return;
    listStatusEl.textContent = msg;
    listStatusEl.className = isError ? 'list-status error' : 'list-status';
    listStatusEl.classList.remove('hidden');
    setTimeout(() => {
        listStatusEl.classList.add('hidden');
    }, 5000);
}

function confirmDeletePersona(name) {
    deletingName = name;
    deleteMessage.textContent = `Delete persona "${name}"? This cannot be undone.`;
    deleteOverlay.classList.remove('hidden');
}

function closeDeleteModal() {
    deleteOverlay.classList.add('hidden');
    deletingName = null;
}

btnConfirmDelete.addEventListener('click', async () => {
    const nameToDelete = deletingName;
    if (!nameToDelete) return;
    closeDeleteModal();

    try {
        const resp = await fetch(`/api/personas/${encodeURIComponent(nameToDelete)}`, {
            method: 'DELETE',
        });
        if (resp.status === 204) {
            await loadPersonas();
            showListStatus(`Persona "${nameToDelete}" deleted.`, false);
        } else if (resp.status === 404) {
            showListStatus(`Persona "${nameToDelete}" not found on server. It may have been deleted already.`, true);
        } else {
            const err = await resp.json().catch(() => ({}));
            showListStatus(`Delete failed: ${err.detail || `HTTP ${resp.status}`}`, true);
        }
    } catch (err) {
        showListStatus('Delete failed. Is the server running?', true);
        console.error('Delete error:', err);
    }
});

btnCancelDelete.addEventListener('click', closeDeleteModal);
btnCloseDelete.addEventListener('click', closeDeleteModal);

// Close modals on backdrop click
editorOverlay.addEventListener('click', (e) => {
    if (e.target === editorOverlay) closeEditor();
});
deleteOverlay.addEventListener('click', (e) => {
    if (e.target === deleteOverlay) closeDeleteModal();
});

// ESC key closes modals
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (!deleteOverlay.classList.contains('hidden')) {
            closeDeleteModal();
        } else if (!editorOverlay.classList.contains('hidden')) {
            closeEditor();
        }
    }
});

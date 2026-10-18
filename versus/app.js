/**
 * Persona Versus — Browser client
 *
 * Flow:
 * 1. Load personas from GET /api/personas
 * 2. User selects personas + topic
 * 3. POST /api/simulations to start
 * 4. Open WebSocket, receive turns, play audio, send playback_done
 * 5. Server sends "done" when finished
 */

// State
let selectedPersonaA = null;
let selectedPersonaB = null;
let currentWebSocket = null;

// --- Load personas on startup ---

async function loadPersonas() {
    try {
        const res = await fetch('/api/personas');
        const data = await res.json();
        populatePersonas(data.personas || []);
    } catch (e) {
        updateStatus('Failed to load personas: ' + e.message);
    }
}

function populatePersonas(personas) {
    const selectA = document.getElementById('persona-a');
    const selectB = document.getElementById('persona-b');

    personas.forEach(p => {
        const optA = new Option(p.description || p.name, p.name);
        optA.dataset.language = p.language;
        optA.dataset.color = p.avatar_color || '#000';
        optA.dataset.avatarUrl = p.avatar_url || '';
        selectA.add(optA);

        const optB = new Option(p.description || p.name, p.name);
        optB.dataset.language = p.language;
        optB.dataset.color = p.avatar_color || '#000';
        optB.dataset.avatarUrl = p.avatar_url || '';
        selectB.add(optB);
    });

    // Default: first two
    if (personas.length >= 2) {
        selectA.selectedIndex = 0;
        selectB.selectedIndex = 1;
        selectedPersonaA = personas[0];
        selectedPersonaB = personas[1];
    }

    selectA.onchange = () => {
        selectedPersonaA = personas.find(p => p.name === selectA.value) || null;
        toggleStartButton();
    };
    selectB.onchange = () => {
        selectedPersonaB = personas.find(p => p.name === selectB.value) || null;
        toggleStartButton();
    };

    toggleStartButton();
}

// --- Topic input & start ---

const topicInput = document.getElementById('topic-input');
const startBtn = document.getElementById('start-btn');

topicInput.addEventListener('input', toggleStartButton);

function toggleStartButton() {
    const valid = selectedPersonaA && selectedPersonaB && selectedPersonaA.name !== selectedPersonaB.name && topicInput.value.trim().length > 0;
    startBtn.disabled = !valid;
}

startBtn.addEventListener('click', startSimulation);

async function startSimulation() {
    const topic = topicInput.value.trim();
    if (!topic || !selectedPersonaA || !selectedPersonaB) {
        updateStatus('Please select two different personas and enter a topic.');
        return;
    }

    // Set up avatars
    setupAvatars(selectedPersonaA, selectedPersonaB);

    // Switch to duet view
    document.getElementById('setup-container').classList.add('hidden');
    document.getElementById('duet-container').classList.remove('hidden');

    updateStatus('Starting simulation...');

    try {
        const turns = parseInt(document.getElementById('turns-input').value) || 6;
        // Create simulation
        const res = await fetch('/api/simulations', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                persona_a: selectedPersonaA.name,
                persona_b: selectedPersonaB.name,
                topic: topic,
                turns: turns,
            })
        });

        if (!res.ok) {
            throw new Error(`Server error: ${res.status} ${res.statusText}`);
        }

        const data = await res.json();
        const simId = data.id;
        updateStatus('Connecting to simulation...');

        // Connect WebSocket
        connectWebSocket(simId, topic);

    } catch (e) {
        updateStatus('Failed to start simulation: ' + e.message);
    }
}

// --- Avatars ---

function setupAvatars(personaA, personaB) {
    const imgA = document.getElementById('avatar-a');
    const imgB = document.getElementById('avatar-b');
    const labelA = document.getElementById('avatar-label-a');
    const labelB = document.getElementById('avatar-label-b');

    // Persona A
    imgA.src = personaA.avatar_url || '';
    imgA.alt = personaA.description || personaA.name;
    labelA.textContent = personaA.description || personaA.name;

    // Persona B
    imgB.src = personaB.avatar_url || '';
    imgB.alt = personaB.description || personaB.name;
    labelB.textContent = personaB.description || personaB.name;
}

// --- WebSocket handling ---

function connectWebSocket(simId, topic) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${location.host}/api/simulations/${simId}/stream`);
    currentWebSocket = ws;

    let hasReceivedDone = false;

    ws.onopen = () => {
        updateStatus('Connected — conversation in progress');
    };

    ws.onmessage = async (event) => {
        try {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'turn':
                    await handleTurn(data);
                    break;
                case 'done':
                    hasReceivedDone = true;
                    handleDone(data);
                    break;
                case 'error':
                    updateStatus('Error: ' + (data.message || 'Unknown error'));
                    break;
            }
        } catch (e) {
            updateStatus('Error parsing server message: ' + e.message);
        }
    };

    ws.onclose = () => {
        if (!hasReceivedDone) {
            updateStatus('Simulation ended');
        }
    };

    ws.onerror = () => {
        // No need to set status here — onmessage or onclose will handle it
    };
}

// --- Handle a turn ---

async function handleTurn(turnData) {
    // Determine speaker type
    const isPersonaA = turnData.speaker === selectedPersonaA?.name;
    const speakerColor = isPersonaA
        ? (selectedPersonaA?.avatar_color || '#e94560')
        : (selectedPersonaB?.avatar_color || '#00d2ff');
    const alignClass = isPersonaA ? 'a' : 'b';

    // Create turn element
    const turnEl = document.createElement('div');
    turnEl.className = `turn turn-${alignClass}`;
    turnEl.innerHTML = `
        <strong style="color: ${speakerColor}">${turnData.speaker}</strong>
        <p>${escapeHtml(turnData.text)}</p>
    `;
    document.getElementById('turns').appendChild(turnEl);

    // Scroll to bottom
    turnEl.scrollIntoView({ behavior: 'smooth', block: 'end' });

    // Play audio
    try {
        await playAudio(turnData.audio)
        // Audio finished — send playback_done
        if (currentWebSocket && currentWebSocket.readyState === WebSocket.OPEN) {
            currentWebSocket.send(JSON.stringify({ type: 'playback_done', turn: turnData.turn }))
        }
    } catch (e) {
        // Log the error more verbosely for debugging
        updateStatus(`Audio playback error: ${e.message}`)
        console.error('Audio error details:', {
            speaker: turnData.speaker,
            textLength: turnData.text?.length,
            audioLength: turnData.audio?.length,
            audioStart: turnData.audio?.slice(0, 100),
        })
    }
}

// --- Audio playback ---

function playAudio(base64Audio) {
    return new Promise((resolve, reject) => {
        const audio = new Audio(`data:audio/wav;base64,${base64Audio}`);
        audio.onended = () => resolve();
        audio.onerror = () => reject(new Error('Audio playback failed'));
        audio.play().catch(reject);
    });
}

// --- Simulation done ---

function handleDone(doneData) {
    updateStatus(`Simulation ${doneData.reason}`);

    // Show a "play again" button
    const turnsEl = document.getElementById('turns');
    const doneDiv = document.createElement('div');
    doneDiv.className = 'turn done';

    const msgParagraph = document.createElement('p');
    msgParagraph.textContent = `Simulation ended: ${doneData.reason}`;
    if (doneData.message) {
        msgParagraph.textContent += ` — ${doneData.message}`;
    }
    doneDiv.appendChild(msgParagraph);

    const playAgainBtn = document.createElement('button');
    playAgainBtn.className = 'play-again-btn';
    playAgainBtn.textContent = 'Play Again';
    playAgainBtn.addEventListener('click', () => {
        console.log('Play Again clicked!');
        resetToStart();
    });
    doneDiv.appendChild(playAgainBtn);

    turnsEl.appendChild(doneDiv);

    // Scroll to the done message
    doneDiv.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function resetToStart() {
    // Clear the conversation history
    const turnsEl = document.getElementById('turns');
    turnsEl.innerHTML = '';

    // Switch back to setup view
    document.getElementById('setup-container').classList.remove('hidden');
    document.getElementById('duet-container').classList.add('hidden');

    // Clear the topic input
    document.getElementById('topic-input').value = '';

    // Clear avatars
    document.getElementById('avatar-a').src = '';
    document.getElementById('avatar-b').src = '';
    document.getElementById('avatar-label-a').textContent = '';
    document.getElementById('avatar-label-b').textContent = '';

    updateStatus('Select personas and enter a topic to start');
}

// --- Utilities ---

function updateStatus(text) {
    const el = document.getElementById('status-text');
    if (el) el.textContent = text;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- Init ---

loadPersonas();

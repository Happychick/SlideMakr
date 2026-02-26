/**
 * SlideMakr Frontend
 *
 * Handles:
 * - WebSocket connection for voice streaming
 * - AudioWorklet for 16kHz PCM capture
 * - Audio playback at 24kHz
 * - Text input fallback
 * - Real-time transcript display
 */

// ============================================================================
// STATE
// ============================================================================

let ws = null;
let audioContext = null;
let mediaStream = null;
let workletNode = null;
let isRecording = false;
let currentPresentationId = null;

// Audio playback queue
let playbackQueue = [];
let isPlaying = false;

// ============================================================================
// UI HELPERS
// ============================================================================

function setStatus(message, isError = false) {
  const el = document.getElementById('status');
  if (message) {
    el.textContent = message;
    el.className = 'status visible' + (isError ? ' error' : '');
  } else {
    el.className = 'status';
  }
}

function addTranscript(role, text) {
  const transcript = document.getElementById('transcript');
  const entry = document.createElement('div');
  entry.className = `transcript-entry ${role}`;

  const roleLabel = document.createElement('div');
  roleLabel.className = 'role';
  roleLabel.textContent = role === 'user' ? 'You' : 'SlideMakr';

  const content = document.createElement('div');
  content.textContent = text;

  entry.appendChild(roleLabel);
  entry.appendChild(content);
  transcript.appendChild(entry);
  transcript.scrollTop = transcript.scrollHeight;
}

function showPresentationUrl(url) {
  const container = document.getElementById('presentationLink');
  const link = document.getElementById('presentationUrl');
  link.href = url;
  link.textContent = 'Open Presentation';
  container.className = 'presentation-link visible';

  // Show email form
  document.getElementById('emailForm').className = 'email-form visible';
}

function setMicState(state) {
  const button = document.getElementById('micButton');
  const label = document.getElementById('micLabel');

  button.className = 'mic-button';

  switch (state) {
    case 'idle':
      label.textContent = 'Click to start speaking';
      break;
    case 'connecting':
      button.className = 'mic-button connecting';
      label.textContent = 'Connecting...';
      break;
    case 'recording':
      button.className = 'mic-button recording';
      label.textContent = 'Listening... Click to stop';
      break;
  }
}

// ============================================================================
// VOICE STREAMING
// ============================================================================

async function toggleVoice() {
  if (isRecording) {
    stopVoice();
  } else {
    await startVoice();
  }
}

async function startVoice() {
  try {
    setMicState('connecting');
    setStatus('Requesting microphone access...');

    // Get microphone access
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    });

    // Create AudioContext at 16kHz
    audioContext = new AudioContext({ sampleRate: 16000 });
    const source = audioContext.createMediaStreamSource(mediaStream);

    // Load AudioWorklet for PCM capture
    await audioContext.audioWorklet.addModule('/static/audio-processor.js');
    workletNode = new AudioWorkletNode(audioContext, 'pcm-capture');

    workletNode.port.onmessage = (event) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        // Convert Float32Array to 16-bit PCM
        const float32 = event.data;
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
          const s = Math.max(-1, Math.min(1, float32[i]));
          int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        // Send as base64
        const b64 = arrayBufferToBase64(int16.buffer);
        ws.send(JSON.stringify({ type: 'audio', data: b64 }));
      }
    };

    source.connect(workletNode);
    workletNode.connect(audioContext.destination);

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    ws.onopen = () => {
      isRecording = true;
      setMicState('recording');
      setStatus('Listening...');
    };

    ws.onmessage = handleWebSocketMessage;

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      setStatus('Connection error. Please try again.', true);
      stopVoice();
    };

    ws.onclose = () => {
      console.log('WebSocket closed');
      if (isRecording) {
        stopVoice();
      }
    };

  } catch (err) {
    console.error('Error starting voice:', err);

    if (err.name === 'NotAllowedError') {
      setStatus('Microphone access denied. Please allow microphone access.', true);
    } else if (err.name === 'NotFoundError') {
      setStatus('No microphone found.', true);
    } else {
      setStatus(`Error: ${err.message}`, true);
    }

    setMicState('idle');
  }
}

function stopVoice() {
  isRecording = false;

  // Stop audio
  if (workletNode) {
    workletNode.disconnect();
    workletNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }
  if (audioContext && audioContext.state !== 'closed') {
    audioContext.close();
    audioContext = null;
  }

  // Close WebSocket
  if (ws) {
    try {
      ws.send(JSON.stringify({ type: 'end' }));
    } catch (e) { /* ignore */ }
    ws.close();
    ws = null;
  }

  setMicState('idle');
  setStatus('');
}

function handleWebSocketMessage(event) {
  try {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
      case 'audio':
        // Queue audio for playback
        const pcmData = base64ToArrayBuffer(msg.data);
        queueAudioPlayback(pcmData);
        break;

      case 'transcript':
        addTranscript(msg.role, msg.text);
        break;

      case 'url':
        showPresentationUrl(msg.url);
        addTranscript('agent', `Your presentation is ready: ${msg.url}`);
        break;

      case 'status':
        setStatus(msg.message);
        break;

      case 'error':
        setStatus(msg.message, true);
        addTranscript('agent', `Error: ${msg.message}`);
        break;
    }
  } catch (e) {
    console.error('Error handling WebSocket message:', e);
  }
}

// ============================================================================
// AUDIO PLAYBACK (24kHz PCM)
// ============================================================================

function queueAudioPlayback(pcmBuffer) {
  playbackQueue.push(pcmBuffer);
  if (!isPlaying) {
    playNextAudio();
  }
}

async function playNextAudio() {
  if (playbackQueue.length === 0) {
    isPlaying = false;
    return;
  }

  isPlaying = true;
  const pcmBuffer = playbackQueue.shift();

  try {
    // Create playback context at 24kHz
    const playCtx = new AudioContext({ sampleRate: 24000 });
    const int16 = new Int16Array(pcmBuffer);
    const float32 = new Float32Array(int16.length);

    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 0x7FFF;
    }

    const buffer = playCtx.createBuffer(1, float32.length, 24000);
    buffer.getChannelData(0).set(float32);

    const source = playCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(playCtx.destination);
    source.onended = () => {
      playCtx.close();
      playNextAudio();
    };
    source.start();
  } catch (e) {
    console.error('Audio playback error:', e);
    playNextAudio();
  }
}

// ============================================================================
// TEXT INPUT (fallback)
// ============================================================================

async function sendText() {
  const input = document.getElementById('textInput');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  addTranscript('user', text);
  setStatus('Generating slides...');
  document.getElementById('sendButton').disabled = true;

  try {
    const response = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });

    const data = await response.json();

    if (data.success) {
      if (data.response) {
        addTranscript('agent', data.response);
      }
      if (data.presentation_url) {
        showPresentationUrl(data.presentation_url);
        currentPresentationId = data.presentation_id;
      }
      setStatus('');
    } else {
      setStatus(`Error: ${data.error}`, true);
      addTranscript('agent', `Sorry, there was an error: ${data.error}`);
    }
  } catch (err) {
    console.error('Error sending text:', err);
    setStatus('Connection error. Please try again.', true);
  } finally {
    document.getElementById('sendButton').disabled = false;
  }
}

// ============================================================================
// SHARE
// ============================================================================

async function sharePresentation() {
  const email = document.getElementById('emailInput').value.trim();
  if (!email || !currentPresentationId) return;

  try {
    const response = await fetch('/share', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        presentation_id: currentPresentationId,
        email: email
      })
    });

    const data = await response.json();

    if (data.success) {
      addTranscript('agent', `Shared with ${email}!`);
      document.getElementById('emailInput').value = '';
    } else {
      addTranscript('agent', `Error sharing: ${data.error}`);
    }
  } catch (err) {
    console.error('Error sharing:', err);
    addTranscript('agent', 'Error sharing presentation.');
  }
}

// ============================================================================
// UTILITIES
// ============================================================================

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

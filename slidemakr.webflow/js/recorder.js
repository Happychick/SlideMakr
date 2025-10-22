let mediaRecorder;
let audioChunks = [];
let stream;
let audioContext;
let analyser;
let isRecording = false;

function updateStatus(message) {
  const statusElement = document.getElementById('statusMessage');
  if (message) {
    statusElement.style.display = 'block';
    statusElement.textContent = message;
  } else {
    statusElement.style.display = 'none';
  }
}

async function toggleRecording() {
  if (!isRecording) {
    try {
      updateStatus('Requesting microphone access...');
      
      // Check if we're on mobile
      const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
      
      // Mobile-optimized audio constraints
      const constraints = {
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: isMobile ? 44100 : 16000
        }
      };

      stream = await navigator.mediaDevices.getUserMedia(constraints);
      updateStatus('Recording Audio...');
      
      // Create audio context with mobile-friendly settings
      const audioContextOptions = {};
      if (!isMobile) {
        audioContextOptions.sampleRate = 16000;
      }
      
      audioContext = new AudioContext(audioContextOptions);
      
      // Resume audio context if suspended (required on mobile)
      if (audioContext.state === 'suspended') {
        await audioContext.resume();
      }
      
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      // Check MediaRecorder support
      if (!MediaRecorder.isTypeSupported('audio/webm')) {
        console.log('audio/webm not supported, trying audio/mp4');
        if (!MediaRecorder.isTypeSupported('audio/mp4')) {
          console.log('audio/mp4 not supported, using default');
          mediaRecorder = new MediaRecorder(stream);
        } else {
          mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/mp4' });
        }
      } else {
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      }

      audioChunks = [];

      mediaRecorder.ondataavailable = (event) => {
        audioChunks.push(event.data);
      };

      mediaRecorder.onstop = sendAudioToServer;
      mediaRecorder.start();
      isRecording = true;

      // Update mic button appearance
      const micButton = document.getElementById('micButton');
      micButton.style.filter = 'brightness(50%)';
    } catch (err) {
      console.error("Error starting recording:", err);
      let errorMessage = 'Error starting recording';
      
      if (err.name === 'NotAllowedError') {
        errorMessage = 'Microphone access denied. Please allow microphone access and try again.';
      } else if (err.name === 'NotFoundError') {
        errorMessage = 'No microphone found. Please check your device settings.';
      } else if (err.name === 'NotSupportedError') {
        errorMessage = 'Recording not supported on this browser.';
      } else if (err.name === 'NotReadableError') {
        errorMessage = 'Microphone is being used by another application.';
      }
      
      updateStatus(errorMessage);
    }
  } else {
    stopRecording();
    // Reset mic button appearance
    const micButton = document.getElementById('micButton');
    micButton.style.filter = 'none';
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    stream.getTracks().forEach(track => track.stop());
    if (audioContext) {
      audioContext.close();
    }
    isRecording = false;
  }
}

function sendAudioToServer() {
  if (audioChunks.length === 0) {
    console.error('No audio recorded');
    alert('No audio recorded. Please try again.');
    updateStatus('');
    return;
  }
  updateStatus('Transcribing instructions...');

  // Capture timestamp when recording stops
  const startedAt = new Date().toISOString();

  // Use the recorded format (webm or mp4) and let server handle conversion
  const audioBlob = new Blob(audioChunks, { type: audioChunks[0].type || 'audio/webm' });
  const reader = new FileReader();
  reader.onloadend = () => {
    updateStatus('Transcribing Audio...');

    fetch('/record', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        audio: reader.result,
        started_at: startedAt
      })
    })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        updateStatus('Slides generated! Please enter your email to share.');
        // Hide mic button and show email form
        document.getElementById('micButton').style.display = 'none';
        document.getElementById('form_label').style.display = 'none';
        const emailForm = document.getElementById('emailForm');
        emailForm.style.display = 'block';

        // Store presentation data globally
        window.currentPresentationData = data;
        
        window.submitEmail = function() {
          const emailInput = document.getElementById('emailInput');
          const email = emailInput.value;
          if (!email) {
            alert('Please enter an email address');
            return;
          }
          
          fetch('/share', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ 
              presentation_id: window.currentPresentationData.presentation_id,
              email: email 
            })
          })
          .then(response => response.json())
          .then(shareData => {
            if (shareData.success) {
              window.location.href = window.currentPresentationData.presentation_url;
            } else {
              alert('Error sharing presentation: ' + shareData.error);
            }
          });
        };
      } else {
        alert('Error creating presentation: ' + data.error);
      }
    })
    .catch(error => {
      console.error('Error:', error);
      alert('Error sending audio to server');
    });
  };
  reader.readAsDataURL(audioBlob);
  audioChunks = [];
}

function handleTextSubmit(event) {
  event.preventDefault();
  const textInput = document.getElementById('field');
  const statusMessage = document.getElementById('textStatusMessage');

  statusMessage.textContent = 'Generating slides...';

  // Capture timestamp when Make those Slides button is clicked
  const startedAt = new Date().toISOString();

  fetch('/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ 
      text: textInput.value,
      started_at: startedAt
    })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      const textEmailForm = document.getElementById('textEmailForm');
      textEmailForm.style.display = 'block';
      statusMessage.textContent = 'Slides generated! Please enter your email to share.';

      window.submitTextEmail = function() {
        const emailInput = document.getElementById('textEmailInput');
        const email = emailInput.value;

        fetch('/share', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            presentation_id: data.presentation_id,
            email: email
          })
        })
        .then(response => response.json())
        .then(shareData => {
          if (shareData.success) {
            window.location.href = data.presentation_url;
          } else {
            alert('Error sharing presentation: ' + shareData.error);
          }
        });
      };
    } else {
      statusMessage.textContent = 'Error: ' + data.error;
    }
  })
  .catch(error => {
    console.error('Error:', error);
    statusMessage.textContent = 'Error generating slides';
  });
}
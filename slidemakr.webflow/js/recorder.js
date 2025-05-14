
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
      updateStatus('Recording Audio...');
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContext = new AudioContext({sampleRate: 16000});
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      
      mediaRecorder = new MediaRecorder(stream);
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
      updateStatus('Error starting recording');
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

  const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
  const reader = new FileReader();
  reader.onloadend = () => {
    updateStatus('Transcribing Audio...');
    fetch('/record', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ audio: reader.result }),
      credentials: 'same-origin'
    })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        updateStatus(''); // Clear status message
        // Hide mic button and show email form
        document.getElementById('micButton').style.display = 'none';
        document.getElementById('form_label').style.display = 'none';
        const emailForm = document.getElementById('emailForm');
        emailForm.style.display = 'block';
        document.querySelector('.w-form-fail').style.display = 'none';
        
        // Add submission handler
        window.submitEmail = function() {
          const email = document.getElementById('emailInput').value;
          if (email) {
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
          }
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

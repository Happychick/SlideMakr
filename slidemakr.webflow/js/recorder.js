let mediaRecorder;
let audioChunks = [];
let isRecording = false;

function updateStatus(message) {
  const statusElement = document.getElementById('statusMessage');
  if (statusElement) {
    statusElement.textContent = message;
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => {
      audioChunks.push(event.data);
    };
    mediaRecorder.onstop = handleStop;
    audioChunks = [];
    mediaRecorder.start();
    isRecording = true;
    updateStatus('Recording...');

    // Update mic button to show recording state
    const micButton = document.getElementById('micButton');
    if (micButton) {
      micButton.src = 'images/Ideas-lab.png';
      micButton.onclick = stopRecording;
    }
  } catch (error) {
    console.error('Error accessing microphone:', error);
    alert('Error accessing microphone. Please ensure microphone permissions are granted.');
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    isRecording = false;
    updateStatus('Processing...');

    // Update mic button back to non-recording state
    const micButton = document.getElementById('micButton');
    if (micButton) {
      micButton.src = 'images/Ideas-lab-1.png';
      micButton.onclick = startRecording;
    }
  }
}

function handleStop() {
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
        updateStatus('Generating Slides...'); // Show generating status
        setTimeout(() => {
          updateStatus(''); // Clear after 2 seconds
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
        }, 2000);
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
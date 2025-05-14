
let mediaRecorder;
let audioChunks = [];
let stream;
let audioContext;
let analyser;
let silenceStart = null;
const threshold = 50;
const silenceDelay = 2000;

async function startRecording() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext();
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
    
    checkSilence();
  } catch (err) {
    console.error("Error starting recording:", err);
  }
}

function checkSilence() {
  if (!analyser) return;
  
  const dataArray = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(dataArray);
  const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;

  if (average < threshold) {
    if (silenceStart === null) {
      silenceStart = Date.now();
    } else if (Date.now() - silenceStart > silenceDelay) {
      stopRecording();
      return;
    }
  } else {
    silenceStart = null;
  }
  
  requestAnimationFrame(checkSilence);
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    stream.getTracks().forEach(track => track.stop());
    if (audioContext) {
      audioContext.close();
    }
  }
}

function sendAudioToServer() {
  if (audioChunks.length === 0) {
    console.error('No audio recorded');
    alert('No audio recorded. Please try again.');
    return;
  }

  const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
  const reader = new FileReader();
  reader.onloadend = () => {
    fetch('http://0.0.0.0:5000/record', {
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
        const email = prompt("Please enter your email to share the presentation:");
        if (email) {
          fetch('http://0.0.0.0:5000/share', {
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


let mediaRecorder;
let audioChunks = [];

function startRecording() {
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (event) => {
        audioChunks.push(event.data);
      };
      mediaRecorder.onstop = sendAudioToServer;
      mediaRecorder.start();
    });
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

function sendAudioToServer() {
  const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
  const reader = new FileReader();
  reader.onloadend = () => {
    fetch('http://0.0.0.0:5000/record', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ audio: reader.result })
    })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        window.location.href = data.presentation_url;
      } else {
        alert('Error creating presentation');
      }
    });
  };
  reader.readAsDataURL(audioBlob);
  audioChunks = [];
}

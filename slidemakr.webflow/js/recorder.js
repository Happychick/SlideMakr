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
        updateStatus('Slides generated! Please enter your email to share.');
        // Hide mic button and show email form
        document.getElementById('micButton').style.display = 'none';
        document.getElementById('form_label').style.display = 'none';
        const emailForm = document.getElementById('emailForm');
        emailForm.style.display = 'block';

        window.submitEmail = function() {
          const emailInput = document.getElementById('emailInput');
          const nameInput = document.getElementById('nameInput');
          const email = emailInput.value;
          const name = nameInput.value;

          if (!email || !name) {
            alert('Please enter both email and presentation name');
            return;
          }

          fetch('/share', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ 
              presentation_id: data.presentation_id,
              email: email,
              name: name
            })
          })
            .then(response => response.json())
            .then(shareData => {
              if (shareData.success) {
                // Store the presentation details for potential editing
                currentPresentationId = data.presentation_id;
                currentPresentationUrl = data.presentation_url;

                // Hide email form, status message and show post-share options in audio section
                const emailForm = document.getElementById('emailForm');
                const statusMessage = document.getElementById('statusMessage');
                const postShareOptions = document.getElementById('postShareOptions');
                emailForm.style.display = 'none';
                statusMessage.style.display = 'none';
                postShareOptions.style.display = 'block';
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

function continueEditing() {
  // Show login interface for accessing saved presentations
  showLoginInterface();
}

function showLoginInterface() {
  const postShareOptions = document.getElementById('postShareOptions');
  postShareOptions.innerHTML = `
    <div class="form_text" style="margin-bottom: 20px; color: #333;">Want to continue editing your slides? Sign up to access your presentations!</div>
    <div style="display: flex; flex-direction: column; align-items: center; gap: 15px;">
      <script authed="handleSuccessfulAuth()" src="https://auth.util.repl.co/script.js"></script>
      <button onclick="showPostShareOptions()" class="form_button w-button" style="background-color: #666;">Back</button>
    </div>
  `;
}

function handleSuccessfulAuth() {
  // User is now authenticated, fetch their presentations
  const userId = getAuthHeaders()['X-Replit-User-Id'];
  const userEmail = getAuthHeaders()['X-Replit-User-Name'] + '@replit.com'; // Use Replit username as identifier

  fetch('/user-presentations', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email: userEmail })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      showUserPresentations(data.presentations);
    } else {
      alert('Error loading presentations: ' + data.error);
    }
  });
}

function getAuthHeaders() {
  // Extract Replit auth headers from the page
  return {
    'X-Replit-User-Id': document.querySelector('meta[name="replit-user-id"]')?.content || '',
    'X-Replit-User-Name': document.querySelector('meta[name="replit-user-name"]')?.content || ''
  };
}

function showUserPresentations(presentations) {
  const postShareOptions = document.getElementById('postShareOptions');

  if (presentations.length === 0) {
    postShareOptions.innerHTML = `
      <div class="form_text" style="margin-bottom: 20px; color: #333;">No presentations found. Create your first one!</div>
      <button onclick="createNew()" class="form_button w-button">Create New Presentation</button>
    `;
    return;
  }

  let presentationsHTML = `
    <div class="form_text" style="margin-bottom: 20px; color: #333;">Your Presentations:</div>
    <div style="max-height: 200px; overflow-y: auto; margin-bottom: 20px;">
  `;

  presentations.forEach(pres => {
    const date = new Date(pres.created_at).toLocaleDateString();
    presentationsHTML += `
      <div style="padding: 10px; border: 1px solid #ddd; margin-bottom: 10px; border-radius: 5px;">
        <div style="font-weight: bold;">${pres.name}</div>
        <div style="font-size: 12px; color: #666;">Created: ${date}</div>
        <button onclick="editPresentation('${pres.presentation_id}')" class="form_button w-button" style="margin-top: 5px; font-size: 12px; padding: 5px 10px;">Open</button>
      </div>
    `;
  });

  presentationsHTML += `
    </div>
    <button onclick="createNew()" class="form_button w-button">Create New Presentation</button>
  `;

  postShareOptions.innerHTML = presentationsHTML;
}

function editPresentation(presentationId) {
  const url = `https://docs.google.com/presentation/d/${presentationId}/edit`;
  window.open(url, '_blank');
}

function showPostShareOptions() {
  const postShareOptions = document.getElementById('postShareOptions');
  postShareOptions.innerHTML = `
    <div class="form_text" style="margin-bottom: 20px; color: #333;">Your presentation has been shared!</div>
    <button onclick="continueEditing()" class="form_button w-button" style="margin: 0 5px 10px 0;">Continue Editing</button>
    <button onclick="createNew()" class="form_button w-button" style="margin: 0 0 10px 5px;">Create New Presentation</button>
  `;
}

function createNew() {
  // Reset everything and start fresh
  window.location.reload();
}

// Override the recording handler when in edit mode
const originalHandleRecordingStop = handleRecordingStop;
function handleRecordingStop() {
  if (window.isEditMode && currentPresentationId) {
    // Edit existing presentation
    const reader = new FileReader();
    reader.onload = function(event) {
      const audioData = event.target.result;

      fetch('/edit-presentation', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          presentation_id: currentPresentationId,
          audio: audioData
        })
      })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          document.getElementById('textStatusMessage').textContent = 'Presentation updated!';
          setTimeout(() => {
            window.location.href = data.presentation_url;
          }, 2000);
        } else {
          alert('Error updating presentation: ' + data.error);
        }
      })
      .catch(error => {
        console.error('Error:', error);
        alert('Error sending audio to server');
      });
    };
    reader.readAsDataURL(audioBlob);
    audioChunks = [];
  } else {
    // Use original handler for new presentations
    originalHandleRecordingStop();
  }
}

let currentPresentationId = null;
let currentPresentationUrl = null;

function handleTextSubmit(event) {
  event.preventDefault();
  const textInput = document.getElementById('field');
  const statusMessage = document.getElementById('textStatusMessage');

  statusMessage.textContent = 'Generating slides...';

  fetch('/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ text: textInput.value })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      currentPresentationId = data.presentation_id;
      currentPresentationUrl = data.presentation_url;

      const textEmailForm = document.getElementById('textEmailForm');
      textEmailForm.style.display = 'block';
      statusMessage.textContent = 'Slides generated! Please enter details to share.';

      window.submitTextEmail = function() {
        const emailInput = document.getElementById('textEmailInput');
        const nameInput = document.getElementById('textNameInput');
        const email = emailInput.value;
        const name = nameInput.value;

        if (!email || !name) {
          alert('Please enter both email and presentation name');
          return;
        }

        fetch('/share', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            presentation_id: currentPresentationId,
            email: email,
            name: name
          })
        })
        .then(response => response.json())
        .then(shareData => {
          if (shareData.success) {
            // Store the presentation details for potential editing
            currentPresentationId = data.presentation_id;
            currentPresentationUrl = data.presentation_url;

            // Hide email form and show post-share options
            const textEmailForm = document.getElementById('textEmailForm');
            const postShareOptions = document.getElementById('postShareOptions');
            textEmailForm.style.display = 'none';
            postShareOptions.style.display = 'block';
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
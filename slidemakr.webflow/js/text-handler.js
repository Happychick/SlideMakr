
// Text-based slide creation handler
let textStartTime = null;

document.addEventListener('DOMContentLoaded', function() {
  const textForm = document.getElementById('email-form');
  
  if (textForm) {
    textForm.addEventListener('submit', function(e) {
      e.preventDefault();
      
      // Capture start time
      textStartTime = new Date().toISOString();
      
      const textInput = document.getElementById('field');
      const text = textInput.value;
      
      if (!text) {
        alert('Please enter presentation text');
        return;
      }
      
      fetch('/generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          text: text,
          started_at: textStartTime
        })
      })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          document.getElementById('email-form').style.display = 'none';
          document.getElementById('textEmailForm').style.display = 'block';
          
          window.currentPresentationData = data;
          
          window.submitTextEmail = function() {
            const emailInput = document.getElementById('textEmailInput');
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
        alert('Error creating presentation');
      });
    });
  }
});

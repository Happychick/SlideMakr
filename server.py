
from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import tempfile
from pydub import AudioSegment
from io import BytesIO
from slide_maker import convert_audio_segment_to_wav, transcribe_audio, generate_code_from_instructions, run_generated_code, credentials, share_presentation

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return "SlideMakr Server is running"

@app.route('/record', methods=['POST'])
def handle_recording():
    try:
        # Get base64 audio data from request
        audio_data = request.json['audio']
        audio_binary = base64.b64decode(audio_data.split(',')[1])
        
        # Convert to AudioSegment
        audio = AudioSegment.from_file(BytesIO(audio_binary))
        
        # Convert to WAV
        wav_buffer = convert_audio_segment_to_wav(audio)
        
        # Transcribe audio
        instructions = transcribe_audio(wav_buffer)
        
        # Generate slides code
        code = generate_code_from_instructions(instructions)
        
        # Create presentation
        presentation_id, url = run_generated_code(code, credentials)
        
        return jsonify({
            'success': True,
            'presentation_url': url,
            'presentation_id': presentation_id
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/share', methods=['POST'])
def share():
    try:
        data = request.json
        presentation_id = data['presentation_id']
        email = data['email']
        share_presentation(presentation_id, email, credentials)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

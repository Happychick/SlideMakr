
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

logging.basicConfig(level=logging.DEBUG)
import base64
import tempfile
from pydub import AudioSegment
from io import BytesIO
from slide_maker import convert_audio_segment_to_wav, transcribe_audio, generate_code_from_instructions, create_presentation, run_generated_code, credentials, share_presentation

app = Flask(__name__, static_folder='slidemakr.webflow')
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})


@app.route('/')
def index():
    return send_from_directory('slidemakr.webflow', 'index.html')


@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('slidemakr.webflow', path)


@app.route('/generate', methods=['POST'])
def handle_generate():
    try:
        instructions = request.json.get('text')
        if not instructions:
            return jsonify({'success': False, 'error': 'No text provided'}), 400

        code = generate_code_from_instructions(instructions)
        service, presentation_id = create_presentation(credentials)
        url, errors = run_generated_code(code, presentation_id, service)

        return jsonify({
            'success': True,
            'presentation_url': url,
            'presentation_id': presentation_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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

        # Create presentation and run code
        service, presentation_id = create_presentation(credentials)
        url, errors = run_generated_code(code, presentation_id,service)

        return jsonify({
            'success': True,
            'presentation_url': url,
            'presentation_id': presentation_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/share', methods=['POST'])
def share():
    try:
        data = request.json
        presentation_id = data['presentation_id']
        email = data['email']
        share_presentation(presentation_id, email, credentials)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

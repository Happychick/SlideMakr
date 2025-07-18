
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

logging.basicConfig(level=logging.DEBUG)
import base64
import tempfile
from pydub import AudioSegment
from io import BytesIO
from slide_maker import convert_audio_segment_to_wav, transcribe_audio, generate_code_from_instructions, create_presentation, run_generated_code, credentials, share_presentation, get_error_stats, reset_error_db, code_client, template_id

app = Flask(__name__, static_folder='slidemakr.webflow')
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})


@app.route('/')
def index():
    # Handle Google Cloud Run health checks
    if request.headers.get('User-Agent', '').startswith('GoogleHC/'):
        return jsonify({'status': 'healthy'}), 200
    try:
        return send_from_directory('slidemakr.webflow', 'index.html')
    except Exception as e:
        logging.error(f"Error serving index.html: {str(e)}")
        return jsonify({'status': 'healthy'}), 200  # Return healthy response as fallback


@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('slidemakr.webflow', path)


@app.route('/generate', methods=['POST'])
def handle_generate():
    try:
        instructions = request.json.get('text')
        email = request.json.get('email')
        if not instructions:
            return jsonify({'success': False, 'error': 'No text provided'}), 400
        if not email:
            return jsonify({'success': False, 'error': 'No email provided'}), 400

        service, presentation_id, presentation_title, use_template = create_presentation(code_client, credentials, instructions, template_id, email)
        code = generate_code_from_instructions(instructions, code_client, use_template)
        url, errors = run_generated_code(code_client, code, presentation_id, service, use_template)

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
        # Get base64 audio data and email from request
        audio_data = request.json['audio']
        email = request.json.get('email')
        if not email:
            return jsonify({'success': False, 'error': 'No email provided'}), 400
            
        audio_binary = base64.b64decode(audio_data.split(',')[1])

        # Convert to AudioSegment
        audio = AudioSegment.from_file(BytesIO(audio_binary))

        # Convert to WAV
        wav_buffer = convert_audio_segment_to_wav(audio)

        # Transcribe audio
        instructions = transcribe_audio(wav_buffer)

        # Create presentation and generate slides code
        service, presentation_id, presentation_title, use_template = create_presentation(code_client, credentials, instructions, template_id, email)
        code = generate_code_from_instructions(instructions, code_client, use_template)
        url, errors = run_generated_code(code_client, code, presentation_id, service, use_template)

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


@app.route('/error-stats', methods=['GET'])
def error_statistics():
    try:
        stats = get_error_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/reset-errors', methods=['POST'])
def reset_errors():
    try:
        reset_error_db()
        return jsonify({'success': True, 'message': 'Error database reset'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

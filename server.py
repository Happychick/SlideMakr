from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging
import os

# Only import what we absolutely need at startup
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__, static_folder='slidemakr.webflow')
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})

# Global variables for lazy loading
_slide_maker_module = None
_credentials = None
_code_client = None
_template_id = None


def get_slide_maker():
    """Lazy load slide_maker module only when needed"""
    global _slide_maker_module
    if _slide_maker_module is None:
        import slide_maker
        _slide_maker_module = slide_maker
    return _slide_maker_module


def get_credentials():
    """Lazy load credentials only when needed"""
    global _credentials
    if _credentials is None:
        slide_maker = get_slide_maker()
        _credentials = slide_maker.credentials
    return _credentials


def get_code_client():
    """Lazy load Anthropic client only when needed"""
    global _code_client
    if _code_client is None:
        slide_maker = get_slide_maker()
        _code_client = slide_maker.code_client
    return _code_client


def get_template_id():
    """Lazy load template ID only when needed"""
    global _template_id
    if _template_id is None:
        slide_maker = get_slide_maker()
        _template_id = slide_maker.template_id
    return _template_id


@app.route('/')
def index():
    # Handle Google Cloud Run health checks
    if request.headers.get('User-Agent', '').startswith('GoogleHC/'):
        return jsonify({'status': 'healthy'}), 200
    try:
        return send_from_directory('slidemakr.webflow', 'index.html')
    except Exception as e:
        logging.error(f"Error serving index.html: {str(e)}")
        return jsonify({'status': 'healthy'}), 200


@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('slidemakr.webflow', path)


@app.route('/generate', methods=['POST'])
def handle_generate():
    try:
        # Lazy load only when endpoint is called
        slide_maker = get_slide_maker()

        instructions = request.json.get('text')
        if not instructions:
            return jsonify({
                'success': False,
                'error': 'No text provided'
            }), 400

        service, presentation_id, presentation_title, use_template = slide_maker.create_presentation(
            get_code_client(), get_credentials(), instructions,
            get_template_id())
        url, errors = slide_maker.run_generated_code(get_code_client(),
                                                     instructions,
                                                     presentation_id, service,
                                                     use_template)

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
        # Lazy load audio processing modules only when needed
        import base64
        import tempfile
        from pydub import AudioSegment
        from io import BytesIO

        slide_maker = get_slide_maker()

        # Get base64 audio data from request
        audio_data = request.json['audio']

        audio_binary = base64.b64decode(audio_data.split(',')[1])

        # Convert to AudioSegment
        audio = AudioSegment.from_file(BytesIO(audio_binary))

        # Convert to WAV file for transcription
        wav_buffer = slide_maker.convert_audio_segment_to_wav(audio)

        # Transcribe audio
        instructions = slide_maker.transcribe_audio(wav_buffer)

        # Create presentation and generate slides using new 2-function workflow
        service, presentation_id, presentation_title, use_template = slide_maker.create_presentation(
            get_code_client(), get_credentials(), instructions,
            get_template_id())
        url, errors = slide_maker.run_generated_code(get_code_client(),
                                                     instructions,
                                                     presentation_id, service,
                                                     use_template)

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
        slide_maker = get_slide_maker()
        data = request.json
        presentation_id = data['presentation_id']
        email = data['email']
        slide_maker.share_presentation(presentation_id, email,
                                       get_credentials())
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/error-stats', methods=['GET'])
def error_statistics():
    try:
        slide_maker = get_slide_maker()
        stats = slide_maker.get_error_stats()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/reset-errors', methods=['POST'])
def reset_errors():
    try:
        slide_maker = get_slide_maker()
        slide_maker.reset_error_db()
        return jsonify({'success': True, 'message': 'Error database reset'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

logging.basicConfig(level=logging.DEBUG)
import base64
import tempfile
from pydub import AudioSegment
from io import BytesIO
from slide_maker import convert_audio_segment_to_wav, transcribe_audio, generate_code_from_instructions, create_presentation, run_generated_code, credentials, share_presentation, get_error_stats, reset_error_db, init_presentations_table, save_presentation, get_user_presentations, edit_existing_presentation

# Initialize both tables on startup
init_presentations_table()

app = Flask(__name__, static_folder='slidemakr.webflow')
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})


@app.route('/')
def index():
    # Handle Google Cloud Run health checks
    if request.headers.get('User-Agent', '').startswith('GoogleHC/'):
        return jsonify({'status': 'healthy'}), 200
    try:
        from flask import render_template_string
        
        # Read the HTML file
        with open('slidemakr.webflow/index.html', 'r') as f:
            html_content = f.read()
        
        # Get Replit auth headers
        user_id = request.headers.get('X-Replit-User-Id', '')
        user_name = request.headers.get('X-Replit-User-Name', '')
        
        # Render with auth data
        return render_template_string(html_content, user_id=user_id, user_name=user_name)
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
        name = data['name']
        
        # Share the presentation
        share_presentation(presentation_id, email, credentials)
        
        # Save presentation metadata
        save_presentation(presentation_id, name, email)
        
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

@app.route('/user-presentations', methods=['POST'])
def user_presentations():
    try:
        data = request.json
        email = data['email']
        presentations = get_user_presentations(email)
        return jsonify({'success': True, 'presentations': presentations})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/edit-presentation', methods=['POST'])
def edit_presentation():
    try:
        data = request.json
        presentation_id = data['presentation_id']
        instructions = data.get('text')
        audio_data = data.get('audio')
        
        # Handle audio or text input
        if audio_data:
            # Convert audio to instructions
            audio_binary = base64.b64decode(audio_data.split(',')[1])
            audio = AudioSegment.from_file(BytesIO(audio_binary))
            wav_buffer = convert_audio_segment_to_wav(audio)
            instructions = transcribe_audio(wav_buffer)
        
        if not instructions:
            return jsonify({'success': False, 'error': 'No instructions provided'}), 400
            
        # Edit the existing presentation
        url, errors = edit_existing_presentation(presentation_id, instructions, credentials)
        
        return jsonify({
            'success': True,
            'presentation_url': url,
            'presentation_id': presentation_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

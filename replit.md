# SlideMakr

## Overview

SlideMakr is a voice-and-text-to-presentation application that allows users to create Google Slides presentations using natural language instructions. Users can either speak or type their presentation requirements, and the system uses AI (OpenAI Whisper for transcription, Anthropic Claude for intent parsing) to generate corresponding Google Slides API requests that build the presentation automatically.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend
- **Technology**: Static HTML/CSS/JS built with Webflow
- **Location**: `slidemakr.webflow/` directory
- **Key Features**:
  - Audio recording interface (`recorder.js`) for voice input
  - Text input form (`text-handler.js`) for typed instructions
  - Mobile-responsive design with touch support
- **Design Pattern**: Progressive enhancement with loading indicator and graceful degradation

### Backend
- **Technology**: Flask (Python) with CORS support
- **Entry Point**: `server.py`
- **Pattern**: Lazy loading for expensive imports (Google services, AI clients, Whisper model)
- **Core Logic**: `slide_maker_rag.py` (production-optimized) and `slide_maker.py` (original implementation)

### AI Pipeline
1. **Speech-to-Text**: OpenAI Whisper for audio transcription
2. **Intent Recognition**: Anthropic Claude parses natural language into structured intents
3. **API Generation**: Intent-to-API mapping converts intents into Google Slides API requests
4. **Execution**: Batched API calls to Google Slides

### Data Storage
- **Database**: PostgreSQL (via `psycopg2`)
- **Connection**: Environment variable `DATABASE_URL`
- **Key Tables**:
  - `intent_to_api`: Maps intent types to Google Slides API templates
  - `presentations`: Stores presentation metadata and audio paths
- **Caching Strategy**: In-memory caches for intents, layouts, and templates to reduce database queries

### Performance Optimizations
- Lazy imports throughout to improve cold start time
- Cached intent database (load once per process)
- Cached layouts per presentation
- Batched template fetching (single query instead of multiple)

## External Dependencies

### APIs & Services
- **Google Slides API**: Core presentation creation and manipulation
- **Google Service Account**: Authentication via `slide-makr-generate@slidemakr.iam.gserviceaccount.com`
- **OpenAI Whisper**: Local speech-to-text transcription
- **Anthropic Claude**: Natural language processing for intent extraction
- **OpenAI API**: Additional AI capabilities

### Database
- **PostgreSQL**: Primary data store accessed via `psycopg2-binary`
- **Connection**: Configured through `DATABASE_URL` environment variable

### Key Python Packages
- `flask` / `flask-cors`: Web server
- `google-api-python-client` / `google-auth`: Google API integration
- `openai-whisper`: Speech recognition
- `anthropic` / `openai`: AI clients
- `pydub` / `soundfile`: Audio processing
- `python-dotenv`: Environment configuration

### Environment Variables Required
- `DATABASE_URL`: PostgreSQL connection string
- Google service account credentials (JSON or environment-based)
- Anthropic API key
- OpenAI API key
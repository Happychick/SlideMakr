"""
SlideMakr - Firestore Data Layer

Replaces the PostgreSQL databases from the Replit version.
Four collections:
- presentations: Track every presentation created
- slide_errors: Log every batchUpdate error (for learning/retry)
- audio_log: Log voice interactions + interruptions
- user_memory: Basic preference logging
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================================
# FIRESTORE CLIENT (lazy init)
# ============================================================================

_firestore_client = None
_firestore_init_attempted = False


def _get_db():
    """Get Firestore client (lazy initialization)."""
    global _firestore_client, _firestore_init_attempted
    if not _firestore_init_attempted:
        _firestore_init_attempted = True
        try:
            from google.cloud import firestore
            project = os.getenv('GOOGLE_CLOUD_PROJECT', 'slidemakr')
            _firestore_client = firestore.Client(project=project)
            logging.info(f"Firestore connected (project: {project})")
        except Exception as e:
            logging.info(f"Firestore not available, using in-memory fallback. ({type(e).__name__})")
            _firestore_client = None
    return _firestore_client


# ============================================================================
# IN-MEMORY FALLBACK (for local dev without Firestore)
# ============================================================================

_memory_store = {
    'presentations': [],
    'slide_errors': [],
    'audio_log': [],
    'user_memory': {},
    'users': {}
}


def _is_firestore_available() -> bool:
    """Check if Firestore is available."""
    db = _get_db()
    return db is not None


# ============================================================================
# PRESENTATIONS COLLECTION
# ============================================================================

def save_presentation(
    presentation_id: str,
    title: str,
    instructions: str,
    user_id: str = "anonymous",
    url: str = None,
    status: str = "created",
    request_count: int = 0,
    started_at: float = None
) -> None:
    """Save presentation metadata."""
    doc = {
        'presentation_id': presentation_id,
        'title': title,
        'instructions': instructions,
        'user_id': user_id,
        'url': url or f'https://docs.google.com/presentation/d/{presentation_id}/edit',
        'status': status,
        'request_count': request_count,
        'created_at': datetime.utcnow().isoformat(),
        'started_at': started_at,
    }

    db = _get_db()
    if db:
        try:
            db.collection('presentations').document(presentation_id).set(doc)
            logging.info(f"Saved presentation {presentation_id} to Firestore")
        except Exception as e:
            logging.error(f"Firestore save_presentation error: {e}")
    else:
        _memory_store['presentations'].append(doc)
        logging.info(f"Saved presentation {presentation_id} to memory")


def update_presentation_status(
    presentation_id: str,
    status: str,
    request_count: int = None,
    email: str = None
) -> None:
    """Update presentation status after creation."""
    updates = {
        'status': status,
        'completed_at': datetime.utcnow().isoformat()
    }
    if request_count is not None:
        updates['request_count'] = request_count
    if email:
        updates['email'] = email

    db = _get_db()
    if db:
        try:
            db.collection('presentations').document(presentation_id).update(updates)
        except Exception as e:
            logging.error(f"Firestore update error: {e}")
    else:
        # In-memory: find and update
        for p in _memory_store['presentations']:
            if p['presentation_id'] == presentation_id:
                p.update(updates)
                break


# ============================================================================
# SLIDE ERRORS COLLECTION
# ============================================================================

def record_error(
    presentation_id: str,
    request_json: str,
    error_message: str,
    was_retried: bool = False,
    retry_succeeded: bool = False
) -> None:
    """Log a batchUpdate error."""
    doc = {
        'presentation_id': presentation_id,
        'request_json': request_json,
        'error_message': error_message,
        'was_retried': was_retried,
        'retry_succeeded': retry_succeeded,
        'created_at': datetime.utcnow().isoformat()
    }

    db = _get_db()
    if db:
        try:
            db.collection('slide_errors').add(doc)
        except Exception as e:
            logging.error(f"Firestore record_error: {e}")
    else:
        _memory_store['slide_errors'].append(doc)


def record_fix(
    presentation_id: str,
    original_request: str,
    fixed_request: str
) -> None:
    """Record a successful retry/fix for a previously failed request."""
    db = _get_db()
    if db:
        try:
            # Find the error doc and update it
            errors = db.collection('slide_errors') \
                .where('presentation_id', '==', presentation_id) \
                .where('request_json', '==', original_request) \
                .where('retry_succeeded', '==', False) \
                .limit(1) \
                .get()

            for error_doc in errors:
                error_doc.reference.update({
                    'was_retried': True,
                    'retry_succeeded': True,
                    'fixed_request': fixed_request,
                    'fixed_at': datetime.utcnow().isoformat()
                })
        except Exception as e:
            logging.error(f"Firestore record_fix: {e}")


def get_error_stats(limit: int = 100) -> List[Dict]:
    """Get recent errors for debugging."""
    db = _get_db()
    if db:
        try:
            docs = db.collection('slide_errors') \
                .order_by('created_at', direction='DESCENDING') \
                .limit(limit) \
                .get()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logging.error(f"Firestore get_error_stats: {e}")
            return []
    else:
        return _memory_store['slide_errors'][-limit:]


# ============================================================================
# AUDIO LOG COLLECTION
# ============================================================================

def log_audio_interaction(
    user_id: str,
    session_id: str,
    transcript_user: str = "",
    transcript_agent: str = "",
    was_interrupted: bool = False
) -> None:
    """Log a voice interaction turn."""
    doc = {
        'user_id': user_id,
        'session_id': session_id,
        'transcript_user': transcript_user,
        'transcript_agent': transcript_agent,
        'was_interrupted': was_interrupted,
        'created_at': datetime.utcnow().isoformat()
    }

    db = _get_db()
    if db:
        try:
            db.collection('audio_log').add(doc)
        except Exception as e:
            logging.error(f"Firestore log_audio: {e}")
    else:
        _memory_store['audio_log'].append(doc)


# ============================================================================
# USER MEMORY COLLECTION
# ============================================================================

def save_user_memory(
    user_id: str,
    session_summary: str,
    slide_types_used: List[str] = None,
    preferences_noted: str = ""
) -> None:
    """Save user interaction memory for preference learning."""
    doc = {
        'session_summary': session_summary,
        'slide_types_used': slide_types_used or [],
        'preferences_noted': preferences_noted,
        'created_at': datetime.utcnow().isoformat()
    }

    db = _get_db()
    if db:
        try:
            db.collection('user_memory').document(user_id) \
                .collection('interactions').add(doc)
        except Exception as e:
            logging.error(f"Firestore save_memory: {e}")
    else:
        if user_id not in _memory_store['user_memory']:
            _memory_store['user_memory'][user_id] = []
        _memory_store['user_memory'][user_id].append(doc)


def get_user_memory(user_id: str, limit: int = 5) -> List[Dict]:
    """Get recent user memories for context."""
    db = _get_db()
    if db:
        try:
            docs = db.collection('user_memory').document(user_id) \
                .collection('interactions') \
                .order_by('created_at', direction='DESCENDING') \
                .limit(limit) \
                .get()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logging.error(f"Firestore get_memory: {e}")
            return []
    else:
        memories = _memory_store['user_memory'].get(user_id, [])
        return memories[-limit:]


# ============================================================================
# USERS COLLECTION
# ============================================================================

def save_user(
    google_id: str,
    email: str,
    name: str,
    picture: str = "",
    refresh_token: str = "",
) -> None:
    """Create or update a user record."""
    doc = {
        'google_id': google_id,
        'email': email,
        'name': name,
        'picture': picture,
        'updated_at': datetime.utcnow().isoformat(),
    }
    if refresh_token:
        doc['refresh_token'] = refresh_token

    db = _get_db()
    if db:
        try:
            ref = db.collection('users').document(google_id)
            existing = ref.get()
            if existing.exists:
                ref.update(doc)
            else:
                doc['created_at'] = datetime.utcnow().isoformat()
                ref.set(doc)
            logging.info(f"Saved user {email}")
        except Exception as e:
            logging.error(f"Firestore save_user error: {e}")
    else:
        doc.setdefault('created_at', datetime.utcnow().isoformat())
        _memory_store['users'][google_id] = doc


def get_user(google_id: str) -> Optional[Dict]:
    """Get a user by Google ID."""
    db = _get_db()
    if db:
        try:
            doc = db.collection('users').document(google_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logging.error(f"Firestore get_user error: {e}")
            return None
    else:
        return _memory_store['users'].get(google_id)


def get_user_presentations(user_id: str, limit: int = 50) -> List[Dict]:
    """Get presentations created by a user."""
    db = _get_db()
    if db:
        try:
            docs = db.collection('presentations') \
                .where('user_id', '==', user_id) \
                .order_by('created_at', direction='DESCENDING') \
                .limit(limit) \
                .get()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logging.error(f"Firestore get_user_presentations error: {e}")
            return []
    else:
        results = [
            p for p in _memory_store['presentations']
            if p.get('user_id') == user_id
        ]
        return sorted(results, key=lambda x: x.get('created_at', ''), reverse=True)[:limit]

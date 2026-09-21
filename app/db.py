"""
SlideMakr - Firestore Data Layer

Collections:
- presentations: Track every presentation created
- presentation_metrics: Timing, quality, and error stats per creation
- slide_errors: Log every batchUpdate error (for learning/retry)
- audio_log: Log voice interactions + interruptions
- user_memory: Basic preference logging
- users: Google OAuth user records
"""

import json
import os
import logging
import secrets
import threading
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
    'presentation_metrics': [],
    'slide_errors': [],
    'audio_log': [],
    'user_memory': {},
    'users': {},
    'brand_cache': {},
    'processed_checkout_sessions': set(),
    'addon_tokens': {},
}

# Guards check-and-mark + credit for the in-memory checkout webhook path so
# two concurrent deliveries of the same session can't both see "not yet
# processed" and double-credit the user.
_checkout_credit_lock = threading.Lock()


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
# PRESENTATION METRICS COLLECTION
# ============================================================================

def save_presentation_metrics(
    presentation_id: str,
    user_id: str,
    instructions: str,
    slide_count: int,
    request_count: int,
    success_count: int,
    error_count: int,
    duration_seconds: float,
    tool_timings: Dict[str, float] = None,
    errors: List[Dict] = None,
    instruction_contract: Dict[str, Any] = None,
    adherence_result: Dict[str, Any] = None,
) -> None:
    """Save metrics for a presentation creation.

    This is the core data for the quality system — every creation gets tracked.
    """
    error_rate = error_count / max(request_count, 1)

    doc = {
        'presentation_id': presentation_id,
        'user_id': user_id,
        'instructions': instructions[:500],  # Truncate long instructions
        'slide_count': slide_count,
        'request_count': request_count,
        'success_count': success_count,
        'error_count': error_count,
        'error_rate': round(error_rate, 4),
        'duration_seconds': round(duration_seconds, 2),
        'tool_timings': tool_timings or {},
        'created_at': datetime.utcnow().isoformat(),
    }
    if instruction_contract:
        doc['instruction_contract'] = instruction_contract
    if adherence_result:
        doc['instruction_adherence_score'] = adherence_result.get('instruction_adherence_score', 0)
        doc['missing_element_errors'] = adherence_result.get('missing_element_errors', [])
        doc['wrong_slide_errors'] = adherence_result.get('wrong_slide_errors', [])
        doc['unasked_clarification_count'] = adherence_result.get('unasked_clarification_count', 0)

    # Log errors to slide_errors collection for pattern analysis
    if errors:
        for err in errors:
            record_error(
                presentation_id=presentation_id,
                request_json=json.dumps(err.get('request', {}))[:2000],
                error_message=str(err.get('error', ''))[:500],
            )

    db = _get_db()
    if db:
        try:
            db.collection('presentation_metrics').document(presentation_id).set(doc)
            logging.info(f"Saved metrics for {presentation_id}: "
                        f"{duration_seconds:.1f}s, {error_count}/{request_count} errors")
        except Exception as e:
            logging.error(f"Firestore save_metrics error: {e}")
    else:
        _memory_store['presentation_metrics'].append(doc)
        logging.info(f"Saved metrics for {presentation_id} to memory")


def get_metrics_summary(limit: int = 50) -> Dict[str, Any]:
    """Get aggregated metrics across recent presentations.

    Returns averages, totals, and trend data for the quality dashboard.
    """
    db = _get_db()
    metrics_list = []

    if db:
        try:
            docs = db.collection('presentation_metrics') \
                .order_by('created_at', direction='DESCENDING') \
                .limit(limit) \
                .get()
            metrics_list = [doc.to_dict() for doc in docs]
        except Exception as e:
            logging.error(f"Firestore get_metrics_summary (order_by failed, trying without): {e}")
            try:
                # Fallback: query without order_by (no index needed)
                docs = db.collection('presentation_metrics').limit(limit).get()
                metrics_list = [doc.to_dict() for doc in docs]
            except Exception as e2:
                logging.error(f"Firestore get_metrics_summary fallback: {e2}")
    else:
        metrics_list = _memory_store['presentation_metrics'][-limit:]

    if not metrics_list:
        return {
            'total_presentations': 0,
            'avg_duration_seconds': 0,
            'avg_error_rate': 0,
            'avg_slide_count': 0,
            'total_errors': 0,
            'total_requests': 0,
            'recent': [],
        }

    total = len(metrics_list)
    avg_duration = sum(m.get('duration_seconds', 0) for m in metrics_list) / total
    avg_error_rate = sum(m.get('error_rate', 0) for m in metrics_list) / total
    avg_slides = sum(m.get('slide_count', 0) for m in metrics_list) / total
    adherence_values = [
        m.get('instruction_adherence_score')
        for m in metrics_list
        if m.get('instruction_adherence_score') is not None
    ]
    avg_adherence = (
        sum(adherence_values) / len(adherence_values)
        if adherence_values else 0
    )
    total_errors = sum(m.get('error_count', 0) for m in metrics_list)
    total_requests = sum(m.get('request_count', 0) for m in metrics_list)

    return {
        'total_presentations': total,
        'avg_duration_seconds': round(avg_duration, 2),
        'avg_error_rate': round(avg_error_rate, 4),
        'avg_slide_count': round(avg_slides, 1),
        'avg_instruction_adherence_score': round(avg_adherence, 4),
        'total_errors': total_errors,
        'total_requests': total_requests,
        'overall_error_rate': round(total_errors / max(total_requests, 1), 4),
        'recent': metrics_list[:10],
    }


def get_error_patterns(limit: int = 200) -> List[Dict]:
    """Analyze error patterns across all presentations.

    Groups errors by error message pattern, counts occurrences,
    and identifies which patterns have auto-fixes vs not.
    """
    errors = get_error_stats(limit=limit)

    # Group by error message pattern (first 100 chars as key)
    patterns = {}
    for err in errors:
        msg = err.get('error_message', '')[:100]
        if msg not in patterns:
            patterns[msg] = {
                'pattern': msg,
                'count': 0,
                'has_auto_fix': err.get('retry_succeeded', False),
                'first_seen': err.get('created_at', ''),
                'last_seen': err.get('created_at', ''),
                'example_request': err.get('request_json', '')[:300],
            }
        patterns[msg]['count'] += 1
        patterns[msg]['last_seen'] = err.get('created_at', '')
        if err.get('retry_succeeded'):
            patterns[msg]['has_auto_fix'] = True

    # Sort by count descending
    sorted_patterns = sorted(patterns.values(), key=lambda x: x['count'], reverse=True)
    return sorted_patterns


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
    access_token: str = "",
    token_expiry: str = "",
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
    if access_token:
        doc['access_token'] = access_token
    if token_expiry:
        doc['token_expiry'] = token_expiry

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


def get_user_credits(google_id: str) -> Dict[str, Any]:
    """Return credit balance and first-free-deck status for a user."""
    user = get_user(google_id) or {}
    return {
        'credits': int(user.get('credits', 0) or 0),
        'first_deck_used': bool(user.get('first_deck_used', False)),
    }


def add_user_credits(google_id: str, credits: int) -> Dict[str, Any]:
    """Add purchased credits to a user account.

    Uses set(..., merge=True) with Increment rather than update() so this
    works even for a user id Firestore has never seen a doc for (e.g. a
    webhook that races ahead of the user's first /generate call).
    """
    credits = int(credits)
    db = _get_db()
    if db:
        try:
            from google.cloud import firestore
            ref = db.collection('users').document(google_id)
            ref.set({
                'google_id': google_id,
                'credits': firestore.Increment(credits),
                'updated_at': datetime.utcnow().isoformat(),
            }, merge=True)
            return get_user_credits(google_id)
        except Exception as e:
            logging.error(f"Firestore add_user_credits error: {e}")
            return {'credits': 0, 'first_deck_used': False}
    user = _memory_store['users'].setdefault(google_id, {'google_id': google_id})
    user['credits'] = int(user.get('credits', 0) or 0) + credits
    user['updated_at'] = datetime.utcnow().isoformat()
    return get_user_credits(google_id)


def consume_generation_credit(google_id: str) -> Dict[str, Any]:
    """Use the first-free deck or deduct one credit for generation.

    The returned `reason` (`first_deck_free` | `credit_used`) is the caller's
    receipt for what was consumed -- pass it back to
    `refund_generation_credit` on failure so the refund undoes exactly what
    this call did, nothing else.
    """
    db = _get_db()
    if db:
        try:
            ref = db.collection('users').document(google_id)
            doc = ref.get()
            data = doc.to_dict() if doc.exists else {'google_id': google_id}
            credits = int(data.get('credits', 0) or 0)
            if not data.get('first_deck_used', False):
                # Write only the fields that change -- never the whole `data`
                # snapshot, which could clobber a concurrent credits Increment
                # (e.g. a webhook landing between our read and this write).
                ref.set({
                    'first_deck_used': True,
                    'updated_at': datetime.utcnow().isoformat(),
                }, merge=True)
                return {'allowed': True, 'reason': 'first_deck_free'}
            if credits <= 0:
                return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}
            from google.cloud import firestore
            ref.set({
                'credits': firestore.Increment(-1),
                'updated_at': datetime.utcnow().isoformat(),
            }, merge=True)
            return {'allowed': True, 'reason': 'credit_used', 'credits': credits - 1}
        except Exception as e:
            logging.error(f"Firestore consume_generation_credit error: {e}")
            return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}

    user = _memory_store['users'].setdefault(google_id, {'google_id': google_id})
    if not user.get('first_deck_used', False):
        user['first_deck_used'] = True
        user['updated_at'] = datetime.utcnow().isoformat()
        return {'allowed': True, 'reason': 'first_deck_free'}
    credits = int(user.get('credits', 0) or 0)
    if credits <= 0:
        return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}
    user['credits'] = credits - 1
    user['updated_at'] = datetime.utcnow().isoformat()
    return {'allowed': True, 'reason': 'credit_used', 'credits': credits - 1}


def consume_edit_credit(google_id: str) -> Dict[str, Any]:
    """Deduct one credit for an edit turn.

    Unlike `consume_generation_credit`, edits never draw on the free first
    deck -- that free deck is for creation only. The returned `reason` is
    always `credit_used` when allowed, kept in the response so callers can
    treat both consume functions uniformly when refunding.
    """
    db = _get_db()
    if db:
        try:
            ref = db.collection('users').document(google_id)
            doc = ref.get()
            data = doc.to_dict() if doc.exists else {'google_id': google_id}
            credits = int(data.get('credits', 0) or 0)
            if credits <= 0:
                return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}
            from google.cloud import firestore
            ref.set({
                'credits': firestore.Increment(-1),
                'updated_at': datetime.utcnow().isoformat(),
            }, merge=True)
            return {'allowed': True, 'reason': 'credit_used', 'credits': credits - 1}
        except Exception as e:
            logging.error(f"Firestore consume_edit_credit error: {e}")
            return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}

    user = _memory_store['users'].setdefault(google_id, {'google_id': google_id})
    credits = int(user.get('credits', 0) or 0)
    if credits <= 0:
        return {'allowed': False, 'reason': 'checkout_required', 'credits': 0}
    user['credits'] = credits - 1
    user['updated_at'] = datetime.utcnow().isoformat()
    return {'allowed': True, 'reason': 'credit_used', 'credits': credits - 1}


def refund_generation_credit(google_id: str, reason: str) -> Dict[str, Any]:
    """Undo exactly one consumption, tied to what actually happened.

    `reason` must be the `reason` value returned by the `consume_*` call this
    refund is undoing -- `credit_used` adds one credit back, `first_deck_free`
    restores the free-deck flag. Any other value (missing, None, or an
    already-refunded call) does nothing and is logged, rather than silently
    granting a credit that was never consumed.
    """
    if reason not in ('credit_used', 'first_deck_free'):
        logging.warning(
            f"refund_generation_credit: no-op for google_id={google_id}, "
            f"unrecognized reason={reason!r}"
        )
        return get_user_credits(google_id)

    db = _get_db()
    if db:
        try:
            ref = db.collection('users').document(google_id)
            if reason == 'first_deck_free':
                ref.set({
                    'first_deck_used': False,
                    'updated_at': datetime.utcnow().isoformat(),
                }, merge=True)
            else:
                from google.cloud import firestore
                ref.set({
                    'credits': firestore.Increment(1),
                    'updated_at': datetime.utcnow().isoformat(),
                }, merge=True)
            return get_user_credits(google_id)
        except Exception as e:
            logging.error(f"Firestore refund_generation_credit error: {e}")
            return get_user_credits(google_id)

    user = _memory_store['users'].setdefault(google_id, {'google_id': google_id})
    if reason == 'first_deck_free':
        user['first_deck_used'] = False
    else:
        user['credits'] = int(user.get('credits', 0) or 0) + 1
    user['updated_at'] = datetime.utcnow().isoformat()
    return get_user_credits(google_id)


def is_checkout_session_processed(session_id: str) -> bool:
    """Peek at whether a checkout session has already been credited, without
    marking it."""
    db = _get_db()
    if db:
        try:
            ref = db.collection('processed_checkout_sessions').document(session_id)
            return ref.get().exists
        except Exception as e:
            logging.error(f"Firestore is_checkout_session_processed error: {e}")
            return False
    processed = _memory_store.setdefault('processed_checkout_sessions', set())
    return session_id in processed


def credit_checkout_session_once(session_id: str, user_id: str, credits: int) -> bool:
    """Atomically credit a checkout session exactly once.

    Two concurrent deliveries of the same Stripe webhook event must never
    both credit the user. The "already processed" marker and the credits
    Increment are written together -- in a single Firestore transaction, or
    (for local/dev use without Firestore) under a process-wide lock around
    the in-memory check-and-write.

    Deliberately does NOT catch exceptions from the Firestore transaction:
    if the transaction fails, this must raise so the webhook handler returns
    a 500 and Stripe redelivers the event. Swallowing the error here and
    returning False would look identical to "already processed" to the
    caller, which turns into an HTTP 200 -- Stripe stops retrying and the
    purchased credits are gone for good. The transaction marker makes this
    safe to retry: a redelivery either lands the transaction fresh (nothing
    was committed on the failed attempt) or finds the marker already set
    (if the failure happened after commit, e.g. the response was lost) and
    correctly reports "already processed" instead of double-crediting.

    Returns True the first time this session_id is credited, False if it was
    already processed (or credited by a concurrent call).
    """
    db = _get_db()
    if db:
        from google.cloud import firestore

        session_ref = db.collection('processed_checkout_sessions').document(session_id)
        user_ref = db.collection('users').document(user_id)
        transaction = db.transaction()

        @firestore.transactional
        def _run(transaction):
            snapshot = session_ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.set(session_ref, {
                'session_id': session_id,
                'created_at': datetime.utcnow().isoformat(),
            })
            transaction.set(user_ref, {
                'google_id': user_id,
                'credits': firestore.Increment(credits),
                'updated_at': datetime.utcnow().isoformat(),
            }, merge=True)
            return True

        return _run(transaction)

    with _checkout_credit_lock:
        processed = _memory_store.setdefault('processed_checkout_sessions', set())
        if session_id in processed:
            return False
        processed.add(session_id)
        user = _memory_store['users'].setdefault(user_id, {'google_id': user_id})
        user['credits'] = int(user.get('credits', 0) or 0) + int(credits)
        user['updated_at'] = datetime.utcnow().isoformat()
        return True


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


# ============================================================================
# BRAND CACHE COLLECTION
# ============================================================================

def get_cached_brand(company_name: str) -> Optional[Dict]:
    """Get cached brand data for a company (avoids repeated web searches)."""
    key = company_name.lower().strip()
    db = _get_db()
    if db:
        try:
            doc = db.collection('brand_cache').document(key).get()
            if doc.exists:
                data = doc.to_dict()
                logging.info(f"Brand cache hit: {company_name}")
                return data
        except Exception as e:
            logging.error(f"Firestore get_cached_brand: {e}")
    else:
        return _memory_store['brand_cache'].get(key)
    return None


def save_brand_cache(
    company_name: str,
    branding_text: str,
    primary_color: str = "",
    logo_url: str = "",
) -> None:
    """Cache brand data for a company."""
    key = company_name.lower().strip()
    doc = {
        'company_name': company_name,
        'branding_text': branding_text[:3000],
        'primary_color': primary_color,
        'logo_url': logo_url,
        'cached_at': datetime.utcnow().isoformat(),
    }
    db = _get_db()
    if db:
        try:
            db.collection('brand_cache').document(key).set(doc)
            logging.info(f"Cached brand for {company_name}")
        except Exception as e:
            logging.error(f"Firestore save_brand_cache: {e}")
    else:
        _memory_store['brand_cache'][key] = doc


# ============================================================================
# GOOGLE SLIDES ADD-ON TOKENS (short-lived, Firestore-backed)
# ============================================================================

_ADDON_TOKEN_TTL_SECONDS = 15 * 60

_addon_token_lock = threading.Lock()


def issue_addon_token(user_id: str, state: str = "") -> str:
    """Issue a short-lived token mapping to `user_id` for Slides add-on calls.

    `state` is the per-connect-attempt nonce generated by the sidebar (see
    `claim_addon_token`) -- binding it to the token at issue time is what
    lets `/api/addon-token/claim` reject a token harvested by a third party
    that opened `/addon/connect` on a signed-in victim without knowing the
    state only the legitimate sidebar instance holds.

    Backed by the `addon_tokens` Firestore collection (doc id = token) so a
    Cloud Run restart doesn't drop live tokens; falls back to an in-memory
    dict for local dev. Expired tokens are purged on every issue so the
    store doesn't grow unbounded.
    """
    purge_expired_addon_tokens()
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow().timestamp() + _ADDON_TOKEN_TTL_SECONDS
    db = _get_db()
    if db:
        try:
            db.collection('addon_tokens').document(token).set({
                'user_id': user_id,
                'expires_at': expires_at,
                'created_at': datetime.utcnow().isoformat(),
                'state': state,
                'claimed': False,
            })
            return token
        except Exception as e:
            logging.error(f"Firestore issue_addon_token: {e}")
    with _addon_token_lock:
        _memory_store['addon_tokens'][token] = {
            'user_id': user_id,
            'expires_at': expires_at,
            'state': state,
            'claimed': False,
        }
    return token


def claim_addon_token(token: str, state: str) -> bool:
    """Claim a freshly-issued token by presenting the state it was bound to.

    Single use: the first caller with the matching, non-expired state wins;
    every later claim (right state or wrong) fails once `claimed` is set.
    This is what makes a `postMessage`d token useless to anyone but the
    sidebar instance that generated the state -- an attacker who tricks a
    victim into opening `/addon/connect` gets a token minted for the
    victim, but has no way to learn the state the victim's own sidebar
    generated, so `claim` always 403s for them.
    """
    if not token or not state:
        return False
    now = datetime.utcnow().timestamp()
    db = _get_db()
    if db:
        from google.cloud import firestore as _firestore

        doc_ref = db.collection('addon_tokens').document(token)
        transaction = db.transaction()

        @_firestore.transactional
        def _run(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            data = snapshot.to_dict() or {}
            if data.get('claimed'):
                return False
            if data.get('expires_at', 0) < now:
                return False
            if data.get('state') != state:
                return False
            transaction.update(doc_ref, {'claimed': True})
            return True

        try:
            return _run(transaction)
        except Exception as e:
            logging.error(f"Firestore claim_addon_token: {e}")
            return False
    with _addon_token_lock:
        data = _memory_store['addon_tokens'].get(token)
        if not data:
            return False
        if data.get('claimed'):
            return False
        if data['expires_at'] < now:
            return False
        if data.get('state') != state:
            return False
        data['claimed'] = True
        return True


def resolve_addon_token(token: str) -> Optional[str]:
    """Return the user_id for a live token, or None if missing/expired."""
    if not token:
        return None
    now = datetime.utcnow().timestamp()
    db = _get_db()
    if db:
        try:
            snap = db.collection('addon_tokens').document(token).get()
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            if data.get('expires_at', 0) < now:
                return None
            return data.get('user_id')
        except Exception as e:
            logging.error(f"Firestore resolve_addon_token: {e}")
            return None
    with _addon_token_lock:
        data = _memory_store['addon_tokens'].get(token)
        if not data:
            return None
        if data['expires_at'] < now:
            return None
        return data['user_id']


def purge_expired_addon_tokens() -> None:
    """Delete expired add-on tokens. Called on every issue to bound growth."""
    now = datetime.utcnow().timestamp()
    db = _get_db()
    if db:
        try:
            from google.cloud import firestore as _firestore

            expired = db.collection('addon_tokens').where(
                filter=_firestore.FieldFilter('expires_at', '<', now)
            ).stream()
            for doc in expired:
                doc.reference.delete()
        except Exception as e:
            logging.error(f"Firestore purge_expired_addon_tokens: {e}")
        return
    with _addon_token_lock:
        expired_keys = [
            k for k, v in _memory_store['addon_tokens'].items()
            if v['expires_at'] < now
        ]
        for k in expired_keys:
            del _memory_store['addon_tokens'][k]

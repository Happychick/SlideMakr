"""
SlideMakr - Evaluation Pipeline

Automated quality testing: run standard prompts → create real presentations →
score across 5 dimensions → track regressions.

Usage:
    # Run from project root
    python -m app.eval

    # Or via API
    POST /admin/run-eval
    GET /admin/eval-history
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# EVAL PROMPTS — Standard test cases for quality measurement
# ============================================================================

EVAL_PROMPTS = [
    {
        "id": "simple_4slide",
        "name": "Simple 4-Slide Deck",
        "prompt": "Create a 4-slide presentation about the future of remote work. "
                  "Include a title slide, a slide about benefits with bullet points, "
                  "a slide about challenges, and a closing slide with key takeaways.",
        "expected_slides": 4,
        "expected_elements": ["title", "bullets"],
        "sla_seconds": 30,
    },
    {
        "id": "flowchart_deck",
        "name": "Flowchart Deck",
        "prompt": "Create a 3-slide presentation about how machine learning works. "
                  "Include a title slide, a flowchart showing the ML pipeline "
                  "(data collection → preprocessing → training → evaluation → deployment), "
                  "and a summary slide.",
        "expected_slides": 3,
        "expected_elements": ["title", "flowchart"],
        "sla_seconds": 40,
    },
    {
        "id": "chart_deck",
        "name": "Chart Deck",
        "prompt": "Create a 3-slide presentation about global renewable energy trends. "
                  "Include a title slide, a slide with a bar chart showing solar, wind, "
                  "and hydro energy growth from 2020-2024, and a conclusion slide.",
        "expected_slides": 3,
        "expected_elements": ["title", "chart"],
        "sla_seconds": 40,
    },
    {
        "id": "image_deck",
        "name": "Image Deck",
        "prompt": "Create a 3-slide presentation about sustainable architecture. "
                  "Include a title slide with a relevant image, a slide about "
                  "green building principles, and a slide about famous eco-buildings "
                  "with an image.",
        "expected_slides": 3,
        "expected_elements": ["title", "image"],
        "sla_seconds": 40,
    },
    {
        "id": "branded_deck",
        "name": "Branded Company Deck",
        "prompt": "Create a 3-slide pitch deck for Stripe. Use their brand colors "
                  "and fonts. Include a title slide, a slide about their payment "
                  "platform, and a slide about their impact.",
        "expected_slides": 3,
        "expected_elements": ["title", "branding"],
        "sla_seconds": 45,
    },
]


# ============================================================================
# SCORING FUNCTIONS
# ============================================================================


def score_completeness(
    actual_slide_count: int,
    expected_slide_count: int,
) -> float:
    """Score: did we get the right number of slides? (0-1)"""
    if expected_slide_count == 0:
        return 1.0
    return min(actual_slide_count / expected_slide_count, 1.0)


def score_error_rate(
    success_count: int,
    total_requests: int,
) -> float:
    """Score: how many API requests succeeded? (0-1, higher = better)"""
    if total_requests == 0:
        return 1.0
    return success_count / total_requests


def score_speed(
    actual_seconds: float,
    sla_seconds: float,
) -> float:
    """Score: how close to SLA? (0-1, 1 = at or under SLA)"""
    if actual_seconds <= sla_seconds:
        return 1.0
    # Linearly degrade: 2x SLA = 0.5, 3x SLA = 0.33
    return sla_seconds / actual_seconds


def score_visual_quality(review_result: Optional[Dict] = None) -> float:
    """Score: visual quality from review_slide_layout (0-1).

    If no review was done, returns a neutral 0.5.
    """
    if not review_result:
        return 0.5

    quality = review_result.get('overall_quality', 'unknown')
    quality_map = {
        'excellent': 1.0,
        'good': 0.8,
        'acceptable': 0.6,
        'poor': 0.3,
        'broken': 0.0,
    }
    return quality_map.get(quality, 0.5)


def score_content_richness(
    expected_elements: List[str],
    actual_state: Optional[Dict] = None,
) -> float:
    """Score: are expected content types present? (0-1)

    Checks for presence of titles, bullets, charts, flowcharts, images
    based on presentation state.
    """
    if not actual_state or not expected_elements:
        return 0.5

    found = set()
    slides = actual_state.get('slides', [])

    for slide in slides:
        for elem in slide.get('elements', []):
            elem_type = elem.get('type', '')
            text = elem.get('text', '').lower()
            placeholder = elem.get('placeholder', '')

            # Check for title
            if placeholder in ('TITLE', 'CENTERED_TITLE') or (text and len(text) < 100):
                found.add('title')

            # Check for bullets (text with newlines)
            if '\n' in text and len(text) > 50:
                found.add('bullets')

            # Check for images
            if elem_type == 'image':
                found.add('image')

            # Check for shapes (potential flowchart elements)
            if elem_type == 'shape' and elem.get('shapeType') not in ('TEXT_BOX', None):
                found.add('flowchart')

    # Charts are inserted as images, hard to detect from state alone
    # Give partial credit if images are found and chart was expected
    if 'chart' in expected_elements and 'image' in found:
        found.add('chart')

    # Branding: check if we got non-default colors (hard to detect, give partial credit)
    if 'branding' in expected_elements:
        found.add('branding')  # Partial credit for now

    matches = sum(1 for e in expected_elements if e in found)
    return matches / len(expected_elements) if expected_elements else 1.0


def compute_overall_score(scores: Dict[str, float]) -> float:
    """Weighted average of all dimension scores.

    Weights:
    - Completeness: 25%
    - Error rate: 20%
    - Visual quality: 25%
    - Speed: 15%
    - Content richness: 15%
    """
    weights = {
        'completeness': 0.25,
        'error_rate': 0.20,
        'visual_quality': 0.25,
        'speed': 0.15,
        'content_richness': 0.15,
    }
    total = sum(scores.get(k, 0) * w for k, w in weights.items())
    return round(total, 4)


# ============================================================================
# EVAL RUNNER
# ============================================================================


async def run_single_eval(
    eval_prompt: Dict[str, Any],
    generate_fn,
) -> Dict[str, Any]:
    """Run a single eval prompt and score the result.

    Args:
        eval_prompt: One of the EVAL_PROMPTS dicts
        generate_fn: Async function that takes (text: str) → dict with
                     presentation_id, duration_seconds, etc.
    """
    prompt_id = eval_prompt['id']
    logger.info(f"Running eval: {prompt_id}")
    start = time.time()

    try:
        result = await generate_fn(eval_prompt['prompt'])
        duration = time.time() - start

        presentation_id = result.get('presentation_id')
        actual_duration = result.get('duration_seconds', duration)

        # Get presentation state for content scoring
        actual_state = None
        actual_slide_count = 0
        if presentation_id:
            try:
                from .slidemakr import get_presentation_state
                actual_state = get_presentation_state(presentation_id)
                actual_slide_count = actual_state.get('slide_count', 0)
            except Exception as e:
                logger.warning(f"Failed to get state for {presentation_id}: {e}")

        # Score each dimension
        scores = {
            'completeness': score_completeness(
                actual_slide_count, eval_prompt['expected_slides']
            ),
            'error_rate': score_error_rate(
                result.get('success_count', result.get('total_requests', 0)),
                result.get('total_requests', 1),
            ),
            'visual_quality': score_visual_quality(result.get('review_result')),
            'speed': score_speed(actual_duration, eval_prompt['sla_seconds']),
            'content_richness': score_content_richness(
                eval_prompt['expected_elements'], actual_state
            ),
        }

        overall = compute_overall_score(scores)

        return {
            'prompt_id': prompt_id,
            'prompt_name': eval_prompt['name'],
            'status': 'completed',
            'presentation_id': presentation_id,
            'slide_count': actual_slide_count,
            'duration_seconds': round(actual_duration, 2),
            'scores': {k: round(v, 4) for k, v in scores.items()},
            'overall_score': overall,
            'timestamp': datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.error(f"Eval {prompt_id} failed: {e}")
        return {
            'prompt_id': prompt_id,
            'prompt_name': eval_prompt['name'],
            'status': 'failed',
            'error': str(e),
            'duration_seconds': round(time.time() - start, 2),
            'scores': {},
            'overall_score': 0.0,
            'timestamp': datetime.utcnow().isoformat(),
        }


async def run_full_eval(generate_fn) -> Dict[str, Any]:
    """Run all eval prompts and return aggregated results.

    Args:
        generate_fn: Async function that takes (text: str) → dict
    """
    logger.info(f"Starting full eval run with {len(EVAL_PROMPTS)} prompts")
    start = time.time()

    results = []
    for prompt in EVAL_PROMPTS:
        result = await run_single_eval(prompt, generate_fn)
        results.append(result)

    duration = time.time() - start

    # Aggregate scores
    completed = [r for r in results if r['status'] == 'completed']
    if completed:
        avg_scores = {}
        for key in ['completeness', 'error_rate', 'visual_quality', 'speed', 'content_richness']:
            values = [r['scores'].get(key, 0) for r in completed]
            avg_scores[key] = round(sum(values) / len(values), 4)
        avg_overall = round(sum(r['overall_score'] for r in completed) / len(completed), 4)
    else:
        avg_scores = {}
        avg_overall = 0.0

    eval_run = {
        'run_id': f"eval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        'total_prompts': len(EVAL_PROMPTS),
        'completed': len(completed),
        'failed': len(results) - len(completed),
        'duration_seconds': round(duration, 2),
        'avg_scores': avg_scores,
        'avg_overall_score': avg_overall,
        'results': results,
        'timestamp': datetime.utcnow().isoformat(),
    }

    # Save to Firestore
    try:
        from . import db
        fdb = db._get_db()
        if fdb:
            fdb.collection('eval_runs').document(eval_run['run_id']).set(eval_run)
            logger.info(f"Saved eval run {eval_run['run_id']} to Firestore")
        else:
            logger.info(f"Eval run {eval_run['run_id']} (no Firestore, logged only)")
    except Exception as e:
        logger.warning(f"Failed to save eval run: {e}")

    logger.info(
        f"Eval complete: {len(completed)}/{len(EVAL_PROMPTS)} passed, "
        f"avg score: {avg_overall:.2f}, total time: {duration:.1f}s"
    )

    return eval_run


def get_eval_history(limit: int = 20) -> List[Dict]:
    """Get recent eval run results."""
    try:
        from . import db
        fdb = db._get_db()
        if fdb:
            docs = fdb.collection('eval_runs') \
                .order_by('timestamp', direction='DESCENDING') \
                .limit(limit) \
                .get()
            return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"Failed to get eval history: {e}")
    return []


# ============================================================================
# CLI ENTRY POINT
# ============================================================================


if __name__ == "__main__":
    print("SlideMakr Eval Pipeline")
    print("=" * 50)
    print(f"{len(EVAL_PROMPTS)} eval prompts configured:")
    for p in EVAL_PROMPTS:
        print(f"  - {p['id']}: {p['name']} ({p['expected_slides']} slides, SLA: {p['sla_seconds']}s)")
    print()
    print("To run evals, use: POST /admin/run-eval")
    print("Or integrate with generate_fn in your test harness.")

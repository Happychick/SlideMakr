from pathlib import Path


def test_blog_outline_covers_ai_slide_creation_comparison():
    text = Path("docs/blog-ai-slide-creation.md").read_text()

    for phrase in [
        "all the ways",
        "voice-first",
        "first-shot adherence",
        "consultants",
        "Google Slides",
        "PowerPoint",
    ]:
        assert phrase in text


def test_video_checklist_covers_each_product_demo():
    text = Path("docs/video-demo-plan.md").read_text()

    for phrase in [
        "web voice creation",
        "voice editing",
        "Stripe checkout",
        "Google Slides add-on",
        "PowerPoint add-in",
        "feedback editing",
    ]:
        assert phrase in text


def test_clay_outreach_plan_targets_5000_consultants():
    text = Path("docs/clay-outreach-plan.md").read_text()

    for phrase in [
        "5000",
        "consultants",
        "one week free",
        "security",
        "demo",
        "convert",
    ]:
        assert phrase in text

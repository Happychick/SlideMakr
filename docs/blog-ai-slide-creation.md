# All the Ways to Make Slides with AI (and Why I Built One More)

I've tried most of the "AI slides" tools out there: chat prompts that spit out
an outline, document-to-deck converters, template generators, meeting
summarizers that turn a call into a recap deck, and the AI add-ons now built
into Google Slides and PowerPoint. Each one is fine at something. None of
them solved the problem I actually had, which is: I say what I want on the
slide, and I want it to land on the slide — the first time, without me
re-arranging boxes afterward.

That's why I built [SlideMakr](https://slidemakr-72711045873.us-central1.run.app),
a voice-first way to create and edit Google Slides decks.

## Why the old workflow doesn't cut it

The chat-prompt tools are great for outlines and weak on finished decks —
you still do the layout work yourself. Document-to-deck tools are good when
you already have a source doc, but slower when you're thinking out loud.
Template-first tools look polished but often miss a specific instruction,
like "put the org chart on slide 4." Meeting-to-deck tools are useful after
a call but hard to steer toward an exact slide. And the in-app AI add-ons
are convenient because you stay in your existing file, but they inherit the
same "close, not exact" problem as the rest.

The thing I kept running into, across all of them, is that "AI made a deck"
and "AI made the deck I asked for" are two different claims. Most tools are
optimizing for the first one.

## What I measure instead of trusting my own eyes

I didn't want to just believe my own tool works, so I built an eval suite
that scores SlideMakr the same way I'd judge it by hand: did the instruction
land, is the slide usable (fonts consistent, nothing overlapping, things
fit), and how fast did it happen. The creation eval runs a set of standard
prompts — a simple four-slide deck, a flowchart deck, a chart deck, an image
deck, a branded deck — plus two prompts built specifically to test whether a
named instruction lands on the exact slide it names, like putting a
flowchart on slide 6. The edit eval does something similar for voice
editing: it creates one deck from a single prompt, then runs it through six
scripted edits in a row — retitle, recolor, add a chart, add a flowchart,
retitle the subtitle, delete the chart — scoring each edit right after it
lands, with a vision pass checking the slide actually looks right, not just
that the API call succeeded.

The full scoring methodology is public in `evals/README.md` in the repo, and
I run these evals before and after every change, not just once for a launch
post — that's what "the eval is the yardstick" means in practice: no shipped
change without a run, and no marketing claim without a score behind it. One
part of the eval matters more the longer I use it: "usability" — does the
slide actually look good, not just technically correct — and it's the part
that keeps growing as I close real gaps turned up by real prompts, edit
after edit.

## What SlideMakr actually does right now

Say what you want, out loud or typed, and SlideMakr creates the deck
directly in Google Slides — no export, no template picker. Once it exists,
you can keep editing it by voice: open the deck, say "add a chart on slide
2," and watch it happen in the embedded preview. Everything works before
you sign in; you only need to log in with Google to keep the deck and share
it from your Drive. And because generating decks costs real compute, the
first deck is free and you buy credits to keep making more — no
subscription, no seat license, just credits when you need them.

## What's not there yet

I'm not claiming a Google Slides sidebar add-on or a PowerPoint add-in here,
because they're not what most people are using day to day yet. This post is
about the web tool: text or voice in, a real Google Slides deck out. If that
changes, the evals will say so before the marketing does.

## Try it

Give SlideMakr one real deck you actually need. If it misses a requested
slide, that's the bug I care about most — tell me and I'll fix it.

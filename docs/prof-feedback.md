# Professor Feedback

Written record of the annotations found in the professor's marked-up PDFs, so
there is a tracked list before we act on any of it. Nothing here has been
addressed yet — each item has a corresponding `% TODO: address prof feedback --
see docs/prof-feedback.md` marker at the point in the blueprint where the fix
belongs.

## 1. Environment setup doesn't cover already-cloned repos

The environment setup walkthrough only covers creating a fresh project with
`lake new`. It says nothing about the case where you are working in a repo that
has already been cloned — which is how anyone joining this project will actually
start.

Related lake gotcha to fold in at the same time: the folder name should be
different from `math`. Naming the project folder `math` collides and causes
trouble, so the walkthrough should call that out explicitly.

**Where it belongs:** environment setup chapter, alongside the `lake new` flow.

## 2. Unanswered open question: "what to refresh?"

The annotation asks "what to refresh?" and the question is currently left
unanswered in the docs. We need to state plainly what needs refreshing, when,
and by what command.

**Where it belongs:** environment setup chapter, at the point the question is
raised.

## 3. Missing section: "Leanblueprint set up"

There is no section covering leanblueprint setup at all. It is missing entirely
rather than being thin — the blueprint toolchain is now part of this repo and
needs its own walkthrough.

**Where it belongs:** environment setup chapter, as a new section.

## Status

| # | Item | Addressed? |
| - | ---- | ---------- |
| 1 | Already-cloned repo workflow (+ folder-name-vs-`math` gotcha) | [ ] |
| 2 | Answer "what to refresh?" | [ ] |
| 3 | Add "Leanblueprint set up" section | [ ] |

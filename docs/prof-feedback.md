# Professor Feedback

Written record of the annotations found in the professor's marked-up PDFs, kept
as a tracked list of what was raised and where it was addressed. All three items
have now been written into the environment setup chapter; the `% TODO: address
prof feedback` markers that previously stood in for them have been replaced by
the actual content.

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
| 1 | Already-cloned repo workflow (+ folder-name-vs-`math` gotcha) | [x] |
| 2 | Answer "what to refresh?" | [x] |
| 3 | Add "Leanblueprint set up" section | [x] |

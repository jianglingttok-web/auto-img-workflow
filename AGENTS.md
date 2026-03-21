# AGENTS.md

## Goal
Build a TK listing automation system with:
1. AI image workflow
2. AI copy workflow
3. Feishu review flow
4. Listing package builder
5. Browser-based publish executor

## Rules
- Do not let AI-generated content directly operate browser actions.
- Browser executor only consumes validated `listing_package.json`.
- All steps must log, screenshot, and return structured errors.
- Build MVP first, then expand.
- Prefer Python implementations first.
- Keep executor swappable so RPA can replace it later.

## Definition of Done
- Can generate `listing_package.json` from one sample product
- Can open browser via 紫鸟 API
- Can fill minimal publish form
- Can upload test images
- Can save draft
- Can push result to Feishu

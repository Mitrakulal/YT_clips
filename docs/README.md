# YT_clips Documentation

This directory contains the design history and the current production operating guide for the project.

| File | Purpose |
|---|---|
| `PRODUCTION_GUIDE.md` | Current segmentation-first architecture, setup, queue operation, artifact contract, and acceptance criteria. Start here. |
| `SEGMENTATION-RESEARCH.md` | Research and validation history for topic/pause boundary detection. |
| `01-RESEARCH-REPORT.md` | Original local-first architecture and deployment rationale. |
| `02-IMPLEMENTATION-PLAN.md` | Historical phased implementation plan and verification notes. Some path/status text is historical; the current code and `PRODUCTION_GUIDE.md` are authoritative. |
| `03-MAC-HANDOFF.md` | Historical Mac setup handoff. Use the current production guide for present-day queue and stage names. |

## Current production contract

The model ranks **pre-built coherent candidate IDs** rather than inventing arbitrary timestamps. Candidates are contiguous transcript units constructed from ASR segments, sentence endings, pauses, and topic boundaries. Candidate spans are preserved into rendering, short fragments are merged, comedy/storytelling reaction pauses remain inside the beat, and FFprobe validation blocks malformed outputs from publication.

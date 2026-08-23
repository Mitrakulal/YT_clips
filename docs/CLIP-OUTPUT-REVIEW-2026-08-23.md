# Generated Clip Output Review

## Scope and method

Nine supplied pipeline outputs were reviewed as finished Shorts, including stand-up comedy and interview clips with and without the captioned render. The review assessed complete narrative beats, opening strength, pacing, vertical reframing, captions, audio, and export quality. Recommendations below preserve the project’s core candidate-first rule: a ranking model may choose a candidate, but it must not invent timestamps or break a complete moment apart.

YouTube’s own analytics guidance supports treating retention as the validation signal: its retention reports expose spikes, dips, and the moments at which viewers start or stop watching; YouTube specifically recommends reviewing dips and moving strong moments earlier when appropriate.[1][2]

## Findings from the supplied outputs

| Area | What is working | What needs correction | Priority |
|---|---|---|---|
| Complete context | Several clips include a clear setup, payoff, and reaction. The strongest comedy clips preserve audience laughter after the punchline. | Some outputs continue into the next unrelated topic or are split into numbered fragments after ranking, weakening the ending. | Critical |
| Captions | Word timing, spoken-word match, and contrast are mostly strong. Audio was consistently clear. | A second static opening/title overlay is visually redundant; several samples show garbled Hindi text or truncated overlay text. Main captions also sit too close to the Shorts lower UI. | Critical |
| Framing | Vertical subject framing is generally centred and stable. | A few interview close-ups are slightly off-centre or tight on headroom. This should be improved with optional face tracking after caption and integrity fixes are verified. | High |
| Hook and pacing | The best outputs open directly on a provocative statement or comedic premise and contain no dead air. | Soft openings and clips that start with a clipped first word lose initial clarity. The system should measure this in real retention data rather than insert generic title cards. | High |
| Export quality | Most 1080p renders are sharp with clean audio. | One short output looked lower resolution/compressed; the system cannot restore missing source detail, so the source-quality fallback must be visible to the user. | Medium |

## Changes implemented

The local renderer now keeps every selected ranked candidate intact at crop time. Semantic and pause boundaries are still used before ranking to form safe candidates, but they can no longer re-split a selected complete moment into partial `short_01_1` / `short_01_2` outputs.

The default static title/hook overlay is now off. This removes the most severe recurring issue in the sample outputs: a transcript-derived title competing with the timed captions and, for Hindi/Hinglish text, appearing garbled or truncated. Dynamic captions remain enabled. A reviewed title card can still be explicitly enabled with `HOOK_TEXT=true`.

Vertical captions now sit 420 pixels above the bottom edge on the 1080×1920 ASS canvas, rather than 230 pixels. This provides a safer lower-middle placement while avoiding the YouTube Shorts description and controls. The caption style uses the Mac-compatible `Kohinoor Devanagari` family, which has Devanagari coverage for Hindi/Hinglish alongside Latin text.

The ranking prompt now uses neighbouring transcript text as an explicit ending audit and instructs the ranker to reject candidates that launch a new unfinished topic rather than conclude on a payoff, conclusion, or natural reaction.

## Validation plan after publishing

The next five generated clips should be reviewed in YouTube Studio after audience-retention data is available, which YouTube says typically takes one to two days to process.[1] Compare clips of similar length, then inspect each retention graph.

| Retention signal | Interpretation | Pipeline response |
|---|---|---|
| First-second dip | Start is unclear, delayed, or audibly clipped. | Extend the start only to the prior word/sentence boundary; do not add a generic static title. |
| Spike at a punchline or quote | Viewers rewatched or shared a high-value moment. | Promote comparable complete beats in ranking; retain setup and reaction. |
| Dip just after a payoff | The candidate carried an extra topic after its natural ending. | Stop candidate construction at the payoff/reaction boundary. |
| Repeated caption-related dips | Subtitle placement or readability is interfering. | Tune only caption margin, line length, or contrast; keep one dynamic caption treatment. |

## Deferred improvements

Face tracking should be evaluated as a separate controlled change, because it can improve interview centring but can also create distracting crop motion. A source-quality warning is also worth adding to the dashboard when the available YouTube source is below the selected target resolution. Neither change should be mixed with the context and caption fixes, so the cause of any quality difference remains clear.

## References

[1] [YouTube Help — Measure key moments for audience retention](https://support.google.com/youtube/answer/9314415?hl=en-GB&co=GENIE.Platform%3DAndroid)

[2] [YouTube Help — Tips to understand your video performance](https://support.google.com/youtube/answer/12942217?hl=en&co=YOUTUBE._YTVideoType%3Dvideo)
